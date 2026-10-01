import { expect, test } from "vitest";
import { mdAnchors } from "../mdAnchors";

test("markdown page anchors come from block positions: measured when mounted, estimated otherwise", () => {
  const blocks = [{ pages: [{ page: 1, at: 0 }] }, { pages: [] }, { pages: [{ page: 2, at: 0.5 }, { page: 3, at: 0.9 }] }];
  const tops = [0, 1000, 1500];
  const heights = [1000, 500, 2000];
  // block 2 is mounted: its anchors were measured at 100 and 1900 px into the block
  const measured = [undefined, undefined, [100, 1900]];
  expect(mdAnchors(blocks, tops, heights, measured, 20)).toEqual([
    { page: 1, top: 20 },
    { page: 2, top: 1620 },
    { page: 3, top: 3420 },
  ]);
  expect(mdAnchors(blocks, tops, heights, [], 20)).toEqual([
    { page: 1, top: 20 },
    { page: 2, top: 2520 },
    { page: 3, top: 3320 },
  ]);
  expect(mdAnchors([{ pages: [] }], [0], [10], [], 0)).toEqual([]);
});
