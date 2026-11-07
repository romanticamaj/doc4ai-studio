from __future__ import annotations

from doc4ai.engines.base import RunnerEngine
from doc4ai.models import ConvertOptions, ProbeResult


class MarkitdownEngine(RunnerEngine):
    name = "markitdown"

    def engine_opts(self, opts: ConvertOptions, probe: ProbeResult, full_page_ocr: bool = False) -> dict:
        return {"allow_online_audio": opts.allow_online_audio}
