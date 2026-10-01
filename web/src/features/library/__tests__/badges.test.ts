import { expect, test } from "vitest";
import { docBadges } from "../badges";
import { parseFilters, toQuery, toSearch } from "../filters";

const base = { flags: [], page_summary: null } as never;

test("badges follow server flags", () => {
  expect(docBadges(base)).toEqual([]);
  expect(docBadges({ flags: ["page_map_incomplete"], page_summary: null } as never)).toEqual([
    { key: "page_map_incomplete", label: "頁碼不完整", tone: "danger" },
  ]);
  expect(
    docBadges({ flags: ["page_quality"], page_summary: { expected: 532, found: 532, coverage: 1, flagged: 55, unrepaired: 55 } } as never),
  ).toEqual([{ key: "page_quality", label: "55 頁有問題", tone: "warn" }]);
  expect(docBadges({ flags: ["unassessed"], page_summary: null } as never)).toEqual([]);
});

test("filters accept warn and flag", () => {
  const f = parseFilters("?status=warn&flag=page_quality");
  expect(f.status).toBe("warn");
  expect(f.flag).toBe("page_quality");
  expect(toSearch(f)).toBe("?status=warn&flag=page_quality");
  expect(toQuery(f)).toEqual({ status: "warn", flag: "page_quality" });
  expect(parseFilters("?flag=bogus").flag).toBe("");
});
