import { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Fragment, jsx, jsxs } from "react/jsx-runtime";
import { defaultUrlTransform } from "react-markdown";
import { toJsxRuntime, type Components } from "hast-util-to-jsx-runtime";
import { urlAttributes } from "html-url-attributes";
import { visit } from "unist-util-visit";
import type { Element, Root } from "hast";
import "katex/dist/katex.min.css";
import { withToken } from "@/api/client";
import { processBlock } from "./mdPipeline";
import { BlockRenderer } from "./mdRenderer";
import { splitMarkdown, type MdBlock } from "./splitMarkdown";
import { useBlockVirtualizer } from "./useBlockVirtualizer";
import { mdAnchors } from "./mdAnchors";
import { publishAnchors } from "./useScrollSync";
import type { PageWarning } from "./pageWarnings";

// Page anchors made by remarkPageAnchors carry this per-load class; raw HTML in a converted document cannot guess
// it, so after sanitizing only our own anchors keep data-page (a fake anchor could hijack the scroll sync).
const NONCE = `pa${Array.from(crypto.getRandomValues(new Uint8Array(8)), (b) => b.toString(16).padStart(2, "0")).join("")}`;

/** Documents up to this size render in one piece on the main thread; larger ones are split and virtualized. */
const SMALL = 48_000;

const SAFE_ASSET = /^(?:\.\/)?assets\/((?:[A-Za-z0-9_@+-][A-Za-z0-9._@+-]*)(?:\/[A-Za-z0-9_@+-][A-Za-z0-9._@+-]*)*)$/;

/**
 * Only a clean `assets/<name>` path (no `..`, `%`, backslash, scheme or leading slash) is rewritten to the asset
 * endpoint, and only that URL gets the API token. Any other link that would land on this server's /api is
 * dropped: a converted document must never be able to point the browser at an API URL (final P4 check C1).
 */
export function markdownUrl(url: string, docId: string, version?: string | number): string {
  const m = SAFE_ASSET.exec(url);
  // `version` (the document's conversion time) changes on every reconversion, which rewrites assets under the same
  // names: a new URL can never be served from an old browser cache entry
  if (m) return withToken(`/api/documents/${docId}/assets/${m[1]}${version != null ? `?v=${encodeURIComponent(String(version))}` : ""}`);
  if (url.includes("\\")) return "";
  if (url.startsWith("#")) return url;
  // other relative URLs point nowhere useful from /documents/<id> (and could be steered at /api): drop them
  if (!/^[a-z][a-z0-9+.-]*:/i.test(url)) return "";
  const safe = defaultUrlTransform(url);
  if (!safe) return "";
  try {
    const origin = typeof location !== "undefined" ? location.origin : "http://localhost";
    const u = new URL(safe, `${origin}/documents/${docId}`);
    // same origin or not: aidoc may be reached under several names (127.0.0.1, a LAN or tailnet address)
    if (/^\/api(\/|$)/i.test(u.pathname)) return "";
  } catch {
    return "";
  }
  return safe;
}

/** The same URL pass react-markdown's urlTransform made: every URL attribute of every element. */
function rewriteUrls(tree: Root, docId: string, version?: string | number): Root {
  visit(tree, "element", (el: Element) => {
    for (const key in urlAttributes) {
      if (!Object.hasOwn(urlAttributes, key) || !Object.hasOwn(el.properties, key)) continue;
      const test = urlAttributes[key];
      if (test === null || test.includes(el.tagName)) el.properties[key] = markdownUrl(String(el.properties[key] || ""), docId, version);
    }
  });
  return tree;
}

function pageAnchorDiv(warnings: ReadonlyMap<number, PageWarning> | undefined) {
  return ({ node: _node, ...props }: { node?: unknown; children?: ReactNode; [k: string]: unknown }) => {
    const page = Number(props["data-page"]);
    const w = Number.isFinite(page) ? warnings?.get(page) : undefined;
    if (!w) return <div {...props} />;
    return (
      <div {...props} className={`${String(props.className ?? "")} page-anchor-warned`}>
        {props.children as ReactNode}
        <span
          data-page-warning={page}
          className={
            "page-warning-chip absolute top-2 left-10 rounded px-1.5 py-0.5 text-[11px] leading-tight " +
            (w.tone === "warn" ? "bg-warn-soft text-warn" : "bg-info-soft text-info")
          }
        >
          {w.text}
        </span>
      </div>
    );
  };
}

const baseComponents: Components = {
  a: ({ node: _node, href, ...props }: { node?: unknown; href?: string; children?: ReactNode }) =>
    !href ? (
      <span>{props.children}</span>
    ) : href.startsWith("#") ? (
      <a href={href} {...props} />
    ) : (
      <a href={href} {...props} target="_blank" rel="noreferrer noopener" />
    ),
  img: ({ node: _node, src, ...props }: { node?: unknown; src?: string }) =>
    src ? <img src={src} {...props} loading="lazy" decoding="async" /> : null,
} as Components;

function toReact(tree: Root, docId: string, warnings?: ReadonlyMap<number, PageWarning>, version?: string | number): ReactNode {
  const components = warnings?.size ? ({ ...baseComponents, div: pageAnchorDiv(warnings) } as Components) : baseComponents;
  return toJsxRuntime(rewriteUrls(tree, docId, version), {
    Fragment,
    jsx,
    jsxs,
    components,
    ignoreInvalidStyle: true,
    passKeys: true,
    passNode: true,
  } as Parameters<typeof toJsxRuntime>[1]);
}

type Props = {
  markdown: string;
  docId: string;
  /** the scroll container; with it, large documents are rendered a block at a time near the viewport */
  scrollRef?: React.RefObject<HTMLElement | null>;
  /** notes shown at the page anchors of flagged pages (spec 2026-10-01 §9.4) */
  warnings?: ReadonlyMap<number, PageWarning>;
  /** the document's conversion version (created_at): appended to asset URLs */
  version?: string | number;
};

function MarkdownViewImpl({ markdown, docId, scrollRef, warnings, version }: Props) {
  if (markdown.length <= SMALL || !scrollRef)
    return <WholeMarkdown markdown={markdown} docId={docId} warnings={warnings} version={version} />;
  return <BlockMarkdown markdown={markdown} docId={docId} scrollRef={scrollRef} warnings={warnings} version={version} />;
}

function WholeMarkdown({ markdown, docId, warnings, version }: { markdown: string; docId: string; warnings?: ReadonlyMap<number, PageWarning>; version?: string | number }) {
  const content = useMemo(() => toReact(processBlock(markdown, NONCE), docId, warnings, version), [markdown, docId, warnings, version]);
  return <div className="md-body">{content}</div>;
}

/** px per character / per image of the first guess at a block's height (calibrated by measurements) */
function estimateBlock(b: MdBlock, width: number): number {
  const perLine = Math.max(20, width / 7.6);
  return Math.ceil(b.text.length / perLine) * 26 + b.images * 280;
}

const KEEP_TREES = 24;

function BlockMarkdown({ markdown, docId, scrollRef, warnings, version }: Omit<Required<Props>, "warnings" | "version"> & Pick<Props, "warnings" | "version">) {
  const blocks = useMemo(() => splitMarkdown(markdown), [markdown]);
  const bodyRef = useRef<HTMLDivElement>(null);
  const renderer = useMemo(() => new BlockRenderer(NONCE), []);
  useEffect(() => () => renderer.dispose(), [renderer]);

  // rendered blocks (LRU); the first one synchronously so the first screen does not wait for the worker
  const trees = useRef(new Map<number, ReactNode>());
  const pending = useRef(new Map<number, () => void>());
  const [, setReady] = useState(0);
  useMemo(() => {
    for (const c of pending.current.values()) c();
    pending.current.clear();
    trees.current = new Map([[0, toReact(processBlock(blocks[0].text, NONCE), docId, warnings, version)]]);
  }, [blocks, docId, warnings, version]);

  const anchorOffsets = useRef<(number[] | undefined)[]>([]);
  useMemo(() => (anchorOffsets.current = []), [blocks]);
  const estimate = useCallback((i: number, w: number) => estimateBlock(blocks[i], w), [blocks]);
  const onMeasured = useCallback((i: number, el: HTMLElement) => {
    if (!blocks[i]?.pages.length) return;
    anchorOffsets.current[i] = Array.from(el.querySelectorAll<HTMLElement>("[data-page]"), (a) => a.offsetTop);
  }, [blocks]);
  const v = useBlockVirtualizer({ count: blocks.length, estimate, scrollRef, bodyRef, overscan: 1200, onMeasured });
  const [lo, hi] = v.range;
  const center = useRef(0);
  center.current = (lo + hi) / 2;

  // ask for the blocks in range (+1 ahead) nearest first; drop requests that fell far behind
  useEffect(() => {
    for (const [i, cancel] of pending.current) {
      if (i < lo - 2 || i > hi + 2) {
        cancel();
        pending.current.delete(i);
      }
    }
    for (let i = Math.max(0, lo - 1); i <= Math.min(blocks.length - 1, hi + 1); i++) {
      if (trees.current.has(i) || pending.current.has(i)) continue;
      pending.current.set(
        i,
        renderer.render(blocks[i].text, () => Math.abs(i - center.current), (tree) => {
          pending.current.delete(i);
          trees.current.set(i, toReact(tree, docId, warnings, version));
          while (trees.current.size > KEEP_TREES) {
            // evict the cached block farthest from the view
            let far = -1;
            for (const k of trees.current.keys()) if (far < 0 || Math.abs(k - center.current) > Math.abs(far - center.current)) far = k;
            trees.current.delete(far);
          }
          setReady((r) => r + 1);
        }),
      );
    }
  }, [lo, hi, blocks, renderer, docId, warnings, version]);
  useEffect(
    () => () => {
      for (const c of pending.current.values()) c();
      pending.current.clear();
    },
    [],
  );

  // the scroll sync reads page positions from here, not from the (mostly unmounted) DOM
  const { tops, heights, offset } = v;
  useLayoutEffect(() => {
    publishAnchors(scrollRef.current, () => mdAnchors(blocks, tops, heights, anchorOffsets.current, offset.current));
  }, [scrollRef, blocks, tops, heights, offset]);
  useEffect(() => () => publishAnchors(scrollRef.current, undefined), [scrollRef]);

  const items: ReactNode[] = [];
  const before = lo > 0 ? v.tops[lo] : 0;
  if (before > 0) items.push(<div key="before" className="md-block" style={{ height: before }} aria-hidden />);
  for (let i = lo; i <= hi && i < blocks.length; i++) {
    const content = trees.current.get(i);
    items.push(
      content ? (
        <div key={`b${i}`} className="md-block" data-block={i} ref={v.measureRef}>
          {content}
        </div>
      ) : (
        <div key={`p${i}`} className="md-block md-placeholder" style={{ height: v.heights[i] }} aria-hidden />
      ),
    );
  }
  const after = hi >= 0 && hi < blocks.length - 1 ? v.total - (v.tops[hi] + v.heights[hi]) : 0;
  if (after > 0) items.push(<div key="after" className="md-block" style={{ height: after }} aria-hidden />);

  return (
    <div ref={bodyRef} className="md-body md-blocks">
      {items}
    </div>
  );
}

export const MarkdownView = memo(MarkdownViewImpl);
