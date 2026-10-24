"""Object storage providers. `build_store` selects the adapter from configuration."""

from __future__ import annotations

from app.core.config import Settings
from app.providers.storage.base import (
    ObjectInfo,
    ObjectNotFound,
    ObjectStore,
    StorageError,
    validate_key,
    validate_prefix,
)
from app.providers.storage.local import LocalObjectStore
from app.providers.storage.memory import MemoryObjectStore

__all__ = [
    "LocalObjectStore",
    "MemoryObjectStore",
    "ObjectInfo",
    "ObjectNotFound",
    "ObjectStore",
    "StorageError",
    "build_store",
    "document_key",
    "project_prefix",
    "validate_key",
    "validate_prefix",
]


def project_prefix(project_id: object) -> str:
    return f"projects/{project_id}/"


def document_key(project_id: object, document_id: object) -> str:
    """Generated storage key. The user's filename never appears in it."""
    return f"projects/{project_id}/documents/{document_id}.pdf"


def build_store(settings: Settings) -> ObjectStore:
    if settings.storage_backend == "memory":
        return MemoryObjectStore()
    return LocalObjectStore(settings.storage_root_path)
