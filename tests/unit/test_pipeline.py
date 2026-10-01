import json
import shutil
from pathlib import Path

import pytest

from aidoc.config import load_config
from aidoc.engines.registry import get_engines
from aidoc.models import ConvertOptions, TaskStatus
from aidoc.names import file_sha256
from aidoc.pipeline import run_task
from aidoc.store import Store
from tests.fakes.scenario import fake_env, write_scenario


@pytest.fixture
def env(tmp_root, monkeypatch, fixtures):
    sc = write_scenario(tmp_root / "sc.json")
    fake_env(monkeypatch, sc)
    cfg = load_config()
    store = Store(tmp_root / "data" / "aidoc.db")

    def make_task(name, out_name=None, **opt):
        src = tmp_root / (out_name or name)
        shutil.copy(fixtures / name, src)
        opts = ConvertOptions(output_dir=tmp_root / "out", **opt)
        job = store.create_job(opts, "cli")
        tid, _ = store.create_task(job, str(src), file_sha256(src), src.stat().st_size, src.stat().st_mtime, opts.lang,
                                   str(tmp_root / "out" / Path(src).stem))
        return tid

    def make_path(src):
        opts = ConvertOptions(output_dir=tmp_root / "out")
        job = store.create_job(opts, "cli")
        tid, _ = store.create_task(job, str(src), file_sha256(src), src.stat().st_size, src.stat().st_mtime, opts.lang,
                                   str(tmp_root / "out" / Path(src).stem))
        return tid
    yield {"cfg": cfg, "store": store, "sc": sc, "make": make_task, "make_path": make_path, "root": tmp_root}
    store.close()


def test_happy_path_docling(env):
    tid = env["make"]("text.pdf")
    events = []
    st = run_task(env["store"], tid, get_engines(env["cfg"]), env["cfg"], emit=lambda k, p: events.append(k))
    assert st == TaskStatus.done
    t = env["store"].get_task(tid)
    assert t["engine"] == "docling" and t["quality"]["level"] == "ok"
    out = env["root"] / "out" / "text"
    assert (out / "text.md").exists() and (out / "assets" / "p1_1.png").exists()
    sc = json.loads((out / "text.json").read_text(encoding="utf-8"))
    assert sc["engine"] == "docling" and sc["tried"][0]["engine"] == "docling" and sc["pages"] == 3 and sc["segments"] == 1
    assert env["store"].find_document(t["sha256"], str(out))["status"] == "ok"
    assert "task.updated" in events and not (env["root"] / "out" / ".tmp" / tid).exists()


def test_fallback_on_low_quality(env):
    write_scenario(env["sc"], rules=[{"match": {"engine": "docling"}, "behavior": "low"}])
    tid = env["make"]("text.pdf")
    assert run_task(env["store"], tid, get_engines(env["cfg"]), env["cfg"]) == TaskStatus.done
    t = env["store"].get_task(tid)
    assert t["engine"] == "mineru" and [a["engine"] for a in t["tried"]] == ["docling", "mineru"] and t["tried"][0]["score"] < 0.5


def test_all_low_keeps_best(env):
    write_scenario(env["sc"], default="low")
    tid = env["make"]("text.pdf")
    assert run_task(env["store"], tid, get_engines(env["cfg"]), env["cfg"]) == TaskStatus.low
    assert env["store"].get_task(tid)["quality"]["level"] == "low" and (env["root"] / "out" / "text" / "text.md").exists()


def test_engine_error_then_fallback(env):
    write_scenario(env["sc"], rules=[{"match": {"engine": "docling"}, "behavior": "error"}])
    tid = env["make"]("text.pdf")
    assert run_task(env["store"], tid, get_engines(env["cfg"]), env["cfg"]) == TaskStatus.done
    assert env["store"].get_task(tid)["tried"][0]["error_kind"] == "engine"


def test_input_errors(env):
    for name, code in [("encrypted.pdf", "encrypted"), ("corrupt.pdf", "corrupt"), ("bad.exe", "unsupported_type")]:
        tid = env["make"](name)
        assert run_task(env["store"], tid, get_engines(env["cfg"]), env["cfg"]) == TaskStatus.failed
        t = env["store"].get_task(tid)
        assert t["error_kind"] == "input" and code in t["error_msg"]


def test_forced_unsupported(env):
    tid = env["make"]("sample.docx", engine="mineru")
    assert run_task(env["store"], tid, get_engines(env["cfg"]), env["cfg"]) == TaskStatus.failed
    assert "engine_unsupported" in env["store"].get_task(tid)["error_msg"]


def test_cache_skip_and_force_and_retry_low(env):
    tid = env["make"]("text.pdf")
    run_task(env["store"], tid, get_engines(env["cfg"]), env["cfg"])
    tid2 = env["make"]("text.pdf")
    assert run_task(env["store"], tid2, get_engines(env["cfg"]), env["cfg"]) == TaskStatus.skipped
    tid3 = env["make"]("text.pdf", force=True)
    assert run_task(env["store"], tid3, get_engines(env["cfg"]), env["cfg"]) == TaskStatus.done
    write_scenario(env["sc"], default="low")
    tid4 = env["make"]("sample.docx")
    assert run_task(env["store"], tid4, get_engines(env["cfg"]), env["cfg"]) == TaskStatus.low
    tid5 = env["make"]("sample.docx")
    assert run_task(env["store"], tid5, get_engines(env["cfg"]), env["cfg"]) == TaskStatus.skipped
    write_scenario(env["sc"])
    tid6 = env["make"]("sample.docx", retry_low=True)
    assert run_task(env["store"], tid6, get_engines(env["cfg"]), env["cfg"]) == TaskStatus.done


def test_cjk_paths(env, fixtures):
    tid = env["make"]("text.pdf", out_name="測試 報告.pdf")
    assert run_task(env["store"], tid, get_engines(env["cfg"]), env["cfg"]) == TaskStatus.done
    assert (env["root"] / "out" / "測試 報告" / "測試 報告.md").exists()


def test_same_stem_different_content(env):
    t1 = env["make"]("text.pdf", out_name="report.pdf")
    t2 = env["make"]("sample.docx", out_name="report.docx")
    for t in (t1, t2):
        assert run_task(env["store"], t, get_engines(env["cfg"]), env["cfg"]) == TaskStatus.done
    dirs = sorted(p.name for p in (env["root"] / "out").iterdir() if p.name.startswith("report"))
    assert len(dirs) == 2 and dirs[0] == "report"


def test_no_engine_available(env, monkeypatch):
    engines = get_engines(env["cfg"])
    for e in engines.values():
        monkeypatch.setattr(e, "available", lambda: False)
    tid = env["make"]("text.pdf")
    assert run_task(env["store"], tid, engines, env["cfg"]) == TaskStatus.failed
    assert "aidoc setup" in env["store"].get_task(tid)["error_msg"]


def test_unexpected_exception_marks_task_failed(env, monkeypatch):
    import aidoc.pipeline as pl

    def boom(*a, **k):
        raise RuntimeError("normalize exploded")
    monkeypatch.setattr(pl, "normalize", boom)
    tid = env["make"]("text.pdf")
    assert run_task(env["store"], tid, get_engines(env["cfg"]), env["cfg"]) == TaskStatus.failed
    t = env["store"].get_task(tid)
    assert t["status"] == "failed" and t["error_kind"] == "engine" and "normalize exploded" in t["error_msg"]


def test_long_source_name_converts(env):
    """P1 verifier 2: a 240-char stem used to overflow the 255-char NTFS component limit at finalize."""
    name = "b" * 240 + ".docx"
    tid = env["make"]("sample.docx", out_name=name)
    assert run_task(env["store"], tid, get_engines(env["cfg"]), env["cfg"]) == TaskStatus.done
    out = Path(env["store"].get_task(tid)["output_dir"])
    assert out.is_dir() and len(out.name) <= 150 and (out / f"{out.name}.md").is_file()
    assert out.name.endswith("-" + env["store"].get_task(tid)["sha256"][:8])
    assert not (env["root"] / "out" / ".tmp").exists()


def test_any_finalize_error_discards_tmp_dir(env, monkeypatch):
    """P1 verifier 2: only FsBusyError used to discard out/.tmp/<task_id>."""
    tid = env["make"]("text.pdf")

    def boom(*a, **k):
        raise OSError(22, "Invalid argument")
    monkeypatch.setattr("aidoc.output.fsops.replace_dir_three_step", boom)
    assert run_task(env["store"], tid, get_engines(env["cfg"]), env["cfg"]) == TaskStatus.failed
    t = env["store"].get_task(tid)
    assert t["error_kind"] == "transient" and "output" in t["error_msg"]
    assert not (env["root"] / "out" / ".tmp" / tid).exists()


def test_fake_output_has_one_marker_per_pdf_page(env):
    tid = env["make"]("twocol.pdf")                       # 2 pages, pages_per_doc is 3
    assert run_task(env["store"], tid, get_engines(env["cfg"]), env["cfg"]) == TaskStatus.done
    out = env["root"] / "out" / "twocol" / "twocol.md"
    assert out.read_text(encoding="utf-8").count("<!-- page: ") == 2


import json as _json

import pymupdf


def _sidecar(env, stem):
    return _json.loads((env["root"] / "out" / stem / f"{stem}.json").read_text(encoding="utf-8"))


def _b10(fixtures, root):
    """broken_tounicode.pdf + 8 pages of text.pdf: 1 broken page of 10 (10 % <= 20 %: repaired per page)."""
    d = pymupdf.open(fixtures / "broken_tounicode.pdf")
    d.delete_page(1)
    t = pymupdf.open(fixtures / "text.pdf")
    for _ in range(9):
        d.insert_pdf(t, from_page=0, to_page=0)
    d.save(root / "b10.pdf")


def test_garbled_page_is_repaired_and_ok(env, fixtures):
    _b10(fixtures, env["root"])
    write_scenario(env["sc"], rules=[{"match": {"engine": "docling"}, "behavior": "garbled"}], garbled_pages=[1])
    tid = env["make_path"](env["root"] / "b10.pdf")
    assert run_task(env["store"], tid, get_engines(env["cfg"]), env["cfg"]) == TaskStatus.done
    q = _sidecar(env, "b10")["quality"]
    assert q["level"] == "ok" and q["pages"][0]["page"] == 1
    assert q["pages"][0]["repaired_by"] == "docling:pypdfium_full_page_ocr" and q["pages_unrepaired"] == 0
    md = (env["root"] / "out" / "b10" / "b10.md").read_text(encoding="utf-8")
    assert "\N{REPLACEMENT CHARACTER}" not in md and md.count("<!-- page: ") == 10


def test_unrepairable_page_is_warn_not_failure(env, fixtures):
    _b10(fixtures, env["root"])
    write_scenario(env["sc"], default="garbled_sticky", garbled_pages=[1])
    tid = env["make_path"](env["root"] / "b10.pdf")
    assert run_task(env["store"], tid, get_engines(env["cfg"]), env["cfg"]) == TaskStatus.done
    q = _sidecar(env, "b10")["quality"]
    assert q["level"] == "warn" and q["pages_unrepaired"] == 1
    assert env["store"].list_documents()[0]["status"] == "warn"


def test_too_many_broken_pages_fall_back_instead_of_repair(env):
    # 1 of 2 pages (50 %) -> document-level pages_flagged -> next engine; mineru output is clean
    write_scenario(env["sc"], rules=[{"match": {"engine": "docling"}, "behavior": "garbled_sticky"}], garbled_pages=[1])
    tid = env["make"]("broken_tounicode.pdf")
    assert run_task(env["store"], tid, get_engines(env["cfg"]), env["cfg"]) == TaskStatus.done
    t = env["store"].get_task(tid)
    assert t["engine"] == "mineru" and "pages_flagged" in t["tried"][0]["reasons"]


def test_no_page_markers_fall_back_to_next_engine(env):
    write_scenario(env["sc"], rules=[{"match": {"engine": "docling"}, "behavior": "no_pages"}])
    tid = env["make"]("text.pdf")
    assert run_task(env["store"], tid, get_engines(env["cfg"]), env["cfg"]) == TaskStatus.done
    t = env["store"].get_task(tid)
    assert t["engine"] == "mineru" and "page_map_incomplete" in t["tried"][0]["reasons"]


def test_misaligned_markers_fall_back(env):
    write_scenario(env["sc"], rules=[{"match": {"engine": "docling"}, "behavior": "misaligned"}])
    tid = env["make"]("big.pdf")
    assert run_task(env["store"], tid, get_engines(env["cfg"]), env["cfg"]) == TaskStatus.done
    assert env["store"].get_task(tid)["engine"] == "mineru"


def test_segment_quick_check_is_quick(env, monkeypatch):
    import aidoc.pipeline as pl
    calls = []
    real = pl.assess
    monkeypatch.setattr(pl, "assess", lambda *a, **k: calls.append(k) or real(*a, **k))
    tid = env["make"]("big.pdf")                                          # 45 pages -> 2 segments
    assert run_task(env["store"], tid, get_engines(env["cfg"]), env["cfg"]) == TaskStatus.done
    seg_calls = [k for k in calls if k.get("quick")]
    assert len(seg_calls) == 2 and all(k.get("pdf") is not None for k in seg_calls)
    assert [k for k in calls if not k.get("quick")][-1].get("pdf") is not None   # document check uses the PDF


def test_forced_engine_repairs_even_many_broken_pages(env):
    # 1 of 2 pages (50 %) but the engine is forced: no fallback exists, so the page is repaired instead
    write_scenario(env["sc"], rules=[{"match": {"engine": "docling"}, "behavior": "garbled"}], garbled_pages=[1])
    tid = env["make"]("broken_tounicode.pdf", engine="docling")
    assert run_task(env["store"], tid, get_engines(env["cfg"]), env["cfg"]) == TaskStatus.done
    q = _sidecar(env, "broken_tounicode")["quality"]
    assert q["level"] == "ok" and q["pages_flagged"] == 1 and q["pages"][0]["repaired_by"].startswith("docling:")
