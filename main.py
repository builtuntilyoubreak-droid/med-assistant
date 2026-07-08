"""
app/main.py

FastAPI backend that ties together:
  - Historical usage data (clinic_inventory_usage.csv)
  - A demand forecasting model (app/forecasting.py)
  - Current stock + expiry batches (SQLite, app/database.py)
  - A transfer-matching engine that pairs low-stock clinics with
    surplus/near-expiry clinics (app/transfer_engine.py)

Run with:
    uvicorn app.main:app --reload --port 8000

Then visit http://localhost:8000/docs for interactive API docs.
"""

import sys
import os
from datetime import datetime

# Add parent directory of 'app' to sys.path to resolve 'app.x' imports when running main.py directly
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app.forecasting import predict_daily_usage
from app.transfer_engine import (
    build_status_table,
    get_alerts,
    suggest_transfers,
    build_batch_status_list,
    suggest_clinic_actions,
)
from app.database import (
    fetch_all_batches,
    fetch_batch,
    update_batch_quantity,
    insert_batch,
    log_transaction,
    fetch_transaction_history,
    create_request,
    fetch_requests,
    fetch_request,
    update_request_status,
    get_connection,
)
from app.schemas import (
    ExecuteTransferRequest,
    NewStockBatch,
    CreateRequestModel,
    RespondRequestModel,
)


app = FastAPI(
    title="Clinic Inventory Redistribution API",
    description=(
        "Predicts clinic-level medicine demand and matches clinics with "
        "low stock to clinics with surplus or soon-to-expire stock for "
        "redistribution."
    ),
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/stock")
def get_stock():
    """Raw stock batches currently on record."""
    return fetch_all_batches()


@app.get("/forecast")
def get_forecast():
    """Predicted daily usage rate per clinic/item."""
    return predict_daily_usage().to_dict(orient="records")


@app.get("/status")
def get_status():
    """Full status table: stock, predicted usage, days-of-supply,
    classification (LOW_STOCK / NORMAL / SURPLUS), and expiry flags."""
    return build_status_table().to_dict(orient="records")


@app.get("/alerts")
def get_alerts_endpoint():
    """Low-stock alerts, surplus clinics, and batches expiring soon."""
    return get_alerts()


@app.get("/transactions/suggested")
def get_suggested_transfers():
    """Proposed clinic-to-clinic transfers that would relieve low-stock
    clinics using surplus or near-expiry stock elsewhere."""
    return suggest_transfers()


@app.get("/transactions/history")
def get_transaction_history():
    """Previously executed (or manually logged) transactions."""
    return fetch_transaction_history()


@app.post("/transactions/execute")
def execute_transfer(request: ExecuteTransferRequest):
    """
    Executes a transfer: deducts quantity from the source batch and adds
    a new stock batch (same expiry date) at the destination clinic, then
    logs the transaction.
    """
    batch = fetch_batch(request.batch_row_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="Source batch not found")
    if batch["clinic"] != request.from_clinic or batch["item"] != request.item:
        raise HTTPException(status_code=400, detail="Batch does not match from_clinic/item")
    if batch["quantity"] < request.quantity:
        raise HTTPException(status_code=400, detail="Not enough quantity in source batch")

    # Deduct from source
    update_batch_quantity(request.batch_row_id, batch["quantity"] - request.quantity)

    # Add to destination (new batch record, same expiry date carried over)
    new_batch_id = f"{request.batch_id}->{request.to_clinic[-1]}"
    insert_batch(
        clinic=request.to_clinic,
        item=request.item,
        batch_id=new_batch_id,
        quantity=request.quantity,
        expiry_date=batch["expiry_date"],
    )

    log_transaction(
        from_clinic=request.from_clinic,
        to_clinic=request.to_clinic,
        item=request.item,
        batch_id=request.batch_id,
        quantity=request.quantity,
        reason=request.reason,
        status="EXECUTED",
    )

    return {"message": "Transfer executed", "detail": request}


@app.post("/stock/add")
def add_stock(batch: NewStockBatch):
    """Manually add a new stock batch (e.g. new delivery arrives)."""
    insert_batch(
        clinic=batch.clinic,
        item=batch.item,
        batch_id=batch.batch_id,
        quantity=batch.quantity,
        expiry_date=batch.expiry_date,
    )
    return {"message": "Stock batch added"}


@app.get("/stock/batches")
def get_batches_status():
    """Stock batches enriched with forecast, days of supply and calculated penalties."""
    return build_batch_status_list()


@app.get("/actions/suggested")
def get_clinic_suggested_actions(clinic: str):
    """Actions (enquiries or offers) suggested for the specified clinic."""
    return suggest_clinic_actions(clinic)


@app.get("/requests")
def get_clinic_requests(clinic: str):
    """Active and past enquiries/offers involving this clinic."""
    return fetch_requests(clinic)


@app.post("/requests/create")
def create_clinic_request(request: CreateRequestModel):
    """Creates a new stock enquiry or redistribution offer."""
    req_id = create_request(
        type=request.type,
        item=request.item,
        from_clinic=request.from_clinic,
        to_clinic=request.to_clinic,
        quantity=request.quantity,
        batch_row_id=request.batch_row_id,
        reason=request.reason,
        penalty_score=request.penalty_score
    )
    return {"message": "Request created successfully", "request_id": req_id}


@app.post("/requests/respond")
def respond_to_clinic_request(body: RespondRequestModel):
    """
    Approve or reject a request.
    If APPROVED:
      - For ENQUIRY (from_clinic needs stock, to_clinic supplies it):
        Deducts quantity from the oldest batch at to_clinic, adds to from_clinic.
      - For OFFER (from_clinic has excess, to_clinic gets it):
        Deducts from the specified batch_row_id, adds to to_clinic.
    """
    req = fetch_request(body.request_id)
    if not req:
        raise HTTPException(status_code=404, detail="Request not found")
        
    if req["status"] != "PENDING":
        raise HTTPException(status_code=400, detail="Request is already resolved")

    if body.action == "REJECTED":
        update_request_status(body.request_id, "REJECTED")
        return {"message": "Request rejected successfully"}

    if body.action != "APPROVED":
        raise HTTPException(status_code=400, detail="Invalid action")

    # APPROVED path: Execute the transfer
    from_clinic = req["from_clinic"]
    to_clinic = req["to_clinic"]
    item = req["item"]
    qty = req["quantity"]
    req_type = req["type"]

    if req_type == "OFFER":
        if not req["batch_row_id"]:
            raise HTTPException(status_code=400, detail="Offer must specify batch_row_id")
        source_batch = fetch_batch(req["batch_row_id"])
        if not source_batch:
            raise HTTPException(status_code=400, detail="Source batch not found")
        
        if source_batch["quantity"] < qty:
            raise HTTPException(
                status_code=400,
                detail=f"Source batch has insufficient stock ({source_batch['quantity']} units available)"
            )

        # Execute single batch transfer
        update_batch_quantity(source_batch["id"], source_batch["quantity"] - qty)
        dest_clinic = to_clinic
        new_batch_id = f"{source_batch['batch_id']}->{dest_clinic[-1]}"
        insert_batch(
            clinic=dest_clinic,
            item=item,
            batch_id=new_batch_id,
            quantity=qty,
            expiry_date=source_batch["expiry_date"]
        )
        log_transaction(
            from_clinic=source_batch["clinic"],
            to_clinic=dest_clinic,
            item=item,
            batch_id=source_batch["batch_id"],
            quantity=qty,
            reason=req["reason"],
            status="EXECUTED"
        )
    else:
        # ENQUIRY: from_clinic asks to_clinic for stock. to_clinic is the source.
        # Find all batches at to_clinic for this item, sorted by oldest first
        conn = get_connection()
        rows = conn.execute(
            "SELECT * FROM stock_batches WHERE clinic = ? AND item = ? ORDER BY expiry_date ASC",
            (to_clinic, item)
        ).fetchall()
        conn.close()
        
        batches_list = [dict(r) for r in rows]
        total_available = sum(b["quantity"] for b in batches_list)
        
        if total_available < qty:
            raise HTTPException(
                status_code=400,
                detail=f"Source clinic {to_clinic} has insufficient total stock of {item} ({total_available} available)"
            )

        # Deduct sequentially (FIFO oldest first)
        remaining_qty = qty
        for b in batches_list:
            if remaining_qty <= 0:
                break
            qty_to_draw = min(remaining_qty, b["quantity"])
            
            # Deduct from source batch
            update_batch_quantity(b["id"], b["quantity"] - qty_to_draw)
            
            # Add to destination clinic (from_clinic)
            dest_clinic = from_clinic
            new_batch_id = f"{b['batch_id']}->{dest_clinic[-1]}"
            insert_batch(
                clinic=dest_clinic,
                item=item,
                batch_id=new_batch_id,
                quantity=qty_to_draw,
                expiry_date=b["expiry_date"]
            )
            
            # Log transaction
            log_transaction(
                from_clinic=to_clinic,
                to_clinic=dest_clinic,
                item=item,
                batch_id=b["batch_id"],
                quantity=qty_to_draw,
                reason=req["reason"],
                status="EXECUTED"
            )
            remaining_qty -= qty_to_draw

    # 4. Complete the request
    update_request_status(body.request_id, "APPROVED")

    return {"message": "Request approved and stock transferred successfully"}


@app.post("/requests/ai-analyze-auto")
def ai_analyze_and_auto_order (clinic: str):
    """
    Runs the machine learning seasonality and trend forecasting model on recent clinic usage data.
    Detects upcoming demand spikes (where predicted daily demand exceeds safety limits or weekly seasonality spikes),
    and automatically executes stock transfer orders from nearby clinics with available surplus.
    """
    # 1. Fetch batches status (computed using forecasting polyfit + seasonality)
    status_list = build_batch_status_list()
    
    # Filter for our clinic to assess local stock and forecasts
    our_items = {}
    for b in status_list:
        if b["clinic"] == clinic:
            item = b["item"]
            if item not in our_items:
                our_items[item] = {
                    "total_stock": 0,
                    "predicted_daily_usage": b["predicted_daily_usage"],
                    "days_of_supply": b["days_of_supply"],
                    "upcoming_forecast": b["upcoming_forecast"],
                    "weekday_multipliers": b["weekday_multipliers"],
                    "season_insight": b["season_insight"]
                }
            our_items[item]["total_stock"] += b["quantity"]

    # Other clinics' status to draw stock from
    other_batches = [b for b in status_list if b["clinic"] != clinic]

    predictions = []
    auto_transfers = []

    for item, info in our_items.items():
        forecast = info["upcoming_forecast"]
        predicted_avg = info["predicted_daily_usage"]
        current_stock = info["total_stock"]
        
        if not forecast:
            continue
            
        max_predicted = max(forecast)
        # Calculate spike percentage: peak day vs average predicted
        spike_pct = 0
        if predicted_avg > 0:
            spike_pct = round(((max_predicted - predicted_avg) / predicted_avg) * 100, 1)
            
        # We classify it as an "upcoming spike" if spike_pct > 15.0 or (forecast[-1] > forecast[0] * 1.1)
        is_spike = spike_pct > 15.0 or (forecast[-1] > forecast[0] * 1.1)
        
        # We need an auto-order if there is a predicted spike AND our current stock is low/normal
        # (e.g. days of supply < 12 days, which is risky during a spike).
        needs_order = is_spike and (info["days_of_supply"] < 12.0)
        
        predictions.append({
            "item": item,
            "current_stock": int(current_stock),
            "predicted_daily_usage": float(predicted_avg),
            "upcoming_peak_units": float(max_predicted),
            "spike_probability_pct": int(min(98, 50 + spike_pct)) if bool(is_spike) else 10,
            "is_spike_predicted": bool(is_spike),
            "needs_preventative_order": bool(needs_order),
            "season_insight": str(info["season_insight"])
        })

        if needs_order:
            # Calculate safety target: we want to reach 15 days of supply
            target_qty = int(round(15 * predicted_avg))
            needed_qty = int(target_qty - current_stock)
            if needed_qty <= 10:
                continue # too small to bother
                
            # Search other clinics for available stock (sorted by days of supply descending)
            item_candidates = [b for b in other_batches if b["item"] == item and b["quantity"] > 10]
            item_candidates = sorted(item_candidates, key=lambda x: x["days_of_supply"], reverse=True)
            
            for cand in item_candidates:
                if needed_qty <= 0:
                    break
                    
                # Sibling clinic must maintain its safety stock (10 days)
                sibling_predicted = cand["predicted_daily_usage"]
                sibling_batches = [b for b in other_batches if b["clinic"] == cand["clinic"] and b["item"] == item]
                sibling_total_stock = sum(b["quantity"] for b in sibling_batches)
                sibling_safety = 10 * sibling_predicted
                
                spare = int(max(0, sibling_total_stock - sibling_safety))
                available_in_batch = cand["quantity"]
                qty_to_pull = min(needed_qty, spare, available_in_batch)
                
                if qty_to_pull <= 5:
                    continue
                    
                # Execute transfer immediately
                # 1. Deduct from source batch
                update_batch_quantity(cand["id"], cand["quantity"] - qty_to_pull)
                
                # 2. Add to our clinic
                new_batch_id = f"{cand['batch_id']}->{clinic[-1]}"
                insert_batch(
                    clinic=clinic,
                    item=item,
                    batch_id=new_batch_id,
                    quantity=qty_to_pull,
                    expiry_date=cand["expiry_date"]
                )
                
                # 3. Log transaction
                log_transaction(
                    from_clinic=cand["clinic"],
                    to_clinic=clinic,
                    item=item,
                    batch_id=cand["batch_id"],
                    quantity=qty_to_pull,
                    reason="AI_SPIKE_REDISTRIBUTION",
                    status="EXECUTED"
                )
                
                # 4. Record request in request database as completed (APPROVED)
                create_request(
                    type="ENQUIRY",
                    item=item,
                    from_clinic=clinic,
                    to_clinic=cand["clinic"],
                    quantity=qty_to_pull,
                    batch_row_id=None,
                    reason="AI_SPIKE_PREDICTION",
                    penalty_score=int(spike_pct)
                )
                
                current_stock += qty_to_pull
                needed_qty -= qty_to_pull
                
                auto_transfers.append({
                    "item": item,
                    "from_clinic": cand["clinic"],
                    "to_clinic": clinic,
                    "quantity": int(qty_to_pull),
                    "source_batch": str(cand["batch_id"]),
                    "peak_day_demand": float(max_predicted)
                })
                
    # Update request status to APPROVED in database for all requests we just executed
    conn = get_connection()
    conn.execute("UPDATE requests SET status = 'APPROVED' WHERE reason = 'AI_SPIKE_PREDICTION' AND status = 'PENDING'")
    conn.commit()
    conn.close()

    return {
        "clinic": clinic,
        "timestamp": datetime.utcnow().isoformat(),
        "predictions": predictions,
        "auto_transfers": auto_transfers
    }


# Serve Frontend Files from Memory to prevent virtual drive/network disconnect issues
from fastapi.responses import HTMLResponse, Response

frontend_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend")

# Cache contents in memory
frontend_cache = {}

def load_frontend_cache():
    try:
        for filename in ["index.html", "index.css", "app.js"]:
            filepath = os.path.join(frontend_dir, filename)
            if os.path.exists(filepath):
                with open(filepath, "r", encoding="utf-8") as f:
                    frontend_cache[filename] = f.read()
    except Exception as e:
        print(f"Error caching frontend: {e}")

load_frontend_cache()

@app.get("/", response_class=HTMLResponse)
def read_root():
    if "index.html" not in frontend_cache:
        load_frontend_cache()
    content = frontend_cache.get("index.html", "<h1>CareShare Server Active</h1><p>Syncing frontend files...</p>")
    return HTMLResponse(content=content, status_code=200)

@app.get("/index.css")
def read_css():
    if "index.css" not in frontend_cache:
        load_frontend_cache()
    content = frontend_cache.get("index.css", "")
    return Response(content=content, media_type="text/css")

@app.get("/app.js")
def read_js():
    if "app.js" not in frontend_cache:
        load_frontend_cache()
    content = frontend_cache.get("app.js", "")
    return Response(content=content, media_type="application/javascript")


if __name__ == "__main__":
    import uvicorn
    # Start uvicorn server on all interfaces (0.0.0.0) with reload disabled to avoid restarts on DB writes
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=False)


