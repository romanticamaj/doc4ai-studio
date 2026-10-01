import { expect, test } from "vitest";
import { parseFilters, toQuery, toSearch } from "../filters";

test("round trips filters through the URL", () => {
  const f = parseFilters("?q=report&engine=mineru&view=table");
  expect(f).toEqual({ q: "report", engine: "mineru", status: "", flag: "", view: "table" });
  expect(toSearch(f)).toBe("?q=report&engine=mineru&view=table");
  expect(toQuery(f)).toEqual({ q: "report", engine: "mineru" });
  expect(parseFilters("")).toEqual({ q: "", engine: "", status: "", flag: "", view: "cards" });
});

test("unknown values fall back to defaults; default view is not written to the URL", () => {
  expect(parseFilters("?status=bogus&view=grid&engine=nope")).toEqual({ q: "", engine: "", status: "", flag: "", view: "cards" });
  expect(toSearch({ q: "", engine: "", status: "low", flag: "", view: "cards" })).toBe("?status=low");
  expect(toSearch({ q: "", engine: "", status: "", flag: "", view: "cards" })).toBe("");
});
