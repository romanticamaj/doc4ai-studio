import { forwardRef, memo, useCallback, useEffect, useImperativeHandle, useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  getDocument,
  GlobalWorkerOptions,
  PDFDataRangeTransport,
  PDFWorker,
  version as pdfjsVersion,
  type PDFDocumentLoadingTask,
  type PDFDocumentProxy,
  type PDFPageProxy,
  type RenderTask,
} from "pdfjs-dist";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import { Loader2 } from "lucide-react";
import { getToken } from "@/api/client";
import { estimateRatios, firstRatios } from "./pageRatios";
import { buildTops, keepAnchor, rangeIn } from "./layout";
import { loadOpenBundle, RangeLoader } from "./pdfSource";
import { RenderQueue } from "./renderQueue";
import { publishAnchors } from "./useScrollSync";

GlobalWorkerOptions.workerSrc = workerUrl;

type Props = {
  url: string;
  onError?: (e: unknown) => void;
  onLoaded?: (pages: number) => void;
  className?: string;
  /** pages with a known problem (spec 2026-10-01 §9.4): a thin warning outline */
  warnPages?: ReadonlySet<number>;
};

const GAP = 12;
const PAD = 16;
/** pages mounted (and rendered) beyond the visible ones, each side */
const OVERSCAN = 1;
/** concurrent page renders */
const RENDERS = 2;
/**
 * pdf.js range chunk size. Measured at 1 s RTT / 2 Mbit/s on a 473 MB, 1,192-page scanned book: what costs time
 * is the number of *serial* round trips, and the open bundle already removes those; past that, bigger chunks only
 * add bytes (open bundle 4.9 MB raw / 1.6 MB gzip at 64 KB vs 7.9 / 2.7 MB at 256 KB and 16 MB at 1 MB), and a page
 * image is still one request (pdf.js asks for contiguous chunks together).
 */
const CHUNK = 64 * 1024;
/** pdf.js fonts / character maps / decoders, in a folder named by the pdf.js version (cached as immutable) */
const PDFJS_ASSETS = `/pdfjs/${pdfjsVersion}`;

/**
 * pdf.js pages in one scroll container. Only the pages in view (±1) are mounted: their positions come from
 * cumulative page heights (binary search, no layout reads), so a 1,200-page PDF costs the same as a 3-page one.
 * Page sizes are estimated from the first pages and corrected as each page is loaded (keeping the view still).
 * The PDF is fetched with range requests only, so opening it does not download the whole file. A page leaving the
 * window cancels its render and releases its canvas. Mounted pages carry `[data-page]`; the scroll sync reads the
 * page positions from `publishAnchors`.
 */
export const PdfViewer = forwardRef<HTMLDivElement, Props>(function PdfViewer({ url, onError, onLoaded, className, warnPages }, ref) {
  const scroller = useRef<HTMLDivElement>(null);
  useImperativeHandle(ref, () => scroller.current as HTMLDivElement);
  const [doc, setDoc] = useState<PDFDocumentProxy | null>(null);
  const known = useRef(new Map<number, number>()); // page -> real height / width
  const [ratioVersion, setRatioVersion] = useState(0);
  const [width, setWidth] = useState(0);
  const [failed, setFailed] = useState(false);
  const [range, setRange] = useState<[number, number]>([0, -1]);
  const queue = useMemo(() => new RenderQueue(RENDERS), []);
  const center = useRef(0);

  // load
  useEffect(() => {
    let cancelled = false;
    let task: PDFDocumentLoadingTask | null = null;
    let loader: RangeLoader | null = null;
    const ctrl = new AbortController();
    const t = getToken();
    const headers = t ? { Authorization: `Bearer ${t}` } : undefined;
    // the worker script (~1.3 MB) loads while the open bundle is on its way, not after it
    const worker = new PDFWorker();
    void (async () => {
      // one round trip for everything pdf.js needs to open the file and draw the first pages (pdfSource.ts)
      const bundle = await loadOpenBundle(url, CHUNK, headers, ctrl.signal);
      if (cancelled) return;
      let source: { url: string; httpHeaders?: Record<string, string> } | { range: PDFDataRangeTransport };
      if (bundle) {
        const transport = new PDFDataRangeTransport(bundle.size, bundle.head());
        const l = new RangeLoader(url, headers, bundle, (begin, data) => transport.onDataRange(begin, data));
        transport.requestDataRange = (begin, end) => l.request(begin, end);
        transport.abort = () => l.abort();
        loader = l;
        source = { range: transport };
      } else source = { url, httpHeaders: headers };
      task = getDocument({
        ...source,
        worker,
        // range requests only: nothing is fetched until a page needs it (a 470 MB PDF opens with a few MB)
        disableAutoFetch: true,
        disableStream: true,
        rangeChunkSize: CHUNK,
        // bundled with the app (vite.config.ts copies them to a versioned folder, cached as immutable): the 14
        // standard fonts and the CJK character maps (Q3)
        standardFontDataUrl: `${PDFJS_ASSETS}/standard_fonts/`,
        cMapUrl: `${PDFJS_ASSETS}/cmaps/`,
        cMapPacked: true,
        // JBIG2 / JPEG 2000 image decoders (scanned books use them; without these the images are left out)
        wasmUrl: `${PDFJS_ASSETS}/wasm/`,
        iccUrl: `${PDFJS_ASSETS}/iccs/`,
      });
      task.promise.then(
        async (d) => {
          if (cancelled) return;
          known.current = await firstRatios(d, 3); // the rest are estimated, then corrected per page (M5)
          if (cancelled) return;
          setDoc(d);
          onLoaded?.(d.numPages);
        },
        (e) => {
          if (cancelled) return;
          setFailed(true);
          onError?.(e);
        },
      );
    })();
    return () => {
      cancelled = true;
      ctrl.abort();
      loader?.abort();
      void task?.destroy().finally(() => worker.destroy());
      if (!task) worker.destroy();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [url]);

  // container width (re-rendering every page at a new width is costly: settle first)
  useEffect(() => {
    const el = scroller.current;
    if (!el) return;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const ro = new ResizeObserver(() => {
      const w = Math.max(200, Math.min(el.clientWidth - PAD * 2, 1100));
      clearTimeout(timer);
      timer = setTimeout(() => setWidth(w), 120);
      setWidth((old) => (old === 0 ? w : old));
    });
    ro.observe(el);
    return () => {
      ro.disconnect();
      clearTimeout(timer);
    };
  }, []);

  const numPages = doc?.numPages ?? 0;
  const ratios = useMemo(() => estimateRatios(numPages, known.current), [numPages, ratioVersion]); // eslint-disable-line react-hooks/exhaustive-deps
  const heights = useMemo(() => ratios.map((r) => Math.round(width * r)), [ratios, width]);
  const layout = useMemo(() => buildTops(heights, GAP), [heights]);
  const layoutRef = useRef(layout);

  // keep the page at the top of the view still when pages above it change size
  useLayoutEffect(() => {
    const el = scroller.current;
    const old = layoutRef.current;
    layoutRef.current = layout;
    if (!el || old === layout || old.tops.length !== layout.tops.length || el.scrollTop <= 0) return;
    const next = keepAnchor(old.tops, layout.tops, el.scrollTop - PAD) + PAD;
    if (Math.abs(next - el.scrollTop) >= 1) el.scrollTop = next;
  }, [layout]);

  const update = useCallback(() => {
    const el = scroller.current;
    if (!el || !numPages || !width) return;
    const top = el.scrollTop - PAD;
    const r = rangeIn(layoutRef.current.tops, top, top + el.clientHeight, OVERSCAN);
    center.current = (r[0] + r[1]) / 2;
    setRange((old) => (old[0] === r[0] && old[1] === r[1] ? old : r));
  }, [numPages, width]);

  useLayoutEffect(update, [update, layout]);

  useEffect(() => {
    const el = scroller.current;
    if (!el) return;
    let raf = 0;
    const on = () => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(update);
    };
    el.addEventListener("scroll", on, { passive: true });
    window.addEventListener("resize", on);
    return () => {
      el.removeEventListener("scroll", on);
      window.removeEventListener("resize", on);
      cancelAnimationFrame(raf);
    };
  }, [update]);

  // page positions for the scroll sync
  useLayoutEffect(() => {
    if (!numPages || !width) return;
    const tops = layout.tops;
    publishAnchors(scroller.current, () => Array.from(tops, (t, i) => ({ page: i + 1, top: PAD + t })));
  }, [layout, numPages, width]);
  useEffect(() => () => publishAnchors(scroller.current, undefined), []);

  // a page reports its real size once loaded; batch the corrections into one layout change per frame
  const pendingRatio = useRef(0);
  const onRatio = useCallback((n: number, r: number) => {
    const prev = known.current.get(n);
    if (prev != null && Math.abs(prev - r) < 0.001) return;
    known.current.set(n, r);
    if (!pendingRatio.current)
      pendingRatio.current = requestAnimationFrame(() => {
        pendingRatio.current = 0;
        setRatioVersion((v) => v + 1);
      });
  }, []);
  useEffect(() => () => cancelAnimationFrame(pendingRatio.current), []);

  const [lo, hi] = range;
  const pages = [];
  if (doc && width > 0) {
    for (let i = lo; i <= hi && i < numPages; i++) {
      pages.push(
        <PdfPage
          key={i}
          doc={doc}
          n={i + 1}
          top={layout.tops[i]}
          width={width}
          height={heights[i]}
          queue={queue}
          center={center}
          onRatio={onRatio}
          warn={warnPages?.has(i + 1) ?? false}
        />,
      );
    }
  }

  return (
    // the bottom padding lets 「跳至頁」 bring the last pages to the top of the view (spec 2026-10-01 §9.4)
    <div ref={scroller} className={className} style={{ padding: PAD, paddingBottom: "70svh" }}>
      {!doc && !failed && (
        <div className="flex h-full min-h-60 items-center justify-center text-muted-foreground">
          <Loader2 className="size-5 animate-spin" />
        </div>
      )}
      {doc && width > 0 && (
        <div className="relative mx-auto" style={{ width, height: layout.total }}>
          {pages}
        </div>
      )}
    </div>
  );
});

type PageProps = {
  doc: PDFDocumentProxy;
  n: number;
  top: number;
  width: number;
  height: number;
  queue: RenderQueue;
  center: React.RefObject<number>;
  onRatio: (n: number, r: number) => void;
  warn?: boolean;
};

const PdfPage = memo(function PdfPage({ doc, n, top, width, height, queue, center, onRatio, warn }: PageProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    let cancelled = false;
    let task: RenderTask | null = null;
    let page: PDFPageProxy | null = null;
    const cancelJob = queue.schedule({
      priority: () => Math.abs(n - 1 - center.current),
      run: async () => {
        if (cancelled) return;
        page = await doc.getPage(n);
        if (cancelled) return;
        const base = page.getViewport({ scale: 1 });
        onRatio(n, base.height / base.width);
        const dpr = Math.min(window.devicePixelRatio || 1, 2);
        const vp = page.getViewport({ scale: (width / base.width) * dpr });
        canvas.width = Math.floor(vp.width);
        canvas.height = Math.floor(vp.height);
        const ctx = canvas.getContext("2d");
        if (!ctx) return;
        task = page.render({ canvasContext: ctx, canvas, viewport: vp });
        try {
          await task.promise;
        } catch {
          /* cancelled */
        } finally {
          task = null;
        }
      },
    });
    return () => {
      // leaving the window (or a new width): stop the work and free the canvas backing store right away
      cancelled = true;
      cancelJob();
      task?.cancel();
      canvas.width = 0;
      canvas.height = 0;
      page?.cleanup();
    };
  }, [doc, n, width, queue, center, onRatio]);

  return (
    <div
      data-page={n}
      data-page-warning={warn ? n : undefined}
      className={
        "absolute left-0 w-full overflow-hidden rounded-sm bg-white shadow-panel " +
        (warn ? "ring-2 ring-warn/70" : "ring-1 ring-black/5")
      }
      style={{ top, height }}
    >
      <canvas ref={canvasRef} className="block size-full" aria-label={`第 ${n} 頁`} />
      <span className="pointer-events-none absolute right-2 bottom-1.5 rounded bg-black/55 px-1.5 py-0.5 font-mono text-[10px] text-white">
        {n}
      </span>
    </div>
  );
});
