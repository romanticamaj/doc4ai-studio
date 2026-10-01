import { afterEach, describe, expect, it, vi } from "vitest";
import { bundleUrl, loadOpenBundle, parseRanges, RangeLoader } from "../pdfSource";

const bytes = (n: number, from = 0) => Uint8Array.from({ length: n }, (_, i) => (from + i) % 251);

afterEach(() => vi.unstubAllGlobals());

describe("parseRanges", () => {
  it("reads begin-end pairs (end exclusive)", () => {
    expect(parseRanges("0-10,20-30")).toEqual([
      [0, 10],
      [20, 30],
    ]);
  });
  it("refuses anything malformed", () => {
    for (const bad of ["", "x", "0-", "10-5", "0-10,5-20", null]) expect(parseRanges(bad)).toBeNull();
  });
});

describe("bundleUrl", () => {
  it("is the source URL + /open, keeping its query, with the chunk size", () => {
    expect(bundleUrl("/api/documents/d1/source?v=1.5&token=t", 65536)).toBe(
      "/api/documents/d1/source/open?v=1.5&token=t&chunk=65536",
    );
  });
});

function bundleResponse(size: number, ranges: [number, number][]) {
  const file = bytes(size);
  const body = new Uint8Array(ranges.reduce((n, [b, e]) => n + e - b, 0));
  let at = 0;
  for (const [b, e] of ranges) {
    body.set(file.subarray(b, e), at);
    at += e - b;
  }
  return new Response(body, {
    status: 200,
    headers: { "X-Pdf-Size": String(size), "X-Pdf-Ranges": ranges.map(([b, e]) => `${b}-${e}`).join(",") },
  });
}

describe("loadOpenBundle", () => {
  it("returns the file size and the prefetched ranges", async () => {
    const fetchMock = vi.fn().mockResolvedValue(bundleResponse(1000, [[0, 100], [900, 1000]]));
    vi.stubGlobal("fetch", fetchMock);
    const b = await loadOpenBundle("/api/documents/d/source?v=1", 100, { Authorization: "Bearer t" });
    expect(fetchMock).toHaveBeenCalledWith("/api/documents/d/source/open?v=1&chunk=100", expect.objectContaining({ headers: { Authorization: "Bearer t" } }));
    expect(b?.size).toBe(1000);
    expect(b?.get(900, 1000)).toEqual(bytes(100, 900));
  });
  it("is null when the server cannot help (old server, not a PDF, broken headers)", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("{}", { status: 415 })));
    expect(await loadOpenBundle("/s", 100)).toBeNull();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("abc", { status: 200, headers: { "X-Pdf-Size": "9" } })));
    expect(await loadOpenBundle("/s", 100)).toBeNull();
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("network")));
    expect(await loadOpenBundle("/s", 100)).toBeNull();
  });
});

describe("RangeLoader", () => {
  it("answers ranges inside the bundle from memory, others with a Range request", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(bundleResponse(1000, [[0, 100], [200, 400]])));
    const bundle = (await loadOpenBundle("/s", 100))!;
    expect(bundle.get(200, 300)).toEqual(bytes(100, 200));
    expect(bundle.get(100, 200)).toBeNull(); // not prefetched
    expect(bundle.get(300, 500)).toBeNull(); // only partly prefetched
    const fetchMock = vi.fn().mockResolvedValue(new Response(bytes(100, 500), { status: 206 }));
    vi.stubGlobal("fetch", fetchMock);
    const got: [number, Uint8Array][] = [];
    const loader = new RangeLoader("/s?v=1", { Authorization: "Bearer t" }, bundle, (b, d) => got.push([b, d]));
    loader.request(200, 300);
    loader.request(500, 600);
    await vi.waitFor(() => expect(got).toHaveLength(2));
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe("/s?v=1");
    expect(fetchMock.mock.calls[0][1].headers).toEqual({ Authorization: "Bearer t", Range: "bytes=500-599" });
    expect(got.find(([b]) => b === 200)?.[1]).toEqual(bytes(100, 200));
    expect(got.find(([b]) => b === 500)?.[1]).toEqual(bytes(100, 500));
  });
  it("cuts a full (200) answer down to the range", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(bytes(1000), { status: 200 })));
    const got: [number, Uint8Array][] = [];
    new RangeLoader("/s", undefined, null, (b, d) => got.push([b, d])).request(500, 600);
    await vi.waitFor(() => expect(got).toHaveLength(1));
    expect(got[0][1]).toEqual(bytes(100, 500));
  });
  it("retries a failed range request (pdf.js has no error path for a transport range)", async () => {
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new TypeError("network"))
      .mockResolvedValueOnce(new Response("busy", { status: 503 }))
      .mockResolvedValueOnce(new Response(bytes(100), { status: 206 }));
    vi.stubGlobal("fetch", fetchMock);
    const got: [number, Uint8Array][] = [];
    new RangeLoader("/s", undefined, null, (b, d) => got.push([b, d]), 0).request(0, 100);
    await vi.waitFor(() => expect(got).toHaveLength(1));
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(got[0][1]).toEqual(bytes(100));
  });
  it("stops delivering after abort", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(bytes(100), { status: 206 })));
    const got: unknown[] = [];
    const l = new RangeLoader("/s", undefined, null, (b, d) => got.push([b, d]));
    l.request(0, 100);
    l.abort();
    await new Promise((r) => setTimeout(r, 20));
    expect(got).toHaveLength(0);
  });
});
