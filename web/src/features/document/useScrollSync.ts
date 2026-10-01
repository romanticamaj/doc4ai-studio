import { useCallback, useEffect, useRef, useState } from "react";

export type Anchor = { page: number; top: number };

/** The anchor at or above the viewport top (8px slack); the first anchor when the view is above all of them. */
export function nearestPage(anchors: Anchor[], scrollTop: number): number | null {
  if (!anchors.length) return null;
  let found = anchors[0];
  for (const a of anchors) {
    if (a.top <= scrollTop + 8) found = a;
    else break;
  }
  return found.page;
}

/** Where the viewport top is: page + fraction (0..1) of the way to the next anchor (or the content end). */
export function locate(anchors: Anchor[], scrollTop: number, scrollHeight: number): { page: number; fraction: number } | null {
  if (!anchors.length) return null;
  let i = 0;
  for (let k = 0; k < anchors.length; k++) {
    if (anchors[k].top <= scrollTop + 8) i = k;
    else break;
  }
  const start = anchors[i].top;
  const end = i + 1 < anchors.length ? anchors[i + 1].top : scrollHeight;
  const span = end - start;
  const fraction = span > 0 ? Math.max(0, Math.min(1, (scrollTop - start) / span)) : 0;
  return { page: anchors[i].page, fraction };
}

export function scrollTopFor(anchors: Anchor[], page: number, fraction: number, scrollHeight: number): number | null {
  const i = anchors.findIndex((a) => a.page === page);
  if (i < 0) return null;
  const start = anchors[i].top;
  const end = i + 1 < anchors.length ? anchors[i + 1].top : scrollHeight;
  return start + fraction * Math.max(0, end - start);
}

/** Anchors sorted by position with O(log n) lookups (binary search) and O(1) page → index; built once per layout. */
export class AnchorTable {
  readonly anchors: Anchor[];
  private tops: Float64Array;
  private byPage = new Map<number, number>();

  readonly scrollHeight: number;

  constructor(anchors: Anchor[], scrollHeight: number) {
    this.scrollHeight = scrollHeight;
    this.anchors = [...anchors].sort((a, b) => a.top - b.top);
    this.tops = Float64Array.from(this.anchors, (a) => a.top);
    this.anchors.forEach((a, i) => {
      if (!this.byPage.has(a.page)) this.byPage.set(a.page, i);
    });
  }

  get size() {
    return this.anchors.length;
  }

  private indexAt(y: number): number {
    let lo = 0;
    let hi = this.tops.length - 1;
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1;
      if (this.tops[mid] <= y) lo = mid;
      else hi = mid - 1;
    }
    return lo;
  }

  locate(scrollTop: number): { page: number; fraction: number } | null {
    if (!this.anchors.length) return null;
    const i = this.indexAt(scrollTop + 8);
    const start = this.tops[i];
    const end = i + 1 < this.tops.length ? this.tops[i + 1] : this.scrollHeight;
    const span = end - start;
    const fraction = span > 0 ? Math.max(0, Math.min(1, (scrollTop - start) / span)) : 0;
    return { page: this.anchors[i].page, fraction };
  }

  scrollTopFor(page: number, fraction: number): number | null {
    const i = this.byPage.get(page);
    if (i == null) return null;
    const start = this.tops[i];
    const end = i + 1 < this.tops.length ? this.tops[i + 1] : this.scrollHeight;
    return start + fraction * Math.max(0, end - start);
  }
}

/** `[data-page]` elements inside a scroll container, with their offset from the container's content top. */
export function measure(container: HTMLElement): Anchor[] {
  const base = container.getBoundingClientRect().top - container.scrollTop;
  const out: Anchor[] = [];
  container.querySelectorAll<HTMLElement>("[data-page]").forEach((el) => {
    const page = Number(el.dataset.page);
    if (Number.isFinite(page)) out.push({ page, top: el.getBoundingClientRect().top - base });
  });
  out.sort((a, b) => a.top - b.top);
  return out;
}

/**
 * A virtualized pane cannot be measured from the DOM (most pages are not mounted), so it publishes its anchors:
 * it sets `anchorSource` on its scroll container and dispatches ANCHORS_EVENT on it whenever its layout changed.
 * Panes without a source (e.g. the image preview) are measured from their `[data-page]` elements instead.
 */
export const ANCHORS_EVENT = "aidoc:anchors";
export type AnchorSource = () => Anchor[];
type WithSource = HTMLElement & { anchorSource?: AnchorSource };

export function publishAnchors(el: HTMLElement | null, source: AnchorSource | undefined) {
  if (!el) return;
  (el as WithSource).anchorSource = source;
  el.dispatchEvent(new Event(ANCHORS_EVENT));
}

function anchorsOf(el: HTMLElement): Anchor[] {
  const src = (el as WithSource).anchorSource;
  return src ? src() : measure(el);
}

/**
 * Keeps two scroll containers on the same page. Scrolling one side moves the other to the same page and the same
 * fraction of that page. The side that was moved programmatically ignores its own scroll events for 150 ms, so the
 * two panes never echo each other. Anchor tables are cached and rebuilt only when a pane reports a layout change
 * (ANCHORS_EVENT) or is resized; a scroll frame does two binary searches and no layout reads.
 * `enabled` is false when either side has no page anchors (e.g. a .docx result without page markers).
 */
export function useScrollSync(
  left: React.RefObject<HTMLElement | null>,
  right: React.RefObject<HTMLElement | null>,
  opts: { active: boolean; deps?: unknown[] },
) {
  const [enabled, setEnabled] = useState(false);
  const [page, setPage] = useState<number | null>(null);
  // per side: ignore that pane's scroll events until this time (it was moved programmatically)
  const lock = useRef<{ left: number; right: number }>({ left: 0, right: 0 });
  // 「跳至頁」: keep both panes on this page while their layouts settle (lazy pages / blocks change heights)
  const goal = useRef<{ page: number; until: number } | null>(null);
  const frame = useRef(0);
  const tables = useRef<{ left: AnchorTable | null; right: AnchorTable | null }>({ left: null, right: null });
  // the side the user scrolled last: when a pane's layout settles (a block rendered, a page got its real size)
  // shortly after, the other side is lined up again
  const leader = useRef<{ side: "left" | "right"; at: number } | null>(null);
  const active = useRef(opts.active);
  active.current = opts.active;

  const table = useCallback(
    (side: "left" | "right"): AnchorTable | null => {
      const el = side === "left" ? left.current : right.current;
      if (!el) return null;
      let t = tables.current[side];
      if (!t || t.scrollHeight !== el.scrollHeight) {
        t = new AnchorTable(anchorsOf(el), el.scrollHeight);
        tables.current[side] = t;
      }
      return t;
    },
    [left, right],
  );

  /** Moves the other pane to where `from` is; returns the page `from` is on. */
  const follow = useCallback(
    (from: "left" | "right") => {
      const src = from === "left" ? left.current : right.current;
      const dst = from === "left" ? right.current : left.current;
      if (!src || !dst) return null;
      const at = table(from)?.locate(src.scrollTop) ?? null;
      if (!active.current || !at) return at;
      const top = table(from === "left" ? "right" : "left")?.scrollTopFor(at.page, at.fraction) ?? null;
      if (top == null || Math.abs(dst.scrollTop - top) < 2) return at;
      lock.current[from === "left" ? "right" : "left"] = performance.now() + 150;
      dst.scrollTop = top;
      return at;
    },
    [left, right, table],
  );

  /** Scrolls both panes to the top of the goal page; false when there is no live goal. */
  const applyGoal = useCallback(() => {
    const g = goal.current;
    if (!g || performance.now() > g.until) {
      goal.current = null;
      return false;
    }
    for (const side of ["left", "right"] as const) {
      const el = side === "left" ? left.current : right.current;
      const top = el ? table(side)?.scrollTopFor(g.page, 0) : null;
      if (!el || top == null || Math.abs(el.scrollTop - top) < 2) continue;
      lock.current[side] = performance.now() + 150;
      el.scrollTop = top;
    }
    return true;
  }, [left, right, table]);

  /** Both panes to the start of `page` (spec 2026-10-01 §9.4 「跳至頁」); re-applied while layouts settle. */
  const goTo = useCallback(
    (page: number) => {
      goal.current = { page, until: performance.now() + 2500 };
      leader.current = null;
      tables.current = { left: null, right: null };
      applyGoal();
      setPage(page);
    },
    [applyGoal],
  );

  const recheck = useCallback(() => {
    tables.current = { left: null, right: null };
    const l = table("left");
    const r = table("right");
    setEnabled(!!l && !!r && l.size > 0 && r.size > 0);
  }, [table]);

  useEffect(() => {
    recheck();
    const t = setTimeout(recheck, 500);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [recheck, ...(opts.deps ?? [])]);

  // invalidate the cached tables when a pane's layout changes; recheck `enabled` at most once a frame
  useEffect(() => {
    const els = [left.current, right.current].filter((e): e is HTMLElement => !!e);
    if (!els.length) return;
    let raf = 0;
    const invalidate = (side?: "left" | "right") => {
      if (side) tables.current[side] = null;
      else tables.current = { left: null, right: null };
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => {
        const l = table("left");
        const r = table("right");
        setEnabled(!!l && !!r && l.size > 0 && r.size > 0);
        if (applyGoal()) return;
        const lead = leader.current;
        if (lead && performance.now() - lead.at < 1500) follow(lead.side);
      });
    };
    const onL = () => invalidate("left");
    const onR = () => invalidate("right");
    left.current?.addEventListener(ANCHORS_EVENT, onL);
    right.current?.addEventListener(ANCHORS_EVENT, onR);
    const ro = new ResizeObserver(() => invalidate());
    for (const el of els) {
      ro.observe(el);
      if (!(el as WithSource).anchorSource && el.firstElementChild) ro.observe(el.firstElementChild);
    }
    return () => {
      left.current?.removeEventListener(ANCHORS_EVENT, onL);
      right.current?.removeEventListener(ANCHORS_EVENT, onR);
      ro.disconnect();
      cancelAnimationFrame(raf);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [left, right, table, follow, applyGoal, ...(opts.deps ?? [])]);

  useEffect(() => {
    const l = left.current;
    const r = right.current;
    if (!l || !r) return;
    const onScroll = (from: "left" | "right") => () => {
      if (performance.now() < lock.current[from]) return; // our own programmatic scroll
      goal.current = null;                                  // the user scrolls: a pending 「跳至頁」 is over
      leader.current = { side: from, at: performance.now() };
      cancelAnimationFrame(frame.current);
      frame.current = requestAnimationFrame(() => {
        const at = follow(from);
        setPage(at?.page ?? null);
      });
    };
    const onL = onScroll("left");
    const onR = onScroll("right");
    l.addEventListener("scroll", onL, { passive: true });
    r.addEventListener("scroll", onR, { passive: true });
    return () => {
      l.removeEventListener("scroll", onL);
      r.removeEventListener("scroll", onR);
      cancelAnimationFrame(frame.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [left, right, follow, opts.active, enabled, ...(opts.deps ?? [])]);

  return { enabled, page, recheck, goTo };
}
