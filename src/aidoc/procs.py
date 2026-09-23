"""Process control: process-tree kill, runner identification (spec §8.2, §8.4)."""
from __future__ import annotations
import subprocess
import sys

import psutil


def popen_kwargs() -> dict:
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def kill_tree(pid: int) -> None:
    """Kill children (recursively) first, then the parent. Missing processes are ignored."""
    try:
        parent = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return
    try:
        children = parent.children(recursive=True)
    except psutil.NoSuchProcess:
        children = []
    for c in reversed(children):
        try:
            c.kill()
        except psutil.NoSuchProcess:
            pass
    try:
        parent.kill()
    except psutil.NoSuchProcess:
        pass
    psutil.wait_procs(children + [parent], timeout=10)


def is_aidoc_runner(pid: int) -> bool:
    try:
        cmd = psutil.Process(pid).cmdline()
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
        return False
    for arg in cmd:
        a = arg.replace("\\", "/")
        if a.endswith("_runner.py") and "/engines/runner/" in a:
            return True
    return False
