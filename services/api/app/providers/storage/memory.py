"""In-memory adapter for tests. It lives in one process, so it is refused outside development/test."""

from __future__ import annotations

import io
import shutil
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

from app.providers.storage.base import ObjectInfo, ObjectNotFound, validate_key, validate_prefix


class MemoryObjectStore:
    name = "memory"

    def __init__(self) -> None:
        self._objects: dict[str, tuple[bytes, datetime]] = {}

    def put_file(self, key: str, src: Path) -> int:
        validate_key(key)
        data = Path(src).read_bytes()
        self._objects[key] = (data, datetime.now(UTC))
        return len(data)

    def open(self, key: str) -> BinaryIO:
        validate_key(key)
        if key not in self._objects:
            raise ObjectNotFound(key)
        return io.BytesIO(self._objects[key][0])

    def download_to(self, key: str, dest: Path) -> None:
        with self.open(key) as src, open(dest, "wb") as out:
            shutil.copyfileobj(src, out)

    def exists(self, key: str) -> bool:
        validate_key(key)
        return key in self._objects

    def delete(self, key: str) -> bool:
        validate_key(key)
        return self._objects.pop(key, None) is not None

    def delete_prefix(self, prefix: str) -> int:
        validate_prefix(prefix)
        doomed = [k for k in self._objects if k.startswith(prefix)]
        for k in doomed:
            del self._objects[k]
        return len(doomed)

    def list_objects(self, prefix: str) -> Iterator[ObjectInfo]:
        validate_prefix(prefix)
        for k in sorted(self._objects):
            if k.startswith(prefix):
                data, at = self._objects[k]
                yield ObjectInfo(key=k, size=len(data), modified_at=at)

    def backdate(self, key: str, when: datetime) -> None:  # test helper: age an object for orphan-sweep tests
        data, _ = self._objects[key]
        self._objects[key] = (data, when)
