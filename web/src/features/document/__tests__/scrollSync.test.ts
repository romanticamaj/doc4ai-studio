import { expect, test } from "vitest";
import { locate, nearestPage, scrollTopFor } from "../useScrollSync";

test("nearest page picks the anchor at or above the viewport top", () => {
  const anchors = [{ page: 1, top: 0 }, { page: 2, top: 500 }, { page: 3, top: 1200 }];
  expect(nearestPage(anchors, 0)).toBe(1);
  expect(nearestPage(anchors, 520)).toBe(2);
  expect(nearestPage(anchors, 5000)).toBe(3);
  expect(nearestPage([], 100)).toBeNull();
});

test("position inside a page maps proportionally to the other pane", () => {
  const md = [{ page: 1, top: 0 }, { page: 2, top: 1000 }, { page: 3, top: 1400 }];
  const pdf = [{ page: 1, top: 0 }, { page: 2, top: 800 }, { page: 3, top: 1600 }];
  const at = locate(md, 1200, 2000)!; // halfway through page 2 in the markdown
  expect(at.page).toBe(2);
  expect(at.fraction).toBeCloseTo(0.5);
  expect(scrollTopFor(pdf, at.page, at.fraction, 2400)).toBeCloseTo(1200);
  expect(scrollTopFor(pdf, 9, 0, 2400)).toBeNull(); // page the other side does not have
  expect(locate([], 10, 100)).toBeNull();
});

test("lookups on large anchor tables match a linear scan", async () => {
  const { AnchorTable } = await import("../useScrollSync");
  const anchors = Array.from({ length: 1200 }, (_, i) => ({ page: i + 1, top: i * 1000 + (i % 7) }));
  const t = new AnchorTable(anchors, 1_300_000);
  for (const y of [0, 5, 999, 1000, 1001, 523_456, 1_199_010, 1_250_000]) {
    expect(t.locate(y)).toEqual(locate(anchors, y, 1_300_000));
  }
  expect(t.scrollTopFor(600, 0.5)).toBeCloseTo(scrollTopFor(anchors, 600, 0.5, 1_300_000)!);
  expect(t.scrollTopFor(5000, 0)).toBeNull();
  expect(new AnchorTable([], 10).locate(5)).toBeNull();
});

test("anchors are sorted by position even when pages are out of order", async () => {
  const { AnchorTable } = await import("../useScrollSync");
  const t = new AnchorTable([{ page: 3, top: 500 }, { page: 1, top: 0 }, { page: 2, top: 200 }], 1000);
  expect(t.locate(250)!.page).toBe(2);
  expect(t.locate(250)!.fraction).toBeCloseTo(50 / 300);
});
