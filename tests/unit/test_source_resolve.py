"""One source resolver: work copy -> source copy -> original (spec 2026-10-29 §6; plan Task 4, index A27)."""
from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path

import pytest

import doc4ai
from doc4ai import sources
from doc4ai.sources import ResolvedSource, document_source, resolve_source, sha_matches_cached
from doc4ai.store import Store


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


@pytest.fixture(autouse=True)
def _fresh_cache():
    sources._SHA_CACHE.clear()
    yield
    sources._SHA_CACHE.clear()


def _setup(tmp_path, data=b"%PDF-1.4 original", ext=".pdf"):
    orig = tmp_path / "in" / f"a{ext}"
    orig.parent.mkdir(parents=True)
    orig.write_bytes(data)
    wc = tmp_path / "data" / "work" / "t1"
    wc.mkdir(parents=True)
    (wc / f"src{ext}").write_bytes(data)
    copy = tmp_path / "lib" / "source" / "a" / f"a{ext}"
    copy.parent.mkdir(parents=True)
    copy.write_bytes(data)
    st = copy.stat()
    doc = {"id": "d1", "sha256": _sha(data), "source_path": str(orig), "work_copy_path": str(wc),
           "source_copy_path": str(copy), "source_copy_size": st.st_size, "source_copy_mtime_ns": st.st_mtime_ns}
    return doc, orig, wc, copy


def test_order_work_copy_then_source_copy_then_original(tmp_path):
    doc, orig, wc, copy = _setup(tmp_path)
    assert resolve_source(doc) == ResolvedSource(wc / "src.pdf", "work_copy")
    (wc / "src.pdf").unlink()
    assert resolve_source(doc) == ResolvedSource(copy, "source_copy")
    copy.unlink()
    assert resolve_source(doc) == ResolvedSource(orig, "original")
    orig.unlink()
    assert resolve_source(doc) is None and document_source(doc) is None


def test_modified_original_is_not_the_source(tmp_path):
    """Behaviour change (spec §6.2): the old document_source showed a rewritten original next to the old result."""
    doc, orig, wc, copy = _setup(tmp_path)
    (wc / "src.pdf").unlink()
    copy.unlink()
    orig.write_bytes(b"%PDF-1.4 edited later")
    assert resolve_source(doc) is None


def test_source_copy_with_wrong_sha_is_skipped(tmp_path):
    doc, orig, wc, copy = _setup(tmp_path)
    (wc / "src.pdf").unlink()
    copy.write_bytes(b"%PDF-1.4 someone edited the copy")      # size and mtime change -> re-hashed -> mismatch
    assert resolve_source(doc) == ResolvedSource(orig, "original")


def test_source_copy_trusted_by_size_and_mtime(tmp_path, monkeypatch):
    doc, _orig, wc, _copy = _setup(tmp_path)
    (wc / "src.pdf").unlink()
    calls = []
    monkeypatch.setattr(sources, "file_sha256", lambda p: calls.append(p) or "x")
    assert resolve_source(doc).kind == "source_copy" and calls == []


def test_source_copy_drift_rehashed_and_refreshed(tmp_path):
    store = Store(tmp_path / "db" / "aidoc.db")
    data = b"%PDF-1.4 drift"
    doc, _orig, wc, copy = _setup(tmp_path, data)
    did = store.upsert_document(sha256=doc["sha256"], source_path=doc["source_path"], output_dir="C:\\out\\a",
                                engine="fake", quality={"level": "ok"}, pages=1, lang="cht", aidoc_version="0",
                                status="ok")
    store.set_source_copy(did, path=str(copy), status="copied", error=None, size=len(data), mtime_ns=1)
    (wc / "src.pdf").unlink()
    row = store.get_document(did)
    os.utime(copy, ns=(time.time_ns(), time.time_ns() - 5_000_000_000))
    assert resolve_source(row, store=store).kind == "source_copy"
    assert store.get_document(did)["source_copy_mtime_ns"] == copy.stat().st_mtime_ns
    store.close()


def test_pdf_only(tmp_path):
    doc, _orig, _wc, _copy = _setup(tmp_path, b"PK docx bytes", ".docx")
    assert resolve_source(doc).kind == "work_copy"
    assert resolve_source(doc, pdf_only=True) is None


def test_original_hash_is_cached(tmp_path, monkeypatch):
    doc, orig, wc, copy = _setup(tmp_path)
    (wc / "src.pdf").unlink()
    copy.unlink()
    real, calls = sources.file_sha256, []
    monkeypatch.setattr(sources, "file_sha256", lambda p: calls.append(p) or real(p))
    assert resolve_source(doc).kind == "original" and len(calls) == 1
    assert resolve_source(doc).kind == "original" and len(calls) == 1          # cached
    assert sha_matches_cached(orig, doc["sha256"]) and len(calls) == 1
    orig.write_bytes(b"%PDF-1.4 other content here")
    assert resolve_source(doc) is None and len(calls) == 2                     # a new (size, mtime) key


def test_long_source_copy_path_is_returned_long_path_safe(tmp_path):
    from doc4ai import fsops
    data = b"%PDF-1.4 deep"
    copy = tmp_path / "source" / ("d" * 150) / ("f" * 150 + ".pdf")
    os.makedirs(fsops.long_path(copy.parent))
    with open(fsops.long_path(copy), "wb") as f:
        f.write(data)
    doc = {"sha256": _sha(data), "source_path": "f.pdf", "source_copy_path": str(copy)}
    r = resolve_source(doc)
    assert r.kind == "source_copy" and r.path == fsops.long_path(copy)
    with open(r.path, "rb") as f:
        assert f.read() == data


def test_no_other_lookup_left():
    """Review Focus 1: the four divergent lookups are gone."""
    src = Path(doc4ai.__file__).parent.rglob("*.py")
    text = "\n".join(p.read_text(encoding="utf-8") for p in src)
    assert "_reconvert_source" not in text and "_source_pdf" not in text
    assert text.count("def document_source") == 1


def test_repeated_lookups_never_rehash_an_unchanged_file(tmp_path, monkeypatch):
    """Every Document-view request resolves the source: a drifted (here: edited) copy and the original are hashed once
    per (path, size, mtime), not on every view."""
    doc, orig, wc, copy = _setup(tmp_path)
    (wc / "src.pdf").unlink()
    copy.write_bytes(b"%PDF-1.4 someone edited the copy")
    real, calls = sources.file_sha256, []
    monkeypatch.setattr(sources, "file_sha256", lambda p: calls.append(str(p)) or real(p))
    for _ in range(5):
        assert resolve_source(doc).kind == "original"
    assert len(calls) == 2                                         # the copy once, the original once
    assert orig.is_file()
