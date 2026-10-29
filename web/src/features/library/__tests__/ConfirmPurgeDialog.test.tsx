import { expect, test, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ConfirmPurgeDialog, purgeText } from "@/features/library/ConfirmPurgeDialog";

test("the text names the copies that go too, and omits the sentence without copies", () => {
  expect(purgeText(2, 2, 3 * 1024 * 1024).body).toContain("也會刪除這些文件在 source\\ 的原始檔備份（2 個檔案，3.0 MB）");
  const none = purgeText(1, 0, 0);
  expect(none.title).toBe("清除 1 筆紀錄？");
  expect(none.body).not.toContain("原始檔備份");
  expect(none.body).toContain("這個動作無法復原");
});

test("only 清除 inside the dialog confirms; 取消 closes", async () => {
  const onConfirm = vi.fn();
  const onOpenChange = vi.fn();
  render(<ConfirmPurgeDialog open records={2} copies={2} bytes={3 * 1024 * 1024} onOpenChange={onOpenChange} onConfirm={onConfirm} />);
  expect(screen.getByText("清除 2 筆紀錄？")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "取消" }));
  expect(onOpenChange).toHaveBeenCalledWith(false);
  expect(onConfirm).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button", { name: "清除" }));
  expect(onConfirm).toHaveBeenCalledTimes(1);
});
