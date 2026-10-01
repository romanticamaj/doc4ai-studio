import { expect, test } from "vitest";
import { render, screen } from "@testing-library/react";
import { MarkdownView } from "../MarkdownView";

test("markdown without page markers still renders (no anchors: sync is off)", () => {
  const { container } = render(<MarkdownView docId="d1" markdown={"# Report\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n![fig](assets/p1_1.png)"} />);
  expect(screen.getByRole("heading", { name: "Report" })).toBeInTheDocument();
  expect(container.querySelector("table")).not.toBeNull();
  expect(container.querySelectorAll("[data-page]").length).toBe(0);
  expect(container.querySelector("img")!.getAttribute("src")).toBe("/api/documents/d1/assets/p1_1.png");
});

test("only clean assets/ paths are rewritten, and nothing links into /api (C1 stored-XSS chain)", () => {
  localStorage.setItem("aidoc_token", "s3cret");
  try {
    const md = [
      "[a](assets/../source)",
      "[b](assets/%2e%2e/source)",
      "[c](assets\\..\\source)",
      "[d](/api/documents/d1/source)",
      "[e](../../api/queue/pause)",
      "[f](http://127.0.0.1:8765/api/documents/d1/source)",
      "![ok](assets/p1_1.png)",
      "[g](https://example.com/x)",
    ].join("\n\n");
    const { container } = render(<MarkdownView docId="d1" markdown={md} />);
    const urls = [...container.querySelectorAll("a[href], img[src]")].map((e) => e.getAttribute("href") ?? e.getAttribute("src") ?? "");
    for (const u of urls) {
      if (u.includes("token=")) expect(u).toMatch(/^\/api\/documents\/d1\/assets\/p1_1\.png\?token=s3cret$/);
      expect(u).not.toMatch(/\/source|queue|\.\./);
    }
    expect(container.querySelector("img")!.getAttribute("src")).toBe("/api/documents/d1/assets/p1_1.png?token=s3cret");
    expect(container.querySelector('a[href="https://example.com/x"]')).not.toBeNull();
  } finally {
    localStorage.clear();
  }
});

test("footnote links stay in the page; raw HTML cannot fake page anchors", () => {
  const md = "Text[^1] <div data-page=\"99\" class=\"page-anchor\">x</div>\n\n<!-- page: 2 -->\n\nmore <!-- page: 3 --> inline\n\n[^1]: note";
  const { container } = render(<MarkdownView docId="d1" markdown={md} />);
  const fn = container.querySelector('a[href^="#"]')!;
  expect(fn.getAttribute("target")).toBeNull();
  const pages = [...container.querySelectorAll("[data-page]")].map((e) => e.getAttribute("data-page"));
  expect(pages).toEqual(["2", "3"]);
  expect(container.querySelector('[data-page="3"]')!.className).toContain("page-anchor-inline");
});

test("page markers, math and HTML tables render; scripts are stripped", () => {
  const md = "<!-- page: 1 -->\n\nx $a^2$\n\n$$\nb\n$$\n\n<table><tr><td>cell</td></tr></table>\n\n<script>alert(1)</script>\n\n<!-- page: 2 -->\n\ny";
  const { container } = render(<MarkdownView docId="d1" markdown={md} />);
  expect([...container.querySelectorAll(".page-anchor")].map((e) => e.getAttribute("data-page"))).toEqual(["1", "2"]);
  expect(container.querySelector(".katex")).not.toBeNull();
  expect(container.querySelector(".katex-display")).not.toBeNull();
  expect(screen.getByText("cell")).toBeInTheDocument();
  expect(container.querySelector("script")).toBeNull();
});

test("a large document renders its first block at once, not the whole text, and publishes page anchors", async () => {
  const { createRef } = await import("react");
  const { ANCHORS_EVENT } = await import("../useScrollSync");
  const pages = Array.from({ length: 60 }, (_, i) => `<!-- page: ${i + 1} -->\n\n# Page ${i + 1}\n\n${"word ".repeat(400)}\n\n![f](assets/p${i}.png)`);
  const md = pages.join("\n\n");
  const ref = createRef<HTMLDivElement>();
  let published = 0;
  const { container } = render(
    <div ref={(el) => {
      (ref as { current: HTMLDivElement | null }).current = el;
      el?.addEventListener(ANCHORS_EVENT, () => published++);
    }}>
      <MarkdownView docId="d1" markdown={md} scrollRef={ref} />
    </div>,
  );
  expect(screen.getByRole("heading", { name: "Page 1" })).toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "Page 60" })).toBeNull();
  expect(container.querySelector("img")!.getAttribute("decoding")).toBe("async");
  expect(published).toBeGreaterThan(0);
  const source = (ref.current as unknown as { anchorSource: () => { page: number }[] }).anchorSource;
  expect(source().map((a) => a.page)).toEqual(Array.from({ length: 60 }, (_, i) => i + 1));
});

test("flagged pages get a note at their page anchor (spec 2026-10-01 §9.4)", () => {
  const warnings = new Map([[2, { text: "第 2 頁：文字層損壞，已用 OCR 修復", tone: "info" as const }]]);
  const md = "<!-- page: 1 -->\n\nOne\n\n<!-- page: 2 -->\n\nTwo\n";
  const { container } = render(<MarkdownView docId="d1" markdown={md} warnings={warnings} />);
  const chip = container.querySelector('[data-page-warning="2"]');
  expect(chip?.textContent).toBe("第 2 頁：文字層損壞，已用 OCR 修復");
  expect(chip?.closest("[data-page]")?.getAttribute("data-page")).toBe("2");
  expect(container.querySelectorAll("[data-page-warning]").length).toBe(1);
});

test("asset URLs carry the conversion version, so a reconverted image is never taken from the browser cache", async () => {
  const { markdownUrl } = await import("../MarkdownView");
  expect(markdownUrl("assets/p1_1.png", "d1", 1790841234.5)).toBe("/api/documents/d1/assets/p1_1.png?v=1790841234.5");
  expect(markdownUrl("assets/p1_1.png", "d1")).toBe("/api/documents/d1/assets/p1_1.png");
  const { container } = render(<MarkdownView docId="d1" version={42} markdown={"![x](assets/p2_1.png)"} />);
  expect(container.querySelector("img")!.getAttribute("src")).toBe("/api/documents/d1/assets/p2_1.png?v=42");
});
