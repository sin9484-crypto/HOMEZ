"""
=========================================================
Homez OS

File : app/domains/automation_safety/schema.py

V2.4 Commerce Safety Layer Schema
=========================================================
"""

from pydantic import BaseModel


class AutomationModeSet(BaseModel):

    mode: str
    reason: str | None = None


class EmergencyStopActivate(BaseModel):

    reason: str
    audit_ref: str | None = None


class ExecutionLimitSet(BaseModel):

    product_id: int | None = None
    daily_funding_limit: float | None = None
    per_product_funding_limit: float | None = None
    daily_quantity_limit: int | None = None
    per_product_quantity_limit: int | None = None
    currency: str = "KRW"


class SafetyEvaluationRequest(BaseModel):

    idempotency_key: str
    product_id: int | None = None
    funding_amount: float = 0
    quantity: int = 0
    operator_approved: bool = False


class SafetyEvaluationResponse(BaseModel):

    decision: str
    reasons: list[str]
    idempotent_replay: bool = False


__all__ = [
    "AutomationModeSet",
    "EmergencyStopActivate",
    "ExecutionLimitSet",
    "SafetyEvaluationRequest",
    "SafetyEvaluationResponse",
]
