"""Engine registry. DOC4AI_FAKE_ENGINES=1 swaps in the fake engine for every name (index A9)."""
from __future__ import annotations

import os

from doc4ai.config import Doc4aiConfig
from doc4ai.engines.base import RunnerEngine
from doc4ai.engines.docling import DoclingEngine
from doc4ai.engines.markitdown import MarkitdownEngine
from doc4ai.engines.mineru import MineruEngine
from doc4ai.engines.vlm import VlmEngine
from doc4ai.models import ENGINE_NAMES

_CLASSES: dict[str, type[RunnerEngine]] = {"markitdown": MarkitdownEngine, "docling": DoclingEngine,
                                           "mineru": MineruEngine, "vlm": VlmEngine}


def get_engines(config: Doc4aiConfig) -> dict[str, RunnerEngine]:
    if os.environ.get("DOC4AI_FAKE_ENGINES") == "1":
        from doc4ai.engines.fake import FakeEngine
        # test hook: DOC4AI_FAKE_ENGINES_EXCEPT=vlm keeps the real engine for the listed names (VLM plan Task 12:
        # the real vlm_runner.py against the fake OpenAI-compatible endpoint, every other engine faked)
        real = {n.strip() for n in os.environ.get("DOC4AI_FAKE_ENGINES_EXCEPT", "").split(",") if n.strip()}
        return {n: (_CLASSES[n](config) if n in real else FakeEngine(n, config)) for n in ENGINE_NAMES}
    return {n: _CLASSES[n](config) for n in ENGINE_NAMES}
