import { expect, test } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import { useUploads } from "../useUploads";

const file = () => new File([new Uint8Array(10)], "a.pdf", { lastModified: 1 });

function slowDeps() {
  const hash = (_f: File, _p?: unknown, signal?: AbortSignal) =>
    new Promise<string>((resolve, reject) => {
      const t = setTimeout(() => resolve("x".repeat(64)), 30);
      signal?.addEventListener("abort", () => {
        clearTimeout(t);
        reject(new DOMException("Aborted", "AbortError"));
      });
    });
  const upload = async (_f: File, o: { sha256: string | (() => Promise<string>); signal?: AbortSignal }) => {
    if (typeof o.sha256 === "function") await o.sha256();
    await new Promise((r) => setTimeout(r, 20));
    if (o.signal?.aborted) throw new DOMException("Aborted", "AbortError");
    return "u1";
  };
  return { hash: hash as never, upload: upload as never };
}

test("remove and re-add the same file quickly: one row, and it completes (Q5)", async () => {
  const { result } = renderHook(() => useUploads(slowDeps()));
  act(() => result.current.add([file()]));
  await waitFor(() => expect(result.current.items[0]?.phase).toBe("hashing"));
  const first = result.current.items[0].key;
  act(() => result.current.cancel(first));
  act(() => result.current.add([file()]));
  act(() => result.current.add([file()])); // a double drop
  expect(result.current.items).toHaveLength(1);
  await waitFor(() => expect(result.current.items[0]?.phase).toBe("done"), { timeout: 2000 });
  expect(result.current.items).toHaveLength(1);
});

test("adding the same file twice in one gesture gives one row", () => {
  const { result } = renderHook(() => useUploads(slowDeps()));
  act(() => result.current.add([file(), file()]));
  expect(result.current.items).toHaveLength(1);
});
