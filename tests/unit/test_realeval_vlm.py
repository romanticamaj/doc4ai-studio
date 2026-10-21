"""Local PaddleOCR-VL measurement over a loopback llama-server, spike S1's method (spec 2026-10-22 §5.6). The fake
endpoint stands in for llama-server; no GPU, no model."""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

import pytest

from bench.realeval import vlm_measure as V
from bench.realeval.samples import RealSample
from tests.fakes.vlm_server import DEFAULT_TEXT, FakeVlmServer, png_size

FIX = Path(__file__).resolve().parents[1] / "fixtures"
ANSWER = "Page 1 " + DEFAULT_TEXT.format(k=1)     # the fake's reply to a prompt without "Page N of M", minus "#"


def _sample(name: str, pdf: Path, pages: list[int], group: str = "A") -> RealSample:
    return RealSample(name, group, "cht", pdf, pdf.parent, pages, 3, None, "0" * 64, False)


def _two_docs():
    return [_sample("one", FIX / "scanned_en.pdf", [1]), _sample("two", FIX / "scanned_cht.pdf", [1, 2])]


def _gt(docs):
    return {s.name: {p: ANSWER for p in s.gt_pages} for s in docs}


class FixedSampler:
    def __init__(self, baseline=1000, peak=3400):
        self.baseline, self.peak = baseline, peak

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    @property
    def delta(self):
        return self.peak - self.baseline


def _measure(tmp_path, base_url, docs=None, **kw):
    docs = docs or _two_docs()
    return V.measure(docs, FIX / "scanned_en.pdf", base_url, out=tmp_path, gt_texts=_gt(docs),
                     sampler_factory=kw.pop("sampler_factory", FixedSampler), **kw)


def test_non_loopback_is_refused(tmp_path):
    with pytest.raises(SystemExit, match="loopback"):
        _measure(tmp_path, "http://10.0.0.5:8080/v1", sampler_factory=lambda: pytest.fail("no measurement"))
    with pytest.raises(SystemExit, match="loopback"):
        _measure(tmp_path, "https://api.example.com/v1")
    assert not (tmp_path / "vlm.jsonl").exists()


def test_request_shape_is_the_spikes(tmp_path):
    srv = FakeVlmServer({"default": "ok"})
    url = srv.start()
    try:
        _measure(tmp_path, url)
    finally:
        srv.stop()
    calls = srv.calls
    assert len(calls) == 4                                   # 3 GT pages + the cover page, one request each
    for c in calls:
        assert c["max_tokens"] == 4096 and c["temperature"] == 0 and c["prompt"] == "OCR:"
        assert c["n_images"] == 1 and c["image_bytes"] > 0      # parsed from a data: URI
        assert max(c["image_px"]) <= 1400


def test_rendering_caps_the_long_side_and_uses_200_dpi():
    small = V.render_page_png(FIX / "text.pdf", 1)
    w, h = png_size(small)
    assert max(w, h) <= 1400
    import pymupdf
    with pymupdf.open(FIX / "text.pdf") as d:
        r = d[0].rect
    if max(r.width, r.height) * 200 / 72 <= 1400:
        assert abs(w - r.width * 200 / 72) <= 1
    big = V.render_page_png(FIX / "text.pdf", 1, dpi=600)
    assert max(png_size(big)) == 1400


def test_recall_and_timing_per_document(tmp_path):
    with FakeVlmServer({"default": "ok"}) as url:
        res = _measure(tmp_path, url)
    assert res["per_doc"]["one"]["recall"] == 1.0 and res["per_doc"]["two"]["recall"] == 1.0
    assert res["per_doc"]["two"]["pages"] == 2 and res["per_doc"]["two"]["complete"] is True
    assert res["max_sec_overall"] > 0
    rows = [json.loads(x) for x in (tmp_path / "vlm.jsonl").read_text(encoding="utf-8").splitlines()]
    pages = [r for r in rows if r["kind"] == "page"]
    assert len(pages) == 3 and all(r["finish_reason"] == "stop" for r in pages)
    assert {r["kind"] for r in rows} == {"page", "cover", "summary"}
    assert all("text" not in r and "answer" not in r for r in rows)          # no document text in the log


def test_document_recall_is_character_weighted(tmp_path):
    docs = [_sample("two", FIX / "scanned_cht.pdf", [1, 2])]
    gt = {"two": {1: ANSWER, 2: ANSWER + " extra words that the fake never says at all"}}
    with FakeVlmServer({"default": "ok"}) as url:
        res = V.measure(docs, FIX / "scanned_en.pdf", url, out=tmp_path, gt_texts=gt, sampler_factory=FixedSampler)
    rows = [json.loads(x) for x in (tmp_path / "vlm.jsonl").read_text(encoding="utf-8").splitlines()]
    p = [r for r in rows if r["kind"] == "page"]
    want = sum(r["common"] for r in p) / sum(r["gt_len"] for r in p)
    assert res["per_doc"]["two"]["recall"] == pytest.approx(want)
    assert res["per_doc"]["two"]["recall"] != pytest.approx((p[0]["recall"] + p[1]["recall"]) / 2)


def test_length_pages_are_counted_not_retried(tmp_path):
    srv = FakeVlmServer({"default": "ok", "rules": [{"match": {"request_no": 2}, "behavior": "length"}]})
    url = srv.start()
    try:
        res = _measure(tmp_path, url)
    finally:
        srv.stop()
    assert res["per_doc"]["two"]["length_pages"] == 1 and res["per_doc"]["one"]["length_pages"] == 0
    assert len(srv.calls) == 4
    assert res["per_doc"]["two"]["recall"] < 1.0                # the partial answer is scored as it came


def test_a_failed_request_leaves_the_document_incomplete(tmp_path):
    with FakeVlmServer({"default": "ok", "rules": [{"match": {"request_no": 3}, "behavior": "server_error"}]}) as url:
        res = _measure(tmp_path, url)
    assert res["per_doc"]["two"]["complete"] is False and res["per_doc"]["one"]["complete"] is True


def test_cover_hit_normalises_apostrophe():
    assert V.cover_hit("…Wouldn’t It Be Dreamy…") is True
    assert V.cover_hit("WOULDNʼT it be dreamy") is True
    assert V.cover_hit("dreamy") is False


LOG = "\n".join(["llama_prepare_model_devices: using device CUDA0 (NVIDIA GeForce RTX 5070 Ti) - 15000 MiB free",
                 "print_info: file type   = BF16", "print_info: n_layer          = 18"]
                + [f"load_tensors: layer {i:3d} assigned to device CUDA0, is_swa = 0" for i in range(19)])


def test_parse_server_log():
    assert V.parse_server_log(LOG) == {"device_cuda": True, "layers_on_gpu": 18, "layers_total": 18}
    cpu = LOG.replace("assigned to device CUDA0", "assigned to device CPU").replace("using device CUDA0", "x")
    assert V.parse_server_log(cpu) == {"device_cuda": False, "layers_on_gpu": 0, "layers_total": 18}


def test_check_pinned(tmp_path, monkeypatch):
    paths = {}
    for k in ("llamacpp_zip", "cudart_zip", "gguf", "mmproj"):
        paths[k] = tmp_path / k
        paths[k].write_bytes(k.encode())
    assert V.check_pinned(paths) == dict.fromkeys(paths, False)
    monkeypatch.setattr(V, "VLM_PINNED", {k: hashlib.sha256(k.encode()).hexdigest() for k in paths})
    assert V.check_pinned(paths) == dict.fromkeys(paths, True)
    assert V.check_pinned({**paths, "gguf": tmp_path / "absent"})["gguf"] is False


def test_vram_sampler_is_used(tmp_path):
    with FakeVlmServer({"default": "ok"}) as url:
        res = _measure(tmp_path, url, sampler_factory=lambda: FixedSampler(1200, 3650))
    assert res["vram"] == {"baseline": 1200, "peak": 3650, "delta": 2450}


class FakeServerProcess:
    """Stands in for a launched llama-server: the baseline must be taken before it starts (spec §5.6)."""

    def __init__(self, url, events):
        self.base_url, self.events, self.log_text = url, events, LOG

    def start(self):
        self.events.append("start")
        return self

    def wait_ready(self, timeout_s=600):
        self.events.append("ready")
        return 1.25

    def stop(self):
        self.events.append("stop")


def test_launched_server_baseline_first_then_load_seconds(tmp_path):
    events: list[str] = []

    class OrderSampler(FixedSampler):
        def __enter__(self):
            events.append("baseline")
            return self

    with FakeVlmServer({"default": "ok"}) as url:
        docs = _two_docs()
        res = V.measure(docs, FIX / "scanned_en.pdf", None, out=tmp_path, gt_texts=_gt(docs),
                        sampler_factory=OrderSampler, server_factory=lambda: FakeServerProcess(url, events))
    assert events == ["baseline", "start", "ready", "stop"]
    assert res["load_seconds"] == 1.25 and res["server"] == {"device_cuda": True, "layers_on_gpu": 18,
                                                             "layers_total": 18}
    assert res["endpoint_host"] == "127.0.0.1"


def test_server_command_is_the_spikes(tmp_path):
    cmd = V.server_command(Path("llama-server.exe"), Path("m.gguf"), Path("mm.gguf"), 8099)
    assert cmd[1:] == ["-m", "m.gguf", "--mmproj", "mm.gguf", "--host", "127.0.0.1", "--port", "8099", "-ngl", "99",
                       "-c", "8192", "--temp", "0", "-np", "1", "--no-ui", "-v"]


def test_data_uri_round_trip():
    png = V.render_page_png(FIX / "scanned_en.pdf", 1)
    uri = V.data_uri(png)
    assert uri.startswith("data:image/png;base64,") and base64.b64decode(uri.split(",", 1)[1]) == png
