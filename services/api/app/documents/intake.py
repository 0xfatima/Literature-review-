"""Streaming upload intake. The request body is written to a private temporary file while its size is counted
(and capped), its SHA-256 computed and its header checked, so a file over the limit or not a PDF is rejected without
reading the rest and without ever holding the file in memory. The body is raw bytes, not multipart (ADR A13.1)."""

from __future__ import annotations

import contextlib
import hashlib
import os
import shutil
import tempfile
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import anyio

from app.documents.validation import UploadRejection, check_signature

_FLUSH_AT = 1024 * 1024
_SNIFF_BYTES = 8


@dataclass
class ReceivedUpload:
    directory: Path
    path: Path
    size: int
    sha256: str
    head: bytes
    tail: bytes

    def cleanup(self) -> None:
        shutil.rmtree(self.directory, ignore_errors=True)


async def receive(
    stream: AsyncIterator[bytes], *, max_bytes: int, declared_length: int | None, tmp_root: Path | None = None
) -> ReceivedUpload:
    """Write the stream to a temp file. Raises UploadRejection (413 too large, 422 empty / not a PDF)."""
    if declared_length is not None and declared_length > max_bytes:
        raise _too_large(max_bytes)
    directory = Path(tempfile.mkdtemp(prefix="srs-upload-", dir=tmp_root))
    path = directory / "upload.pdf"
    digest = hashlib.sha256()
    size = 0
    head = b""
    tail = b""
    pending = bytearray()
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as out:
            async for chunk in stream:
                if not chunk:
                    continue
                size += len(chunk)
                if size > max_bytes:
                    raise _too_large(max_bytes)
                digest.update(chunk)
                if len(head) < _SNIFF_BYTES:
                    head = (head + chunk)[:_SNIFF_BYTES]
                    if len(head) >= _SNIFF_BYTES:
                        check_signature(head)  # reject a non-PDF before reading the rest of the body
                tail = (tail + chunk)[-1024:]
                pending += chunk
                if len(pending) >= _FLUSH_AT:
                    await anyio.to_thread.run_sync(out.write, bytes(pending))
                    pending.clear()
            if pending:
                await anyio.to_thread.run_sync(out.write, bytes(pending))
        if size == 0:
            raise UploadRejection(422, "empty_file", "The file is empty (0 bytes).")
        check_signature(head)  # also covers files shorter than the early-check window
        return ReceivedUpload(directory, path, size, digest.hexdigest(), head, tail)
    except BaseException:
        with contextlib.suppress(OSError):
            shutil.rmtree(directory, ignore_errors=True)
        raise


def _too_large(max_bytes: int) -> UploadRejection:
    return UploadRejection(
        413, "file_too_large", f"The file is larger than the {max_bytes // (1024 * 1024)} MB upload limit."
    )
