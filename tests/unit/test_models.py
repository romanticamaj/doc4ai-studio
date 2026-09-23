from pathlib import Path
from aidoc.models import (ConvertOptions, ProbeResult, QualityResult, Attempt, TableEdge,
                          TaskStatus, TERMINAL_TASK, SEGMENT_PAGES, ENGINE_NAMES)


def test_convert_options_roundtrip():
    o = ConvertOptions(output_dir=Path("out"), engine="mineru", lang="en", force=True, timeout_s=30)
    d = o.to_json()
    assert d["output_dir"] == "out" and d["engine"] == "mineru"
    assert ConvertOptions.from_json(d) == o


def test_defaults():
    o = ConvertOptions(output_dir=Path("x"))
    assert o.lang == "cht" and o.mineru_tier == "basic" and o.allow_online_audio is False


def test_enums_and_constants():
    assert TaskStatus.done in TERMINAL_TASK and TaskStatus.converting not in TERMINAL_TASK
    assert SEGMENT_PAGES == 40 and ENGINE_NAMES == ("markitdown", "docling", "mineru")


def test_attempt_and_edge_roundtrip():
    a = Attempt(engine="docling", attempt=1, score=0.5, reasons=["garbage_ratio"], error_kind=None, error_msg=None)
    assert Attempt.from_json(a.to_json()) == a
    e = TableEdge(page=3, n_cols=4, touches_edge=True)
    assert TableEdge.from_json(e.to_json()) == e


def test_probe_and_quality_json():
    p = ProbeResult(kind="pdf", ext=".pdf", size=10, pages=2, text_ratio=1.0)
    assert p.to_json()["text_ratio"] == 1.0
    q = QualityResult(score=0.9, level="ok", reasons=[])
    assert q.to_json() == {"score": 0.9, "level": "ok", "reasons": [], "metrics": {}}
