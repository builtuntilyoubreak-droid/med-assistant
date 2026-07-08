# CareShare: Predictive Clinic Inventory & Redistribution Network

CareShare is a lightweight, single-clinic inventory manager and peer-to-peer redistribution client. It helps clinics track stock batches and expiration dates, analyze historical demand seasonality, compute holding and expiry penalties, and request or offer stock transfers to sister clinics.

Both the backend API and the high-speed frontend dashboard are served together from a single Python process on a single port, running **100% offline-ready** with zero external client-side downloads or compilation steps.

---

## 1. Who Needs This?
* **Community Clinics & Healthcare Providers**: Local clinics that want to manage their own inventories while easily cooperating with neighboring clinics to stay stocked.
* **Regional Health Networks**: Networks of clinics (e.g. Clinic A, B, and C) that need to share resources locally without a slow, centralized procurement cycle.
* **Rural Health Coordinators**: Providers in areas with low or unstable internet connections who need local, offline-capable systems to prevent medicine wastage and shortages.

---

## 2. Key Use Cases & Scenarios
1. **Preventing Local Shortages**: A clinic running low on Amoxicillin (e.g. days of supply < 7) can automatically enquire and pull stock from a neighboring clinic that has a surplus.
2. **Mitigating Expiration Waste**: A clinic sitting on a batch of Antiseptic Wipes expiring in 10 days can automatically offer it to a sister clinic that can consume it immediately, avoiding discard.
3. **Reducing Excess Holding Costs**: A clinic overstocked with Nitrile Gloves (e.g. days of supply > 25) can identify clinics with low supply and offer to transfer the surplus to reduce local holding penalties.

---

## 3. Core Features & Architecture

### 📊 Single-Clinic Dashboards
The dashboard can be toggled to represent any clinic's internal inventory manager. It aggregates local stock types, low-stock warnings, expiring batches, and holding penalties.

### 📈 Sales & Season Analysis
* **Weekday Seasonal Multiplier**: Analyzes 30-day historical usage data to calculate seasonal indices per day of the week (e.g. `1.4x` demand on Tuesdays, `0.6x` on Sundays).
* **Upcoming 7-Day Demand Forecast**: Fits a 1D linear regression polyfit baseline trend and applies the weekday seasonal multipliers to project daily demand for the upcoming week.

### ⚠️ Stock Penalty Scoring
* **Expiry Penalty**: Batches expiring in $\le 14$ days accrue points based on waste risk:
  $$\text{Expiry Penalty} = \max(0, 15 - \text{days to expiry}) \times 10 \quad (\text{Max 150 pts})$$
* **Holding Penalty**: Overstocked items (supply $> 25$ days) accrue points based on storage costs:
  $$\text{Holding Penalty} = \min(100, (\text{days of supply} - 25) \times 2)$$

### ✉️ Inter-Clinic Requests Inbox
* **Stock Enquiry**: Low-stock warnings automatically prompt sending a PENDING enquiry to sibling clinics.
* **Redistribution Offer**: High-penalty batches prompt sending a PENDING offer to sibling clinics.
* **Response Approval**: Incoming requests can be approved with one click, which deducts quantities from the source batch, adds them to the destination clinic, logs the transaction, and completes the request.

### ⚡ AI Analysis & Auto-Order
* **Prediction Engine**: Evaluates linear trend slopes and weekday multipliers across all stocked medicines.
* **Auto-Replenish Mapping**: If an upcoming spike is predicted AND local days of supply is low ($\le 12$ days), the engine searches neighboring clinics for available surplus stock.
* **Preventative Transfers**: Immediately executes approved transfer orders from sibling clinics (holding the most excess supply) to cover the upcoming peak demand, logging the events in real-time.

---

## 4. System Advantages
* **Offline-Ready**: Built using native, clean Vanilla JavaScript with **zero external CDN dependencies or npm registry requirements**. It compiles nothing in the browser and loads in under 20ms over unstable networks.
* **Unified Host**: Frontend files are served directly by FastAPI's static file mount.
* **Zero CORS or Port Overhead**: Both the database, API, and dashboard are served from `http://localhost:8000`.
* **Lightweight Precision**: Linear regressions are computed in native C speed via NumPy's polyfit rather than downloading heavy libraries like Scikit-Learn.

---

## 5. API Integration Details

### Raw Inventory & Status
* **`GET /stock/batches`**: Returns all stock batches, enriched with forecast usage, days of supply, and calculated holding/expiry penalties.
* **`GET /status`**: Returns aggregate clinic/item days of supply and status classifications (`LOW_STOCK`, `NORMAL`, `SURPLUS`).
* **`GET /forecast`**: Returns baseline usage forecast calculations.

### Matching & Requests
* **`GET /actions/suggested?clinic={clinic_name}`**: Computes suggested enquiries or offers for a specific clinic based on low-stock states or high penalty scores.
* **`GET /requests?clinic={clinic_name}`**: Fetches active and past requests involving this clinic.
* **`POST /requests/create`**: Dispatches a new pending request.
* **`POST /requests/ai-analyze-auto?clinic={clinic_name}`**: Triggers the machine learning forecasting engine for the clinic. Identifies upcoming spikes, searches for sibling surplus, and executes preventative transfers automatically.
* **`POST /requests/respond`**: Approves or rejects a pending request. Approving automatically performs the database stock transfer.

### Inventory Management
* **`POST /stock/add`**: Manually records a local delivery delivery.
  * **Payload**:
    ```json
    {
      "clinic": "Clinic A",
      "item": "Surgical Masks",
      "batch_id": "A-MSK-4",
      "quantity": 1000,
      "expiry_date": "2026-12-31"
    }
    ```
* **`GET /transactions/history`**: Returns transaction logs of completed redistributions.

---

## 6. Setup & Launch Instructions

### Prerequisites
* Python 3.9+ installed.

### Installation
1. Change directory to the backend folder:
   ```bash
   cd inventory_backend
   ```
2. Install the lightweight requirements:
   ```bash
   pip install -r requirements.txt
   ```
3. Seed the SQLite database (`inventory.db`) with 36 initial stock batches:
   ```bash
   python seed_data.py
   ```

### Start the Server
Run the FastAPI application:
```bash
uvicorn app.main:app --reload --port 8000
```
Open your browser and navigate to:
👉 **[http://localhost:8000](http://localhost:8000)**
