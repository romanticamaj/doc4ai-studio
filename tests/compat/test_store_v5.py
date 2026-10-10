import time

from aidoc.models import ConvertOptions


def _opts(tmp_root):
    return ConvertOptions(output_dir=tmp_root / "out")


def test_schema_v5_and_compat_task_roundtrip(tmp_root):
    from aidoc.store import Store
    s = Store(tmp_root / "data" / "aidoc.db")
    assert s._q1("SELECT value FROM meta WHERE key='schema_version'")["value"] == "5"
    job = s.create_job(_opts(tmp_root), "compat")
    s.create_compat_task(job, token_id="tok1", options={"to_formats": ["md"], "placeholder": "\f"}, protocol="docling-serve-v1")
    row = s.get_compat_task(job)
    assert row["token_id"] == "tok1" and row["options"] == {"to_formats": ["md"], "placeholder": "\f"}
    assert row["protocol"] == "docling-serve-v1" and s.get_compat_task("nope") is None
    assert s.get_job(job)["origin"] == "compat"


def test_v4_database_upgrades_in_place(tmp_root):
    """A v4 database (no compat_tasks) opens as v5 with its data intact (idempotent migration)."""
    import sqlite3

    from aidoc.store import Store
    db = tmp_root / "data" / "aidoc.db"
    s = Store(db)
    job = s.create_job(_opts(tmp_root), "web")
    s.close()
    con = sqlite3.connect(db)
    con.execute("DROP TABLE compat_tasks")
    con.execute("UPDATE meta SET value='4' WHERE key='schema_version'")
    con.commit()
    con.close()
    for _ in range(2):
        s = Store(db)
        assert s._q1("SELECT value FROM meta WHERE key='schema_version'")["value"] == "5"
        assert s.get_job(job) is not None and s.get_compat_task(job) is None
        s.close()


def test_prune_compat_tasks(tmp_root):
    from aidoc.store import Store
    s = Store(tmp_root / "data" / "aidoc.db")
    old = s.create_job(_opts(tmp_root), "compat")
    new = s.create_job(_opts(tmp_root), "compat")
    s.create_compat_task(old, token_id="t", options={}, protocol="docling-serve-v1")
    s.create_compat_task(new, token_id="t", options={}, protocol="docling-serve-v1")
    s._update("compat_tasks", "job_id", old, {"created_at": time.time() - 40 * 86400})
    assert s.prune_compat_tasks(older_than_ts=time.time() - 30 * 86400) == 1
    assert s.get_compat_task(old) is None and s.get_compat_task(new) is not None


def test_maintenance_prunes_compat_tasks(ctx):
    from aidoc.server.maintenance import Maintenance
    job = ctx.store.create_job(_opts(ctx.config.root), "compat")
    ctx.store.create_compat_task(job, token_id="t", options={}, protocol="docling-serve-v1")
    ctx.store._update("compat_tasks", "job_id", job, {"created_at": time.time() - 31 * 86400})
    res = Maintenance(ctx).run_once()
    assert res["compat_tasks_pruned"] == 1
