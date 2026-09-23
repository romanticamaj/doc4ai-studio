from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from aidoc.store import Store


@pytest.fixture
def batch_proc():
    """Launch `python -m aidoc.cli batch <input_dir> -o <out>` as a real process against an isolated root.

    stdout/stderr go to <root>/batch<n>.out / .err (n = launch order, starting at 0)."""
    started: list[subprocess.Popen] = []

    def start(root: Path, input_dir: Path, out: Path, scenario: Path, *extra: str) -> subprocess.Popen:
        env = {**os.environ, "AIDOC_ROOT": str(root), "AIDOC_DATA": str(root / "data"), "AIDOC_FAKE_ENGINES": "1",
               "AIDOC_FAKE_SCENARIO": str(scenario), "PYTHONUTF8": "1"}
        env.pop("AIDOC_CONFIG", None)
        n = len(started)
        with open(root / f"batch{n}.out", "w") as so, open(root / f"batch{n}.err", "w") as se:
            p = subprocess.Popen([sys.executable, "-m", "aidoc.cli", "batch", str(input_dir), "-o", str(out), *extra],
                                 env=env, stdout=so, stderr=se)
        started.append(p)
        return p
    yield start
    from aidoc import procs
    for p in started:
        if p.poll() is None:
            procs.kill_tree(p.pid)


@pytest.fixture
def wait_for():
    def wait(pred, timeout: float, interval: float = 0.1):
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if pred():
                    return True
            except Exception:  # noqa: BLE001, S110  the DB may be mid-write in the other process
                pass
            time.sleep(interval)
        raise AssertionError(f"condition not met within {timeout}s")
    return wait


@pytest.fixture
def db():
    stores: list[Store] = []

    def open_db(root: Path) -> Store:
        s = Store(root / "data" / "aidoc.db")
        stores.append(s)
        return s
    yield open_db
    for s in stores:
        s.close()
