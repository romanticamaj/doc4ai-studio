"""A13 (spec 2026-10-29 §14): a 2 GiB file is copied and verified with bounded memory (streamed in COPY_CHUNK)."""
from __future__ import annotations

import hashlib
import os
import shutil
import time
import tracemalloc

import pytest

from aidoc.source_copy import copy_verified

pytestmark = pytest.mark.slow

SIZE = 2 * 1024 ** 3
NEED_FREE = 5 * 1024 ** 3


def test_two_gib_streaming_copy(tmp_path):
    free = shutil.disk_usage(tmp_path).free
    if free < NEED_FREE:
        msg = f"needs 5 GiB free in {tmp_path} (has {free / 1024 ** 3:.1f} GiB)"
        pytest.fail(msg) if os.environ.get("AIDOC_REQUIRE_MANUAL") == "1" else pytest.skip(msg)
    src = tmp_path / "big.bin"
    block = bytearray(os.urandom(1 << 20))
    h = hashlib.sha256()
    with open(src, "wb") as f:
        for i in range(SIZE >> 20):
            block[:8] = i.to_bytes(8, "little")              # every MiB differs: not sparse, not dedupable
            f.write(block)
            h.update(block)
    dst = tmp_path / "source" / "big" / "big.bin"
    try:
        t0 = time.perf_counter()
        tracemalloc.start()
        try:
            size, _ = copy_verified(src, dst, h.hexdigest())
            _cur, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        elapsed = time.perf_counter() - t0
        print(f"2 GiB copy + verify: {elapsed:.1f} s, tracemalloc peak {peak / 1024 ** 2:.1f} MiB")
        assert size == SIZE and dst.stat().st_size == SIZE
        assert peak < 64 * 1024 ** 2, peak                    # A13
        hv = hashlib.sha256()
        with open(dst, "rb") as f:
            while b := f.read(8 << 20):
                hv.update(b)
        assert hv.hexdigest() == h.hexdigest()
    finally:
        for p in (dst, src):
            p.unlink(missing_ok=True)
