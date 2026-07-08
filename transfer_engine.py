"""
app/transfer_engine.py

Core matching logic:
  1. Combine current stock (from SQLite) with predicted daily usage
     (from the forecasting model) to compute "days of supply" per
     clinic/item.
  2. Classify each clinic/item as LOW_STOCK, SURPLUS, or NORMAL, and
     flag any individual batch nearing expiry.
  3. Match LOW_STOCK clinics with SURPLUS / near-expiry clinics for the
     same item, and propose transfer transactions - prioritizing
     soonest-expiring stock first (so it gets used before it's wasted),
     then filling remaining need from other surplus clinics.
"""

from datetime import datetime, timedelta
import pandas as pd

from app.forecasting import predict_daily_usage
from app.database import fetch_all_batches

TODAY = datetime(2026, 7, 1)

# --- Tunable thresholds ---
LOW_STOCK_DAYS = 7        # below this many days of supply -> LOW_STOCK
SURPLUS_DAYS = 25         # above this many days of supply -> SURPLUS
TARGET_DAYS_OF_SUPPLY = 15  # what a transfer tries to top a low clinic up to
EXPIRY_WINDOW_DAYS = 14   # batches expiring within this window -> EXPIRING_SOON
SAFETY_STOCK_DAYS = 10    # surplus clinics won't give up stock below this


def build_stock_summary() -> pd.DataFrame:
    """Aggregate stock batches into total quantity per clinic/item, plus
    the nearest expiry date in that group."""
    batches = fetch_all_batches()
    if not batches:
        return pd.DataFrame(columns=["clinic", "item", "total_quantity", "nearest_expiry"])

    df = pd.DataFrame(batches)
    df["expiry_date"] = pd.to_datetime(df["expiry_date"])

    summary = (
        df.groupby(["clinic", "item"])
        .agg(total_quantity=("quantity", "sum"), nearest_expiry=("expiry_date", "min"))
        .reset_index()
    )
    return summary


def build_status_table() -> pd.DataFrame:
    """Join stock levels with predicted usage and classify each
    clinic/item combination."""
    stock = build_stock_summary()
    forecast = predict_daily_usage()

    merged = stock.merge(forecast, on=["clinic", "item"], how="left")
    merged["predicted_daily_usage"] = merged["predicted_daily_usage"].fillna(0.1)

    merged["days_of_supply"] = (
        merged["total_quantity"] / merged["predicted_daily_usage"]
    ).round(1)

    merged["days_to_expiry"] = (merged["nearest_expiry"] - TODAY).dt.days

    def classify(row):
        if row["days_of_supply"] < LOW_STOCK_DAYS:
            return "LOW_STOCK"
        elif row["days_of_supply"] > SURPLUS_DAYS:
            return "SURPLUS"
        return "NORMAL"

    merged["status"] = merged.apply(classify, axis=1)
    merged["expiring_soon"] = merged["days_to_expiry"] <= EXPIRY_WINDOW_DAYS

    return merged.sort_values(["item", "days_of_supply"])


def get_alerts() -> dict:
    """Return low-stock and expiring-soon alerts, grouped for readability."""
    status = build_status_table()

    low_stock = status[status["status"] == "LOW_STOCK"].to_dict(orient="records")
    surplus = status[status["status"] == "SURPLUS"].to_dict(orient="records")
    expiring_soon = status[status["expiring_soon"]].to_dict(orient="records")

    return {
        "low_stock": low_stock,
        "surplus": surplus,
        "expiring_soon": expiring_soon,
    }


def suggest_transfers() -> list[dict]:
    """
    Keep this for compatibility. Maps low-stock to surplus.
    """
    status = build_status_table()
    batches = pd.DataFrame(fetch_all_batches())
    if batches.empty:
        return []
    batches["expiry_date"] = pd.to_datetime(batches["expiry_date"])
    remaining_in_batch = {int(r["id"]): int(r["quantity"]) for _, r in batches.iterrows()}
    suggestions = []

    for item, item_status in status.groupby("item"):
        low_clinics = (
            item_status[item_status["status"] == "LOW_STOCK"]
            .sort_values("days_of_supply")
            .to_dict(orient="records")
        )
        if not low_clinics:
            continue

        source_candidates = item_status[
            (item_status["status"] == "SURPLUS") | (item_status["expiring_soon"])
        ].copy()

        for low in low_clinics:
            to_clinic = low["clinic"]
            need_qty = int(round(
                TARGET_DAYS_OF_SUPPLY * low["predicted_daily_usage"] - low["total_quantity"]
            ))
            if need_qty <= 0:
                continue

            item_batches = batches[
                (batches["item"] == item) & (batches["clinic"] != to_clinic)
            ].merge(
                source_candidates[["clinic", "predicted_daily_usage"]],
                on="clinic", how="inner",
            ).sort_values("expiry_date")

            for _, batch in item_batches.iterrows():
                if need_qty <= 0:
                    break

                batch_row_id = int(batch["id"])
                qty_left_in_batch = remaining_in_batch.get(batch_row_id, 0)
                if qty_left_in_batch <= 0:
                    continue

                from_clinic = batch["clinic"]
                is_expiring = (batch["expiry_date"] - TODAY).days <= EXPIRY_WINDOW_DAYS

                if is_expiring:
                    available = qty_left_in_batch
                    reason = "EXPIRY_RISK_REDISTRIBUTION"
                else:
                    safety_qty = SAFETY_STOCK_DAYS * batch["predicted_daily_usage"]
                    source_total = item_status.loc[
                        item_status["clinic"] == from_clinic, "total_quantity"
                    ].values[0]
                    spare_at_clinic = max(0, source_total - safety_qty)
                    available = int(min(qty_left_in_batch, spare_at_clinic))
                    reason = "LOW_STOCK_REPLENISH"

                if available <= 0:
                    continue

                transfer_qty = int(min(available, need_qty))
                if transfer_qty <= 0:
                    continue

                suggestions.append({
                    "item": item,
                    "from_clinic": from_clinic,
                    "to_clinic": to_clinic,
                    "batch_id": batch["batch_id"],
                    "batch_row_id": batch_row_id,
                    "quantity": transfer_qty,
                    "expiry_date": batch["expiry_date"].strftime("%Y-%m-%d"),
                    "reason": reason,
                })
                need_qty -= transfer_qty
                remaining_in_batch[batch_row_id] = qty_left_in_batch - transfer_qty

    return suggestions


# --- Single Clinic Enquiries & Offers Proposing Logic ---

def build_batch_status_list() -> list[dict]:
    """Returns a list of all stock batches, enriched with their item-level
    forecast, days of supply, and computed holding/expiry penalties."""
    batches = fetch_all_batches()
    if not batches:
        return []
    
    forecast = predict_daily_usage()
    df_batches = pd.DataFrame(batches)
    df_batches["expiry_date_dt"] = pd.to_datetime(df_batches["expiry_date"])
    
    merged = df_batches.merge(forecast, on=["clinic", "item"], how="left")
    merged["predicted_daily_usage"] = merged["predicted_daily_usage"].fillna(0.1)
    
    # Calculate total clinic stock for days-of-supply aggregation
    stock_summary = (
        df_batches.groupby(["clinic", "item"])["quantity"]
        .sum()
        .reset_index()
        .rename(columns={"quantity": "total_clinic_stock"})
    )
    
    merged = merged.merge(stock_summary, on=["clinic", "item"], how="left")
    merged["days_of_supply"] = (merged["total_clinic_stock"] / merged["predicted_daily_usage"]).round(1)
    
    results = []
    for _, row in merged.iterrows():
        days_to_expiry = (row["expiry_date_dt"] - TODAY).days
        
        # Expiry Penalty
        expiry_penalty = 0
        if days_to_expiry <= 14:
            expiry_penalty = max(0, (15 - days_to_expiry) * 10)
            
        # Holding Penalty
        holding_penalty = 0
        if row["days_of_supply"] > 25:
            holding_penalty = min(100, int((row["days_of_supply"] - 25) * 2))
            
        total_penalty = int(expiry_penalty + holding_penalty)
        
        # Serialize multipliers for easier rendering
        mults = row["weekday_multipliers"]
        if not isinstance(mults, dict):
            mults = {}
            
        results.append({
            "id": int(row["id"]),
            "clinic": row["clinic"],
            "item": row["item"],
            "batch_id": row["batch_id"],
            "quantity": int(row["quantity"]),
            "expiry_date": row["expiry_date"],
            "days_to_expiry": int(days_to_expiry),
            "predicted_daily_usage": float(row["predicted_daily_usage"]),
            "days_of_supply": float(row["days_of_supply"]),
            "expiry_penalty": int(expiry_penalty),
            "holding_penalty": int(holding_penalty),
            "total_penalty": total_penalty,
            "upcoming_forecast": row["upcoming_forecast"] if isinstance(row["upcoming_forecast"], list) else [],
            "weekday_multipliers": mults,
            "season_insight": row["season_insight"] if isinstance(row["season_insight"], str) else ""
        })
        
    return results


def suggest_clinic_actions(clinic: str) -> list[dict]:
    """
    Generate proposed requests (ENQUIRIES or OFFERS) for a specific clinic.
    """
    status = build_status_table()
    batches = fetch_all_batches()
    
    # Exclude items that already have a PENDING request
    import sqlite3
    conn = sqlite3.connect("inventory.db")
    conn.row_factory = sqlite3.Row
    pending_rows = conn.execute("SELECT item, from_clinic, to_clinic, type FROM requests WHERE status = 'PENDING'").fetchall()
    conn.close()
    pending_keys = {(r["item"], r["from_clinic"], r["to_clinic"], r["type"]) for r in pending_rows}

    suggestions = []
    our_status = status[status["clinic"] == clinic]
    other_status = status[status["clinic"] != clinic]
    
    # 1. LOW STOCK -> Send ENQUIRY to get stock
    for _, row in our_status[our_status["status"] == "LOW_STOCK"].iterrows():
        item = row["item"]
        predicted = row["predicted_daily_usage"]
        current_stock = row["total_quantity"]
        
        needed_qty = int(max(10, round(TARGET_DAYS_OF_SUPPLY * predicted - current_stock)))
        if needed_qty <= 0:
            continue
            
        candidates = other_status[(other_status["item"] == item) & (other_status["total_quantity"] > 10)].sort_values("days_of_supply", ascending=False)
        
        for _, cand in candidates.iterrows():
            if needed_qty <= 0:
                break
            
            source_clinic = cand["clinic"]
            if (item, clinic, source_clinic, "ENQUIRY") in pending_keys:
                continue
                
            spare = int(max(0, cand["total_quantity"] - SAFETY_STOCK_DAYS * cand["predicted_daily_usage"]))
            if spare <= 0:
                continue
                
            qty_to_ask = min(needed_qty, spare)
            if qty_to_ask <= 5:
                continue
                
            suggestions.append({
                "type": "ENQUIRY",
                "item": item,
                "from_clinic": clinic,
                "to_clinic": source_clinic,
                "quantity": qty_to_ask,
                "reason": "LOW_STOCK_REPLENISH",
                "penalty_score": 0,
                "batch_row_id": None,
                "description": f"Request {qty_to_ask} units of {item} from {source_clinic} to replenish low stock."
            })
            needed_qty -= qty_to_ask

    # 2. SURPLUS or EXPIRING SOON -> Send OFFER to other clinics
    our_batches = [b for b in batches if b["clinic"] == clinic]
    for batch in our_batches:
        batch_id = batch["batch_id"]
        batch_row_id = batch["id"]
        item = batch["item"]
        qty = batch["quantity"]
        expiry_date_dt = pd.to_datetime(batch["expiry_date"])
        days_to_expiry = (expiry_date_dt - TODAY).days
        
        item_status = our_status[our_status["item"] == item]
        if item_status.empty:
            continue
        days_of_supply = float(item_status["days_of_supply"].values[0])
        predicted = float(item_status["predicted_daily_usage"].values[0])
        
        expiry_penalty = max(0, (15 - days_to_expiry) * 10) if days_to_expiry <= 14 else 0
        holding_penalty = min(100, int((days_of_supply - 25) * 2)) if days_of_supply > 25 else 0
        total_penalty = int(expiry_penalty + holding_penalty)
        
        if total_penalty <= 0:
            continue
            
        target_candidates = other_status[other_status["item"] == item].sort_values("days_of_supply")
        
        if expiry_penalty > 0:
            qty_to_offer = qty
            reason = "EXPIRY_RISK_REDISTRIBUTION"
        else:
            safety_qty = SAFETY_STOCK_DAYS * predicted
            total_stock = item_status["total_quantity"].values[0]
            spare = max(0, total_stock - safety_qty)
            qty_to_offer = int(min(qty, spare))
            reason = "HOLDING_PENALTY_REDUCTION"
            
        if qty_to_offer <= 5:
            continue
            
        for _, target_row in target_candidates.iterrows():
            target_clinic = target_row["clinic"]
            if (item, clinic, target_clinic, "OFFER") in pending_keys:
                continue
                
            target_needed = int(max(0, TARGET_DAYS_OF_SUPPLY * target_row["predicted_daily_usage"] - target_row["total_quantity"]))
            if target_needed <= 0 and target_row["status"] != "LOW_STOCK":
                if expiry_penalty > 0 and target_row["days_of_supply"] < 20:
                    target_needed = qty_to_offer
                else:
                    continue
                    
            transfer_qty = min(qty_to_offer, target_needed)
            if transfer_qty <= 5:
                continue
                
            suggestions.append({
                "type": "OFFER",
                "item": item,
                "from_clinic": clinic,
                "to_clinic": target_clinic,
                "quantity": transfer_qty,
                "reason": reason,
                "penalty_score": total_penalty,
                "batch_row_id": batch_row_id,
                "description": f"Offer {transfer_qty} units of expiring/surplus {item} (Batch {batch_id}) to {target_clinic}."
            })
            break
            
    return suggestions
