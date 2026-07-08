"""
app/database.py

Thin SQLite access layer for stock batches and transaction records.
Kept deliberately simple (raw sqlite3, no ORM) to match the lightweight
spirit of the rest of this project.
"""

import sqlite3
from datetime import datetime
from typing import Optional

import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "inventory.db")


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def fetch_all_batches() -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM stock_batches ORDER BY clinic, item, expiry_date"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def fetch_batch(batch_row_id: int) -> Optional[dict]:
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM stock_batches WHERE id = ?", (batch_row_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def update_batch_quantity(batch_row_id: int, new_quantity: int):
    conn = get_connection()
    if new_quantity <= 0:
        conn.execute("DELETE FROM stock_batches WHERE id = ?", (batch_row_id,))
    else:
        conn.execute(
            "UPDATE stock_batches SET quantity = ? WHERE id = ?",
            (new_quantity, batch_row_id),
        )
    conn.commit()
    conn.close()


def insert_batch(clinic: str, item: str, batch_id: str, quantity: int, expiry_date: str):
    conn = get_connection()
    conn.execute(
        "INSERT INTO stock_batches (clinic, item, batch_id, quantity, expiry_date) "
        "VALUES (?, ?, ?, ?, ?)",
        (clinic, item, batch_id, quantity, expiry_date),
    )
    conn.commit()
    conn.close()


def log_transaction(from_clinic: str, to_clinic: str, item: str, batch_id: str,
                     quantity: int, reason: str, status: str = "EXECUTED"):
    conn = get_connection()
    conn.execute(
        "INSERT INTO transactions "
        "(timestamp, from_clinic, to_clinic, item, batch_id, quantity, reason, status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (datetime.utcnow().isoformat(), from_clinic, to_clinic, item,
         batch_id, quantity, reason, status),
    )
    conn.commit()
    conn.close()


def fetch_transaction_history() -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM transactions ORDER BY timestamp DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# --- Requests Table Initialization and Helper Methods ---

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,
        type TEXT NOT NULL,
        item TEXT NOT NULL,
        from_clinic TEXT NOT NULL,
        to_clinic TEXT NOT NULL,
        quantity INTEGER NOT NULL,
        batch_row_id INTEGER,
        reason TEXT NOT NULL,
        status TEXT NOT NULL,
        penalty_score INTEGER DEFAULT 0
    )
    """)
    conn.commit()
    conn.close()


init_db()


def create_request(type: str, item: str, from_clinic: str, to_clinic: str,
                   quantity: int, batch_row_id: Optional[int], reason: str,
                   penalty_score: int) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO requests (timestamp, type, item, from_clinic, to_clinic, quantity, batch_row_id, reason, status, penalty_score) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?)",
        (datetime.utcnow().isoformat(), type, item, from_clinic, to_clinic,
         quantity, batch_row_id, reason, penalty_score)
    )
    conn.commit()
    req_id = cursor.lastrowid
    conn.close()
    return req_id


def fetch_requests(clinic: str) -> list[dict]:
    """Fetch all requests related to this clinic (either outgoing or incoming)."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM requests WHERE from_clinic = ? OR to_clinic = ? ORDER BY timestamp DESC",
        (clinic, clinic)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def fetch_request(request_id: int) -> Optional[dict]:
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM requests WHERE id = ?", (request_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def update_request_status(request_id: int, status: str):
    conn = get_connection()
    conn.execute(
        "UPDATE requests SET status = ? WHERE id = ?", (status, request_id)
    )
    conn.commit()
    conn.close()

