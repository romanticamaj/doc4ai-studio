"""Schema v7: documents.source_copy_* (spec 2026-10-29 §9.1; plan Task 2, A2)."""
from __future__ import annotations

import sqlite3

import pytest

from aidoc.store import _SOURCE_COPY_COLS, SCHEMA_VERSION, Store

# the v6 documents table, verbatim from the v6 store (git show d307f47:src/aidoc/store.py)
_V6_DOCUMENTS = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS documents(
  id TEXT PRIMARY KEY, sha256 TEXT NOT NULL, source_path TEXT NOT NULL, output_dir TEXT NOT NULL UNIQUE,
  engine TEXT NOT NULL, quality_json TEXT NOT NULL, pages INTEGER, lang TEXT NOT NULL, aidoc_version TEXT NOT NULL,
  created_at REAL NOT NULL, status TEXT NOT NULL, work_copy_path TEXT, work_copy_expires_at REAL);
CREATE INDEX IF NOT EXISTS documents_sha ON documents(sha256);
"""
_V6_COLS = ("id, sha256, source_path, output_dir, engine, quality_json, pages, lang, aidoc_version, created_at, "
            "status, work_copy_path, work_copy_expires_at")


def _v6_db(path, n=25) -> list[tuple]:
    """A real v6 file: the v6 documents DDL, meta schema_version = 6, n rows. Returns the rows."""
    con = sqlite3.connect(str(path))
    con.executescript(_V6_DOCUMENTS)
    con.execute("INSERT INTO meta(key, value) VALUES('schema_version', '6')")
    rows = []
    for i in range(n):
        r = (f"d{i:02d}", f"{i:064x}", f"C:\\src\\文件{i}.pdf", f"C:\\out\\doc{i}", "docling",
             '{"level": "ok", "score": 0.9}', i + 1, "cht", "0.1.0", 1000.0 + i,
             "ok" if i % 5 else "orphaned", f"C:\\data\\work\\t{i}" if i % 2 else None,
             2000.0 + i if i % 2 else None)
        con.execute(f"INSERT INTO documents({_V6_COLS}) VALUES({', '.join('?' * 13)})", r)
        rows.append(r)
    con.commit()
    con.close()
    return rows


def _dump(s: Store) -> list[tuple]:
    return [tuple(r) for r in s.con.execute(f"SELECT {_V6_COLS} FROM documents ORDER BY id")]


def test_v6_to_v7_keeps_every_row(tmp_path):
    db = tmp_path / "aidoc.db"
    before = _v6_db(db)
    s = Store(db)
    cols = {r[1] for r in s.con.execute("PRAGMA table_info(documents)")}
    assert {c for c, _ in _SOURCE_COPY_COLS} <= cols and s.schema_version() == 8 == SCHEMA_VERSION
    after = _dump(s)
    assert after == before and len(after) == 25                 # A2
    assert all(r["source_copy_status"] is None and r["source_copy_path"] is None for r in s.list_documents())
    s.close()


def test_migration_is_idempotent(tmp_path):
    db = tmp_path / "aidoc.db"
    _v6_db(db)
    s = Store(db)
    cols1, rows1 = [tuple(r) for r in s.con.execute("PRAGMA table_info(documents)")], _dump(s)
    s.close()
    s = Store(db)
    assert [tuple(r) for r in s.con.execute("PRAGMA table_info(documents)")] == cols1
    assert _dump(s) == rows1 and s.schema_version() == 8
    s.close()


def _doc(s: Store, n: int, **kw) -> str:
    return s.upsert_document(sha256=f"{n:064x}", source_path=f"C:\\x{n}.pdf", output_dir=f"C:\\out\\x{n}",
                             engine="fake", quality={"level": "ok"}, pages=1, lang="cht", aidoc_version="0.1.0",
                             status="ok", **kw)


def test_source_copy_path_is_unique_ignoring_case(tmp_path):
    s = Store(tmp_path / "aidoc.db")
    a, b = _doc(s, 1), _doc(s, 2)
    s.set_source_copy(a, path="C:\\lib\\source\\Report\\Report.pdf", status="copied", error=None, size=3, mtime_ns=4)
    with pytest.raises(sqlite3.IntegrityError):
        s.set_source_copy(b, path="c:\\LIB\\source\\report\\report.PDF", status="copied", error=None)
    # NULL paths never collide
    s.set_source_copy(b, path=None, status="failed", error="no_space: full")
    c = _doc(s, 3)
    s.set_source_copy(c, path=None, status="failed", error="no_space: full")
    s.close()


def test_set_source_copy_and_lookup(tmp_path):
    s = Store(tmp_path / "aidoc.db")
    a = _doc(s, 1)
    s.set_source_copy(a, path="C:\\lib\\source\\x\\x.pdf", status="copied", error=None, size=10, mtime_ns=99, at=5.0)
    d = s.get_document(a)
    assert (d["source_copy_path"], d["source_copy_status"], d["source_copy_size"], d["source_copy_mtime_ns"],
            d["source_copy_at"]) == ("C:\\lib\\source\\x\\x.pdf", "copied", 10, 99, 5.0)
    assert s.document_by_source_copy("c:\\LIB\\source\\x\\X.pdf")["id"] == a
    assert s.document_by_source_copy("C:\\other.pdf") is None
    _doc(s, 2)
    s.set_document_status(_doc(s, 3), "orphaned")
    assert [r["id"] for r in s.source_copy_rows()] == [a, s.find_document(f"{2:064x}", "C:\\out\\x2")["id"]]
    s.close()


def test_index_created_after_columns(tmp_path):
    db = tmp_path / "aidoc.db"
    _v6_db(db, n=1)
    s = Store(db)                                                 # no "no such column: source_copy_path"
    idx = s.con.execute("SELECT sql FROM sqlite_master WHERE type='index' AND name='documents_source_copy'").fetchone()
    assert idx is not None and "NOCASE" in idx[0]
    s.close()
