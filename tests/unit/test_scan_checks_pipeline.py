"""T-002 wired in: the scan checks raise the level (assess / verify), and the pipeline repairs the pages like
page_dropped — both sides of a misattribution, so the text is not duplicated."""
from __future__ import annotations

import json
import shutil

import pytest

from doc4ai.config import load_config
from doc4ai.engines.registry import get_engines
from doc4ai.models import ConvertOptions, TaskStatus
from doc4ai.names import file_sha256
from doc4ai.pipeline import run_task
from doc4ai.probe import probe_file
from doc4ai.quality import PAGE_CHECK_VERSION, PAGE_REASONS, assess
from doc4ai.store import Store
from tests.fakes.scenario import fake_env, write_scenario
from tests.fixtures.make_fixtures import EN, ZH, make_scanned_pdf

PAGES = [[ZH * 5, EN], [ZH * 6], [ZH * 5, EN], [ZH * 6], [ZH * 5, EN], [ZH * 6], [ZH * 5, EN], [ZH * 6],
         [ZH * 5, EN], [ZH * 6]]                         # ten full pages: 2 flagged = 20 % (repaired, not pages_flagged)
N = len(PAGES)


def _texts():
    return {i: "\n".join(t) for i, t in enumerate(PAGES, 1)}


def _md(secs):
    return "".join(f"<!-- page: {p} -->\n{t}\n\n" for p, t in secs.items())


@pytest.fixture
def scan(tmp_path):
    p = tmp_path / "scan10.pdf"
    make_scanned_pdf(p, PAGES)
    return p


def test_page_empty_scan_is_level_raising_and_versioned():
    assert "page_empty_scan" in PAGE_REASONS and PAGE_CHECK_VERSION >= 6


def test_assess_flags_an_empty_scan_page(scan):
    probe = probe_file(scan)
    ok = assess(_md(_texts()), probe, N, pdf=scan, page_map_method="docling_per_page")
    assert ok.level == "ok" and ok.pages == []
    secs = _texts()
    secs[3] = ""
    q = assess(_md(secs), probe, N, pdf=scan, page_map_method="docling_per_page")
    assert q.level == "warn" and [(e["page"], e["reasons"]) for e in q.pages] == [(3, ["page_empty_scan"])]


def test_assess_flags_a_misattribution_on_a_scan(scan):
    secs = _texts()
    secs[4] = secs[4] + "\n" + secs[5]
    secs[5] = ""
    q = assess(_md(secs), probe_file(scan), N, pdf=scan, page_map_method="docling_per_page")
    assert q.level == "warn"
    assert {e["page"]: e["reasons"] for e in q.pages} == {4: ["page_oversized"], 5: ["page_misaligned"]}


def test_verify_reports_the_scan_fault(scan):
    from doc4ai.verify import verify_markdown
    secs = _texts()
    secs[2] = ""
    rep = verify_markdown(_md(secs), scan)
    assert rep.level == "warn" and any(e["page"] == 2 and "page_empty_scan" in e["reasons"] for e in rep.pages)


# ---------------------------------------------------------------- the pipeline (fake engines)

@pytest.fixture
def env(tmp_root, monkeypatch, scan):
    sc = write_scenario(tmp_root / "sc.json")
    fake_env(monkeypatch, sc)
    cfg = load_config()
    store = Store(tmp_root / "data" / "aidoc.db")

    def make(**opt):
        src = tmp_root / "scan10.pdf"
        shutil.copy(scan, src)
        opts = ConvertOptions(output_dir=tmp_root / "out", **opt)
        job = store.create_job(opts, "cli")
        tid, _ = store.create_task(job, str(src), file_sha256(src), src.stat().st_size, src.stat().st_mtime, opts.lang,
                                   str(tmp_root / "out" / "scan10"))
        return tid
    yield {"cfg": cfg, "store": store, "sc": sc, "make": make, "root": tmp_root}
    store.close()


def _quality(env):
    return json.loads((env["root"] / "out" / "scan10" / "scan10.json").read_text(encoding="utf-8"))["quality"]


def _md_out(env):
    return (env["root"] / "out" / "scan10" / "scan10.md").read_text(encoding="utf-8")


def test_a_silently_dropped_scan_page_is_flagged_and_repaired(env):
    write_scenario(env["sc"], rules=[{"match": {"engine": "mineru"}, "behavior": "silent_drop"}], dropped_pages=[3],
                   page_texts={str(k): v for k, v in _texts().items()})
    assert run_task(env["store"], env["make"](), get_engines(env["cfg"]), env["cfg"]) == TaskStatus.done
    q = _quality(env)
    e = next(p for p in q["pages"] if p["page"] == 3)
    assert e["reasons"] == ["page_empty_scan"] and e["repaired_by"] and q["level"] == "ok"


def test_an_unrepairable_empty_scan_page_is_warn_not_ok(env):
    write_scenario(env["sc"], default="silent_drop_sticky", dropped_pages=[3],
                   page_texts={str(k): v for k, v in _texts().items()})
    assert run_task(env["store"], env["make"](), get_engines(env["cfg"]), env["cfg"]) == TaskStatus.done
    q = _quality(env)
    assert q["level"] == "warn" and q["pages_unrepaired"] >= 1
    assert any(p["page"] == 3 and p["reasons"] == ["page_empty_scan"] and not p.get("repaired_by") for p in q["pages"])


def test_a_scan_misattribution_repairs_both_pages_and_keeps_the_text_once(env):
    texts = {str(k): v for k, v in _texts().items()}
    texts["5"] += " UNIQUEPAGEFIVETOKEN"
    write_scenario(env["sc"], rules=[{"match": {"engine": "mineru"}, "behavior": "misplace"}], misplaced={"5": 4},
                   page_texts=texts)
    assert run_task(env["store"], env["make"](), get_engines(env["cfg"]), env["cfg"]) == TaskStatus.done
    q = _quality(env)
    got = {p["page"]: (p["reasons"], bool(p.get("repaired_by"))) for p in q["pages"]}
    assert got == {4: (["page_oversized"], True), 5: (["page_misaligned"], True)} and q["level"] == "ok"
    assert _md_out(env).count("UNIQUEPAGEFIVETOKEN") <= 1                   # never twice
