import { forwardRef, memo, useCallback, useEffect, useImperativeHandle, useLayoutEffect, useMemo, useRef, useState } from "react";
import { getDocument, GlobalWorkerOptions, type PDFDocumentProxy, type PDFPageProxy, type RenderTask } from "pdfjs-dist";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import { Loader2 } from "lucide-react";
import { getToken } from "@/api/client";
import { estimateRatios, firstRatios } from "./pageRatios";
import { buildTops, keepAnchor, rangeIn } from "./layout";
import { RenderQueue } from "./renderQueue";
import { publishAnchors } from "./useScrollSync";

GlobalWorkerOptions.workerSrc = workerUrl;

type Props = {
  url: string;
  onError?: (e: unknown) => void;
  onLoaded?: (pages: number) => void;
  className?: string;
};

const GAP = 12;
const PAD = 16;
/** pages mounted (and rendered) beyond the visible ones, each side */
const OVERSCAN = 1;
/** concurrent page renders */
const RENDERS = 2;

/**
 * pdf.js pages in one scroll container. Only the pages in view (±1) are mounted: their positions come from
 * cumulative page heights (binary search, no layout reads), so a 1,200-page PDF costs the same as a 3-page one.
 * Page sizes are estimated from the first pages and corrected as each page is loaded (keeping the view still).
 * The PDF is fetched with range requests only, so opening it does not download the whole file. A page leaving the
 * window cancels its render and releases its canvas. Mounted pages carry `[data-page]`; the scroll sync reads the
 * page positions from `publishAnchors`.
 */
export const PdfViewer = forwardRef<HTMLDivElement, Props>(function PdfViewer({ url, onError, onLoaded, className }, ref) {
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
    const t = getToken();
    const task = getDocument({
      url,
      httpHeaders: t ? { Authorization: `Bearer ${t}` } : undefined,
      // range requests only: nothing is fetched until a page needs it (a 470 MB PDF opens with a few MB)
      disableAutoFetch: true,
      disableStream: true,
      rangeChunkSize: 256 * 1024,
      // bundled with the app (vite.config.ts copies them): the 14 standard fonts and the CJK character maps (Q3)
      standardFontDataUrl: "/pdfjs/standard_fonts/",
      cMapUrl: "/pdfjs/cmaps/",
      cMapPacked: true,
      // JBIG2 / JPEG 2000 image decoders (scanned books use them; without these the images are left out)
      wasmUrl: "/pdfjs/wasm/",
      iccUrl: "/pdfjs/iccs/",
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
    return () => {
      cancelled = true;
      void task.destroy();
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
        />,
      );
    }
  }

  return (
    <div ref={scroller} className={className} style={{ padding: PAD }}>
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
};

const PdfPage = memo(function PdfPage({ doc, n, top, width, height, queue, center, onRatio }: PageProps) {
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
      className="absolute left-0 w-full overflow-hidden rounded-sm bg-white shadow-panel ring-1 ring-black/5"
      style={{ top, height }}
    >
      <canvas ref={canvasRef} className="block size-full" aria-label={`第 ${n} 頁`} />
      <span className="pointer-events-none absolute right-2 bottom-1.5 rounded bg-black/55 px-1.5 py-0.5 font-mono text-[10px] text-white">
        {n}
      </span>
    </div>
  );
});
