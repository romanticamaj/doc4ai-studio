/** Vertical layout of a virtualized list from item heights: no DOM reads, O(log n) lookups. */

export function buildTops(heights: ArrayLike<number>, gap: number): { tops: Float64Array; total: number } {
  const n = heights.length;
  const tops = new Float64Array(n);
  let y = 0;
  for (let i = 0; i < n; i++) {
    tops[i] = y;
    y += heights[i] + (i < n - 1 ? gap : 0);
  }
  return { tops, total: y };
}

/** The last item whose top is at or above `y` (0 when `y` is above everything). */
export function indexAt(tops: ArrayLike<number>, y: number): number {
  let lo = 0;
  let hi = tops.length - 1;
  if (hi < 0) return 0;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (tops[mid] <= y) lo = mid;
    else hi = mid - 1;
  }
  return lo;
}

/** Items overlapping [top, bottom) plus `overscan` on each side, clamped; [0, -1] for an empty list. */
export function rangeIn(tops: ArrayLike<number>, top: number, bottom: number, overscan: number): [number, number] {
  const n = tops.length;
  if (!n) return [0, -1];
  const a = indexAt(tops, top);
  const b = indexAt(tops, Math.max(top, bottom - 1));
  return [Math.max(0, a - overscan), Math.min(n - 1, b + overscan)];
}

/** New scrollTop that keeps the item at the viewport top at the same offset after the layout changed. */
export function keepAnchor(oldTops: ArrayLike<number>, newTops: ArrayLike<number>, scrollTop: number): number {
  if (!oldTops.length || !newTops.length) return scrollTop;
  const i = Math.min(indexAt(oldTops, scrollTop), newTops.length - 1);
  return newTops[i] + (scrollTop - oldTops[i]);
}
