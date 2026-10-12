"""Engine registry. AIDOC_FAKE_ENGINES=1 swaps in the fake engine for every name (index A9)."""
from __future__ import annotations

import os

from aidoc.config import AidocConfig
from aidoc.engines.base import RunnerEngine
from aidoc.engines.docling import DoclingEngine
from aidoc.engines.markitdown import MarkitdownEngine
from aidoc.engines.mineru import MineruEngine
from aidoc.engines.vlm import VlmEngine
from aidoc.models import ENGINE_NAMES

_CLASSES: dict[str, type[RunnerEngine]] = {"markitdown": MarkitdownEngine, "docling": DoclingEngine,
                                           "mineru": MineruEngine, "vlm": VlmEngine}


def get_engines(config: AidocConfig) -> dict[str, RunnerEngine]:
    if os.environ.get("AIDOC_FAKE_ENGINES") == "1":
        from aidoc.engines.fake import FakeEngine
        # test hook: AIDOC_FAKE_ENGINES_EXCEPT=vlm keeps the real engine for the listed names (VLM plan Task 12:
        # the real vlm_runner.py against the fake OpenAI-compatible endpoint, every other engine faked)
        real = {n.strip() for n in os.environ.get("AIDOC_FAKE_ENGINES_EXCEPT", "").split(",") if n.strip()}
        return {n: (_CLASSES[n](config) if n in real else FakeEngine(n, config)) for n in ENGINE_NAMES}
    return {n: _CLASSES[n](config) for n in ENGINE_NAMES}
