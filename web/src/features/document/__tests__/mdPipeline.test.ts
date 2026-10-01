import { expect, test } from "vitest";
import type { Element, Root, RootContent } from "hast";
import { processBlock } from "../mdPipeline";

function all(tree: Root): RootContent[] {
  const out: RootContent[] = [];
  const walk = (n: Root | RootContent) => {
    if (n.type !== "root") out.push(n);
    if ("children" in n) (n.children as RootContent[]).forEach(walk);
  };
  walk(tree);
  return out;
}
const elements = (t: Root) => all(t).filter((n): n is Element => n.type === "element");

test("the result is plain data that can cross a worker boundary", () => {
  const t = processBlock("# T\n\nx $a^2$ and <b>bold</b>\n\n<table><tr><td>c</td></tr></table>", "pa1");
  expect(() => structuredClone(t)).not.toThrow();
  expect(elements(t).some((e) => e.tagName === "table")).toBe(true);
  expect(all(t).some((n) => (n as { type: string }).type === "raw")).toBe(false);
});

test("raw HTML is sanitized: no scripts, handlers or styles", () => {
  const t = processBlock('<div onclick="x()" style="color:red">a</div>\n\n<script>alert(1)</script>\n\n<img src=x onerror=alert(1)>', "pa1");
  const els = elements(t);
  expect(els.some((e) => e.tagName === "script")).toBe(false);
  for (const e of els) {
    expect(Object.keys(e.properties ?? {}).filter((k) => /^on|style/i.test(k))).toEqual([]);
  }
});

test("only our nonce anchors keep data-page", () => {
  // the nonce is random per page load, so raw HTML in a document cannot carry it
  const t = processBlock('<!-- page: 2 -->\n\n<div data-page="99" class="page-anchor">x</div>\n\n<span data-page="7">y</span>', "pa1");
  const pages = elements(t)
    .filter((e) => e.properties?.dataPage != null)
    .map((e) => [String(e.properties.dataPage), e.properties.className]);
  expect(pages).toEqual([["2", ["page-anchor"]]]);
});

test("blocks without raw HTML skip the HTML re-parse and still render math", () => {
  const t = processBlock("text $$x$$\n\n$$\ny\n$$", "pa1");
  const cls = elements(t).flatMap((e) => (Array.isArray(e.properties?.className) ? e.properties.className : []));
  expect(cls).toContain("katex");
  expect(cls).toContain("katex-display");
});
