"""
seed_data.py

Generates synthetic *current stock* data (with batch-level expiry dates)
for the same clinics/items used in clinic_inventory_usage.csv, and loads
everything into a local SQLite database (inventory.db).

Deliberately introduces stock imbalance between clinics (independent of
their usage rate) so that the transfer-matching engine has realistic
"one clinic is low, another is sitting on surplus/near-expiry stock"
scenarios to work with.
"""

import sqlite3
import numpy as np
import pandas as pd
from datetime import datetime, timedelta

import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "inventory.db")
USAGE_CSV = os.path.join(BASE_DIR, "clinic_inventory_usage.csv")
TODAY = datetime(2026, 7, 1)  # the day after the 30-day usage history ends
RANDOM_SEED = 7

CLINICS = ["Clinic A", "Clinic B", "Clinic C"]


def init_schema(conn: sqlite3.Connection):
    conn.executescript("""
    DROP TABLE IF EXISTS stock_batches;
    DROP TABLE IF EXISTS transactions;

    CREATE TABLE stock_batches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        clinic TEXT NOT NULL,
        item TEXT NOT NULL,
        batch_id TEXT NOT NULL,
        quantity INTEGER NOT NULL,
        expiry_date TEXT NOT NULL
    );

    CREATE TABLE transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,
        from_clinic TEXT NOT NULL,
        to_clinic TEXT NOT NULL,
        item TEXT NOT NULL,
        batch_id TEXT NOT NULL,
        quantity INTEGER NOT NULL,
        reason TEXT NOT NULL,
        status TEXT NOT NULL
    );
    """)
    conn.commit()


CLINIC_STOCK_PROFILE = {
    "Clinic A": (1, 8),    # tends to run low
    "Clinic B": (6, 18),   # roughly balanced
    "Clinic C": (18, 45),  # tends to sit on surplus
}


def generate_batches(rng: np.random.Generator) -> list[dict]:
    """Create 1-3 stock batches per (clinic, item). Quantities are sized
    relative to each clinic/item's *predicted* daily usage (so the
    resulting days-of-supply numbers are realistic) but scaled by a
    clinic-specific stock profile that's independent of true demand,
    producing genuine low-stock / surplus imbalance. A handful of
    surplus batches are deliberately pushed into the near-expiry window
    to also exercise expiry-driven redistribution."""

    from app.forecasting import predict_daily_usage
    forecast = predict_daily_usage()

    batches = []
    near_expiry_quota = 4
    forced = 0

    for clinic in CLINICS:
        low_days, high_days = CLINIC_STOCK_PROFILE[clinic]
        for _, row in forecast[forecast["clinic"] == clinic].iterrows():
            item = row["item"]
            daily_usage = row["predicted_daily_usage"]

            target_days_of_stock = rng.uniform(low_days, high_days)
            total_quantity = max(10, int(daily_usage * target_days_of_stock))

            num_batches = int(rng.integers(1, 4))
            splits = rng.dirichlet(np.ones(num_batches))
            quantities = np.maximum(1, (splits * total_quantity).astype(int))

            for b, qty in enumerate(quantities):
                batch_id = f"{clinic[-1]}-{item[:3].upper()}-{b + 1}"

                if target_days_of_stock > 20 and forced < near_expiry_quota and rng.random() < 0.5:
                    days_to_expiry = int(rng.integers(3, 12))
                    forced += 1
                else:
                    days_to_expiry = int(rng.integers(15, 180))

                expiry_date = (TODAY + timedelta(days=days_to_expiry)).strftime("%Y-%m-%d")

                batches.append({
                    "clinic": clinic,
                    "item": item,
                    "batch_id": batch_id,
                    "quantity": int(qty),
                    "expiry_date": expiry_date,
                })
    return batches


def main():
    rng = np.random.default_rng(RANDOM_SEED)
    batches = generate_batches(rng)

    conn = sqlite3.connect(DB_PATH)
    init_schema(conn)

    conn.executemany(
        "INSERT INTO stock_batches (clinic, item, batch_id, quantity, expiry_date) "
        "VALUES (:clinic, :item, :batch_id, :quantity, :expiry_date)",
        batches,
    )
    conn.commit()

    total = conn.execute("SELECT COUNT(*) FROM stock_batches").fetchone()[0]
    print(f"Seeded {total} stock batches into {DB_PATH}")
    conn.close()


if __name__ == "__main__":
    main()
