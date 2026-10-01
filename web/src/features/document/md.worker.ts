/// <reference lib="webworker" />
import { processBlock } from "./mdPipeline";

// Parses and sanitizes one Markdown block per message; the reply is a plain hast tree (no URLs rewritten yet).
self.onmessage = (e: MessageEvent<{ id: number; text: string; nonce: string }>) => {
  const { id, text, nonce } = e.data;
  try {
    (self as unknown as Worker).postMessage({ id, tree: processBlock(text, nonce) });
  } catch (err) {
    (self as unknown as Worker).postMessage({ id, error: String(err) });
  }
};
