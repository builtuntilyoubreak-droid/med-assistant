"""
app/schemas.py

Pydantic models for request/response validation.
"""

from pydantic import BaseModel
from typing import Optional


class ExecuteTransferRequest(BaseModel):
    from_clinic: str
    to_clinic: str
    item: str
    batch_id: str
    batch_row_id: int
    quantity: int
    reason: str = "MANUAL_TRANSFER"


class NewStockBatch(BaseModel):
    clinic: str
    item: str
    batch_id: str
    quantity: int
    expiry_date: str  # "YYYY-MM-DD"


class CreateRequestModel(BaseModel):
    type: str
    item: str
    from_clinic: str
    to_clinic: str
    quantity: int
    batch_row_id: Optional[int] = None
    reason: str
    penalty_score: int = 0


class RespondRequestModel(BaseModel):
    request_id: int
    action: str  # APPROVED or REJECTED

