from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from doc4ai.store import Store


@pytest.fixture
def batch_proc():
    """Launch `python -m doc4ai.cli batch <input_dir> -o <out>` as a real process against an isolated root.

    stdout/stderr go to <root>/batch<n>.out / .err (n = launch order, starting at 0)."""
    started: list[subprocess.Popen] = []

    def start(root: Path, input_dir: Path, out: Path, scenario: Path, *extra: str) -> subprocess.Popen:
        env = {**os.environ, "DOC4AI_ROOT": str(root), "DOC4AI_DATA": str(root / "data"), "DOC4AI_FAKE_ENGINES": "1",
               "DOC4AI_FAKE_SCENARIO": str(scenario), "PYTHONUTF8": "1"}
        env.pop("DOC4AI_CONFIG", None)
        n = len(started)
        with open(root / f"batch{n}.out", "w") as so, open(root / f"batch{n}.err", "w") as se:
            p = subprocess.Popen([sys.executable, "-m", "doc4ai.cli", "batch", str(input_dir), "-o", str(out), *extra],
                                 env=env, stdout=so, stderr=se)
        started.append(p)
        return p
    yield start
    from doc4ai import procs
    for p in started:
        if p.poll() is None:
            procs.kill_tree(p.pid)


@pytest.fixture
def wait_for(tmp_root):
    def wait(pred, timeout: float, interval: float = 0.1):
        timeout *= 3 if os.environ.get("CI") else 1             # hosted runners are several times slower
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if pred():
                    return True
            except Exception:  # noqa: BLE001, S110  the DB may be mid-write in the other process
                pass
            time.sleep(interval)
        errs = "".join(f"
--- {f.name}
{f.read_text(errors='replace')[-2000:]}" for f in sorted(tmp_root.glob("batch*.err")))
        raise AssertionError(f"condition not met within {timeout}s{errs}")
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
