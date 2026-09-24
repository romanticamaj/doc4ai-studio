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
 * Keeps two scroll containers on the same page. Each side marks its pages with `[data-page]`. Scrolling one side
 * moves the other to the same page and the same fraction of that page. The side that was moved programmatically
 * ignores its own scroll events for 150 ms, so the two panes never echo each other.
 * `enabled` is false when either side has no page anchors (e.g. a .docx result without page markers).
 */
export function useScrollSync(
  left: React.RefObject<HTMLElement | null>,
  right: React.RefObject<HTMLElement | null>,
  opts: { active: boolean; deps?: unknown[] },
) {
  const [enabled, setEnabled] = useState(false);
  const [page, setPage] = useState<number | null>(null);
  const lock = useRef<{ side: "left" | "right"; until: number } | null>(null);
  const frame = useRef(0);

  const recheck = useCallback(() => {
    const l = left.current;
    const r = right.current;
    setEnabled(!!l && !!r && measure(l).length > 0 && measure(r).length > 0);
  }, [left, right]);

  useEffect(() => {
    recheck();
    const t = setTimeout(recheck, 500);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [recheck, ...(opts.deps ?? [])]);

  useEffect(() => {
    const l = left.current;
    const r = right.current;
    if (!l || !r) return;
    const onScroll = (from: "left" | "right") => () => {
      const src = from === "left" ? l : r;
      const dst = from === "left" ? r : l;
      const lk = lock.current;
      if (lk && lk.side === from && performance.now() < lk.until) return; // our own programmatic scroll
      cancelAnimationFrame(frame.current);
      frame.current = requestAnimationFrame(() => {
        const at = locate(measure(src), src.scrollTop, src.scrollHeight);
        setPage(at?.page ?? null);
        if (!opts.active || !at) return;
        const top = scrollTopFor(measure(dst), at.page, at.fraction, dst.scrollHeight);
        if (top == null || Math.abs(dst.scrollTop - top) < 2) return;
        lock.current = { side: from === "left" ? "right" : "left", until: performance.now() + 150 };
        dst.scrollTop = top;
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
  }, [left, right, opts.active, enabled, ...(opts.deps ?? [])]);

  return { enabled, page, recheck };
}
