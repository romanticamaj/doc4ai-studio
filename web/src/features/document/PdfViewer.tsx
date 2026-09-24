import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState } from "react";
import { getDocument, GlobalWorkerOptions, type PDFDocumentProxy, type RenderTask } from "pdfjs-dist";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import { Loader2 } from "lucide-react";
import { getToken } from "@/api/client";
import { pageRatios } from "./pageRatios";

GlobalWorkerOptions.workerSrc = workerUrl;

type Props = {
  url: string;
  onError?: (e: unknown) => void;
  onLoaded?: (pages: number) => void;
  className?: string;
};

const GAP = 12;
const PAD = 16;
const RENDER_AHEAD = 2;
const KEEP = 6;

/**
 * pdf.js pages in one scroll container, each wrapped in `[data-page]` for the scroll sync. Placeholders use
 * the page-1 aspect ratio until a page is rendered; only pages within ±2 of the viewport are rendered, and
 * canvases far from it are released so 300-page documents stay light.
 */
export const PdfViewer = forwardRef<HTMLDivElement, Props>(function PdfViewer({ url, onError, onLoaded, className }, ref) {
  const scroller = useRef<HTMLDivElement>(null);
  useImperativeHandle(ref, () => scroller.current as HTMLDivElement);
  const [doc, setDoc] = useState<PDFDocumentProxy | null>(null);
  const [ratios, setRatios] = useState<number[]>([]); // height / width per page
  const [width, setWidth] = useState(0);
  const [failed, setFailed] = useState(false);
  const canvases = useRef(new Map<number, HTMLCanvasElement>());
  const rendered = useRef(new Map<number, number>()); // page -> width it was rendered at
  const tasks = useRef(new Map<number, RenderTask>());

  // load
  useEffect(() => {
    let cancelled = false;
    const t = getToken();
    const task = getDocument({
      url,
      httpHeaders: t ? { Authorization: `Bearer ${t}` } : undefined,
      // bundled with the app (vite.config.ts copies them): the 14 standard fonts and the CJK character maps (Q3)
      standardFontDataUrl: "/pdfjs/standard_fonts/",
      cMapUrl: "/pdfjs/cmaps/",
      cMapPacked: true,
    });
    task.promise.then(
      async (d) => {
        if (cancelled) return;
        const ratios = await pageRatios(d);          // per page: mixed sizes must line up (M5)
        if (cancelled) return;
        setRatios(ratios);
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

  // container width
  useEffect(() => {
    const el = scroller.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setWidth(Math.max(200, Math.min(el.clientWidth - PAD * 2, 1100))));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const renderPage = useCallback(
    async (n: number) => {
      if (!doc || !width) return;
      const canvas = canvases.current.get(n);
      if (!canvas || rendered.current.get(n) === width || tasks.current.has(n)) return;
      const page = await doc.getPage(n);
      const base = page.getViewport({ scale: 1 });
      const ratio = base.height / base.width;
      setRatios((r) => (Math.abs((r[n - 1] ?? 0) - ratio) > 0.001 ? Object.assign([...r], { [n - 1]: ratio }) : r));
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const vp = page.getViewport({ scale: (width / base.width) * dpr });
      canvas.width = Math.floor(vp.width);
      canvas.height = Math.floor(vp.height);
      const ctx = canvas.getContext("2d");
      if (!ctx) return;
      const task = page.render({ canvasContext: ctx, canvas, viewport: vp });
      tasks.current.set(n, task);
      try {
        await task.promise;
        rendered.current.set(n, width);
      } catch {
        /* cancelled */
      } finally {
        tasks.current.delete(n);
      }
    },
    [doc, width],
  );

  const update = useCallback(() => {
    const el = scroller.current;
    if (!el || !doc || !width) return;
    const top = el.scrollTop;
    const bottom = top + el.clientHeight;
    const visible: number[] = [];
    el.querySelectorAll<HTMLElement>("[data-page]").forEach((node) => {
      const y = node.offsetTop;
      if (y + node.offsetHeight >= top && y <= bottom) visible.push(Number(node.dataset.page));
    });
    if (!visible.length) return;
    const lo = Math.max(1, Math.min(...visible) - RENDER_AHEAD);
    const hi = Math.min(doc.numPages, Math.max(...visible) + RENDER_AHEAD);
    for (let n = lo; n <= hi; n++) void renderPage(n);
    // release far canvases
    for (const n of [...rendered.current.keys()]) {
      if (n < lo - KEEP || n > hi + KEEP) {
        const c = canvases.current.get(n);
        if (c) {
          c.width = 0;
          c.height = 0;
        }
        rendered.current.delete(n);
      }
    }
  }, [doc, width, renderPage]);

  useEffect(() => {
    rendered.current.clear();
    update();
  }, [width, update]);

  useEffect(() => {
    const el = scroller.current;
    if (!el) return;
    let raf = 0;
    const on = () => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(update);
    };
    el.addEventListener("scroll", on, { passive: true });
    return () => {
      el.removeEventListener("scroll", on);
      cancelAnimationFrame(raf);
    };
  }, [update]);

  return (
    <div ref={scroller} className={className} style={{ padding: PAD }}>
      {!doc && !failed && (
        <div className="flex h-full min-h-60 items-center justify-center text-muted-foreground">
          <Loader2 className="size-5 animate-spin" />
        </div>
      )}
      {doc && width > 0 && (
        <div className="mx-auto flex flex-col items-center" style={{ gap: GAP, width }}>
          {ratios.map((r, i) => (
            <div
              key={i}
              data-page={i + 1}
              className="relative w-full overflow-hidden rounded-sm bg-white shadow-panel ring-1 ring-black/5"
              style={{ height: Math.round(width * r) }}
            >
              <canvas
                ref={(c) => {
                  if (c) canvases.current.set(i + 1, c);
                  else canvases.current.delete(i + 1);
                }}
                className="block size-full"
                aria-label={`第 ${i + 1} 頁`}
              />
              <span className="pointer-events-none absolute right-2 bottom-1.5 rounded bg-black/55 px-1.5 py-0.5 font-mono text-[10px] text-white">
                {i + 1}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
});
