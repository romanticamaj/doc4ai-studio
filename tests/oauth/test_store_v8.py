"""Schema v8 (spec 2026-11-05 §5.5): oauth_clients columns via ALTER, oauth_codes / oauth_grants / oauth_tokens."""
import sqlite3
import time

from aidoc.store import SCHEMA_VERSION, Store

V8_CLIENT_COLS = {"kind", "client_uri", "logo_uri", "software_id", "software_version", "application_type", "metadata_json",
                  "fetched_at", "cache_until", "disabled_at"}
DAY = 86400


def _make_v7(path):
    """A real v7 file: Store builds the current schema, then the v8 additions are taken out again (the v7
    oauth_clients DDL with one row, no codes/grants/tokens tables) and meta says 7."""
    Store(path).close()
    con = sqlite3.connect(str(path))
    con.executescript(
        "DROP TABLE IF EXISTS oauth_tokens; DROP TABLE IF EXISTS oauth_grants; DROP TABLE IF EXISTS oauth_codes;"
        "DROP TABLE oauth_clients;"
        "CREATE TABLE oauth_clients(client_id TEXT PRIMARY KEY, metadata_url TEXT, name TEXT, redirect_uris_json TEXT, "
        "created_at REAL NOT NULL, last_seen REAL);"
        "INSERT INTO oauth_clients VALUES('old-client', NULL, 'Old', '[\"http://localhost/cb\"]', 1.0, NULL);"
        "UPDATE meta SET value='7' WHERE key='schema_version';")
    con.commit()
    con.close()


def test_v7_file_migrates_to_v8(tmp_path):
    db = tmp_path / "aidoc.db"
    _make_v7(db)
    s = Store(db)
    assert SCHEMA_VERSION == 8 and s.schema_version() == 8
    cols = {r[1] for r in s.con.execute("PRAGMA table_info(oauth_clients)")}
    assert V8_CLIENT_COLS <= cols
    tables = {r[0] for r in s.con.execute("SELECT name FROM sqlite_master WHERE type IN ('table','index')")}
    assert {"oauth_codes", "oauth_grants", "oauth_tokens", "oauth_tokens_grant"} <= tables
    assert "redirect_uri" in {r[1] for r in s.con.execute("PRAGMA table_info(oauth_grants)")}
    old = s.get_oauth_client("old-client")
    assert old["name"] == "Old" and old["redirect_uris"] == ["http://localhost/cb"] and old["kind"] is None
    s.close()
    s = Store(db)                                           # idempotent
    assert s.schema_version() == 8 and s.get_oauth_client("old-client")["name"] == "Old"
    s.close()


def test_store_surface_round_trip(tmp_path):
    s = Store(tmp_path / "aidoc.db")
    now = time.time()
    s.upsert_oauth_client(client_id="https://claude.ai/x", kind="cimd", name="Claude Code", redirect_uris=["http://localhost/callback"],
                          metadata_url="https://claude.ai/x", client_uri="https://claude.ai", metadata_json={"a": 1},
                          fetched_at=now, cache_until=now + 60)
    c = s.get_oauth_client("https://claude.ai/x")
    assert c["kind"] == "cimd" and c["redirect_uris"] == ["http://localhost/callback"] and c["metadata"] == {"a": 1}
    assert c["disabled_at"] is None and c["cache_until"] == now + 60
    s.upsert_oauth_client(client_id="https://claude.ai/x", kind="cimd", name="Claude Code 2", redirect_uris=["http://127.0.0.1/cb"])
    c2 = s.get_oauth_client("https://claude.ai/x")
    assert c2["name"] == "Claude Code 2" and c2["created_at"] == c["created_at"] and len(s.list_oauth_clients()) == 1
    s.touch_oauth_client("https://claude.ai/x", now + 5)
    assert s.get_oauth_client("https://claude.ai/x")["last_seen"] == now + 5

    s.create_oauth_code(code_hash="h1", client_id=c["client_id"], redirect_uri="http://localhost:1/callback", code_challenge="cc",
                        scopes=["doc4ai:read"], resource="http://127.0.0.1:8765/mcp", subject="owner", expires_at=now + 300,
                        created_at=now)
    first = s.take_oauth_code("h1", now + 1)
    assert first["scopes"] == ["doc4ai:read"] and first["consumed_at"] is None and first["expires_at"] - first["created_at"] == 300
    second = s.take_oauth_code("h1", now + 2)
    assert second["consumed_at"] == now + 1                 # the caller sees the reuse
    assert s.take_oauth_code("nope", now) is None

    g = s.create_oauth_grant(client_id=c["client_id"], subject="owner", scopes=["doc4ai:read"], resource="http://127.0.0.1:8765/mcp",
                             created_ip="127.0.0.1", absolute_expires_at=now + 90 * DAY)
    s.bind_oauth_code("h1", g)
    assert s._q1("SELECT grant_id FROM oauth_codes WHERE code_hash='h1'")[0] == g
    at = s.create_oauth_token(grant_id=g, kind="at", token_hash="at1", prefix="doc4ai_at_abcd", expires_at=now + 3600)
    rt = s.create_oauth_token(grant_id=g, kind="rt", token_hash="rt1", prefix="doc4ai_rt_abcd", expires_at=now + 30 * DAY)
    row = s.get_oauth_token_by_hash("at1")
    assert row["id"] == at and row["grant_id"] == g and row["scopes"] == ["doc4ai:read"] and row["grant_scopes"] == ["doc4ai:read"]
    assert row["grant_revoked_at"] is None and row["client_id"] == c["client_id"] and row["resource"].endswith("/mcp")
    assert row["subject"] == "owner" and row["absolute_expires_at"] == now + 90 * DAY and row["kind"] == "at"
    assert [t["id"] for t in s.active_oauth_tokens(g, "at", now)] == [at]
    s.touch_oauth_token(at, now + 3)
    assert s.get_oauth_token_by_hash("at1")["last_used_at"] == now + 3
    rt2 = s.rotate_oauth_refresh(rt, new_hash="rt2", new_prefix="doc4ai_rt_efgh", expires_at=now + 30 * DAY, at=now + 10)
    assert s.get_oauth_token_by_hash("rt1")["rotated_at"] == now + 10 and s.get_oauth_token_by_hash("rt2")["parent_id"] == rt
    assert s.rotate_oauth_refresh(rt, new_hash="rt3", new_prefix="doc4ai_rt_x", expires_at=now, at=now) is None   # already rotated
    assert [t["id"] for t in s.active_oauth_tokens(g, "rt", now)] == [rt2]
    s.touch_oauth_grant(g, now=now + 4, ip="127.0.0.1", client_label="claude-code/2.1")
    gr = s.get_oauth_grant(g)
    assert gr["last_used_at"] == now + 4 and gr["last_client"] == "claude-code/2.1" and gr["scopes"] == ["doc4ai:read"]
    names = s.token_names()
    pat = s.create_api_token(name="my pat", prefix="doc4ai_pat_abcd", token_hash="p", scopes=["doc4ai:read"], expires_at=None)
    names = s.token_names()
    assert names[g] == ("Claude Code 2", "oauth") and names[pat] == ("my pat", "pat")

    assert s.revoke_oauth_grant(g, "admin", at=now + 20) == 3                 # at, rt (rotated), rt2
    assert s.revoke_oauth_grant(g, "admin", at=now + 21) == 0
    gr = s.get_oauth_grant(g)
    assert gr["revoked_at"] == now + 20 and gr["revoked_reason"] == "admin"
    assert s.get_oauth_token_by_hash("at1")["grant_revoked_at"] == now + 20
    assert all(t["revoked_at"] == now + 20 for t in s._qa("SELECT * FROM oauth_tokens"))
    assert len(s.list_oauth_grants(include_revoked=True)) == 1 and s.list_oauth_grants(include_revoked=False) == []


def test_prune_oauth(tmp_path):
    s = Store(tmp_path / "aidoc.db")
    now = time.time()
    s.upsert_oauth_client(client_id="https://claude.ai/x", kind="cimd", name="C", redirect_uris=["http://localhost/cb"])
    s.upsert_oauth_client(client_id="doc4ai_cl_unused", kind="dcr", name="U", redirect_uris=["http://localhost/cb"])
    s.upsert_oauth_client(client_id="doc4ai_cl_used", kind="dcr", name="V", redirect_uris=["http://localhost/cb"])
    s.upsert_oauth_client(client_id="doc4ai_cl_fresh", kind="dcr", name="F", redirect_uris=["http://localhost/cb"])
    s.con.execute("UPDATE oauth_clients SET created_at=? WHERE client_id != 'doc4ai_cl_fresh'", (now - 31 * DAY,))

    def code(h, consumed=None, expires=now + 300):
        s.create_oauth_code(code_hash=h, client_id="doc4ai_cl_used", redirect_uri="x", code_challenge="c", scopes=["doc4ai:read"],
                            resource="r", subject="owner", expires_at=expires)
        if consumed is not None:
            s.con.execute("UPDATE oauth_codes SET consumed_at=? WHERE code_hash=?", (consumed, h))
    code("live")
    code("used-recent", consumed=now - 600, expires=now - 300)        # kept for reuse detection (< 1 h)
    code("used-old", consumed=now - 3700, expires=now - 3400)
    code("expired", expires=now - 1)

    live = s.create_oauth_grant(client_id="doc4ai_cl_used", subject="owner", scopes=["doc4ai:read"], resource="r", created_ip=None,
                                absolute_expires_at=now + 90 * DAY)
    old_rev = s.create_oauth_grant(client_id="doc4ai_cl_used", subject="owner", scopes=["doc4ai:read"], resource="r", created_ip=None,
                                   absolute_expires_at=now + 90 * DAY)
    s.create_oauth_token(grant_id=old_rev, kind="at", token_hash="r-at", prefix="p", expires_at=now + 3600)
    s.revoke_oauth_grant(old_rev, "admin", at=now - 31 * DAY)
    s.create_oauth_token(grant_id=live, kind="at", token_hash="ok", prefix="p", expires_at=now + 3600)
    s.create_oauth_token(grant_id=live, kind="at", token_hash="old-exp", prefix="p", expires_at=now - 91 * DAY)
    s.create_oauth_token(grant_id=live, kind="at", token_hash="recent-exp", prefix="p", expires_at=now - DAY)
    res = s.prune_oauth(now=now)
    assert res == {"codes": 2, "tokens": 2, "grants": 1, "clients": 1}
    left = {r[0] for r in s.con.execute("SELECT code_hash FROM oauth_codes")}
    assert left == {"live", "used-recent"}
    assert {r[0] for r in s.con.execute("SELECT token_hash FROM oauth_tokens")} == {"ok", "recent-exp"}
    assert s.get_oauth_grant(old_rev) is None and s.get_oauth_grant(live) is not None
    ids = {c["client_id"] for c in s.list_oauth_clients()}
    assert ids == {"https://claude.ai/x", "doc4ai_cl_used", "doc4ai_cl_fresh"}       # CIMD never pruned


def test_maintenance_runs_prune_oauth(ctx):
    from aidoc.server.maintenance import Maintenance
    now = time.time()
    ctx.store.create_oauth_code(code_hash="gone", client_id="c", redirect_uri="x", code_challenge="c", scopes=["doc4ai:read"],
                                resource="r", subject="owner", expires_at=now - 1)
    res = Maintenance(ctx).run_once()
    assert res["oauth_pruned"] == {"codes": 1, "tokens": 0, "grants": 0, "clients": 0}
