import { expect, test } from "vitest";
import { filtersFromParams, paramsFromFilters } from "@/features/mcp/callFilters";

test("round trip keeps tab and drops empty values", () => {
  const p = new URLSearchParams("tab=calls&token=t1&tool=read_document&status=ok");
  const f = filtersFromParams(p);
  expect(f).toEqual({ token_id: "t1", tool: "read_document", status: "ok" });
  const back = paramsFromFilters({ ...f, client_id: "", since: "100" }, p);
  expect(back.toString()).toBe("tab=calls&token=t1&tool=read_document&status=ok&since=100");
});

test("the method filter (compat/docling) survives the URL round trip and filters live rows", async () => {
  const { matchesFilters } = await import("@/features/mcp/mergeCalls");
  const f = filtersFromParams(new URLSearchParams("tab=calls&method=compat%2Fdocling"));
  expect(f).toEqual({ method: "compat/docling" });
  expect(paramsFromFilters(f, new URLSearchParams("tab=calls")).get("method")).toBe("compat/docling");
  const row = { method: "tools/call" } as Parameters<typeof matchesFilters>[0];
  expect(matchesFilters(row, f)).toBe(false);
  expect(matchesFilters({ ...row, method: "compat/docling" }, f)).toBe(true);
});
