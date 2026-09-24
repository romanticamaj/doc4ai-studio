import { expect, test } from "vitest";
import { baseName, formatBytes, formatDuration, gb } from "../format";

test("baseName handles Windows and POSIX paths and bare names", () => {
  expect(baseName("D:\\projects\\tests\\fixtures\\text.pdf")).toBe("text.pdf");
  expect(baseName("/home/me/a b.docx")).toBe("a b.docx");
  expect(baseName("upload.pdf")).toBe("upload.pdf");
});

test("byte and duration formatting", () => {
  expect(formatBytes(512)).toBe("512 B");
  expect(formatBytes(1536)).toBe("1.5 KB");
  expect(formatBytes(2147483648, 0)).toBe("2 GB");
  expect(formatBytes(null)).toBe("—");
  expect(gb(4e9)).toBe("4.0");
  expect(formatDuration(7.69)).toBe("7.7 秒");
  expect(formatDuration(125)).toBe("2 分 5 秒");
});
