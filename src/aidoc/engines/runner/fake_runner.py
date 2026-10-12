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
    if eo.get("fake_engine") == "vlm":                 # VLM repair runs get the flagged pages as pages.pdf
        return os.path.basename(req["src"]) == "pages.pdf"
    return eo.get("backend") == "pypdfium" or eo.get("ocr_mode") == "ocr"


def _vlm_block(req, n, refused=(), truncated=()):
    eo = req.get("engine_opts") or {}
    backend = eo.get("backend") if eo.get("backend") in ("openai", "local") else "openai"
    host = eo.get("base_url", "").split("://", 1)[-1].split("/", 1)[0] or "fake-vlm:0"
    # like vlm_runner: the usage table is fed by per-request events, the block below only goes to the sidecar
    _proto._emit("AIDOC_VLM_USAGE", {"backend": backend, "endpoint_host": host if backend == "openai" else "local",
                                     "pages": n, "requests": n, "prompt_tokens": 100 * n, "completion_tokens": 50 * n})
    return {"backend": backend, "model": eo.get("model") or "fake-vlm",
            "endpoint_host": host if backend == "openai" else "local", "local_endpoint": host.startswith("127."),
            "pages_sent": n, "usage": {"requests": n, "prompt_tokens": 100 * n, "completion_tokens": 50 * n},
            "truncated_pages": list(truncated), "refused_pages": list(refused), "dpi": eo.get("dpi", 150),
            "prompt_version": eo.get("prompt_version", 1)}


def _write_ok(req, sc, with_pages=True, shift=0, garbled=False, sticky=False, failing=False, vlm_flag=None,
              silent=False):
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
    # partial success: scenario failed_pages are absolute pages; a repair run (pages.pdf) fails all its pages when sticky
    failed = {}
    if failing and (sticky or not _repairing(req)):
        first = pages[0] if pages else 1
        repair_src = os.path.basename(req["src"]) == "pages.pdf"
        for i in range(1, n + 1):
            if (repair_src and sticky) or (not repair_src and first - 1 + i in set(sc.get("failed_pages", []))):
                failed[i] = "Page failed to parse."
    # silent drop: scenario dropped_pages come out empty and are NOT reported (unlike failed_pages)
    dropped = set()
    if silent and (sticky or not (_repairing(req) or os.path.basename(req["src"]) == "pages.pdf")):
        first = pages[0] if pages else 1
        repair_src = os.path.basename(req["src"]) == "pages.pdf"
        dropped = {i for i in range(1, n + 1) if (repair_src and sticky)
                   or (not repair_src and first - 1 + i in set(sc.get("dropped_pages", [])))}
    is_vlm = (req.get("engine_opts") or {}).get("fake_engine") == "vlm"
    if vlm_flag:                                        # fake vlm refuse / length: every page (spec §4.4)
        failed = {i: vlm_flag for i in range(1, n + 1)}
    parts, images = [], []
    images_missing = {}
    for i in range(1, n + 1):
        if with_pages:
            parts.append(f"<!-- page: {i + shift} -->")
        if (i in failed and vlm_flag != "truncated") or i in dropped:   # the engine lost this page: empty section
            continue
        excerpt = ""
        if texts is not None and len(texts) == n:
            excerpt = " ".join(texts[i - 1][:400].split())
        elif texts is not None and pages and len(texts) >= pages[1]:
            excerpt = " ".join(texts[pages[0] - 1 + i - 1][:400].split())
        parts.append(f"# 第 {i} 頁 Heading {i}\n\n" + ("假引擎產生的內容 fake engine content. " * 4) + "\n"
                     + (excerpt + "\n" if excerpt else "")
                     + (GARBLE + "\n" if i in garbled_pages else ""))
        parts.append(f"| col1 | col2 |\n| --- | --- |\n| a{i} | b{i} |\n")
        if (pages[0] if pages else 1) - 1 + i in set(sc.get("missing_image_pages", [])):
            images_missing[i] = 1                       # like Docling: a picture whose image could not be made
            parts.append("<!-- image missing -->\n")
            _proto.progress(i, n)
            continue
        img = out / "images" / f"img_{i}.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 16)
        images.append(str(img))
        parts.append(f"![](images/img_{i}.png)\n")
        _proto.progress(i, n)
    md = out / "out.md"
    md.write_text("\n".join(parts), encoding="utf-8")
    res = {"markdown_path": str(md), "images": images, "has_page_markers": with_pages, "page_count": n,
           "first_table": {"page": 1, "n_cols": 2, "touches_edge": False},
           "last_table": {"page": n, "n_cols": 2, "touches_edge": False},
           "page_map_method": ("vlm_per_page" if is_vlm else "fake_per_page") if with_pages else "none",
           "failed_pages": {str(k): v for k, v in failed.items()},
           "errors": [f"p{k}: {v}" for k, v in failed.items()],
           "images_missing": {str(k): v for k, v in images_missing.items()}}
    if is_vlm:
        res["vlm"] = _vlm_block(req, n, refused=sorted(failed) if vlm_flag == "refused" else (),
                                truncated=sorted(failed) if vlm_flag == "truncated" else ())
    return res


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
    if b in ("refuse", "length") and req["engine_opts"].get("fake_engine") == "vlm":
        return _write_ok(req, sc, vlm_flag="refused" if b == "refuse" else "truncated")
    if b == "partial":                                  # PARTIAL_SUCCESS: scenario failed_pages come out empty
        return _write_ok(req, sc, failing=True)
    if b == "silent_drop":                              # scenario dropped_pages come out empty, unreported
        return _write_ok(req, sc, silent=True)
    if b == "silent_drop_sticky":                       # ... and the repair runs drop them too
        return _write_ok(req, sc, silent=True, sticky=True)
    if b == "partial_sticky":                           # ... and the repair runs fail those pages too
        return _write_ok(req, sc, failing=True, sticky=True)
    if b == "slow_ok":
        # index §6: slow_ok sleeps delay_s; `slow_s` (tests) overrides it for slow_ok only
        time.sleep(float(sc.get("slow_s", sc.get("delay_s", 0))))
        return _write_ok(req, sc)
    if b == "low":
        out = Path(req["out_dir"])
        out.mkdir(parents=True, exist_ok=True)
        (out / "out.md").write_text("<!-- page: 1 -->\nshort", encoding="utf-8")
        res = {"markdown_path": str(out / "out.md"), "images": [], "has_page_markers": True, "page_count": 1,
               "first_table": None, "last_table": None}
        if req["engine_opts"].get("fake_engine") == "vlm":   # a low VLM answer still sent its pages
            res["vlm"] = _vlm_block(req, len(_pdf_texts(req) or [None]))
        return res
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
    if b == "memory_hog":                               # a decompression bomb inside the engine: grows until killed
        hog = []
        for _ in range(200):                            # safety stop at ~4 GB if no watchdog kills us
            hog.append(b"\x01" * (20 * 1024 * 1024))
            time.sleep(0.05)
        time.sleep(3600)
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
