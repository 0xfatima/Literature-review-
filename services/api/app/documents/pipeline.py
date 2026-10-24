"""Text extraction pipeline: parse in a child process, classify each page, OCR only the pages that need it, and
produce page-level text with warnings. Pure orchestration over `runner`/`ocr`; no database access here.

Page classes:
  text        a usable text layer
  ocr         recognised by OCR (confidence recorded)
  blank       no text and no images (a genuinely empty page)
  unreadable  needs OCR but OCR is off/unavailable/out of budget, or the page could not be read
Nothing is ever invented: an unreadable page stays unreadable and is listed in the warnings.
"""

from __future__ import annotations

import json
import re
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.config import Settings
from app.documents import runner
from app.documents.ocr import OcrEngine, OcrError
from app.documents.text import GARBLED_THRESHOLD, clean_page_text, garbled_ratio

MIN_TEXT_CHARS = 25  # fewer non-space characters than this on a page with images = no usable text layer
MIN_BODY_CHARS = 200  # a page mostly covered by an image with less text than this is treated as scanned
IMAGE_COVER_SCANNED = 0.5
MAX_EXTRACT_OUTPUT = 120 * 1024 * 1024
LOW_OCR_CONFIDENCE = 60.0


class ProcessingFailure(Exception):
    """The document cannot be turned into text. `code` is stable and shown to the user with `message`."""

    def __init__(self, code: str, message: str, *, retryable: bool = True, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code, self.message, self.retryable, self.details = code, message, retryable, details or {}


@dataclass
class PageResult:
    page_no: int
    text: str
    extraction: str  # text | ocr | blank | unreadable
    ocr_confidence: float | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def char_count(self) -> int:
        return len(self.text)


@dataclass
class Extraction:
    page_count: int
    metadata: dict[str, str]
    pages: list[PageResult]
    warnings: list[dict[str, Any]]
    flags: dict[str, Any]

    def count(self, kind: str) -> int:
        return sum(1 for p in self.pages if p.extraction == kind)


def _warn(code: str, message: str, pages: list[int] | None = None, **extra: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"code": code, "message": message}
    if pages:
        out["pages"] = pages
    out.update(extra)
    return out


def needs_ocr(non_space_chars: int, image_count: int, image_cover: float) -> bool:
    """A page with images and (almost) no text layer, or mostly covered by one image with little text."""
    if image_count <= 0:
        return False
    return non_space_chars < MIN_TEXT_CHARS or (image_cover >= IMAGE_COVER_SCANNED and non_space_chars < MIN_BODY_CHARS)


def parse_child_output(stdout: bytes) -> tuple[dict[str, Any] | None, list[dict[str, Any]], dict[str, Any] | None]:
    """(header, page records, terminal record). The terminal record is `done` or `error`; None means the child died."""
    header: dict[str, Any] | None = None
    pages: list[dict[str, Any]] = []
    terminal: dict[str, Any] | None = None
    for raw in stdout.decode("utf-8", "replace").splitlines():
        try:
            rec = json.loads(raw)
        except ValueError:
            continue
        kind = rec.get("type")
        if kind == "header":
            header = rec
        elif kind == "page":
            pages.append(rec)
        elif kind in ("done", "error"):
            terminal = rec
    return header, pages, terminal


def extract(
    pdf: Path, settings: Settings, *, on_tick: Callable[[], None] | None = None
) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    """Run the extraction child. Returns (header, raw page records, engine warnings) or raises ProcessingFailure."""
    argv = runner.pdf_child_argv(
        "extract",
        str(pdf),
        "--max-pages",
        str(settings.upload_max_pages),
        "--max-chars",
        str(settings.upload_max_extracted_chars),
        "--mem-mb",
        str(settings.document_child_memory_mb),
        "--cpu-s",
        str(int(settings.document_process_timeout_s)),
    )
    res = runner.run_child(
        argv, timeout_s=settings.document_process_timeout_s, max_output_bytes=MAX_EXTRACT_OUTPUT, on_tick=on_tick
    )
    if res.timed_out:
        raise ProcessingFailure(
            "parse_timeout",
            "Reading the PDF took too long and was stopped. Try again, or upload a smaller or simpler copy.",
        )
    if res.output_truncated:
        raise ProcessingFailure(
            "text_too_large", "The PDF produced more text than the extraction limit allows.", retryable=False
        )
    header, pages, terminal = parse_child_output(res.stdout)
    if terminal is not None and terminal.get("type") == "error":
        code = str(terminal.get("code") or "parse_failed")
        retryable = code not in {"password_protected", "too_many_pages", "text_too_large", "no_pages", "corrupt"}
        raise ProcessingFailure(code, str(terminal.get("message") or "The PDF could not be read."), retryable=retryable)
    if terminal is None or header is None:
        raise ProcessingFailure(
            "parser_crashed",
            "The PDF reader stopped unexpectedly on this file. It may be damaged. Try a different copy of the paper.",
            details={"returncode": res.returncode},
        )
    if len(pages) != int(header["page_count"]):
        raise ProcessingFailure(
            "pages_missing",
            f"Only {len(pages)} of {header['page_count']} pages could be read. The file may be damaged.",
            details={"read": len(pages), "expected": header["page_count"]},
        )
    return header, pages, [str(w) for w in terminal.get("engine_warnings", [])]


def classify(raw: dict[str, Any]) -> tuple[str, str]:
    """(kind, cleaned text) where kind is text | blank | needs_ocr | garbled | failed."""
    text = clean_page_text(str(raw.get("text") or ""))
    if raw.get("error"):
        return "failed", ""
    n = len(re.sub(r"\s+", "", text))
    images = int(raw.get("image_count") or 0)
    if n == 0 and images == 0:
        return "blank", ""
    if needs_ocr(n, images, float(raw.get("image_cover") or 0.0)):
        return "needs_ocr", text
    if n and garbled_ratio(text) > GARBLED_THRESHOLD:
        return "garbled", text
    return "text", text


def _ocr_pages(
    pdf: Path,
    candidates: list[int],
    settings: Settings,
    engine: OcrEngine,
    on_tick: Callable[[], None] | None,
) -> tuple[dict[int, tuple[str, float | None]], list[int], list[dict[str, Any]]]:
    """OCR the given pages. Returns ({page: (text, confidence)}, pages not done, warnings)."""
    done: dict[int, tuple[str, float | None]] = {}
    warnings: list[dict[str, Any]] = []
    allowed = candidates[: settings.ocr_max_pages]
    skipped = candidates[settings.ocr_max_pages :]
    if skipped:
        warnings.append(
            _warn(
                "ocr_page_limit",
                f"OCR is limited to {settings.ocr_max_pages} pages per document; the remaining pages were not read.",
                skipped,
            )
        )
    if not allowed:
        return done, skipped, warnings
    start = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="srs-ocr-") as tmp:
        argv = runner.pdf_child_argv(
            "render",
            str(pdf),
            tmp,
            "--pages",
            ",".join(str(p) for p in allowed),
            "--dpi",
            str(settings.ocr_dpi),
            "--max-pixels",
            str(40_000_000),
            "--mem-mb",
            str(settings.document_child_memory_mb),
            "--cpu-s",
            str(int(settings.ocr_budget_s)),
        )
        res = runner.run_child(argv, timeout_s=settings.ocr_budget_s, max_output_bytes=4 * 1024 * 1024, on_tick=on_tick)
        rendered: dict[int, Path] = {}
        for line in res.stdout.decode("utf-8", "replace").splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("type") == "rendered" and rec.get("path") and not rec.get("error"):
                rendered[int(rec["page_no"])] = Path(rec["path"])
        failed_render = [p for p in allowed if p not in rendered]
        exhausted: list[int] = []
        failed_ocr: list[int] = []
        for page_no in allowed:
            if page_no not in rendered:
                continue
            if time.monotonic() - start > settings.ocr_budget_s:
                exhausted.append(page_no)
                continue
            if on_tick is not None:
                on_tick()
            try:
                result = engine.recognize(rendered[page_no])
            except OcrError:
                failed_ocr.append(page_no)
                continue
            done[page_no] = (clean_page_text(result.text), result.confidence)
    if exhausted:
        warnings.append(_warn("ocr_budget_exhausted", "OCR ran out of time before reaching these pages.", exhausted))
    if failed_render or failed_ocr:
        warnings.append(_warn("ocr_page_failed", "OCR could not read these pages.", failed_render + failed_ocr))
    return done, skipped + exhausted + failed_render + failed_ocr, warnings


def process(pdf: Path, settings: Settings, ocr: OcrEngine, *, on_tick: Callable[[], None] | None = None) -> Extraction:
    """Extract page text (OCR only where needed). Raises ProcessingFailure when no page yields usable text."""
    header, raw_pages, engine_warnings = extract(pdf, settings, on_tick=on_tick)
    warnings: list[dict[str, Any]] = []
    pages: list[PageResult] = []
    to_ocr: list[int] = []
    garbled: list[int] = []
    failed: list[int] = []
    for raw in raw_pages:
        n = int(raw["page_no"])
        kind, text = classify(raw)
        if kind == "text":
            pages.append(PageResult(n, text, "text"))
        elif kind == "blank":
            pages.append(PageResult(n, "", "blank"))
        elif kind == "failed":
            failed.append(n)
            pages.append(PageResult(n, "", "unreadable", warnings=["page_failed"]))
        else:  # needs_ocr or garbled: keep the text layer for now; OCR may replace it
            pages.append(PageResult(n, text, "text" if kind == "garbled" else "unreadable"))
            (to_ocr if kind == "needs_ocr" else garbled).append(n)
            if kind == "garbled":
                pages[-1].warnings.append("garbled_text_layer")

    ocr_ready = settings.feature_ocr and ocr.available()
    by_no = {p.page_no: p for p in pages}
    if (to_ocr or garbled) and ocr_ready:
        candidates = sorted(set(to_ocr) | set(garbled))
        done, _not_done, ocr_warnings = _ocr_pages(pdf, candidates, settings, ocr, on_tick)
        warnings.extend(ocr_warnings)
        for n, (text, conf) in done.items():
            useful = len(re.sub(r"\s+", "", text)) >= MIN_TEXT_CHARS
            if n in to_ocr and useful:
                by_no[n].text, by_no[n].extraction, by_no[n].ocr_confidence = text, "ocr", conf
            elif n in to_ocr:  # OCR found (almost) nothing on an image page: a picture or a blank scan
                by_no[n].text, by_no[n].extraction, by_no[n].ocr_confidence = text, "blank", conf
                by_no[n].warnings.append("no_text_found_by_ocr")
            elif useful and garbled_ratio(text) < garbled_ratio(by_no[n].text):  # garbled layer: OCR is better
                by_no[n].text, by_no[n].extraction, by_no[n].ocr_confidence = text, "ocr", conf
                by_no[n].warnings.append("replaced_garbled_text_layer")
        ocr_used = sorted(n for n, p in by_no.items() if p.extraction == "ocr")
        if ocr_used:
            confs = [c for c in (by_no[n].ocr_confidence for n in ocr_used) if c is not None]
            mean = round(sum(confs) / len(confs), 1) if confs else None
            warnings.append(
                _warn(
                    "ocr_used",
                    "These pages had no usable text layer and were read by OCR, which can contain recognition errors.",
                    ocr_used,
                    mean_confidence=mean,
                )
            )
            low = [n for n in ocr_used if (by_no[n].ocr_confidence or 100.0) < LOW_OCR_CONFIDENCE]
            if low:
                warnings.append(_warn("ocr_low_confidence", "OCR confidence is low on these pages.", low))
    unreadable = sorted(
        n
        for n, p in by_no.items()
        if p.extraction == "unreadable" and not {"page_failed", "blank_in_damaged_file"} & set(p.warnings)
    )
    if unreadable:
        if not settings.feature_ocr:
            why = "OCR is turned off."
        elif not ocr.available():
            why = "OCR is enabled but the OCR engine is not available on the worker."
        else:
            why = "OCR could not read them."
        warnings.append(
            _warn(
                "image_only_pages",
                f"These pages are images without a text layer. {why}",
                unreadable,
                ocr_enabled=ocr_ready,
            )
        )
    garbled_left = sorted(n for n in garbled if by_no[n].extraction == "text")
    if garbled_left:
        warnings.append(
            _warn(
                "garbled_text_layer",
                "The text layer on these pages looks corrupted (unreadable characters).",
                garbled_left,
            )
        )
    if failed:
        warnings.append(_warn("page_failed", "These pages could not be read.", failed))
    damaged = (
        bool(header.get("is_repaired"))
        or header.get("eof_marker") is False
        or any("repair" in w.lower() for w in engine_warnings)
    )
    if damaged:
        # In a damaged file a page with no content may be a page that was LOST, not an empty one. Never report it as
        # blank: it is unreadable, and the warning says why.
        lost = [p.page_no for p in pages if p.extraction == "blank"]
        for n in lost:
            by_no[n].extraction = "unreadable"
            by_no[n].warnings.append("blank_in_damaged_file")
        warnings.append(
            _warn(
                "file_repaired",
                "The PDF was damaged or cut short and had to be repaired to be read; some content may be missing.",
                lost,
            )
        )
    if header.get("active_content"):
        warnings.append(
            _warn(
                "active_content",
                "The PDF contains elements that can carry scripts or attachments ("
                + ", ".join(header["active_content"])
                + "). It is only ever stored and downloaded, never opened by this application. "
                "This is a heuristic, not a malware scan.",
            )
        )
    meta = {k: v for k, v in (header.get("metadata") or {}).items() if v}
    ex = Extraction(int(header["page_count"]), meta, pages, warnings, {"engine_warnings": engine_warnings[:5]})
    _require_usable(ex, settings, ocr_ready)
    return ex


def _require_usable(ex: Extraction, settings: Settings, ocr_ready: bool) -> None:
    n = ex.page_count
    if ex.count("text") + ex.count("ocr") > 0:
        if len(ex.pages) > 0 and sum(1 for p in ex.pages if "page_failed" in p.warnings) * 2 >= n:
            raise ProcessingFailure(
                "likely_corrupt",
                "Most pages of this PDF could not be read. The file is probably damaged. Try a different copy.",
            )
        return
    unreadable = [
        p
        for p in ex.pages
        if p.extraction == "unreadable" and not {"page_failed", "blank_in_damaged_file"} & set(p.warnings)
    ]
    if unreadable and not settings.feature_ocr:
        raise ProcessingFailure(
            "scanned_no_ocr",
            "This PDF has no text layer (it looks like a scan). Turn on OCR (FEATURE_OCR=true) and retry, or upload "
            "a text-based copy.",
        )
    if unreadable and not ocr_ready:
        raise ProcessingFailure(
            "ocr_unavailable",
            "This PDF has no text layer and OCR is enabled, but the OCR engine is not available on the worker. "
            "Install it (tesseract) or upload a text-based copy.",
        )
    if any({"page_failed", "blank_in_damaged_file"} & set(p.warnings) for p in ex.pages):
        raise ProcessingFailure("likely_corrupt", "No page of this PDF could be read. The file is probably damaged.")
    raise ProcessingFailure(
        "no_extractable_text",
        "No text could be extracted from this PDF (the pages are blank or contain only pictures).",
        retryable=False,
    )
