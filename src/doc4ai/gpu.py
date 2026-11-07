"""GPU status for /api/system (spec §8.8): NVML via nvidia-ml-py. Never raises."""
from __future__ import annotations


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
