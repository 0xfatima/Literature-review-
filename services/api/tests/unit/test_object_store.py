"""ObjectStore contract, run against both adapters, plus the local adapter's safety properties."""

import os
import stat
from datetime import UTC, datetime, timedelta

import pytest

from app.providers.storage import (
    LocalObjectStore,
    MemoryObjectStore,
    ObjectNotFound,
    StorageError,
    document_key,
    project_prefix,
    validate_key,
    validate_prefix,
)


@pytest.fixture(params=["local", "memory"])
def store(request, tmp_path):
    return LocalObjectStore(tmp_path / "root") if request.param == "local" else MemoryObjectStore()


@pytest.fixture
def src(tmp_path):
    p = tmp_path / "src.bin"
    p.write_bytes(b"hello pdf")
    return p


K = "projects/p1/documents/d1.pdf"


def test_put_open_download_exists_delete(store, src, tmp_path):
    assert not store.exists(K)
    assert store.put_file(K, src) == 9 and store.exists(K)
    with store.open(K) as f:
        assert f.read() == b"hello pdf"
    dest = tmp_path / "out.bin"
    store.download_to(K, dest)
    assert dest.read_bytes() == b"hello pdf"
    assert store.delete(K) is True and store.delete(K) is False and not store.exists(K)


def test_missing_objects_raise_object_not_found(store, tmp_path):
    with pytest.raises(ObjectNotFound):
        store.open(K)
    with pytest.raises(ObjectNotFound):
        store.download_to(K, tmp_path / "x")


def test_overwrite_replaces_the_content(store, src, tmp_path):
    store.put_file(K, src)
    other = tmp_path / "o.bin"
    other.write_bytes(b"second")
    store.put_file(K, other)
    with store.open(K) as f:
        assert f.read() == b"second"


def test_list_and_delete_prefix_touch_only_that_prefix(store, src):
    for k in ("projects/p1/documents/a.pdf", "projects/p1/documents/b.pdf", "projects/p2/documents/c.pdf"):
        store.put_file(k, src)
    assert [o.key for o in store.list_objects("projects/")] == [
        "projects/p1/documents/a.pdf",
        "projects/p1/documents/b.pdf",
        "projects/p2/documents/c.pdf",
    ]
    assert all(o.size == 9 for o in store.list_objects("projects/p1/"))
    assert store.delete_prefix("projects/p1/") == 2
    assert [o.key for o in store.list_objects("projects/")] == ["projects/p2/documents/c.pdf"]
    assert store.delete_prefix("projects/p1/") == 0  # idempotent
    assert store.delete_prefix("projects/zzz/") == 0


@pytest.mark.parametrize(
    "key",
    [
        "",
        "/abs/path",
        "../x",
        "a/../b",
        "a//b",
        "a/./b",
        "A/upper",
        "a b",
        "a\\b",
        "a/b/",
        ".hidden",
        "a/.hidden",
        "a" * 301,
        "a/b..c",
        "x/\u00e9",
    ],
)
def test_unsafe_keys_are_refused(store, src, key):
    with pytest.raises(StorageError):
        store.put_file(key, src)
    with pytest.raises(StorageError):
        store.open(key)
    with pytest.raises(StorageError):
        validate_key(key)


@pytest.mark.parametrize("prefix", ["", "/", "projects", "projects/p1", "../", "a//"])
def test_unsafe_prefixes_are_refused_so_a_store_cannot_be_wiped(store, prefix):
    with pytest.raises(StorageError):
        store.delete_prefix(prefix)
    with pytest.raises(StorageError):
        validate_prefix(prefix)


def test_generated_keys_are_valid_and_never_contain_a_filename():
    import uuid

    pid, did = uuid.uuid4(), uuid.uuid4()
    assert validate_key(document_key(pid, did)) and project_prefix(pid).endswith("/")
    assert document_key(pid, did) == f"projects/{pid}/documents/{did}.pdf"


# ---- local adapter only ----


def test_local_files_and_every_new_directory_are_private(src, tmp_path):
    s = LocalObjectStore(tmp_path / "root")
    s.put_file(K, src)
    for rel, mode in (("projects", 0o700), ("projects/p1", 0o700), ("projects/p1/documents", 0o700), (K, 0o600)):
        assert stat.S_IMODE(os.stat(tmp_path / "root" / rel).st_mode) == mode, rel


def test_a_symlink_inside_the_root_cannot_redirect_writes_or_reads_outside(src, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.pdf").write_bytes(b"top secret")
    root = tmp_path / "root"
    s = LocalObjectStore(root)
    (root / "projects").symlink_to(outside)
    with pytest.raises(StorageError):
        s.put_file("projects/p1/documents/x.pdf", src)
    with pytest.raises(StorageError):
        s.open("projects/secret.pdf")
    assert list(outside.iterdir()) == [outside / "secret.pdf"]  # nothing was written next to the secret


def test_a_failed_write_leaves_no_partial_object_or_temp_file(tmp_path):
    s = LocalObjectStore(tmp_path / "root")
    with pytest.raises(StorageError):
        s.put_file(K, tmp_path / "does-not-exist.bin")
    assert not s.exists(K)
    leftovers = [p for p in (tmp_path / "root").rglob("*") if p.is_file()]
    assert leftovers == []


def test_temp_files_are_not_listed(src, tmp_path):
    s = LocalObjectStore(tmp_path / "root")
    s.put_file(K, src)
    (tmp_path / "root" / "projects/p1/documents/.tmp-abc").write_bytes(b"partial")
    assert [o.key for o in s.list_objects("projects/")] == [K]


def test_a_root_that_cannot_be_created_is_a_clear_storage_error(tmp_path):
    f = tmp_path / "file"
    f.write_text("x")
    with pytest.raises(StorageError):
        LocalObjectStore(f / "sub")


def test_memory_backdate_helper_changes_listing_age():
    m = MemoryObjectStore()
    import tempfile
    from pathlib import Path

    with tempfile.NamedTemporaryFile() as t:
        Path(t.name).write_bytes(b"x")
        m.put_file(K, Path(t.name))
    when = datetime.now(UTC) - timedelta(days=5)
    m.backdate(K, when)
    assert next(m.list_objects("projects/")).modified_at == when
