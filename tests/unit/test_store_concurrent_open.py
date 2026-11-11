"""Two processes opening the same database at once (the server and a CLI run starting together) both run the
schema upgrade. Each step has to tolerate the other process finishing it first."""
import sqlite3
import subprocess
import sys

from doc4ai.store import Store


def test_a_column_added_by_another_process_meanwhile_is_not_an_error(tmp_path, monkeypatch):
    s = Store(tmp_path / "aidoc.db")
    real = s.con.execute

    def stale(sql, args=()):                       # our check ran before the other process's ALTER
        if sql.startswith("PRAGMA table_info"):
            return real("SELECT 'x', 'none' WHERE 0")
        return real(sql, args)

    monkeypatch.setattr(s.con, "execute", stale)
    s._add_column("documents", "source_copy_path", "TEXT")
    monkeypatch.undo()
    s.close()


def test_other_alter_errors_still_raise(tmp_path):
    s = Store(tmp_path / "aidoc.db")
    try:
        try:
            s._add_column("no_such_table", "x", "TEXT")
        except sqlite3.OperationalError:
            pass
        else:
            raise AssertionError("expected OperationalError")
    finally:
        s.close()


def test_page_index_created_by_another_process_meanwhile_is_used(tmp_path, monkeypatch):
    s = Store(tmp_path / "aidoc.db")
    tok = s.page_index_tokenizer()
    calls = iter([None])                           # the first look misses the table the other process just made
    real = s._existing_page_index
    monkeypatch.setattr(s, "_existing_page_index", lambda: next(calls, real()))
    assert s._ensure_page_index() == tok
    s.close()


def test_many_processes_open_a_fresh_database_together(tmp_path):
    db = tmp_path / "aidoc.db"
    code = ("import sys; from doc4ai.store import Store; s = Store(sys.argv[1]); "
            "print(s.schema_version(), s.page_index_tokenizer()); s.close()")
    procs = [subprocess.Popen([sys.executable, "-c", code, str(db)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True) for _ in range(8)]
    results = [p.communicate(timeout=120) for p in procs]
    assert [p.returncode for p in procs] == [0] * 8, [err for _, err in results if err]
    assert len({out for out, _ in results}) == 1
