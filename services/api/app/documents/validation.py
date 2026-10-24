"""Upload validation that needs no PDF parser: extension, declared MIME type, magic bytes, safe display filename.
Stdlib only. Every rejection carries an HTTP status, a stable code and a message the user can act on.

The declared type and the extension are never trusted on their own: the file signature decides, and the real parse
(in a resource-limited child process) decides whether the file is actually a readable PDF.
"""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import unquote

ALLOWED_DECLARED_TYPES = frozenset({"application/pdf", "application/x-pdf", "application/octet-stream"})
MAX_FILENAME_LENGTH = 180
MAX_FILENAME_HEADER = 1024
_SIGNATURE = re.compile(rb"^%PDF-[12]\.\d")
_SPACES = re.compile(r"\s+")


class UploadRejection(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code, self.code, self.message = status_code, code, message


def decode_filename_header(raw: str | None) -> str:
    """`X-Filename` carries the original name percent-encoded as UTF-8 (HTTP headers are ASCII)."""
    if raw is None or len(raw) > MAX_FILENAME_HEADER:
        return ""
    return unquote(raw, encoding="utf-8", errors="replace")


def sanitize_filename(raw: str | None) -> str:
    """A name that is safe to DISPLAY and to put in a download header. It is never used as a storage path.
    Strips directories, control and format characters (bidi overrides can spoof an extension), collapses spaces and
    bounds the length while keeping the extension."""
    name = (raw or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = unicodedata.normalize("NFC", name)
    name = "".join(ch for ch in name if unicodedata.category(ch) not in ("Cc", "Cf", "Cs", "Co", "Cn"))
    name = _SPACES.sub(" ", name).strip(" .")
    if not name:
        return "document.pdf"
    if len(name) > MAX_FILENAME_LENGTH:
        stem, dot, ext = name.rpartition(".")
        keep = MAX_FILENAME_LENGTH - len(ext) - 1
        name = f"{stem[:keep]}.{ext}" if dot and 0 < len(ext) <= 10 and keep > 0 else name[:MAX_FILENAME_LENGTH]
    return name


def check_extension(filename: str) -> None:
    if not filename.lower().endswith(".pdf"):
        raise UploadRejection(
            415, "unsupported_file_type", "Only PDF files can be uploaded. The file name must end in .pdf."
        )


def check_declared_type(content_type: str | None) -> None:
    declared = (content_type or "").split(";", 1)[0].strip().lower()
    if declared and declared not in ALLOWED_DECLARED_TYPES:
        raise UploadRejection(
            415,
            "unsupported_media_type",
            f"The file was sent as '{declared[:60]}', not as a PDF. Choose a PDF file.",
        )


def check_signature(head: bytes) -> None:
    """The first bytes must be the PDF header (`%PDF-1.x` or `%PDF-2.x`)."""
    if not _SIGNATURE.match(head):
        raise UploadRejection(
            422,
            "not_a_pdf",
            "This file is not a PDF: it does not start with the PDF header. It may be a renamed file of another "
            "type, or damaged.",
        )


def has_eof_marker(tail: bytes) -> bool:
    return b"%%EOF" in tail[-1024:]
