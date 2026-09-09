"""
=========================================================
Homez OS

File : app/domains/account_recovery/schema.py
=========================================================
"""

from datetime import datetime

from pydantic import BaseModel
from pydantic import Field


class GenericMessageResponse(BaseModel):

    message: str


class ForgotIdRequest(BaseModel):

    email: str = Field(..., min_length=3, max_length=200)


class RecoveryCodesGenerateResponse(BaseModel):

    codes: list[str]


class RecoveryCodeResetRequest(BaseModel):

    email: str = Field(..., min_length=3, max_length=200)
    code: str = Field(..., min_length=1, max_length=64)
    new_password: str
    new_password_confirmation: str


class PasswordResetEmailStatusResponse(BaseModel):

    configured: bool


class PasswordResetRequestRequest(BaseModel):

    email: str = Field(..., min_length=3, max_length=200)


class PasswordResetTokenConsumeRequest(BaseModel):

    token: str = Field(..., min_length=1, max_length=200)
    new_password: str
    new_password_confirmation: str


class SuperAdminResetInitiateRequest(BaseModel):

    user_id: int


class SuperAdminResetInitiateResponse(BaseModel):

    reset_token: str
    expires_at: datetime


__all__ = [
    "GenericMessageResponse",
    "ForgotIdRequest",
    "RecoveryCodesGenerateResponse",
    "RecoveryCodeResetRequest",
    "PasswordResetEmailStatusResponse",
    "PasswordResetRequestRequest",
    "PasswordResetTokenConsumeRequest",
    "SuperAdminResetInitiateRequest",
    "SuperAdminResetInitiateResponse",
]
