from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class Credentials(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=1024)  # hard cap; real policy is enforced in validate_password


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    email: str
    email_verified: bool = False
    created_at: datetime
    # password_hash is deliberately not part of any response schema


class EmailOnly(BaseModel):
    email: str = Field(max_length=254)


class TokenOnly(BaseModel):
    token: str = Field(max_length=200)


class PasswordResetConfirm(BaseModel):
    token: str = Field(max_length=200)
    password: str = Field(max_length=1024)


class RegisterOut(UserOut):
    verification_required: bool = False
    verification_sent: bool = False


class AuthConfig(BaseModel):
    password_reset_available: bool
    email_verification_required: bool


class LLMSettingsIn(BaseModel):
    provider: str = Field(max_length=32)
    model: str = Field(max_length=200)
    api_key: str | None = Field(default=None, max_length=500)  # write-only; never returned
    clear_key: bool = False


class LLMModelsRequest(BaseModel):
    provider: str = Field(max_length=32)
    api_key: str | None = Field(default=None, max_length=500)


class LLMTestRequest(LLMSettingsIn):
    pass
