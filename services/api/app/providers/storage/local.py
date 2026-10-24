"""Local-filesystem adapter (development and single-host deployments).

Safety properties (all unit-tested): keys are validated; the resolved path must stay inside the root (so a symlink
planted inside the root cannot redirect a read or write); writes are atomic (temp file in the target directory,
fsync, os.replace); files are 0600 and directories 0700; deletes never follow a symlinked directory out of the root.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import tempfile
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

from app.providers.storage.base import (
    ObjectInfo,
    ObjectNotFound,
    StorageError,
    validate_key,
    validate_prefix,
)

_CHUNK = 1024 * 1024


class LocalObjectStore:
    name = "local"

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        try:
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        except OSError as e:
            raise StorageError(f"storage root is not usable ({type(e).__name__})") from e
        self._root_real = self.root.resolve()

    def _path(self, key: str) -> Path:
        validate_key(key)
        target = (self._root_real / key).resolve()
        if self._root_real not in target.parents:
            raise StorageError("invalid storage key")
        return target

    def _make_dirs(self, directory: Path) -> None:
        """Create missing directories one level at a time so EVERY new level is 0700 (parents=True would leave
        intermediate directories at the umask default)."""
        missing: list[Path] = []
        cur = directory
        while cur != self._root_real and not cur.exists():
            missing.append(cur)
            cur = cur.parent
        for d in reversed(missing):
            d.mkdir(mode=0o700, exist_ok=True)
            os.chmod(d, 0o700)

    def put_file(self, key: str, src: Path) -> int:
        target = self._path(key)
        try:
            self._make_dirs(target.parent)
            fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=".tmp-")
            tmp = Path(tmp_name)
            try:
                with os.fdopen(fd, "wb") as out, open(src, "rb") as inp:
                    shutil.copyfileobj(inp, out, _CHUNK)
                    out.flush()
                    os.fsync(out.fileno())
                os.chmod(tmp, 0o600)
                size = tmp.stat().st_size
                os.replace(tmp, target)
            except BaseException:
                with contextlib.suppress(OSError):
                    tmp.unlink()
                raise
            return size
        except OSError as e:
            raise StorageError(f"could not store object ({type(e).__name__})") from e

    def open(self, key: str) -> BinaryIO:
        path = self._path(key)
        try:
            return open(path, "rb")  # noqa: SIM115  the caller owns and closes the handle
        except FileNotFoundError:
            raise ObjectNotFound(key) from None
        except OSError as e:
            raise StorageError(f"could not read object ({type(e).__name__})") from e

    def download_to(self, key: str, dest: Path) -> None:
        with self.open(key) as src, open(dest, "wb") as out:
            shutil.copyfileobj(src, out, _CHUNK)

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def delete(self, key: str) -> bool:
        path = self._path(key)
        try:
            path.unlink()
        except FileNotFoundError:
            return False
        except OSError as e:
            raise StorageError(f"could not delete object ({type(e).__name__})") from e
        return True

    def delete_prefix(self, prefix: str) -> int:
        validate_prefix(prefix)
        base = self._path(prefix[:-1])
        if not base.is_dir():
            return 0
        count = sum(1 for p in base.rglob("*") if p.is_file())
        shutil.rmtree(base, ignore_errors=False)
        return count

    def list_objects(self, prefix: str) -> Iterator[ObjectInfo]:
        validate_prefix(prefix)
        base = self._path(prefix[:-1])
        if not base.is_dir():
            return
        for p in sorted(base.rglob("*")):
            if p.is_file() and not p.name.startswith(".tmp-") and not p.is_symlink():
                st = p.stat()
                yield ObjectInfo(
                    key=p.relative_to(self._root_real).as_posix(),
                    size=st.st_size,
                    modified_at=datetime.fromtimestamp(st.st_mtime, tz=UTC),
                )
