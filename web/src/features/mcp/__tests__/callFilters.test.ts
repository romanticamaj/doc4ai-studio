import { expect, test } from "vitest";
import { filtersFromParams, paramsFromFilters } from "@/features/mcp/callFilters";

test("round trip keeps tab and drops empty values", () => {
  const p = new URLSearchParams("tab=calls&token=t1&tool=read_document&status=ok");
  const f = filtersFromParams(p);
  expect(f).toEqual({ token_id: "t1", tool: "read_document", status: "ok" });
  const back = paramsFromFilters({ ...f, client_id: "", since: "100" }, p);
  expect(back.toString()).toBe("tab=calls&token=t1&tool=read_document&status=ok&since=100");
});
