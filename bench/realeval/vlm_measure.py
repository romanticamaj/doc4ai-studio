"""Local PaddleOCR-VL-1.6 measurement over a loopback llama-server, exactly as spike S1 measured it
(spec 2026-10-22 §5.6; docs/superpowers/spikes/2026-10-vlm-ocr.md).

One user message per page = the page image (PyMuPDF, 200 dpi, long side ≤ 1400 px, PNG data URI) + the text "OCR:";
max_tokens 4096, temperature 0, one request at a time, no retry; a `length` answer is scored as it came. Recall is the
character-multiset recall after the §5.1 normalisation; a document's recall = Σ common ÷ Σ GT characters.

VRAM: `bench.gpu_sampler.Sampler` over the whole phase; its baseline is read before the server starts, so `measure`
launches llama-server itself (`server_factory`) and times model load to /health 200. A server started by hand can be
measured with `base_url` (then the baseline includes the loaded model and the record must say so)."""
from __future__ import annotations

import base64
import json
import re
import socket
import subprocess
import time
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from bench.gpu_sampler import Sampler
from bench.realeval.normalize import APOSTROPHES, norm_gt, norm_pred
from bench.realeval.rules import (
    VLM_COVER_PHRASE,
    VLM_MAX_LONG_SIDE_PX,
    VLM_MAX_TOKENS,
    VLM_PINNED,
    VLM_RENDER_DPI,
)
from bench.realeval.samples import sha256_file
from bench.realeval.scoring import bag_prf

PROMPT = "OCR:"
COVER_PAGE = 1


def check_pinned(paths: dict[str, Path]) -> dict[str, bool]:
    """sha256 of each artefact against rules.VLM_PINNED (absent file → False)."""
    out = {}
    for k, p in paths.items():
        p = Path(p) if p else None
        out[k] = bool(p and p.is_file() and sha256_file(p) == VLM_PINNED.get(k))
    return out


def render_page_png(pdf: Path, page: int, dpi: int = VLM_RENDER_DPI, max_px: int = VLM_MAX_LONG_SIDE_PX) -> bytes:
    """Page `page` (1-based) at `dpi`, scaled down so the long side is at most `max_px`; the PDF's text layer is
    never read."""
    import pymupdf
    with pymupdf.open(pdf) as d:
        pg = d[page - 1]
        long_pt = max(pg.rect.width, pg.rect.height)
        zoom = dpi / 72
        if long_pt * zoom > max_px:
            zoom = max_px / long_pt
        pix = pg.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
        while max(pix.width, pix.height) > max_px:          # pixel rounding
            zoom *= (max_px - 0.5) / max(pix.width, pix.height)
            pix = pg.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
        return pix.tobytes("png")


def data_uri(png: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


def ocr_request(base_url: str, png: bytes, *, timeout_s: int = 120) -> dict:
    """One Chat Completions request; seconds = wall time of the request only (rendering excluded)."""
    body = {"model": "paddleocr-vl-1.6", "temperature": 0, "max_tokens": VLM_MAX_TOKENS, "stream": False,
            "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": data_uri(png)}},
                                                      {"type": "text", "text": PROMPT}]}]}
    req = urllib.request.Request(base_url.rstrip("/") + "/chat/completions", data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout_s) as r:
        data = json.loads(r.read().decode("utf-8"))
    seconds = time.perf_counter() - t0
    ch = (data.get("choices") or [{}])[0]
    return {"text": (ch.get("message") or {}).get("content") or "", "seconds": seconds,
            "finish_reason": ch.get("finish_reason"), "usage": data.get("usage") or {}}


_LAYER = re.compile(r"layer\s+(\d+)\s+assigned to device (\S+?),?\s")
_NLAYER = re.compile(r"n_layer\s*=\s*(\d+)")


def parse_server_log(text: str) -> dict:
    """{"device_cuda", "layers_on_gpu", "layers_total"} from a `llama-server -v` log (spec §5.6, §7.0 check 5)."""
    m = _NLAYER.search(text)
    total = int(m.group(1)) if m else 0
    on = {int(i) for i, dev in _LAYER.findall(text + "\n") if dev.startswith("CUDA0") and int(i) < total}
    return {"device_cuda": "using device CUDA0" in text, "layers_on_gpu": len(on), "layers_total": total}


def page_recall(gt_sq: str, answer: str) -> float:
    return bag_prf(gt_sq, norm_pred(answer))[0]


def cover_hit(answer: str) -> bool:
    s = unicodedata.normalize("NFKC", answer)
    for k, v in APOSTROPHES.items():
        s = s.replace(k, v)
    return VLM_COVER_PHRASE in s.lower()


def _check_endpoint(base_url: str | None) -> None:
    from bench.run import _check_vlm_endpoint
    _check_vlm_endpoint(base_url)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def server_command(exe: Path, gguf: Path, mmproj: Path, port: int) -> list[str]:
    return [str(exe), "-m", str(gguf), "--mmproj", str(mmproj), "--host", "127.0.0.1", "--port", str(port),
            "-ngl", "99", "-c", "8192", "--temp", "0", "-np", "1", "--no-ui", "-v"]


class LlamaServer:
    """llama-server launched on loopback with the spike's arguments; its -v log goes to `log_path`."""

    def __init__(self, exe: Path, gguf: Path, mmproj: Path, log_path: Path, port: int | None = None):
        self.cmd = server_command(exe, gguf, mmproj, port or free_port())
        self.port = int(self.cmd[self.cmd.index("--port") + 1])
        self.base_url = f"http://127.0.0.1:{self.port}/v1"
        self.log_path = Path(log_path)
        self.proc: subprocess.Popen | None = None
        self._log = None
        self._t0 = 0.0

    def start(self) -> LlamaServer:
        self._log = open(self.log_path, "wb")             # noqa: SIM115  closed in stop()
        self._t0 = time.perf_counter()
        self.proc = subprocess.Popen(self.cmd, stdout=self._log, stderr=subprocess.STDOUT)
        return self

    def wait_ready(self, timeout_s: float = 600) -> float:
        """Seconds from launch to GET /health 200."""
        url = f"http://127.0.0.1:{self.port}/health"
        while time.perf_counter() - self._t0 < timeout_s:
            if self.proc is not None and self.proc.poll() is not None:
                raise RuntimeError(f"llama-server exited with {self.proc.returncode}")
            try:
                with urllib.request.urlopen(url, timeout=5) as r:
                    if r.status == 200:
                        return time.perf_counter() - self._t0
            except (urllib.error.URLError, OSError):
                pass
            time.sleep(0.5)
        raise TimeoutError("llama-server did not become ready")

    @property
    def log_text(self) -> str:
        try:
            return self.log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    def stop(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=30)
        if self._log:
            self._log.close()
            self._log = None


def measure(samples, cover_pdf: Path | None, base_url: str | None, *, out: Path, sampler_factory=Sampler,
            gt_texts: dict[str, dict[int, str]] | None = None, server_factory=None) -> dict:
    """Writes `<out>/vlm.jsonl` (per page: recall, common, gt_len, seconds, finish_reason, tokens — never text) and
    returns {per_doc, max_sec_overall, cover_hit, cover_measured, vram, load_seconds, server, endpoint_host}."""
    if server_factory is None:
        _check_endpoint(base_url)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    per_doc: dict[str, dict] = {}
    cover = {"hit": False, "measured": False}
    load_seconds = None
    server_info = None
    srv = None
    with sampler_factory() as smp:
        try:
            if server_factory is not None:
                srv = server_factory().start()
                load_seconds = srv.wait_ready()
                base_url = srv.base_url
                _check_endpoint(base_url)
            for s in samples:
                gts = (gt_texts or {}).get(s.name) or {p: s.gt_text(p) for p in s.gt_pages}
                pdf = s.scoring_pdf()
                prow: list[dict] = []
                for p in sorted(gts):
                    g = norm_gt(gts[p])
                    row = {"kind": "page", "doc": s.name, "page": p, "gt_len": len(g)}
                    try:
                        r = ocr_request(base_url, render_page_png(pdf, p))
                        rec, _, _ = bag_prf(g, norm_pred(r["text"]))
                        row.update(recall=round(rec, 4), common=round(rec * len(g)), seconds=round(r["seconds"], 3),
                                   finish_reason=r["finish_reason"],
                                   prompt_tokens=r["usage"].get("prompt_tokens"),
                                   completion_tokens=r["usage"].get("completion_tokens"), error=None)
                    except (urllib.error.URLError, OSError, ValueError, KeyError) as e:
                        row.update(recall=0.0, common=0, seconds=None, finish_reason=None, error=str(e)[:200])
                    prow.append(row)
                rows += prow
                secs = [r["seconds"] for r in prow if r["seconds"] is not None]
                gl = sum(r["gt_len"] for r in prow)
                per_doc[s.name] = {"recall": (sum(r["common"] for r in prow) / gl) if gl else 0.0,
                                   "pages": len(prow), "max_sec": max(secs) if secs else None,
                                   "mean_sec": (sum(secs) / len(secs)) if secs else None,
                                   "length_pages": sum(1 for r in prow if r["finish_reason"] == "length"),
                                   "complete": all(r["error"] is None for r in prow) and bool(prow),
                                   "errors": sum(1 for r in prow if r["error"] is not None)}
            if cover_pdf is not None and Path(cover_pdf).is_file():
                try:
                    r = ocr_request(base_url, render_page_png(Path(cover_pdf), COVER_PAGE))
                    cover = {"hit": cover_hit(r["text"]), "measured": True}
                    rows.append({"kind": "cover", "hit": cover["hit"], "seconds": round(r["seconds"], 3),
                                 "finish_reason": r["finish_reason"]})
                except (urllib.error.URLError, OSError, ValueError, KeyError) as e:
                    rows.append({"kind": "cover", "hit": False, "error": str(e)[:200]})
        finally:
            if srv is not None:
                srv.stop()
                server_info = parse_server_log(srv.log_text)
    secs_all = [r["seconds"] for r in rows if r["kind"] == "page" and r.get("seconds") is not None]
    res = {"per_doc": per_doc, "max_sec_overall": max(secs_all) if secs_all else None,
           "cover_hit": cover["hit"], "cover_measured": cover["measured"],
           "vram": {"baseline": smp.baseline, "peak": smp.peak, "delta": smp.delta},
           "load_seconds": load_seconds, "server": server_info, "endpoint_host": urlparse(base_url or "").hostname,
           "launched": server_factory is not None}
    rows.append({"kind": "summary", **{k: v for k, v in res.items() if k != "per_doc"}})
    with open(out / "vlm.jsonl", "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return res
