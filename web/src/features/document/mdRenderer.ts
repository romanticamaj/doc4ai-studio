import type { Root } from "hast";
import { processBlock } from "./mdPipeline";
import { RenderQueue } from "./renderQueue";

/**
 * Turns Markdown blocks into sanitized hast off the main thread (one Web Worker), nearest-to-the-view first.
 * Falls back to the main thread, a block per task, where workers are unavailable (tests) or the worker failed.
 */
export class BlockRenderer {
  private worker: Worker | null = null;
  private waiting = new Map<number, { resolve: (t: Root) => void; reject: (e: unknown) => void }>();
  private seq = 0;
  private queue = new RenderQueue(2);

  private nonce: string;

  constructor(nonce: string) {
    this.nonce = nonce;
    if (typeof Worker === "undefined") return;
    try {
      this.worker = new Worker(new URL("./md.worker.ts", import.meta.url), { type: "module" });
      this.worker.onmessage = (e: MessageEvent<{ id: number; tree?: Root; error?: string }>) => {
        const w = this.waiting.get(e.data.id);
        if (!w) return;
        this.waiting.delete(e.data.id);
        if (e.data.tree) w.resolve(e.data.tree);
        else w.reject(new Error(e.data.error ?? "markdown worker failed"));
      };
      this.worker.onerror = () => this.fail();
    } catch {
      this.worker = null;
    }
  }

  private fail() {
    this.worker?.terminate();
    this.worker = null;
    // whatever was in flight is redone on the main thread
    for (const w of this.waiting.values()) w.reject(new Error("retry"));
    this.waiting.clear();
  }

  private inWorker(text: string): Promise<Root> {
    const worker = this.worker;
    if (!worker) return this.onMain(text);
    const id = ++this.seq;
    return new Promise<Root>((resolve, reject) => {
      this.waiting.set(id, { resolve, reject });
      worker.postMessage({ id, text, nonce: this.nonce });
    }).catch((e) => (this.worker ? Promise.reject(e) : this.onMain(text)));
  }

  private onMain(text: string): Promise<Root> {
    return new Promise((resolve, reject) =>
      setTimeout(() => {
        try {
          resolve(processBlock(text, this.nonce));
        } catch (e) {
          reject(e);
        }
      }, 0),
    );
  }

  /** Resolves with the block's tree unless cancelled before it started. */
  render(text: string, priority: () => number, done: (t: Root) => void): () => void {
    let cancelled = false;
    const cancel = this.queue.schedule({
      priority,
      run: async () => {
        if (cancelled) return;
        const tree = await this.inWorker(text);
        if (!cancelled) done(tree);
      },
    });
    return () => {
      cancelled = true;
      cancel();
    };
  }

  dispose() {
    this.worker?.terminate();
    this.worker = null;
    this.waiting.clear();
  }
}
