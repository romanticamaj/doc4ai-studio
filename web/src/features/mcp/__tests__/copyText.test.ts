import { afterEach, expect, test, vi } from "vitest";
import { toast } from "sonner";
import { copyText } from "@/features/mcp/ConfigSnippets";

const original = Object.getOwnPropertyDescriptor(navigator, "clipboard");

afterEach(() => {
  vi.restoreAllMocks();
  if (original) Object.defineProperty(navigator, "clipboard", original);
  else delete (navigator as { clipboard?: unknown }).clipboard;
});

function setClipboard(value: unknown) {
  Object.defineProperty(navigator, "clipboard", { value, configurable: true });
}

function mockExec(result: boolean) {
  const exec = vi.fn().mockReturnValue(result);
  Object.defineProperty(document, "execCommand", { value: exec, configurable: true });
  return exec;
}

test("uses the async clipboard when the page is a secure context", async () => {
  const writeText = vi.fn().mockResolvedValue(undefined);
  setClipboard({ writeText });
  const exec = mockExec(true);
  expect(await copyText("abc")).toBe(true);
  expect(writeText).toHaveBeenCalledWith("abc");
  expect(exec).not.toHaveBeenCalled();
});

test("plain HTTP (no navigator.clipboard): copies through a hidden textarea and restores focus", async () => {
  setClipboard(undefined);
  const button = document.createElement("button");
  document.body.appendChild(button);
  button.focus();
  let copied = "";
  const exec = vi.fn(() => {
    copied = (document.activeElement as HTMLTextAreaElement).value;
    return true;
  });
  Object.defineProperty(document, "execCommand", { value: exec, configurable: true });
  const ok = vi.spyOn(toast, "success");
  expect(await copyText("doc4ai_pat_X")).toBe(true);
  expect(exec).toHaveBeenCalledWith("copy");
  expect(copied).toBe("doc4ai_pat_X");
  expect(document.querySelectorAll("textarea")).toHaveLength(0);
  expect(document.activeElement).toBe(button);
  expect(ok).toHaveBeenCalled();
  button.remove();
});

test("a rejected async clipboard falls back to execCommand", async () => {
  setClipboard({ writeText: vi.fn().mockRejectedValue(new Error("denied")) });
  const exec = mockExec(true);
  expect(await copyText("abc")).toBe(true);
  expect(exec).toHaveBeenCalledWith("copy");
});

test("only when both fail does it ask the user to copy by hand", async () => {
  setClipboard(undefined);
  mockExec(false);
  const err = vi.spyOn(toast, "error");
  expect(await copyText("abc")).toBe(false);
  expect(err).toHaveBeenCalledWith(expect.stringMatching(/手動/));
});
