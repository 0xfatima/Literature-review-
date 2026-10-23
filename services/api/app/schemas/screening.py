from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

HumanDecision = Literal["include", "exclude", "uncertain"]


class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: HumanDecision
    reason_code: str | None = Field(default=None, max_length=80)
    note: str | None = Field(default=None, max_length=2000)
    expected_row_version: int | None = Field(default=None, ge=0)


class BulkDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    paper_ids: list[uuid.UUID] = Field(min_length=1, max_length=200)
    decision: HumanDecision
    reason_code: str | None = Field(default=None, max_length=80)
    note: str | None = Field(default=None, max_length=2000)


class UndoRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    batch_id: uuid.UUID | None = None
    decision_id: uuid.UUID | None = None


class RelevanceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    use_llm: bool = False
    force: bool = False


class DecisionResult(BaseModel):
    decision_id: uuid.UUID | None
    unchanged: bool


class BulkResult(BaseModel):
    batch_id: uuid.UUID | None
    changed: int
    unchanged: int


class UndoResult(BaseModel):
    undone: int
    skipped_changed_since: int
    batch_id: uuid.UUID | None


class ReasonOut(BaseModel):
    code: str
    label: str
    group: str


class ReasonList(BaseModel):
    items: list[ReasonOut]
    require_reason: bool


class QueueItem(BaseModel):
    paper_id: uuid.UUID
    title: str
    authors: list[str]
    author_count: int
    year: int | None
    venue: str | None
    publication_type: str | None
    abstract: str | None
    keywords: list[str]
    doi: str | None
    providers: list[str]
    is_preprint: bool
    is_retracted: bool | None
    access_status: str
    possible_duplicate_of_id: uuid.UUID | None
    band: str
    band_label: str
    band_source: str | None
    lexical: dict[str, Any] | None
    ai: dict[str, Any] | None
    decision: str | None
    decision_label: str
    decision_id: uuid.UUID | None
    decision_protocol_version_id: uuid.UUID | None
    decision_is_current_protocol: bool | None
    state_row_version: int | None
    decided_at: datetime | None


class Queue(BaseModel):
    items: list[QueueItem]
    total: int
    offset: int
    limit: int
    progress: dict[str, int]
    protocol_version_id: uuid.UUID
    protocol_version: int


class PaperScreeningHistory(BaseModel):
    paper_id: uuid.UUID
    decisions: list[dict[str, Any]]
    assessments: list[dict[str, Any]]


class RelevanceStatus(BaseModel):
    llm: dict[str, Any]
    total_papers: int
    protocol_version_id: uuid.UUID | None
    counts: dict[str, dict[str, int]]
    active_job_id: uuid.UUID | None
    last_result: dict[str, Any] | None
    last_error: str | None
    model_runs: list[dict[str, Any]]
    methods: dict[str, str]
