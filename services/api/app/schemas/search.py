from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.documents import DocumentOut

Tier = Literal["open_access", "additional"]


class SearchPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tier: Tier = "open_access"
    expression: str | None = Field(default=None, max_length=2000)
    providers: list[str] | None = Field(default=None, max_length=10)


class SearchRunCreate(SearchPlanRequest):
    pass


class BroadenDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["yes", "no", "later"]


class ProviderInfo(BaseModel):
    key: str
    label: str
    tier: str
    description: str
    attribution: str
    terms_url: str
    aliases: list[str]


class PlannedQuery(BaseModel):
    provider: str
    provider_label: str
    query_text: str
    endpoint: str
    params: dict[str, Any]
    url: str
    filters_applied: dict[str, Any]
    limitations: list[str]


class SearchPlan(BaseModel):
    protocol_version_id: uuid.UUID
    protocol_version: int
    tier: str
    expression: str
    expression_source: str
    derived_expression: str
    derivation_notes: list[str]
    filters: dict[str, Any]
    providers: list[str]
    protocol_sources: dict[str, Any]
    limitations: list[str]
    queries: list[PlannedQuery]
    ready: bool
    max_records_per_provider: int
    catalog: list[ProviderInfo] = []


class SearchQueryOut(BaseModel):
    id: uuid.UUID
    provider: str
    provider_label: str
    attribution: str | None
    endpoint: str
    query_text: str
    params: dict[str, Any]
    url: str
    filters_applied: dict[str, Any]
    limitations: list[str]
    status: str
    pages_fetched: int
    request_count: int
    total_reported: int | None
    records_retrieved: int
    records_skipped: int
    skipped: list[dict[str, Any]]
    error_kind: str | None
    error_summary: str | None
    started_at: datetime | None
    retrieved_at: datetime | None
    finished_at: datetime | None


class SearchRunOut(BaseModel):
    id: uuid.UUID
    tier: str
    tier_label: str
    status: str
    protocol_version_id: uuid.UUID
    is_current_protocol: bool
    expression: str
    expression_source: str
    derived_expression: str | None
    derivation_notes: list[str]
    filters: dict[str, Any]
    providers: list[str]
    protocol_sources: dict[str, Any]
    limitations: list[str]
    records_retrieved: int
    records_skipped: int
    papers_new: int
    papers_matched_existing: int
    duplicates_in_run: int
    possible_duplicates: int
    stop_reason: str | None
    error_summary: str | None
    attempt_count: int
    broaden_decision: str | None
    broaden_decided_at: datetime | None
    job_id: uuid.UUID | None
    row_version: int
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    queries: list[SearchQueryOut]


class SearchRunList(BaseModel):
    items: list[SearchRunOut]


class PaperBrief(BaseModel):
    id: uuid.UUID
    title: str
    authors: list[str]
    author_count: int
    year: int | None
    venue: str | None
    publication_type: str | None
    doi: str | None
    pmid: str | None
    pmcid: str | None
    arxiv_id: str | None
    openalex_id: str | None
    is_preprint: bool
    has_preprint_version: bool
    is_retracted: bool | None
    oa_status: str
    access_status: str
    access_label: str
    full_text_status: str
    full_text_label: str
    oa_url: str | None
    pdf_url: str | None
    has_abstract: bool
    providers: list[str]
    source_count: int
    possible_duplicate_of_id: uuid.UUID | None
    possible_duplicate_reason: str | None
    created_at: datetime


class PaperList(BaseModel):
    items: list[PaperBrief]
    total: int
    offset: int
    limit: int


class PaperSourceOut(BaseModel):
    id: uuid.UUID
    provider: str
    provider_record_id: str
    source_url: str | None
    rank: int
    match_basis: str
    retrieved_at: datetime
    search_run_id: uuid.UUID
    search_run_created_at: datetime
    search_run_tier: str
    protocol_version_id: uuid.UUID
    query_text: str
    normalized: dict[str, Any]
    raw: dict[str, Any]


class PaperDetail(PaperBrief):
    abstract: str | None
    authors_full: list[dict[str, Any]]
    keywords: list[str]
    language: str | None
    license: str | None
    other_ids: dict[str, Any]
    field_sources: dict[str, Any]
    possible_duplicate_of: dict[str, Any] | None
    sources: list[PaperSourceOut]
    documents: list[DocumentOut] = []  # uploaded full texts linked to this paper (Phase 6)


class SearchSummary(BaseModel):
    all_runs: dict[str, Any]
    current_protocol: dict[str, Any] | None
    approved_protocol_version_id: uuid.UUID | None
    run_count: int
    incomplete_runs: int
    stale_runs: int
    broaden_prompt: dict[str, Any] | None
    counting_note: str
