from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ProjectCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    topic: str = Field(min_length=1, max_length=5000)


class ProjectUpdate(BaseModel):
    expected_version: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    archived: bool | None = None


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    title: str
    topic_original: str
    status: str
    row_version: int
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ProjectList(BaseModel):
    items: list[ProjectOut]
    total: int
    limit: int
    offset: int
