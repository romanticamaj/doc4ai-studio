"""Per-page OCR repair of broken text layers (spec 2026-10-01 §8.2).

Flagged pages are extracted into one small PDF, converted with a repair engine configuration that was measured to
work on T-001 (Docling pypdfium backend + FULL_PAGE OCR, or MinerU `ocr_mode="ocr"`), re-checked page by page and
spliced back between their page markers. Repair never fails the task: engine errors leave pages unrepaired.
"""
from __future__ import annotations

import os
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
    vlm: dict | None = None            # the VLM runner's usage block when VLM repair ran (spec 2026-10-05 VLM §8.1)


def repair_order(main_engine: str, available: dict[str, bool], *, engines: dict | None = None,
                 allow_vlm: bool = True) -> list[tuple[str, dict, str]]:
    """(engine, engine_opts override, label): the main engine's own style first (spec §4 decision 4), then the VLM
    last (spec 2026-10-05 VLM §6, index A21). A VLM main engine gets no repair: there is no text layer to be wrong."""
    if main_engine == "vlm":
        return []
    order = [_MINERU, _DOCLING] if main_engine == "mineru" else [_DOCLING, _MINERU]
    out = [(e, dict(o), lbl) for e, o, lbl in order if available.get(e)]
    if allow_vlm and available.get("vlm") and engines and "vlm" in engines:
        out.append(("vlm", {}, engines["vlm"].label()))
    only = os.environ.get("AIDOC_REPAIR_ONLY")         # TEST HOOK (slow VLM tests): keep only these engines
    if only:
        keep = {n.strip() for n in only.split(",") if n.strip()}
        out = [o for o in out if o[0] in keep]
    return out


def accept_repaired_section(section: str, label: str, failed: dict[int, str], page: int, min_chars: int = 1) -> bool:
    """A repaired page section is accepted when it has text (at least `min_chars`: a dropped page needs a real
    section, spec 2026-10-05 verify §2.3) and none of the broken-text signals (spec 2026-10-01 §8.2 step 5); a VLM
    section also must not be a refusal / truncation / "blank" answer (VLM spec §6)."""
    sig = page_signals(section)
    if sig["chars"] < max(1, min_chars) or flag_page(sig, broken_font=True):
        return False
    return not (label.startswith("vlm:") and (page in failed or "<!-- blank -->" in section))


def _merge_vlm(acc: dict | None, block: dict | None) -> dict | None:
    if not block:
        return acc
    if acc is None:
        return {**block, "usage": dict(block.get("usage") or {})}
    acc["pages_sent"] = acc.get("pages_sent", 0) + int(block.get("pages_sent") or 0)
    u, b = acc.setdefault("usage", {}), block.get("usage") or {}
    for key in ("requests", "prompt_tokens", "completion_tokens"):
        if b.get(key) is not None:
            u[key] = (u.get(key) or 0) + int(b[key])
    for key in ("truncated_pages", "refused_pages"):
        acc[key] = sorted(set(acc.get(key) or []) | set(block.get(key) or []))
    return acc


def _asset_page(name: str) -> int | None:
    m = re.match(r"p(\d+)_\d+", name)
    return int(m.group(1)) if m else None


def repair_pages(ctx, engines: dict, norm: NormalizedResult, pages: list[int], main_engine: str,
                 min_chars: dict[int, int] | None = None) -> RepairOutcome:
    """Repair `pages` (1-based, of ctx.work_src) of `norm`. EngineCancelled propagates; everything else is logged.
    `min_chars`: per page, the non-space characters a repaired section needs (dropped pages)."""
    min_chars = min_chars or {}
    remaining = sorted(set(pages))
    accepted: dict[int, tuple[str, list[tuple[Path, str]], str]] = {}
    log: list[str] = []
    vlm_cfg = getattr(getattr(ctx, "config", None), "vlm", None)
    allow_vlm = bool(getattr(vlm_cfg, "use_for_repair", True))
    order = repair_order(main_engine, {n: e.available() for n, e in engines.items()}, engines=engines,
                         allow_vlm=allow_vlm)
    vlm_block: dict | None = None
    rdir = ctx.work_dir / "repair"
    fsops.remove_tree(rdir)
    for k, (name, override, label) in enumerate(order):
        if not remaining:
            break
        if ctx.cancelled():
            raise EngineCancelled("cancelled")
        engine = engines[name]
        wd = rdir / f"{k}_{name}"
        if name == "vlm":
            why = _vlm_repair_veto(ctx, engine, len(remaining))
            if why:
                log.append(f"repair: vlm skipped ({why})")
                ctx.log(log[-1])
                continue
            if engine.is_remote():
                ctx.log(f"vlm(openai): sending {len(remaining)} page image(s) to {engine.endpoint_host()} "
                        f"model={engine.model_name()}")
        try:
            pdf = extract_pages(ctx.work_src, remaining, wd / "pages.pdf")
            eo = {**engine.engine_opts(ctx.opts, ctx.probe), **override}
            timeout = compute_timeout(len(remaining), pdf.stat().st_size, ctx.opts, ctx.config.limits)
            if hasattr(engine, "timeout_factor") and not ctx.opts.timeout_s:   # like the main conversion
                timeout = engine.timeout_factor(len(remaining), timeout)
            session = engine.open_session(ctx.opts, ctx.probe, engine_opts=eo)
            ctx.log(f"repair: {label} on {len(remaining)} page(s) pid={session.pid}")
            try:
                def on_line(_f, line, _ctx=ctx):
                    if line and not (hasattr(_ctx, "vlm_usage_line") and _ctx.vlm_usage_line(line)):
                        _ctx.log(line)
                raw = session.convert(pdf, wd, None, on_line,
                                      timeout_s=timeout, segment_idx=REPAIR_SEG_IDX, cancel=ctx.cancel)
            finally:
                session.close()
            if name == "vlm" and raw.extra.get("vlm"):
                block = dict(raw.extra["vlm"])
                vlm_block = _merge_vlm(vlm_block, block)
                if hasattr(ctx, "record_vlm_usage"):
                    ctx.record_vlm_usage(block)
            fixed = normalize(raw, 0, REPAIR_SEG_IDX, page_numbers=remaining)
        except EngineCancelled:
            raise
        except Exception as e:  # noqa: BLE001  any repair failure only leaves pages unrepaired (spec §8.2 step 8)
            msg = getattr(e, "message", None) or f"{type(e).__name__}: {e}"
            log.append(f"repair with {label} failed: {msg[:300]}")
            ctx.log(log[-1])
            continue
        _, secs = split_pages(fixed.markdown)
        # the runner's failed pages are relative to pages.pdf: page k of it is remaining[k - 1]
        failed = {remaining[rk - 1]: msg for rk, msg in raw.failed_pages.items() if 0 < rk <= len(remaining)}
        still = []
        for p in remaining:
            section = secs.get(p, "")
            if accept_repaired_section(section, label, failed, p, min_chars.get(p, 1)):
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
                         unrepaired=remaining, log=log, vlm=vlm_block)


def _vlm_repair_veto(ctx, engine, n_pages: int) -> str | None:
    """Why the VLM may not repair these pages now: the run's --no-vlm-remote, or the daily page budget."""
    if engine.is_remote() and not engine.remote_allowed(ctx.opts):
        return "disabled_remote"
    budget_ok = getattr(ctx, "vlm_budget_ok", None)
    if budget_ok is not None and not budget_ok(n_pages):
        return "budget"
    return None
