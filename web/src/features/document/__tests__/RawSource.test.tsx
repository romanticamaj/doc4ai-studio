import { expect, test } from "vitest";
import { render } from "@testing-library/react";
import { createRef } from "react";
import { RawSource, splitLines } from "../RawSource";

test("splitLines cuts at line breaks near the size and loses nothing", () => {
  const text = Array.from({ length: 1000 }, (_, i) => `line ${i}`).join("\n");
  const parts = splitLines(text, 500);
  expect(parts.join("")).toBe(text);
  for (const p of parts.slice(0, -1)) {
    expect(p.endsWith("\n")).toBe(true);
    expect(p.length).toBeGreaterThanOrEqual(500);
    expect(p.length).toBeLessThan(520);
  }
  expect(splitLines("", 10)).toEqual([""]);
});

test("a large source mounts only the slices near the view", () => {
  const text = Array.from({ length: 20_000 }, (_, i) => `line ${i} of the markdown source`).join("\n");
  const ref = createRef<HTMLDivElement>();
  const { container } = render(
    <div ref={ref} style={{ height: 400, overflow: "auto" }}>
      <RawSource text={text} scrollRef={ref} />
    </div>,
  );
  expect(container.textContent).toContain("line 0 of");
  expect(container.textContent).not.toContain("line 19999 of");
});
