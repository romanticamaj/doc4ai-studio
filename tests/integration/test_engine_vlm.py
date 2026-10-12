"""VLM engine on real samples (VLM spec §11.5, plan Task 13).

The local backend is not delivered (spike S1 rule 4, docs/superpowers/spikes/2026-10-vlm-ocr.md), so these run the
`openai` backend against a real OpenAI-compatible endpoint named by AIDOC_VLM_REAL_BASE_URL / AIDOC_VLM_REAL_MODEL
(e.g. Ollama on loopback: http://127.0.0.1:11434/v1, gemma4:12b; AIDOC_VLM_API_KEY when it needs a key). Without
them the tests skip — and FAIL in acceptance mode (AIDOC_REQUIRE_MANUAL=1). Pages only go where that URL points."""
from __future__ import annotations

import collections
import json
import os
import re
from pathlib import Path

import pytest

from aidoc.config import load_config
from aidoc.engines.registry import get_engines
from aidoc.models import ConvertOptions, TaskStatus
from aidoc.names import file_sha256
from aidoc.pagemap import squash
from aidoc.pipeline import run_task
from aidoc.store import Store
from tests.manual import manual_sample, require_engine

pytestmark = pytest.mark.slow

ZH = "這是一份用來測試文件轉換流程的繁體中文段落。內容包含標題、表格與圖片，用於驗證引擎的中文辨識能力。"
EN = ("This is an English paragraph used to test the document conversion pipeline. "
      "It contains enough words to exceed the per-page threshold.")
RECALL_MIN = 0.90                      # spec §11.5: remote endpoint threshold (the local backend's would be 0.95)


def endpoint() -> tuple[str, str]:
    base, model = os.environ.get("AIDOC_VLM_REAL_BASE_URL"), os.environ.get("AIDOC_VLM_REAL_MODEL")
    if base and model:
        return base, model
    msg = "no real VLM endpoint: set AIDOC_VLM_REAL_BASE_URL and AIDOC_VLM_REAL_MODEL"
    pytest.fail(msg) if os.environ.get("AIDOC_REQUIRE_MANUAL") == "1" else pytest.skip(msg)


@pytest.fixture
def vlm_env(tmp_root, monkeypatch):
    base, model = endpoint()
    cfg = tmp_root / "aidoc.toml"
    keyed = bool(os.environ.get("AIDOC_VLM_API_KEY"))
    cfg.write_text("[vlm]\n" + "".join(f"{k} = {json.dumps(v)}\n" for k, v in {
        "backend": "openai", "base_url": base, "model": model, "remote_enabled": True, "allow_no_key": not keyed,
        "request_timeout_s": 300, "max_concurrent": 1}.items()), encoding="utf-8")
    monkeypatch.setenv("AIDOC_CONFIG", str(cfg))
    store = Store(tmp_root / "data" / "aidoc.db")
    logs: list[str] = []

    def convert(src: Path, **opt) -> tuple[TaskStatus, dict, str]:
        config = load_config()
        opts = ConvertOptions(output_dir=tmp_root / "out", **opt)
        job = store.create_job(opts, "cli")
        tid, _ = store.create_task(job, str(src), file_sha256(src), src.stat().st_size, src.stat().st_mtime, "cht",
                                   str(tmp_root / "out" / src.stem))
        st = run_task(store, tid, get_engines(config), config,
                      emit=lambda k, p: logs.append(p.get("line", "")) if k == "task.log" else None)
        out = tmp_root / "out" / src.stem
        return st, json.loads((out / f"{src.stem}.json").read_text(encoding="utf-8")), \
            (out / f"{src.stem}.md").read_text(encoding="utf-8")
    convert.logs = logs
    convert.model = model
    return convert


def recall(expected: str, got: str) -> float:
    e, g = collections.Counter(squash(expected)), collections.Counter(squash(got))
    return sum(min(c, g[k]) for k, c in e.items()) / max(1, sum(e.values()))


@pytest.mark.parametrize("name,expected", [
    ("scanned_cht.pdf", ZH * 8), ("scanned_mixed.pdf", ZH + EN + ZH + EN + ZH + EN), ("page.png", ZH + EN)])
def test_vlm_recall(vlm_env, fixtures, name, expected):
    st, side, md = vlm_env(fixtures / name, engine="vlm")
    r = recall(expected, re.sub(r"<!--.*?-->", "", md))
    print(f"{name}: recall {r:.3f}, {side['vlm']['usage']}")
    assert st == TaskStatus.done and side["quality"]["level"] in ("ok", "warn")
    assert side["engine"] == "vlm" and side["vlm"]["role"] == "primary" and side["vlm"]["pages_sent"] >= 1
    assert r >= RECALL_MIN, f"{name}: recall {r:.3f} < {RECALL_MIN}"


def test_vlm_formula_and_table(vlm_env, fixtures):
    _, _, md = vlm_env(fixtures / "formula.pdf", engine="vlm")
    assert "$" in md or "\\sum" in md or "∑" in md
    _, _, md = vlm_env(fixtures / "text.pdf", engine="vlm")
    page2 = md.split("<!-- page: 2 -->", 1)[1].split("<!-- page: 3 -->", 1)[0]
    assert re.search(r"(?m)^\s*\|.*\|\s*$\n\s*\|?\s*:?-{3,}", page2), page2[:500]


def test_cover_repaired_by_vlm_when_a_and_b_are_disabled(vlm_env, monkeypatch):
    """T-001 cover: Docling main engine, repairs A/B switched off (AIDOC_REPAIR_ONLY=vlm) -> the VLM repairs it."""
    require_engine("docling")
    monkeypatch.setenv("AIDOC_REPAIR_ONLY", "vlm")
    st, side, md = vlm_env(manual_sample("hfad_cover_p1.pdf"), engine="docling")
    assert st == TaskStatus.done
    rep = [p for p in side["quality"]["pages"] if p.get("repaired_by")]
    assert rep and all(p["repaired_by"].startswith("vlm:openai:") for p in rep), side["quality"]["pages"]
    assert "wouldn't it be dreamy" in " ".join(md.replace("\N{RIGHT SINGLE QUOTATION MARK}", "'").lower().split())
    assert side["vlm"]["role"] == "repair" and side["vlm"]["pages_sent"] == 1
    assert any(line.startswith("vlm(openai): sending 1 page image(s)") for line in vlm_env.logs)
