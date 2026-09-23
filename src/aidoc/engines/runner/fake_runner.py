"""Fake engine runner for tests. STDLIB ONLY."""
from __future__ import annotations
import fnmatch
import json
import os
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import _proto  # noqa: E402


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


def _write_ok(req, sc, with_pages=True):
    out = Path(req["out_dir"])
    (out / "images").mkdir(parents=True, exist_ok=True)
    pages = req.get("pages")
    n = (pages[1] - pages[0] + 1) if pages else sc.get("pages_per_doc", 3)
    parts, images = [], []
    for i in range(1, n + 1):
        if with_pages:
            parts.append(f"<!-- page: {i} -->")
        parts.append(f"# 第 {i} 頁 Heading {i}\n\n" + ("假引擎產生的內容 fake engine content. " * 4) + "\n")
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
            "last_table": {"page": n, "n_cols": 2, "touches_edge": False}}


def handle(req):
    sc = _load_scenario()
    attempt = _attempt_no(sc, req["src"], req["engine_opts"].get("fake_engine", ""), req.get("pages"))
    b = _behavior(sc, req, attempt)
    time.sleep(float(sc.get("delay_s", 0)))
    if b == "ok":
        return _write_ok(req, sc)
    if b == "no_pages":
        return _write_ok(req, sc, with_pages=False)
    if b == "slow_ok":
        time.sleep(float(sc.get("slow_s", 5)))
        return _write_ok(req, sc)
    if b == "low":
        out = Path(req["out_dir"])
        out.mkdir(parents=True, exist_ok=True)
        (out / "out.md").write_text("<!-- page: 1 -->\nshort", encoding="utf-8")
        return {"markdown_path": str(out / "out.md"), "images": [], "has_page_markers": True, "page_count": 1,
                "first_table": None, "last_table": None}
    if b == "error":
        raise _proto.RunnerError("engine", "fake engine error")
    if b == "input_error":
        raise _proto.RunnerError("input", "fake input error")
    if b == "oom":
        _proto.log("torch.OutOfMemoryError: CUDA out of memory. Tried to allocate 2.00 GiB")
        sys.exit(1)
    if b == "timeout":
        time.sleep(3600)
    if b == "crash":
        _proto.progress(1, 3)
        os._exit(137)
    raise _proto.RunnerError("engine", f"unknown behavior {b}")


if __name__ == "__main__":
    _proto.serve(handle)
