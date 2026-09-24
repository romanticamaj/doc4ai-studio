/// <reference lib="webworker" />
import { createSHA256 } from "hash-wasm";

const SLICE = 8 * 1024 * 1024;

// Streaming sha256 (spec §8.7): 8 MB slices, memory stays flat even for 2 GB files.
self.onmessage = async (e: MessageEvent<{ file: File }>) => {
  const { file } = e.data;
  try {
    const h = await createSHA256();
    h.init();
    for (let off = 0; off < file.size; off += SLICE) {
      const buf = await file.slice(off, Math.min(off + SLICE, file.size)).arrayBuffer();
      h.update(new Uint8Array(buf));
      postMessage({ type: "progress", done: Math.min(off + SLICE, file.size), total: file.size });
    }
    postMessage({ type: "done", sha256: h.digest("hex") });
  } catch (err) {
    postMessage({ type: "error", message: err instanceof Error ? err.message : String(err) });
  }
};
