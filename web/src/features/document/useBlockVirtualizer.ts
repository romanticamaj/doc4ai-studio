import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { buildTops, rangeIn } from "./layout";

type Options = {
  count: number;
  /** estimated height (px) of block i at the given content width, before it has been measured */
  estimate: (i: number, width: number) => number;
  scrollRef: React.RefObject<HTMLElement | null>;
  /** the element that holds the blocks (its offset inside the scroll content is read on resize only) */
  bodyRef: React.RefObject<HTMLElement | null>;
  /** extra px above and below the viewport to keep mounted */
  overscan: number;
  /** called after a mounted block was measured (layout is clean at that point) */
  onMeasured?: (i: number, el: HTMLElement) => void;
};

/**
 * Block virtualization in normal flow: every block is either its mounted content or a placeholder of its
 * estimated / last measured height, so the scroll height stays right and the browser's scroll anchoring keeps the
 * view still when a block above it changes size. The mounted range comes from cumulative heights (binary search),
 * heights come from one ResizeObserver; a scroll frame reads only scrollTop / clientHeight.
 * Estimates are calibrated by the measured / estimated ratio of the blocks seen so far.
 */
export function useBlockVirtualizer({ count, estimate, scrollRef, bodyRef, overscan, onMeasured }: Options) {
  const measured = useRef<Map<number, number>>(new Map());
  const ratio = useRef({ measured: 0, estimated: 0 });
  const [width, setWidth] = useState(0);
  const [version, setVersion] = useState(0);
  const [range, setRange] = useState<[number, number]>([0, Math.min(count - 1, 0)]);
  const offset = useRef(0);

  useEffect(() => {
    measured.current = new Map();
    ratio.current = { measured: 0, estimated: 0 };
    setVersion((v) => v + 1);
  }, [count, estimate]);

  const heights = useMemo(() => {
    const r = ratio.current.estimated > 0 ? ratio.current.measured / ratio.current.estimated : 1;
    const out = new Float64Array(count);
    for (let i = 0; i < count; i++) out[i] = measured.current.get(i) ?? Math.max(24, Math.round(estimate(i, width || 600) * r));
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [count, estimate, width, version]);
  const layout = useMemo(() => buildTops(heights, 0), [heights]);
  const layoutRef = useRef(layout);
  layoutRef.current = layout;

  const update = useCallback(() => {
    const sc = scrollRef.current;
    if (!sc || !count) return;
    const top = sc.scrollTop - offset.current;
    const r = rangeIn(layoutRef.current.tops, top - overscan, top + sc.clientHeight + overscan, 0);
    setRange((old) => (old[0] === r[0] && old[1] === r[1] ? old : r));
  }, [scrollRef, count, overscan]);

  useLayoutEffect(update, [update, layout]);

  useEffect(() => {
    const sc = scrollRef.current;
    if (!sc) return;
    let raf = 0;
    const on = () => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(update);
    };
    sc.addEventListener("scroll", on, { passive: true });
    const ro = new ResizeObserver(() => {
      const body = bodyRef.current;
      if (body) {
        offset.current = body.getBoundingClientRect().top - sc.getBoundingClientRect().top + sc.scrollTop;
        setWidth(Math.round(body.clientWidth));
      }
      update();
    });
    ro.observe(sc);
    if (bodyRef.current) ro.observe(bodyRef.current);
    return () => {
      sc.removeEventListener("scroll", on);
      ro.disconnect();
      cancelAnimationFrame(raf);
    };
  }, [scrollRef, bodyRef, update]);

  // one ResizeObserver for the mounted blocks
  const onMeasuredRef = useRef(onMeasured);
  onMeasuredRef.current = onMeasured;
  const estimateRef = useRef(estimate);
  estimateRef.current = estimate;
  const widthRef = useRef(width);
  widthRef.current = width;
  const blockRO = useMemo(
    () =>
      typeof ResizeObserver === "undefined"
        ? null
        : new ResizeObserver((entries) => {
            let changed = false;
            for (const e of entries) {
              const el = e.target as HTMLElement;
              const i = Number(el.dataset.block);
              const h = e.borderBoxSize?.[0]?.blockSize ?? el.offsetHeight;
              if (!Number.isFinite(i) || h <= 0) continue;
              const prev = measured.current.get(i);
              if (prev == null) {
                ratio.current.measured += h;
                ratio.current.estimated += Math.max(24, estimateRef.current(i, widthRef.current || 600));
              }
              onMeasuredRef.current?.(i, el);
              if (prev == null || Math.abs(prev - h) > 0.5) {
                measured.current.set(i, h);
                changed = true;
              }
            }
            if (changed) setVersion((v) => v + 1);
          }),
    [],
  );
  useEffect(() => () => blockRO?.disconnect(), [blockRO]);

  /** ref callback for a mounted block element (it must carry data-block={i}) */
  const measureRef = useCallback(
    (el: HTMLElement | null) => {
      if (!el || !blockRO) return;
      blockRO.observe(el);
      return () => blockRO.unobserve(el);
    },
    [blockRO],
  );

  return { range, heights, tops: layout.tops, total: layout.total, measureRef, version, offset, width };
}
