"""Chunked, resumable uploads (spec §8.7): `data/uploads/<id>.part`, 8 MB chunks accepted only at
`offset == received`, sha256 verified on the last chunk, unfinished uploads purged after 24 h."""
from __future__ import annotations

import os
import shutil
import threading
import time
from pathlib import Path

from aidoc.names import file_sha256

CHUNK_SIZE = 8 * 1024 * 1024
STALE_AFTER_S = 24 * 3600


class UploadError(Exception):
    def __init__(self, http_status: int, error: str, **extra):     # `status` is a legal extra (upload status)
        super().__init__(error)
        self.status, self.error, self.extra = http_status, error, extra


class BadOffset(UploadError):
    def __init__(self, received: int):
        super().__init__(409, "bad_offset", received=received)
        self.received = received


class ChunkTooLarge(UploadError):
    def __init__(self):
        super().__init__(413, "chunk_too_large", chunk_size=CHUNK_SIZE)


class UploadTooLarge(UploadError):
    def __init__(self, limit: int):
        super().__init__(413, "upload_too_large", limit=limit)


class ShaMismatch(UploadError):
    def __init__(self):
        super().__init__(422, "sha_mismatch")


def display_name(filename: str) -> str:
    """Only the last path component of a client-supplied name (never a path)."""
    name = filename.replace("\\", "/").rsplit("/", 1)[-1].strip()
    return name or "upload"


class UploadManager:
    def __init__(self, ctx):
        self.ctx = ctx
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    @property
    def dir(self) -> Path:
        return self.ctx.config.data_dir / "uploads"

    def part_path(self, upload_id: str) -> Path:
        return self.dir / f"{upload_id}.part"

    def _lock(self, upload_id: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(upload_id, threading.Lock())

    def _publish(self, row: dict) -> None:
        self.ctx.bus.publish("upload.updated", row["id"],
                             {"id": row["id"], "received": row["received"], "status": row["status"]})

    # ------------------------------------------------------------------ API
    def create(self, filename: str, size: int, sha256: str) -> dict:
        limit = self.ctx.config.limits.upload_max_bytes
        if size > limit:
            raise UploadTooLarge(limit)
        self.dir.mkdir(parents=True, exist_ok=True)
        needed = size * self.ctx.config.limits.disk_space_factor
        free = shutil.disk_usage(self.dir).free
        if free < needed:
            raise UploadError(507, "insufficient_disk", needed=needed, free=free)
        uid = self.ctx.store.create_upload(display_name(filename), size, sha256.lower())
        self.part_path(uid).touch()
        row = self.ctx.store.get_upload(uid)
        self._publish(row)
        return row

    def received(self, upload_id: str) -> int:
        row = self.ctx.store.get_upload(upload_id)
        if row is None:
            raise UploadError(404, "not_found")
        return row["received"]

    def append(self, upload_id: str, offset: int, data: bytes) -> dict:
        store = self.ctx.store
        with self._lock(upload_id):
            row = store.get_upload(upload_id)
            if row is None:
                raise UploadError(404, "not_found")
            if row["status"] != "receiving":
                raise UploadError(409, "upload_not_receiving", status=row["status"], received=row["received"])
            if len(data) > CHUNK_SIZE:
                raise ChunkTooLarge()
            if offset != row["received"]:
                raise BadOffset(row["received"])
            if offset + len(data) > row["size"]:
                raise UploadError(400, "exceeds_declared_size", size=row["size"], received=row["received"])
            path = self.part_path(upload_id)
            with open(path, "r+b" if path.exists() else "w+b") as f:
                f.seek(offset)
                f.write(data)
                f.truncate()
                f.flush()
                os.fsync(f.fileno())
            received = offset + len(data)
            store.update_upload(upload_id, received=received)
            status = "receiving"
            if received == row["size"]:
                if file_sha256(path) == row["sha256"]:
                    status = "complete"
                    store.update_upload(upload_id, status=status)
                else:
                    path.unlink(missing_ok=True)
                    store.update_upload(upload_id, status="failed")
                    self._publish(store.get_upload(upload_id))
                    raise ShaMismatch()
            row = store.get_upload(upload_id)
        self._publish(row)
        return {"received": received, "status": status}

    def consume(self, upload_id: str, dest: Path) -> Path:
        """Move the finished upload to `dest` (rename on the same volume, else copy + delete)."""
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        src = self.part_path(upload_id)
        with self._lock(upload_id):
            try:
                os.replace(src, dest)
            except OSError:
                shutil.copy2(src, dest)
                src.unlink(missing_ok=True)
            self.ctx.store.update_upload(upload_id, status="consumed")
        self._publish(self.ctx.store.get_upload(upload_id))
        return dest

    def create_task_from_upload(self, job_id: str, upload_id: str, opts) -> str:
        """Task for a finished upload: source_path = the display name (never a path), the file is moved to
        data/work/<task_id>/src<ext> (the pipeline only verifies it there)."""
        from aidoc.output import planned_output_dir
        from aidoc.store import TaskBusyError
        store = self.ctx.store
        row = store.get_upload(upload_id)
        name = row["filename"]
        out_dir = planned_output_dir(store, opts.output_dir, Path(name), row["sha256"])
        try:
            tid, _ = store.create_task(job_id, name, row["sha256"], row["size"], time.time(), opts.lang, str(out_dir))
        except TaskBusyError as e:
            raise UploadError(409, "already_converting", task_id=e.task_id) from None
        dest = self.ctx.config.data_dir / "work" / tid / f"src{Path(name).suffix.lower()}"
        self.consume(upload_id, dest)
        store.update_task(tid, work_path=str(dest))
        return tid

    # ------------------------------------------------------------------ maintenance
    def reconcile(self) -> int:
        """At startup: DB `received` and the .part size may disagree after a crash; keep the smaller and truncate
        the file to it. Returns how many uploads were adjusted."""
        n = 0
        for row in self.ctx.store.stale_uploads(time.time() + 1, statuses=("receiving",)):
            path = self.part_path(row["id"])
            disk = path.stat().st_size if path.exists() else 0
            good = min(row["received"], disk)
            if good != row["received"] or good != disk:
                if path.exists():
                    with open(path, "r+b") as f:
                        f.truncate(good)
                self.ctx.store.update_upload(row["id"], received=good)
                n += 1
        return n

    def purge_stale(self, older_than_s: float = STALE_AFTER_S) -> int:
        """Unfinished (or finished but never used) uploads older than 24 h: file and row are removed."""
        n = 0
        for row in self.ctx.store.stale_uploads(time.time() - older_than_s,
                                                statuses=("receiving", "failed", "complete")):
            with self._lock(row["id"]):
                try:
                    self.part_path(row["id"]).unlink(missing_ok=True)
                except OSError:
                    continue
                self.ctx.store.delete_upload(row["id"])
            n += 1
        return n
