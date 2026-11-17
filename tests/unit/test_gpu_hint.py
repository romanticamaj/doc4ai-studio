from doc4ai import gpu

MiB = 2**20


def test_hint_with_busy_gpu(monkeypatch):
    monkeypatch.setattr(gpu, "query", lambda: {"name": "x", "mem_total": 16000 * MiB, "mem_used": 11000 * MiB})
    h = gpu.memory_hint()
    assert h.startswith("; GPU memory: 11000 of 16000 MiB in use") and "short of memory" in h and "MarkItDown" in h


def test_hint_with_idle_gpu_has_numbers_only(monkeypatch):
    monkeypatch.setattr(gpu, "query", lambda: {"name": "x", "mem_total": 16000 * MiB, "mem_used": 1000 * MiB})
    assert gpu.memory_hint() == "; GPU memory: 1000 of 16000 MiB in use"


def test_no_gpu_no_hint(monkeypatch):
    monkeypatch.setattr(gpu, "query", lambda: None)
    assert gpu.memory_hint() == ""
