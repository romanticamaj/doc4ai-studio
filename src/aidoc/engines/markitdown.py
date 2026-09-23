from __future__ import annotations

from aidoc.engines.base import RunnerEngine
from aidoc.models import ConvertOptions, ProbeResult


class MarkitdownEngine(RunnerEngine):
    name = "markitdown"

    def engine_opts(self, opts: ConvertOptions, probe: ProbeResult, full_page_ocr: bool = False) -> dict:
        return {"allow_online_audio": opts.allow_online_audio}
