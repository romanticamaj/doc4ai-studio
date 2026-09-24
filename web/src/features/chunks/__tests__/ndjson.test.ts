import { expect, test } from "vitest";
import { pageLabel, parseNdjson } from "../ndjson";

test("parses ndjson ignoring blank lines", () => {
  expect(parseNdjson('{"id":"a#0000","text":"x"}\n\n{"id":"a#0001","text":"y"}\n')).toHaveLength(2);
});

test("page labels", () => {
  expect(pageLabel({ page_start: 3, page_end: 3 } as any)).toBe("p. 3");
  expect(pageLabel({ page_start: 3, page_end: 5 } as any)).toBe("p. 3–5");
  expect(pageLabel({ page_start: null, page_end: null } as any)).toBe("無頁碼");
});
