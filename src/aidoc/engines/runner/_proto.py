"""Shared by all runners. STDLIB ONLY. Never import aidoc here."""
from __future__ import annotations

import json
import sys
import traceback
from collections.abc import Callable


def _emit(prefix: str, obj: dict) -> None:
    sys.stderr.write(f"{prefix} {json.dumps(obj, ensure_ascii=False)}\n")
    sys.stderr.flush()


def ready() -> None:
    _emit("AIDOC_READY", {})


def progress(page: int, total: int) -> None:
    _emit("AIDOC_PROGRESS", {"page": page, "total": total})


def log(msg: str) -> None:
    sys.stderr.write(msg.rstrip("\n") + "\n")
    sys.stderr.flush()


class RunnerError(Exception):
    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


OOM_MARKERS = ("CUDA out of memory", "OutOfMemoryError", "out of memory")


def is_oom_text(text: str) -> bool:
    return any(m.lower() in text.lower() for m in OOM_MARKERS)


def _result_path(req_path: str) -> str:
    if req_path.endswith("request.json"):
        return req_path[: -len("request.json")] + "result.json"
    return req_path + ".result.json"


def serve(handle: Callable[[dict], dict]) -> None:
    """Loop: each stdin line = path of request.json. handle(req) returns the result dict."""
    ready()
    for line in sys.stdin:
        req_path = line.strip()
        if not req_path:
            continue
        try:
            with open(req_path, encoding="utf-8") as f:
                req = json.load(f)
            result = handle(req)
            res_path = _result_path(req_path)
            with open(res_path, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False)
            _emit("AIDOC_DONE", {"request": req_path, "result": res_path})
        except RunnerError as e:
            _emit("AIDOC_ERROR", {"request": req_path, "kind": e.kind, "message": str(e)})
        except Exception as e:  # noqa: BLE001
            log(traceback.format_exc())
            msg = f"{type(e).__name__}: {e}"
            # a CUDA OOM raised inside the engine leaves the runner alive: report it as transient so the host can
            # apply the OOM downgrade (index §0 retry policy); the message keeps the OOM marker the host looks for
            kind = "transient" if is_oom_text(msg) else "engine"
            _emit("AIDOC_ERROR", {"request": req_path, "kind": kind, "message": msg})
