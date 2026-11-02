from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
    # Phase 15: queued but not yet admitted to a worker (fair-share limit reached); it will start automatically.
    waiting_for_capacity: bool = False

    @model_validator(mode="before")
    @classmethod
    def _capacity(cls, data: Any) -> Any:
        if hasattr(data, "enqueued_at") and hasattr(data, "status"):  # ORM row
            return {
                **{k: getattr(data, k) for k in cls.model_fields if k != "waiting_for_capacity"},
                "waiting_for_capacity": data.status == "queued" and data.enqueued_at is None,
            }
        return data


class JobList(BaseModel):
    items: list[JobOut]
