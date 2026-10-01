/**
 * Opening a PDF over a high-latency link (Tailscale DERP, ~1 s RTT). pdf.js reads a PDF with range requests and
 * every structural step waits for the previous one (trailer, each xref section of the /Prev chain, catalog, page
 * tree, page, its image): a 1,192-page book saved incrementally took ~45 serial requests (~100 s) to show page 1.
 * The server lists those byte ranges (GET …/source/open, aidoc.pdfprefetch) and sends them back to back in one
 * gzip-compressed response; pdf.js is then given a range transport that answers from that bundle and falls back
 * to ordinary Range requests for everything else (later pages).
 */

export type ByteRange = [number, number];

/** `0-10,20-30` (end exclusive, ascending, non-overlapping) */
export function parseRanges(header: string | null): ByteRange[] | null {
  if (!header) return null;
  const out: ByteRange[] = [];
  for (const part of header.split(",")) {
    const m = /^(\d+)-(\d+)$/.exec(part.trim());
    if (!m) return null;
    const b = Number(m[1]);
    const e = Number(m[2]);
    if (e <= b || (out.length && b < out[out.length - 1][1])) return null;
    out.push([b, e]);
  }
  return out;
}

export function bundleUrl(sourceUrl: string, chunk: number): string {
  const [path, query] = sourceUrl.split("?", 2);
  const q = new URLSearchParams(query ?? "");
  q.set("chunk", String(chunk));
  return `${path}/open?${q.toString()}`;
}

export class OpenBundle {
  readonly size: number;
  private readonly ranges: ByteRange[];
  private readonly offsets: number[];
  private readonly data: Uint8Array;
  private readonly starts: number[];
  constructor(size: number, ranges: ByteRange[], offsets: number[], data: Uint8Array) {
    this.size = size;
    this.ranges = ranges;
    this.offsets = offsets;
    this.data = data;
    this.starts = ranges.map((r) => r[0]);
  }

  /** bytes [begin, end) when they lie inside one prefetched range, else null */
  get(begin: number, end: number): Uint8Array | null {
    let lo = 0;
    let hi = this.starts.length - 1;
    let i = -1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (this.starts[mid] <= begin) {
        i = mid;
        lo = mid + 1;
      } else hi = mid - 1;
    }
    if (i < 0 || end > this.ranges[i][1]) return null;
    const at = this.offsets[i] + (begin - this.ranges[i][0]);
    return this.data.subarray(at, at + (end - begin));
  }

  /** the bytes from the start of the file that were prefetched (pdf.js' initialData) */
  head(): Uint8Array | null {
    return this.ranges.length && this.ranges[0][0] === 0 ? this.data.subarray(0, this.ranges[0][1]) : null;
  }
}

/** The open bundle for `sourceUrl`, or null when the server cannot provide one (the viewer then opens the URL). */
export async function loadOpenBundle(
  sourceUrl: string,
  chunk: number,
  headers?: Record<string, string>,
  signal?: AbortSignal,
): Promise<OpenBundle | null> {
  try {
    const r = await fetch(bundleUrl(sourceUrl, chunk), { headers, signal });
    if (!r.ok) return null;
    const size = Number(r.headers.get("X-Pdf-Size"));
    const ranges = parseRanges(r.headers.get("X-Pdf-Ranges"));
    if (!ranges || !Number.isSafeInteger(size) || size <= 0 || ranges[ranges.length - 1][1] > size) return null;
    const data = new Uint8Array(await r.arrayBuffer());
    const offsets: number[] = [];
    let at = 0;
    for (const [b, e] of ranges) {
      offsets.push(at);
      at += e - b;
    }
    if (at !== data.length) return null;
    return new OpenBundle(size, ranges, offsets, data);
  } catch {
    return null;
  }
}

/** Serves pdf.js range requests: from the bundle when it holds them, else with a Range request on the source. */
export class RangeLoader {
  private readonly ctrl = new AbortController();
  private readonly url: string;
  private readonly headers: Record<string, string> | undefined;
  private readonly bundle: OpenBundle | null;
  private readonly deliver: (begin: number, data: Uint8Array) => void;
  private readonly retryDelayMs: number;
  constructor(
    url: string,
    headers: Record<string, string> | undefined,
    bundle: OpenBundle | null,
    deliver: (begin: number, data: Uint8Array) => void,
    retryDelayMs = 1000,
  ) {
    this.url = url;
    this.headers = headers;
    this.bundle = bundle;
    this.deliver = deliver;
    this.retryDelayMs = retryDelayMs;
  }

  request(begin: number, end: number): void {
    const hit = this.bundle?.get(begin, end);
    if (hit) {
      queueMicrotask(() => {
        if (!this.ctrl.signal.aborted) this.deliver(begin, hit);
      });
      return;
    }
    void this.fetchRange(begin, end);
  }

  private async fetchRange(begin: number, end: number): Promise<void> {
    // pdf.js has no error path for a transport range: a lost request would leave the page loading forever
    for (let attempt = 0; attempt < 3 && !this.ctrl.signal.aborted; attempt++) {
      if (attempt) await new Promise((r) => setTimeout(r, this.retryDelayMs * attempt));
      try {
        const r = await fetch(this.url, {
          headers: { ...this.headers, Range: `bytes=${begin}-${end - 1}` },
          signal: this.ctrl.signal,
        });
        if (r.status !== 206 && r.status !== 200) continue;
        let data = new Uint8Array(await r.arrayBuffer());
        if (r.status === 200 && data.length > end - begin) data = data.subarray(begin, end);
        if (!this.ctrl.signal.aborted) this.deliver(begin, data);
        return;
      } catch {
        /* aborted (the loop ends) or the network failed: try again */
      }
    }
  }

  abort(): void {
    this.ctrl.abort();
  }
}
