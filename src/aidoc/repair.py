"""Per-page OCR repair of broken text layers (spec 2026-10-01 §8.2).

Flagged pages are extracted into one small PDF, converted with a repair engine configuration that was measured to
work on T-001 (Docling pypdfium backend + FULL_PAGE OCR, or MinerU `ocr_mode="ocr"`), re-checked page by page and
spliced back between their page markers. Repair never fails the task: engine errors leave pages unrepaired.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from aidoc import fsops
from aidoc.engines.base import EngineCancelled
from aidoc.engines.host import compute_timeout
from aidoc.models import NormalizedResult
from aidoc.normalize import normalize
from aidoc.pagemap import splice_pages, split_pages
from aidoc.quality import flag_page, page_signals
from aidoc.segment import extract_pages

REPAIR_SEG_IDX = 900

_DOCLING = ("docling", {"backend": "pypdfium", "full_page_ocr": True}, "docling:pypdfium_full_page_ocr")
_MINERU = ("mineru", {"ocr_mode": "ocr"}, "mineru:ocr")


@dataclass
class RepairOutcome:
    markdown: str
    assets: list[tuple[Path, str]]
    repaired: dict[int, str] = field(default_factory=dict)      # page -> label of the engine that repaired it
    unrepaired: list[int] = field(default_factory=list)
    log: list[str] = field(default_factory=list)


def repair_order(main_engine: str, available: dict[str, bool]) -> list[tuple[str, dict, str]]:
    """(engine, engine_opts override, label): the main engine's own style first (spec §4 decision 4)."""
    order = [_MINERU, _DOCLING] if main_engine == "mineru" else [_DOCLING, _MINERU]
    return [(e, dict(o), lbl) for e, o, lbl in order if available.get(e)]


def _asset_page(name: str) -> int | None:
    m = re.match(r"p(\d+)_\d+", name)
    return int(m.group(1)) if m else None


def repair_pages(ctx, engines: dict, norm: NormalizedResult, pages: list[int], main_engine: str) -> RepairOutcome:
    """Repair `pages` (1-based, of ctx.work_src) of `norm`. EngineCancelled propagates; everything else is logged."""
    remaining = sorted(set(pages))
    accepted: dict[int, tuple[str, list[tuple[Path, str]], str]] = {}
    log: list[str] = []
    order = repair_order(main_engine, {n: e.available() for n, e in engines.items()})
    rdir = ctx.work_dir / "repair"
    fsops.remove_tree(rdir)
    for k, (name, override, label) in enumerate(order):
        if not remaining:
            break
        if ctx.cancelled():
            raise EngineCancelled("cancelled")
        engine = engines[name]
        wd = rdir / f"{k}_{name}"
        try:
            pdf = extract_pages(ctx.work_src, remaining, wd / "pages.pdf")
            eo = {**engine.engine_opts(ctx.opts, ctx.probe), **override}
            timeout = compute_timeout(len(remaining), pdf.stat().st_size, ctx.opts, ctx.config.limits)
            session = engine.open_session(ctx.opts, ctx.probe, engine_opts=eo)
            ctx.log(f"repair: {label} on {len(remaining)} page(s) pid={session.pid}")
            try:
                raw = session.convert(pdf, wd, None, lambda f, line: ctx.log(line) if line else None,
                                      timeout_s=timeout, segment_idx=REPAIR_SEG_IDX, cancel=ctx.cancel)
            finally:
                session.close()
            fixed = normalize(raw, 0, REPAIR_SEG_IDX, page_numbers=remaining)
        except EngineCancelled:
            raise
        except Exception as e:  # noqa: BLE001  any repair failure only leaves pages unrepaired (spec §8.2 step 8)
            msg = getattr(e, "message", None) or f"{type(e).__name__}: {e}"
            log.append(f"repair with {label} failed: {msg[:300]}")
            ctx.log(log[-1])
            continue
        _, secs = split_pages(fixed.markdown)
        still = []
        for p in remaining:
            section = secs.get(p, "")
            sig = page_signals(section)
            if sig["chars"] > 0 and not flag_page(sig, broken_font=True):
                accepted[p] = (section, [a for a in fixed.assets if _asset_page(a[1]) == p], label)
            else:
                still.append(p)
        log.append(f"repair with {label}: {len(remaining) - len(still)}/{len(remaining)} page(s) clean")
        ctx.log(log[-1])
        remaining = still
    md, assets = norm.markdown, list(norm.assets)
    if accepted:
        md = splice_pages(md, {p: sec for p, (sec, _, _) in accepted.items()})
        assets = [a for a in assets if _asset_page(a[1]) not in accepted]
        for _, (_, new_assets, _) in sorted(accepted.items()):
            assets.extend(new_assets)
    return RepairOutcome(markdown=md, assets=assets, repaired={p: v[2] for p, v in accepted.items()},
                         unrepaired=remaining, log=log)
