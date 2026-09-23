from __future__ import annotations

import json
from pathlib import Path


def write_scenario(path: Path, **kw) -> Path:
    sc = {"default": "ok", "delay_s": 0.0, "pages_per_doc": 3, "rules": [], "call_log": str(path.parent / "calls.jsonl")}
    sc.update(kw)
    path.write_text(json.dumps(sc), encoding="utf-8")
    return path


def fake_env(monkeypatch, scenario_path: Path) -> None:
    monkeypatch.setenv("AIDOC_FAKE_ENGINES", "1")
    monkeypatch.setenv("AIDOC_FAKE_SCENARIO", str(scenario_path))
