from __future__ import annotations

from aidoc.engines.base import RunnerEngine
from aidoc.models import ConvertOptions, ProbeResult


def is_scanned(probe: ProbeResult) -> bool:
    return probe.kind == "image" or (probe.kind == "pdf" and (probe.text_ratio < 0.5 or probe.image_cover > 0.6))


class DoclingEngine(RunnerEngine):
    name = "docling"

    def engine_opts(self, opts: ConvertOptions, probe: ProbeResult, full_page_ocr: bool = False) -> dict:
        return {"ocr": opts.docling_ocr,
                "lang": ["ch_tra", "en"] if opts.lang == "cht" else ["en"],
                "full_page_ocr": bool(full_page_ocr or is_scanned(probe)),
                "page_batch_size": int(self.config.engines.docling_page_batch_size),
                "formula": bool(probe.math_hint)}

    def oom_downgrade(self, engine_opts: dict) -> dict | None:
        size = int(engine_opts.get("page_batch_size", 16))
        if size <= 1:
            return None
        return {**engine_opts, "page_batch_size": max(1, size // 2)}
