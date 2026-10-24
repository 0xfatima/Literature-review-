from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class CandidateOut(BaseModel):
    paper_id: str
    score: float | None
    reasons: list[str]
    title: str | None
    year: int | None
    doi: str | None


class DocumentOut(BaseModel):
    id: uuid.UUID
    paper_id: uuid.UUID | None
    paper_title: str | None
    original_filename: str
    size_bytes: int
    sha256: str
    parse_status: str
    error_code: str | None
    error_message: str | None
    page_count: int | None
    text_version: int
    pages_text: int
    pages_ocr: int
    pages_blank: int
    pages_unreadable: int
    text_coverage: float | None
    pdf_metadata: dict[str, Any]
    warnings: list[dict[str, Any]]
    scan_status: str
    scan_detail: str | None
    match_status: str
    match_method: str | None
    match_score: float | None
    candidates: list[CandidateOut]
    needs_confirmation: bool
    matched_at: datetime | None
    can_retry: bool
    can_download: bool
    created_at: datetime
    processed_at: datetime | None
    row_version: int


class DocumentList(BaseModel):
    items: list[DocumentOut]
    total: int
    offset: int
    limit: int


class DocumentLimits(BaseModel):
    max_bytes: int
    max_pages: int
    quota_bytes_per_project: int
    quota_files_per_project: int
    quota_bytes_per_user: int
    removed_retention_days: int
    ocr_enabled: bool
    ocr_languages: str | None
    malware_scanner: str
    storage_backend: str


class DocumentUsage(BaseModel):
    project_bytes: int
    project_files: int
    user_bytes: int


class DocumentCounts(BaseModel):
    parse: dict[str, int]
    match: dict[str, int]


class DocumentSummary(BaseModel):
    limits: DocumentLimits
    usage: DocumentUsage
    counts: DocumentCounts


class LinkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    paper_id: uuid.UUID | None = None  # null = unlink
    expected_version: int | None = Field(default=None, ge=0)


class PageOut(BaseModel):
    page_no: int
    text: str
    extraction: str
    char_count: int
    ocr_confidence: float | None
    warnings: list[str]


class PageList(BaseModel):
    document_id: uuid.UUID
    parse_status: str
    text_version: int
    page_count: int | None
    total: int
    offset: int
    limit: int
    items: list[PageOut]
