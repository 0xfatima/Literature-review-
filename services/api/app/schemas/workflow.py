from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class WorkflowRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    project_id: uuid.UUID
    conversation_id: uuid.UUID | None
    graph_name: str
    graph_version: str
    stage: str
    status: str
    pending_question_id: uuid.UUID | None
    pending_question: dict[str, Any] | None
    protocol_version_id: uuid.UUID | None
    iteration_count: int
    max_iterations: int
    stop_reason: str | None
    error_summary: str | None
    stage_before_revision: str | None
    row_version: int
    created_at: datetime
    updated_at: datetime


class WorkflowState(BaseModel):
    run: WorkflowRunOut | None
    stages: list[dict[str, str]]


class WorkflowAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question_id: uuid.UUID
    action: str = Field(min_length=1, max_length=16)
    expected_row_version: int | None = Field(default=None, ge=1)
    values: dict[str, Any] = Field(default_factory=dict)


class WorkflowEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    kind: str
    question_id: uuid.UUID | None
    stage: str | None
    created_at: datetime


class WorkflowEventList(BaseModel):
    items: list[WorkflowEventOut]
