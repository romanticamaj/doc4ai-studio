import { afterEach, expect, test, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { TokenRevealDialog } from "@/features/mcp/TokenRevealDialog";

afterEach(() => vi.restoreAllMocks());

const result = { token: "doc4ai_pat_SECRETVALUE", record: { id: "t", name: "n", prefix: "doc4ai_pat_SECR" } as never,
  snippets: [{ client: "claude-code", title: "Claude Code", language: "bash", text: "claude mcp add ... Bearer doc4ai_pat_SECRETVALUE" }] } as never;

test("shows the token once with copy, snippets and the warning; closing hands control back", async () => {
  const writeText = vi.fn().mockResolvedValue(undefined);
  Object.assign(navigator, { clipboard: { writeText } });
  const onClose = vi.fn();
  render(<TokenRevealDialog result={result} onClose={onClose} />);
  expect(screen.getByDisplayValue("doc4ai_pat_SECRETVALUE")).toHaveAttribute("readonly");
  expect(screen.getByText("關閉後無法再次查看這把 token")).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: "Claude Code" })).toBeInTheDocument();
  await userEvent.click(screen.getAllByRole("button", { name: "複製" })[0]);
  expect(writeText).toHaveBeenCalledWith("doc4ai_pat_SECRETVALUE");
  await userEvent.click(screen.getByRole("button", { name: "我已複製，關閉" }));
  expect(onClose).toHaveBeenCalled();
});

test("renders nothing without a result", () => {
  const { container } = render(<TokenRevealDialog result={null} onClose={() => {}} />);
  expect(container).toBeEmptyDOMElement();
});
