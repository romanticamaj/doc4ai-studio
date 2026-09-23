"""SQLite store (index §4). One connection per Store; not thread-safe by itself."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from enum import Enum
from pathlib import Path

from aidoc.models import TERMINAL_TASK, Attempt, ConvertOptions, JobStatus, TaskStatus

SCHEMA_VERSION = 1

_DDL = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS jobs(
  id TEXT PRIMARY KEY, created_at REAL NOT NULL, options_json TEXT NOT NULL,
  status TEXT NOT NULL, origin TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tasks(
  id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id),
  source_path TEXT NOT NULL, work_path TEXT, sha256 TEXT NOT NULL, size INTEGER NOT NULL, mtime REAL NOT NULL,
  lang TEXT NOT NULL, engine TEXT, tried_json TEXT NOT NULL DEFAULT '[]', attempt INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL, error_kind TEXT, error_msg TEXT, quality_json TEXT, output_dir TEXT NOT NULL,
  pid INTEGER, created_at REAL NOT NULL, updated_at REAL NOT NULL,
  UNIQUE(sha256, output_dir));
CREATE TABLE IF NOT EXISTS segments(
  id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id), idx INTEGER NOT NULL,
  page_start INTEGER, page_end INTEGER, status TEXT NOT NULL, attempt INTEGER NOT NULL DEFAULT 0,
  output_path TEXT, UNIQUE(task_id, idx));
CREATE TABLE IF NOT EXISTS documents(
  id TEXT PRIMARY KEY, sha256 TEXT NOT NULL, source_path TEXT NOT NULL, output_dir TEXT NOT NULL UNIQUE,
  engine TEXT NOT NULL, quality_json TEXT NOT NULL, pages INTEGER, lang TEXT NOT NULL, aidoc_version TEXT NOT NULL,
  created_at REAL NOT NULL, status TEXT NOT NULL, work_copy_path TEXT, work_copy_expires_at REAL);
CREATE INDEX IF NOT EXISTS documents_sha ON documents(sha256);
CREATE TABLE IF NOT EXISTS uploads(
  id TEXT PRIMARY KEY, filename TEXT NOT NULL, size INTEGER NOT NULL, sha256 TEXT NOT NULL,
  received INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL, status TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events(
  seq INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, kind TEXT NOT NULL, ref_id TEXT, payload_json TEXT NOT NULL);
"""

_ACTIVE = {TaskStatus.probing.value, TaskStatus.converting.value, TaskStatus.checking.value}


class TaskBusyError(Exception):
    """The (sha256, output_dir) task is being converted right now by another process."""

    def __init__(self, task_id: str):
        super().__init__(f"task {task_id} is being converted by another process")
        self.task_id = task_id


_JSON_COLS = {"options_json", "tried_json", "quality_json", "payload_json", "flags_json"}
_TERMINAL = {s.value for s in TERMINAL_TASK}
# index A14 (P2): rows with these statuses are re-parented and resumed; done/low/skipped rows are replaced
_REUSABLE = {TaskStatus.queued.value, TaskStatus.probing.value, TaskStatus.converting.value,
             TaskStatus.checking.value, TaskStatus.failed.value, TaskStatus.cancelled.value}


def _new_id() -> str:
    return uuid.uuid4().hex


def _row(r: sqlite3.Row | None) -> dict | None:
    if r is None:
        return None
    d = {}
    for k in r.keys():  # noqa: SIM118  sqlite3.Row iterates values, not keys
        v = r[k]
        if k in _JSON_COLS:
            d[k[: -len("_json")]] = json.loads(v) if v is not None else None
        else:
            d[k] = v
    return d


def _val(v):
    if isinstance(v, Enum):
        return v.value
    if isinstance(v, Path):
        return str(v)
    return v


def _fields_to_cols(fields: dict) -> dict:
    """Accept both `quality=` (encoded to quality_json) and raw column names."""
    out = {}
    for k, v in fields.items():
        if k + "_json" in _JSON_COLS:
            out[k + "_json"] = None if v is None else json.dumps(v, ensure_ascii=False)
        elif k in _JSON_COLS and not isinstance(v, (str, type(None))):
            out[k] = json.dumps(v, ensure_ascii=False)
        else:
            out[k] = _val(v)
    return out


class _Result:
    """A fully fetched cursor result (safe to use after the lock is released)."""

    def __init__(self, cur: sqlite3.Cursor):
        self.rows = cur.fetchall() if cur.description is not None else []
        self.rowcount = cur.rowcount
        self.lastrowid = cur.lastrowid

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)

    def __iter__(self):
        return iter(self.rows)


class _LockedConnection:
    def __init__(self, con: sqlite3.Connection):
        self._con = con
        self.lock = threading.RLock()

    def execute(self, sql, args=()):
        with self.lock:
            return _Result(self._con.execute(sql, args))

    def executescript(self, sql):
        with self.lock:
            return self._con.executescript(sql)

    def close(self):
        with self.lock:
            self._con.close()


class Store:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # Watcher threads (cancel / progress / tests) share this Store: the connection allows other threads and
        # every statement runs, and is fully fetched, under one RLock (concurrent use of a sqlite3 connection
        # raises "bad parameter or other API misuse").
        raw = sqlite3.connect(str(self.db_path), isolation_level=None, check_same_thread=False)
        raw.row_factory = sqlite3.Row
        self.con = _LockedConnection(raw)
        self.con.execute("PRAGMA journal_mode=WAL")
        self.con.execute("PRAGMA busy_timeout=5000")
        self.con.execute("PRAGMA foreign_keys=ON")
        self.con.executescript(_DDL)
        if self._q1("SELECT value FROM meta WHERE key='schema_version'") is None:
            self.con.execute("INSERT INTO meta(key, value) VALUES('schema_version', ?)", (str(SCHEMA_VERSION),))

    def close(self) -> None:
        self.con.close()

    # ---- helpers
    def _q1(self, sql, args=()):
        return self.con.execute(sql, args).fetchone()

    def _qa(self, sql, args=()) -> list[dict]:
        return [_row(r) for r in self.con.execute(sql, args).fetchall()]

    def _update(self, table: str, key: str, key_val, fields: dict) -> None:
        cols = _fields_to_cols(fields)
        if not cols:
            return
        sets = ", ".join(f"{c}=?" for c in cols)
        self.con.execute(f"UPDATE {table} SET {sets} WHERE {key}=?", (*cols.values(), key_val))

    # ---- jobs
    def create_job(self, options: ConvertOptions, origin: str) -> str:
        jid = _new_id()
        self.con.execute("INSERT INTO jobs(id, created_at, options_json, status, origin) VALUES(?,?,?,?,?)",
                         (jid, time.time(), json.dumps(options.to_json(), ensure_ascii=False),
                          JobStatus.queued.value, origin))
        return jid

    def get_job(self, job_id) -> dict | None:
        return _row(self._q1("SELECT * FROM jobs WHERE id=?", (job_id,)))

    def list_jobs(self, limit=100) -> list[dict]:
        return self._qa("SELECT * FROM jobs ORDER BY created_at DESC, rowid DESC LIMIT ?", (limit,))

    def set_job_status(self, job_id, status: JobStatus) -> None:
        self.con.execute("UPDATE jobs SET status=? WHERE id=?", (_val(status), job_id))

    def refresh_job_status(self, job_id) -> JobStatus:
        job = self.get_job(job_id)
        statuses = [r[0] for r in self.con.execute("SELECT status FROM tasks WHERE job_id=?", (job_id,))]
        if not statuses:
            st = JobStatus.queued
        elif all(s in _TERMINAL for s in statuses):
            st = JobStatus.cancelled if job and job["status"] == JobStatus.cancelled.value else JobStatus.done
        elif any(s != TaskStatus.queued.value for s in statuses):
            st = JobStatus.running
        else:
            st = JobStatus.queued
        self.set_job_status(job_id, st)
        return st

    # ---- tasks
    def create_task(self, job_id, source_path, sha256, size, mtime, lang, output_dir) -> tuple[str, bool]:
        source_path, output_dir = str(source_path), str(output_dir)
        now = time.time()
        old = self._q1("SELECT id, status FROM tasks WHERE sha256=? AND output_dir=?", (sha256, output_dir))
        if old is not None:
            if old["status"] in _ACTIVE and self.task_is_live(old["id"]):
                raise TaskBusyError(old["id"])
            if old["status"] in _REUSABLE:            # A14: unfinished work resumes (segments and history kept)
                self.con.execute("UPDATE tasks SET job_id=?, source_path=?, size=?, mtime=?, lang=?, status=?, "
                                 "pid=NULL, error_kind=NULL, error_msg=NULL, updated_at=? WHERE id=?",
                                 (job_id, source_path, size, mtime, lang, TaskStatus.queued.value, now, old["id"]))
                return old["id"], True
            self.con.execute("DELETE FROM segments WHERE task_id=?", (old["id"],))
            self.con.execute("DELETE FROM tasks WHERE id=?", (old["id"],))
        tid = _new_id()
        self.con.execute(
            "INSERT INTO tasks(id, job_id, source_path, sha256, size, mtime, lang, status, output_dir, created_at, "
            "updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (tid, job_id, source_path, sha256, size, mtime, lang, TaskStatus.queued.value, output_dir, now, now))
        return tid, False

    def requeue_task(self, task_id, *, reset_segments: bool, new_sha: str | None = None) -> None:
        """Back to `queued` (errors and pid cleared). `new_sha` = "convert the new version": sha256/size/mtime
        are re-read from source_path by the caller and stored here."""
        fields = {"status": TaskStatus.queued, "error_kind": None, "error_msg": None, "pid": None}
        if new_sha is not None:
            fields["sha256"] = new_sha
            t = self.get_task(task_id)
            try:
                st = Path(t["source_path"]).stat()
                fields.update(size=st.st_size, mtime=st.st_mtime)
            except OSError:
                pass
        self.update_task(task_id, **fields)
        if reset_segments:
            self.reset_segments(task_id)

    def task_is_live(self, task_id) -> bool:
        """True while some process holds the task's liveness lock (see aidoc.tasklock)."""
        from aidoc.tasklock import is_locked, lock_path
        return is_locked(lock_path(self.db_path.parent, task_id))

    def get_task(self, task_id) -> dict | None:
        return _row(self._q1("SELECT * FROM tasks WHERE id=?", (task_id,)))

    def list_tasks(self, job_id=None, status=None, status_in: list[str] | None = None) -> list[dict]:
        sql, args = "SELECT * FROM tasks WHERE 1=1", []
        if status_in is not None:
            vals = [_val(v) for v in status_in] or [""]
            sql += f" AND status IN ({', '.join('?' * len(vals))})"
            args += vals
        if job_id is not None:
            sql += " AND job_id=?"
            args.append(job_id)
        if status is not None:
            sql += " AND status=?"
            args.append(_val(status))
        return self._qa(sql + " ORDER BY created_at, rowid", args)

    def tasks_with_pid(self) -> list[dict]:
        return self._qa("SELECT * FROM tasks WHERE pid IS NOT NULL ORDER BY created_at, rowid")

    def update_task(self, task_id, **fields) -> None:
        fields["updated_at"] = time.time()
        self._update("tasks", "id", task_id, fields)

    def append_attempt(self, task_id, attempt: Attempt) -> None:
        t = self.get_task(task_id)
        tried = (t["tried"] if t else []) + [attempt.to_json()]
        self.update_task(task_id, tried=tried)

    # ---- segments
    def create_segments(self, task_id, ranges: list[tuple[int | None, int | None]]) -> list[str]:
        ids = []
        for idx, (a, b) in enumerate(ranges):
            sid = _new_id()
            self.con.execute("INSERT INTO segments(id, task_id, idx, page_start, page_end, status) VALUES(?,?,?,?,?,?)",
                             (sid, task_id, idx, a, b, "queued"))
            ids.append(sid)
        return ids

    def list_segments(self, task_id) -> list[dict]:
        return self._qa("SELECT * FROM segments WHERE task_id=? ORDER BY idx", (task_id,))

    def update_segment(self, segment_id, **fields) -> None:
        self._update("segments", "id", segment_id, fields)

    def delete_segments(self, task_id) -> None:
        self.con.execute("DELETE FROM segments WHERE task_id=?", (task_id,))

    def get_segment(self, segment_id) -> dict | None:
        return _row(self._q1("SELECT * FROM segments WHERE id=?", (segment_id,)))

    def reset_segments(self, task_id) -> None:
        self.con.execute("UPDATE segments SET status='queued', attempt=0, output_path=NULL WHERE task_id=?", (task_id,))

    # ---- documents
    def upsert_document(self, **fields) -> str:
        fields = dict(fields)
        fields["output_dir"] = str(fields["output_dir"])
        existing = self._q1("SELECT id FROM documents WHERE output_dir=?", (fields["output_dir"],))
        if existing is not None:
            fields.pop("id", None)
            self._update("documents", "id", existing["id"], fields)
            return existing["id"]
        did = fields.pop("id", None) or _new_id()
        fields.setdefault("created_at", time.time())
        cols = _fields_to_cols(fields)
        cols["id"] = did
        self.con.execute(f"INSERT INTO documents({', '.join(cols)}) VALUES({', '.join('?' * len(cols))})",
                         tuple(cols.values()))
        return did

    def find_document(self, sha256, output_dir) -> dict | None:
        return _row(self._q1("SELECT * FROM documents WHERE sha256=? AND output_dir=?", (sha256, str(output_dir))))

    def get_document(self, doc_id) -> dict | None:
        return _row(self._q1("SELECT * FROM documents WHERE id=?", (doc_id,)))

    def get_document_by_output(self, output_dir) -> dict | None:
        return _row(self._q1("SELECT * FROM documents WHERE output_dir=?", (str(output_dir),)))

    def list_documents(self, status=None, engine=None, q=None) -> list[dict]:
        sql, args = "SELECT * FROM documents WHERE 1=1", []
        if status is not None:
            sql += " AND status=?"
            args.append(_val(status))
        if engine is not None:
            sql += " AND engine=?"
            args.append(engine)
        if q:
            sql += " AND (source_path LIKE ? OR output_dir LIKE ?)"
            args += [f"%{q}%", f"%{q}%"]
        return self._qa(sql + " ORDER BY created_at DESC", args)

    def update_document(self, doc_id, **fields) -> None:
        self._update("documents", "id", doc_id, fields)

    def set_document_status(self, doc_id, status) -> None:
        self.con.execute("UPDATE documents SET status=? WHERE id=?", (_val(status), doc_id))

    def delete_documents(self, status) -> int:
        return self.con.execute("DELETE FROM documents WHERE status=?", (_val(status),)).rowcount

    def known_output_dirs(self) -> set[str]:
        return {r[0] for r in self.con.execute("SELECT output_dir FROM documents")}

    # ---- uploads
    def create_upload(self, filename, size, sha256) -> str:
        uid = _new_id()
        self.con.execute("INSERT INTO uploads(id, filename, size, sha256, received, created_at, status) "
                         "VALUES(?,?,?,?,0,?,'receiving')", (uid, filename, size, sha256, time.time()))
        return uid

    def get_upload(self, upload_id) -> dict | None:
        return _row(self._q1("SELECT * FROM uploads WHERE id=?", (upload_id,)))

    def update_upload(self, upload_id, **fields) -> None:
        self._update("uploads", "id", upload_id, fields)

    def stale_uploads(self, older_than_ts) -> list[dict]:
        return self._qa("SELECT * FROM uploads WHERE created_at < ? AND status IN ('receiving', 'failed') "
                        "ORDER BY created_at", (older_than_ts,))

    # ---- events
    def append_event(self, kind, ref_id, payload: dict) -> int:
        cur = self.con.execute("INSERT INTO events(ts, kind, ref_id, payload_json) VALUES(?,?,?,?)",
                               (time.time(), kind, ref_id, json.dumps(payload, ensure_ascii=False)))
        return cur.lastrowid

    def events_since(self, seq) -> list[dict]:
        return self._qa("SELECT * FROM events WHERE seq > ? ORDER BY seq", (seq,))

    def oldest_event_seq(self) -> int | None:
        r = self._q1("SELECT MIN(seq) FROM events")
        return r[0] if r else None

    def prune_events(self, keep=10000) -> None:
        r = self._q1("SELECT MAX(seq) FROM events")
        if r and r[0] is not None:
            self.con.execute("DELETE FROM events WHERE seq <= ?", (r[0] - keep,))
