from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ProtocolVersionOut(BaseModel):
    id: uuid.UUID
    version: int
    status: str
    row_version: int
    content: dict[str, Any]
    framework_rationale: str | None
    issues: list[dict[str, str]]
    can_approve: bool
    summary: list[str]
    change_reason: str | None
    supersedes_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    approved_at: datetime | None
    approved_by: uuid.UUID | None
    content_hash: str | None
    impact: dict[str, Any] | None
    superseded_at: datetime | None


class ProtocolVersionBrief(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    version: int
    status: str
    change_reason: str | None
    created_at: datetime
    approved_at: datetime | None
    superseded_at: datetime | None


class ProtocolOverview(BaseModel):
    draft: ProtocolVersionOut | None
    approved: ProtocolVersionOut | None
    versions: list[ProtocolVersionBrief]
    stage_labels: dict[str, str]
    field_labels: dict[str, str]


class DraftUpdate(BaseModel):
    """Partial update: only the fields present are changed. Lists (questions, criteria, ...) are replaced."""

    model_config = ConfigDict(extra="forbid")
    expected_row_version: int = Field(ge=1)
    objective: str | None = None
    framework: str | None = None
    framework_source: str | None = None
    framework_elements: list[dict[str, Any]] | None = None
    questions: list[dict[str, Any]] | None = None
    criteria: list[dict[str, Any]] | None = None
    date_from: int | None = None
    date_to: int | None = None
    languages: list[str] | None = None
    study_designs: list[str] | None = None
    sources: list[str] | None = None
    search_strategy: str | None = None
    extraction_plan: str | None = None
    synthesis_plan: str | None = None


class RevisionCreate(BaseModel):
    reason: str = Field(min_length=3, max_length=2000)
