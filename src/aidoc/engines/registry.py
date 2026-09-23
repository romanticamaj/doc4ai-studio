"""Engine registry. AIDOC_FAKE_ENGINES=1 swaps in the fake engine for all three names (index A9)."""
from __future__ import annotations

import os

from aidoc.config import AidocConfig
from aidoc.engines.base import RunnerEngine
from aidoc.engines.docling import DoclingEngine
from aidoc.engines.markitdown import MarkitdownEngine
from aidoc.engines.mineru import MineruEngine
from aidoc.models import ENGINE_NAMES

_CLASSES: dict[str, type[RunnerEngine]] = {"markitdown": MarkitdownEngine, "docling": DoclingEngine,
                                           "mineru": MineruEngine}


def get_engines(config: AidocConfig) -> dict[str, RunnerEngine]:
    if os.environ.get("AIDOC_FAKE_ENGINES") == "1":
        from aidoc.engines.fake import FakeEngine
        return {n: FakeEngine(n, config) for n in ENGINE_NAMES}
    return {n: _CLASSES[n](config) for n in ENGINE_NAMES}
