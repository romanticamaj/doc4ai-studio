/** sha256 of a file, computed in a Web Worker (streaming, hash-wasm). Rejects with AbortError when aborted. */
export function hashFile(file: File, onProgress?: (done: number, total: number) => void, signal?: AbortSignal): Promise<string> {
  return new Promise((resolve, reject) => {
    const worker = new Worker(new URL("./hashWorker.ts", import.meta.url), { type: "module" });
    const stop = () => worker.terminate();
    signal?.addEventListener(
      "abort",
      () => {
        stop();
        reject(new DOMException("Aborted", "AbortError"));
      },
      { once: true },
    );
    worker.onmessage = (e: MessageEvent<{ type: string; done?: number; total?: number; sha256?: string; message?: string }>) => {
      const m = e.data;
      if (m.type === "progress") onProgress?.(m.done ?? 0, m.total ?? file.size);
      else if (m.type === "done") {
        stop();
        resolve(m.sha256!);
      } else if (m.type === "error") {
        stop();
        reject(new Error(m.message));
      }
    };
    worker.onerror = (e) => {
      stop();
      reject(new Error(e.message || "hash worker failed"));
    };
    worker.postMessage({ file });
  });
}
