import { expect, test } from "vitest";
import { buildTops, indexAt, keepAnchor, rangeIn } from "../layout";

test("tops are cumulative heights plus gaps; total has no trailing gap", () => {
  const { tops, total } = buildTops([100, 200, 50], 10);
  expect([...tops]).toEqual([0, 110, 320]);
  expect(total).toBe(370);
  expect(buildTops([], 10).total).toBe(0);
});

test("indexAt finds the item under a y coordinate by binary search", () => {
  const { tops } = buildTops([100, 200, 50], 10);
  expect(indexAt(tops, -5)).toBe(0);
  expect(indexAt(tops, 0)).toBe(0);
  expect(indexAt(tops, 109)).toBe(0);
  expect(indexAt(tops, 110)).toBe(1);
  expect(indexAt(tops, 319)).toBe(1);
  expect(indexAt(tops, 5000)).toBe(2);
  const big = buildTops(new Array(100_000).fill(10), 0).tops;
  expect(indexAt(big, 123_456)).toBe(12_345);
});

test("rangeIn gives the visible items plus overscan, clamped", () => {
  const { tops } = buildTops(new Array(100).fill(100), 0);
  expect(rangeIn(tops, 1000, 1250, 0)).toEqual([10, 12]);
  expect(rangeIn(tops, 1000, 1250, 2)).toEqual([8, 14]);
  expect(rangeIn(tops, 0, 50, 3)).toEqual([0, 3]);
  expect(rangeIn(tops, 9950, 10_500, 3)).toEqual([96, 99]);
  expect(rangeIn(new Float64Array(0), 0, 100, 2)).toEqual([0, -1]);
});

test("keepAnchor keeps the item at the viewport top in place when sizes above it change", () => {
  const before = buildTops([100, 100, 100, 100], 10).tops; // 0 110 220 330
  const after = buildTops([300, 100, 100, 100], 10).tops; // 0 310 420 530
  expect(keepAnchor(before, after, 240)).toBe(440); // 20px into item 2 stays 20px into item 2
  expect(keepAnchor(before, after, 50)).toBe(50); // the item that changed is the one at the top: keep its offset
});
