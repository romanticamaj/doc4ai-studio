from fastapi.testclient import TestClient

from aidoc.server.app import create_app


def test_envelope_and_version(client):
    r = client.get("/api/system")
    assert r.status_code == 200
    assert r.json()["workspace"] == "default" and r.json()["version"] == "0.1.0"


def test_404_has_envelope(client):
    r = client.get("/api/nope")
    assert r.status_code == 404 and r.json()["workspace"] == "default"


def test_token_required_when_configured(token_ctx):
    with TestClient(create_app(token_ctx)) as c:
        r = c.get("/api/system")
        assert r.status_code == 401 and r.json() == {"error": "unauthorized", "workspace": "default"}
        assert c.get("/api/system", headers={"Authorization": "Bearer s3cret"}).status_code == 200
        assert c.get("/api/system?token=s3cret").status_code == 200
        assert c.get("/api/system?token=wrong").status_code == 401


def test_store_threadsafe_and_flags(tmp_root):
    import threading
    from pathlib import Path

    from aidoc.models import ConvertOptions
    from aidoc.store import Store
    s = Store(tmp_root / "data" / "aidoc.db", threadsafe=True)
    job = s.create_job(ConvertOptions(output_dir=Path("o")), "web")

    def work(i):
        s.create_task(job, f"{i}.pdf", f"{i:064x}", 1, 1.0, "cht", f"o/{i}")
    ts = [threading.Thread(target=work, args=(i,)) for i in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(s.list_tasks(job)) == 8
    tid = s.list_tasks(job)[0]["id"]
    s.set_task_flags(tid, {"force": True})
    assert s.get_task(tid)["flags"] == {"force": True} and s.next_queued_task()["id"] == s.list_tasks(job)[0]["id"]
    assert s.con.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == "4"
    s.close()


def test_schema_v1_db_is_migrated(tmp_root):
    import sqlite3

    from aidoc.store import Store
    db = tmp_root / "data" / "old.db"
    con = sqlite3.connect(db)
    con.executescript("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);"
                      "INSERT INTO meta VALUES('schema_version','1');"
                      "CREATE TABLE tasks(id TEXT PRIMARY KEY, job_id TEXT NOT NULL, source_path TEXT NOT NULL,"
                      " work_path TEXT, sha256 TEXT NOT NULL, size INTEGER NOT NULL, mtime REAL NOT NULL,"
                      " lang TEXT NOT NULL, engine TEXT, tried_json TEXT NOT NULL DEFAULT '[]',"
                      " attempt INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL, error_kind TEXT, error_msg TEXT,"
                      " quality_json TEXT, output_dir TEXT NOT NULL, pid INTEGER, created_at REAL NOT NULL,"
                      " updated_at REAL NOT NULL, UNIQUE(sha256, output_dir));"
                      "INSERT INTO tasks(id, job_id, source_path, sha256, size, mtime, lang, status, output_dir,"
                      " created_at, updated_at) VALUES('t','j','a.pdf','x',1,1,'cht','done','o',1,1);")
    con.commit()
    con.close()
    s = Store(db)
    assert s.get_task("t")["flags"] == {}
    s.close()
