from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class PingJobRequest(BaseModel):
    project_id: uuid.UUID | None = None
    delay_s: int = Field(default=0, ge=0, le=60)


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    project_id: uuid.UUID | None
    type: str
    status: str
    progress: int
    progress_message: str | None
    attempts: int
    cancel_requested: bool
    result: dict[str, Any] | None
    error_summary: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class JobList(BaseModel):
    items: list[JobOut]
