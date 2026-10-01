"""Fake engine runner for tests. STDLIB ONLY."""
from __future__ import annotations

import fnmatch
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import _proto


class OutOfMemoryError(RuntimeError):                  # stands in for torch.OutOfMemoryError
    pass


def _load_scenario():
    p = os.environ.get("AIDOC_FAKE_SCENARIO")
    return json.loads(Path(p).read_text(encoding="utf-8")) if p else {"default": "ok"}


def _attempt_no(sc, source, engine, pages):
    logp = sc.get("call_log")
    key = {"source": os.path.basename(source), "engine": engine, "pages": pages}
    n = 1
    if logp and Path(logp).exists():
        for line in Path(logp).read_text(encoding="utf-8").splitlines():
            if line.strip() and json.loads(line) == key:
                n += 1
    if logp:
        with open(logp, "a", encoding="utf-8") as f:
            f.write(json.dumps(key) + "\n")
    return n


def _behavior(sc, req, attempt):
    src = os.path.basename(req["src"])
    engine = req["engine_opts"].get("fake_engine", "")
    seg_idx = req.get("segment_idx", 0)
    for rule in sc.get("rules", []):
        m = rule.get("match", {})
        if "source_glob" in m and not fnmatch.fnmatch(src, m["source_glob"]):
            continue
        if "engine" in m and m["engine"] != engine:
            continue
        if "attempt" in m and m["attempt"] != attempt:
            continue
        if "segment_idx" in m and m["segment_idx"] != seg_idx:
            continue
        return rule["behavior"]
    return sc.get("default", "ok")


GARBLE = " :RXOGQ\N{REPLACEMENT CHARACTER}W\N{REPLACEMENT CHARACTER}LW\N{REPLACEMENT CHARACTER}EH "   # T-001 style


def _pdf_texts(req):
    """Text layer of every page of a PDF source (the fake runs on the host interpreter, so PyMuPDF is there)."""
    if not str(req["src"]).lower().endswith(".pdf"):
        return None
    try:
        import pymupdf
        with pymupdf.open(req["src"]) as d:
            return [p.get_text() for p in d]
    except Exception:  # noqa: BLE001  unreadable: behave like a page-less source
        return None


def _repairing(req):
    eo = req.get("engine_opts") or {}
    return eo.get("backend") == "pypdfium" or eo.get("ocr_mode") == "ocr"


def _write_ok(req, sc, with_pages=True, shift=0, garbled=False, sticky=False):
    out = Path(req["out_dir"])
    (out / "images").mkdir(parents=True, exist_ok=True)
    pages = req.get("pages")
    texts = _pdf_texts(req)
    if pages:
        n = pages[1] - pages[0] + 1
    elif texts is not None:
        n = len(texts)
    else:
        n = sc.get("pages_per_doc", 3)
    garbled_pages = set(sc.get("garbled_pages", [])) if garbled and (sticky or not _repairing(req)) else set()
    parts, images = [], []
    for i in range(1, n + 1):
        if with_pages:
            parts.append(f"<!-- page: {i + shift} -->")
        excerpt = ""
        if texts is not None and len(texts) == n:
            excerpt = " ".join(texts[i - 1][:400].split())
        elif texts is not None and pages and len(texts) >= pages[1]:
            excerpt = " ".join(texts[pages[0] - 1 + i - 1][:400].split())
        parts.append(f"# 第 {i} 頁 Heading {i}\n\n" + ("假引擎產生的內容 fake engine content. " * 4) + "\n"
                     + (excerpt + "\n" if excerpt else "")
                     + (GARBLE + "\n" if i in garbled_pages else ""))
        parts.append(f"| col1 | col2 |\n| --- | --- |\n| a{i} | b{i} |\n")
        img = out / "images" / f"img_{i}.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 16)
        images.append(str(img))
        parts.append(f"![](images/img_{i}.png)\n")
        _proto.progress(i, n)
    md = out / "out.md"
    md.write_text("\n".join(parts), encoding="utf-8")
    return {"markdown_path": str(md), "images": images, "has_page_markers": with_pages, "page_count": n,
            "first_table": {"page": 1, "n_cols": 2, "touches_edge": False},
            "last_table": {"page": n, "n_cols": 2, "touches_edge": False},
            "page_map_method": "fake_per_page" if with_pages else "none"}


def handle(req):
    sc = _load_scenario()
    attempt = _attempt_no(sc, req["src"], req["engine_opts"].get("fake_engine", ""), req.get("pages"))
    b = _behavior(sc, req, attempt)
    if b != "slow_ok":
        time.sleep(float(sc.get("delay_s", 0)))
    if b == "ok":
        return _write_ok(req, sc)
    if b == "no_pages":
        return _write_ok(req, sc, with_pages=False)
    if b == "misaligned":                               # markers numbered +1: every page under the wrong marker
        return _write_ok(req, sc, shift=1)
    if b == "garbled":                                  # broken text layer on scenario garbled_pages; OCR repairs it
        return _write_ok(req, sc, garbled=True)
    if b == "garbled_sticky":                           # ... and the repair engines cannot fix it either
        return _write_ok(req, sc, garbled=True, sticky=True)
    if b == "slow_ok":
        # index §6: slow_ok sleeps delay_s; `slow_s` (tests) overrides it for slow_ok only
        time.sleep(float(sc.get("slow_s", sc.get("delay_s", 0))))
        return _write_ok(req, sc)
    if b == "low":
        out = Path(req["out_dir"])
        out.mkdir(parents=True, exist_ok=True)
        (out / "out.md").write_text("<!-- page: 1 -->\nshort", encoding="utf-8")
        return {"markdown_path": str(out / "out.md"), "images": [], "has_page_markers": True, "page_count": 1,
                "first_table": None, "last_table": None}
    if b == "error":
        raise _proto.RunnerError("engine", "fake engine error")
    if b == "bad_kind":                                 # protocol robustness test: unknown error kind
        raise _proto.RunnerError("weird", "weird kind error")
    if b == "input_error":
        raise _proto.RunnerError("input", "fake input error")
    if b == "oom":
        _proto.log("torch.OutOfMemoryError: CUDA out of memory. Tried to allocate 2.00 GiB")
        sys.exit(1)
    if b == "oom_raise":                                # OOM raised inside the engine; the runner survives
        raise OutOfMemoryError("CUDA out of memory. Tried to allocate 2.00 GiB")
    if b == "timeout":
        time.sleep(3600)
    if b == "crash":
        _proto.progress(1, 3)
        os._exit(137)
    raise _proto.RunnerError("engine", f"unknown behavior {b}")


if __name__ == "__main__":
    # simulated model loading before AIDOC_READY (index A1 clock / startup-cap tests)
    _proto.log("fake runner loading models")
    time.sleep(float(_load_scenario().get("startup_delay_s", 0)))
    _proto.serve(handle)
