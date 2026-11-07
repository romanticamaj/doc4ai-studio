from __future__ import annotations

from doc4ai.engines.base import RunnerEngine
from doc4ai.models import ConvertOptions, ProbeResult


class MineruEngine(RunnerEngine):
    name = "mineru"

    def engine_opts(self, opts: ConvertOptions, probe: ProbeResult, full_page_ocr: bool = False) -> dict:
        return {"tier": opts.mineru_tier}

    def oom_downgrade(self, engine_opts: dict) -> dict | None:
        if engine_opts.get("tier") == "standard":
            return {**engine_opts, "tier": "basic"}
        return None
