"""GPU status for /api/system (spec §8.8) and for engine error messages: NVML via nvidia-ml-py. Never raises."""
from __future__ import annotations

BUSY_FRACTION = 0.5     # over half the memory taken: say the GPU may be short of memory


def _nvml_query() -> dict:
    import pynvml
    pynvml.nvmlInit()
    try:
        h = pynvml.nvmlDeviceGetHandleByIndex(0)
        name = pynvml.nvmlDeviceGetName(h)
        if isinstance(name, bytes):
            name = name.decode("utf-8", "replace")
        mem = pynvml.nvmlDeviceGetMemoryInfo(h)
        return {"name": name, "mem_total": int(mem.total), "mem_used": int(mem.used)}
    finally:
        pynvml.nvmlShutdown()


def query() -> dict | None:
    """{name, mem_total, mem_used} of GPU 0, or None when NVML is unavailable."""
    try:
        return _nvml_query()
    except Exception:  # noqa: BLE001  no driver, no GPU, no library
        return None


def memory_hint() -> str:
    """'; GPU memory: 11437 of 16303 MiB in use[. advice]' for appending to an engine error message, or ''.

    A runner that hangs while loading weights, times out or runs out of memory is often starved by another
    program holding VRAM (a local LLM server, a game); the message says so with numbers instead of leaving the
    user to guess. Measured before the runner is killed, so the runner's own share is included."""
    g = query()
    if not g or not g.get("mem_total"):
        return ""
    used, total = g["mem_used"] // 2**20, g["mem_total"] // 2**20
    note = f"; GPU memory: {used} of {total} MiB in use"
    if g["mem_used"] / g["mem_total"] >= BUSY_FRACTION:
        note += ". The GPU may be short of memory: close other programs that use it, or convert with MarkItDown"
    return note
