import { expect, test } from "vitest";
import { estimateRatios, firstRatios } from "../pageRatios";

test("only the first few pages are loaded to start (no getPage for the whole document)", async () => {
  const sizes = [
    [612, 792],
    [792, 612],
    [595, 842],
  ];
  const asked: number[] = [];
  const doc = {
    numPages: 1192,
    getPage: async (n: number) => {
      asked.push(n);
      const s = sizes[(n - 1) % 3];
      return { getViewport: () => ({ width: s[0], height: s[1] }) };
    },
  };
  const known = await firstRatios(doc, 3);
  expect(asked.sort()).toEqual([1, 2, 3]);
  expect([...known.entries()].map(([k, r]) => [k, r.toFixed(3)])).toEqual([
    [1, "1.294"],
    [2, "0.773"],
    [3, "1.415"],
  ]);
});

test("pages not loaded yet take the ratio of the nearest loaded page before them (M5 mixed sizes)", () => {
  const known = new Map([
    [1, 1.5],
    [2, 1.3],
    [5, 0.7],
  ]);
  expect(estimateRatios(7, known)).toEqual([1.5, 1.3, 1.3, 1.3, 0.7, 0.7, 0.7]);
  expect(estimateRatios(3, new Map([[2, 0.5]]))).toEqual([0.5, 0.5, 0.5]);
  expect(estimateRatios(2, new Map())).toEqual([1.4142, 1.4142]);
});
