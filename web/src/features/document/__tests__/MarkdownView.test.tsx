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

test("page markers, math and HTML tables render; scripts are stripped", () => {
  const md = "<!-- page: 1 -->\n\nx $a^2$\n\n$$\nb\n$$\n\n<table><tr><td>cell</td></tr></table>\n\n<script>alert(1)</script>\n\n<!-- page: 2 -->\n\ny";
  const { container } = render(<MarkdownView docId="d1" markdown={md} />);
  expect([...container.querySelectorAll(".page-anchor")].map((e) => e.getAttribute("data-page"))).toEqual(["1", "2"]);
  expect(container.querySelector(".katex")).not.toBeNull();
  expect(container.querySelector(".katex-display")).not.toBeNull();
  expect(screen.getByText("cell")).toBeInTheDocument();
  expect(container.querySelector("script")).toBeNull();
});
