import { expect, test } from "vitest";
import { pageRatios } from "../pageRatios";

test("every page keeps its own aspect ratio (mixed portrait/landscape, M5)", async () => {
  const sizes = [
    [612, 792],
    [792, 612],
    [595, 842],
  ];
  const doc = {
    numPages: 3,
    getPage: async (n: number) => ({ getViewport: () => ({ width: sizes[n - 1][0], height: sizes[n - 1][1] }) }),
  };
  const r = await pageRatios(doc as any);
  expect(r.map((x) => x.toFixed(3))).toEqual(["1.294", "0.773", "1.415"]);
});
