import { expect, test } from "vitest";
import { render, screen } from "@testing-library/react";
import { Sparkline } from "@/features/mcp/Sparkline";

test("draws one point per value and labels the graphic", () => {
  render(<Sparkline values={[0, 2, 5, 1]} label="24 小時呼叫" />);
  const svg = screen.getByRole("img", { name: "24 小時呼叫" });
  const pts = svg.querySelector("polyline")!.getAttribute("points")!.trim().split(" ");
  expect(pts).toHaveLength(4);
});

test("all-zero values still render a flat line", () => {
  render(<Sparkline values={[0, 0, 0]} label="x" />);
  const ys = new Set(screen.getByRole("img").querySelector("polyline")!.getAttribute("points")!.trim().split(" ").map((p) => p.split(",")[1]));
  expect(ys.size).toBe(1);
});
