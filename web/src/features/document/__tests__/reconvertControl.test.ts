import { expect, test } from "vitest";
import { reconvertControl } from "../reconvertControl";

test("reconvert is offered only when a source is left", () => {
  const flagged = { flags: ["page_map_incomplete"] } as never;
  expect(reconvertControl(flagged, true)).toBe("offer");
  expect(reconvertControl(flagged, false)).toBe("source_gone");
  expect(reconvertControl({ flags: [] } as never, true)).toBe("none");
  expect(reconvertControl({ flags: [] } as never, false)).toBe("none");
});
