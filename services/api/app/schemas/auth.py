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
    created_at: datetime
    # password_hash is deliberately not part of any response schema
