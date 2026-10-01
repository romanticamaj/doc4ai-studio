import sqlite3
import time

import pytest

from aidoc.store import SCHEMA_VERSION, Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "aidoc.db")
    yield s
    s.close()


def test_schema_v4_tables_and_version(store):
    names = {r[0] for r in store.con.execute("SELECT name FROM sqlite_master WHERE type IN ('table','index')")}
    assert {"api_tokens", "mcp_clients", "mcp_calls", "oauth_clients", "mcp_calls_ts", "mcp_calls_token_ts"} <= names
    assert SCHEMA_VERSION == 4
    assert store._q1("SELECT value FROM meta WHERE key='schema_version'")[0] == "4"


def test_v2_database_upgrades_in_place(tmp_path):
    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    con.executescript("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT); INSERT INTO meta VALUES('schema_version','2');"
                      "CREATE TABLE tasks(id TEXT PRIMARY KEY, job_id TEXT NOT NULL, source_path TEXT NOT NULL, work_path TEXT,"
                      " sha256 TEXT NOT NULL, size INTEGER NOT NULL, mtime REAL NOT NULL, lang TEXT NOT NULL, engine TEXT,"
                      " tried_json TEXT NOT NULL DEFAULT '[]', attempt INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL,"
                      " error_kind TEXT, error_msg TEXT, quality_json TEXT, output_dir TEXT NOT NULL, pid INTEGER,"
                      " created_at REAL NOT NULL, updated_at REAL NOT NULL, flags_json TEXT NOT NULL DEFAULT '{}');")
    con.commit()
    con.close()
    s = Store(db)
    assert s._q1("SELECT value FROM meta WHERE key='schema_version'")[0] == "4"
    assert s.list_api_tokens() == []
    s.close()


def test_api_token_crud(store):
    tid = store.create_api_token(name="Claude Code @ laptop", prefix="doc4ai_pat_3kX9", token_hash="h" * 64,
                                 scopes=["doc4ai:read", "doc4ai:convert"], expires_at=time.time() + 86400, note="n",
                                 rate_limit_per_min=None)
    row = store.get_api_token(tid)
    assert row["name"] == "Claude Code @ laptop" and row["scopes"] == ["doc4ai:read", "doc4ai:convert"]
    assert row["workspace"] == "default" and row["revoked_at"] is None and row["rotated_from"] is None
    assert store.get_api_token_by_hash("h" * 64)["id"] == tid
    assert store.get_api_token_by_hash("x" * 64) is None
    store.update_api_token(tid, name="renamed", last_used_at=5.0, last_used_ip="100.64.0.9", last_client="claude-code/2.3")
    row = store.get_api_token(tid)
    assert row["name"] == "renamed" and row["last_client"] == "claude-code/2.3"
    assert store.revoke_api_token(tid, "lost laptop", at=123.0) is True
    assert store.revoke_api_token(tid, "again", at=124.0) is False          # already revoked: unchanged
    row = store.get_api_token(tid)
    assert row["revoked_at"] == 123.0 and row["revoked_reason"] == "lost laptop"
    t2 = store.create_api_token(name="second", prefix="doc4ai_pat_AAAA", token_hash="g" * 64, scopes=["doc4ai:read"],
                                expires_at=None, rotated_from=tid)
    ids = [t["id"] for t in store.list_api_tokens()]
    assert ids == [t2, tid]                                                  # newest first


def test_token_hash_is_unique(store):
    store.create_api_token(name="a", prefix="p", token_hash="same", scopes=["doc4ai:read"], expires_at=None)
    with pytest.raises(sqlite3.IntegrityError):
        store.create_api_token(name="b", prefix="p", token_hash="same", scopes=["doc4ai:read"], expires_at=None)


def test_mcp_clients_upsert_and_active_filter(store):
    tid = store.create_api_token(name="a", prefix="p", token_hash="h1", scopes=["doc4ai:read"], expires_at=None)
    cid, created = store.upsert_mcp_client(token_id=tid, client_name="claude-code", client_version="2.3.1",
                                          protocol_version="2026-07-28", user_agent="ua", ip="127.0.0.1", now=1000.0)
    assert created is True
    cid2, created2 = store.upsert_mcp_client(token_id=tid, client_name="claude-code", client_version="2.3.1",
                                            protocol_version="2026-07-28", user_agent="ua2", ip="127.0.0.2", now=1500.0)
    assert cid2 == cid and created2 is False
    row = store.get_mcp_client(cid)
    assert row["request_count"] == 2 and row["first_seen"] == 1000.0 and row["last_seen"] == 1500.0
    assert row["last_ip"] == "127.0.0.2" and row["user_agent"] == "ua2"
    other, _ = store.upsert_mcp_client(token_id=tid, client_name="cursor", client_version=None, protocol_version="2025-11-25",
                                       user_agent=None, ip="127.0.0.1", now=100.0)
    assert {c["id"] for c in store.list_mcp_clients()} == {cid, other}
    assert [c["id"] for c in store.list_mcp_clients(active_since=1200.0)] == [cid]
    assert [c["id"] for c in store.list_mcp_clients(token_id=tid)] == [cid, other]      # last_seen DESC


def test_mcp_calls_insert_list_count_prune(store):
    tid = store.create_api_token(name="a", prefix="p", token_hash="h1", scopes=["doc4ai:read"], expires_at=None)
    ids = []
    for i in range(10):
        ids.append(store.insert_mcp_call(ts=1000.0 + i, token_id=tid if i % 2 else None, method="tools/call",
                                         tool_name="read_document" if i < 7 else "search_library",
                                         status="ok" if i % 3 else "tool_error", duration_ms=10 * i, http_status=200,
                                         args_summary='{"doc_id":"d"}', response_tokens_est=100 + i, ip="127.0.0.1",
                                         protocol_version="2026-07-28"))
    assert ids == sorted(ids)
    rows = store.list_mcp_calls(limit=3)
    assert [r["id"] for r in rows] == ids[-1:-4:-1]                           # newest first
    page2 = store.list_mcp_calls(limit=3, before_id=rows[-1]["id"])
    assert [r["id"] for r in page2] == ids[-4:-7:-1]
    assert len(store.list_mcp_calls(token_id=tid)) == 5
    assert len(store.list_mcp_calls(tool="search_library")) == 3
    assert len(store.list_mcp_calls(status="tool_error")) == 4
    assert len(store.list_mcp_calls(since=1005.0, until=1007.0)) == 3
    assert store.count_mcp_calls(since=0) == 10 and store.count_mcp_calls(since=0, errors_only=True) == 4
    assert store.count_mcp_calls(since=0, token_id=tid) == 5
    assert len(store.mcp_call_rows_since(1008.0)) == 2
    # retention: by age first, then by row cap (oldest go)
    assert store.prune_mcp_calls(older_than_ts=1003.0, max_rows=100) == 3
    assert store.prune_mcp_calls(older_than_ts=0, max_rows=5) == 2
    assert [r["id"] for r in store.list_mcp_calls(limit=100)] == ids[-1:-6:-1]


def test_insert_mcp_call_requires_status_and_defaults_ts(store):
    before = time.time()
    cid = store.insert_mcp_call(status="auth_error", token_prefix_seen="doc4ai_pat_3kX9", http_status=401, ip="::1")
    row = store.list_mcp_calls(limit=1)[0]
    assert row["id"] == cid and row["ts"] >= before and row["token_id"] is None and row["method"] is None
    with pytest.raises(TypeError):
        store.insert_mcp_call(http_status=200)
