import { memo } from "react";
import ReactMarkdown, { defaultUrlTransform } from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeRaw from "rehype-raw";
import rehypeSanitize, { defaultSchema } from "rehype-sanitize";
import rehypeKatex from "rehype-katex";
import { visit } from "unist-util-visit";
import type { Element, Root } from "hast";
import "katex/dist/katex.min.css";
import { withToken } from "@/api/client";
import { remarkPageAnchors } from "./remarkPageAnchors";

// Page anchors made by remarkPageAnchors carry this per-load class; raw HTML in a converted document cannot guess
// it, so after sanitizing only our own anchors keep data-page (a fake anchor could hijack the scroll sync).
const NONCE = `pa${Array.from(crypto.getRandomValues(new Uint8Array(8)), (b) => b.toString(16).padStart(2, "0")).join("")}`;

// Converted documents are untrusted input: raw HTML is allowed only because MinerU writes tables as <table>,
// and everything passes through a sanitizer (no scripts, handlers or styles).
const schema = {
  ...defaultSchema,
  tagNames: [...(defaultSchema.tagNames ?? []), "div", "span", "caption", "colgroup", "col"],
  attributes: {
    ...defaultSchema.attributes,
    div: [...(defaultSchema.attributes?.div ?? []), "dataPage", ["className", NONCE]],
    span: [...(defaultSchema.attributes?.span ?? []), "dataPage", ["className", NONCE]],
    code: [["className", /^language-./, "math-inline", "math-display"]],
    td: [...(defaultSchema.attributes?.td ?? []), "rowSpan", "colSpan"],
    th: [...(defaultSchema.attributes?.th ?? []), "rowSpan", "colSpan"],
  },
};

/** After sanitizing: our anchors get their real classes; data-page anywhere else is removed. */
function rehypeFinalizeAnchors() {
  return (tree: Root) => {
    visit(tree, "element", (el: Element) => {
      const cls = el.properties?.className;
      const ours = Array.isArray(cls) && cls.includes(NONCE);
      if (ours) {
        el.properties.className = el.tagName === "span" ? ["page-anchor", "page-anchor-inline"] : ["page-anchor"];
      } else if (el.properties && "dataPage" in el.properties) {
        delete el.properties.dataPage;
      }
    });
  };
}

const SAFE_ASSET = /^(?:\.\/)?assets\/((?:[A-Za-z0-9_@+-][A-Za-z0-9._@+-]*)(?:\/[A-Za-z0-9_@+-][A-Za-z0-9._@+-]*)*)$/;

/**
 * Only a clean `assets/<name>` path (no `..`, `%`, backslash, scheme or leading slash) is rewritten to the asset
 * endpoint, and only that URL gets the API token. Any other link that would land on this server's /api is
 * dropped: a converted document must never be able to point the browser at an API URL (final P4 check C1).
 */
export function markdownUrl(url: string, docId: string): string {
  const m = SAFE_ASSET.exec(url);
  if (m) return withToken(`/api/documents/${docId}/assets/${m[1]}`);
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

function MarkdownViewImpl({ markdown, docId }: { markdown: string; docId: string }) {
  return (
    <div className="md-body">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath, [remarkPageAnchors, { nonce: NONCE }]]}
        rehypePlugins={[rehypeRaw, [rehypeSanitize, schema], rehypeFinalizeAnchors, rehypeKatex]}
        urlTransform={(url) => markdownUrl(url, docId)}
        components={{
          a: ({ node: _node, href, ...props }) =>
            !href ? (
              <span>{props.children}</span>
            ) : href.startsWith("#") ? (
              <a href={href} {...props} />
            ) : (
              <a href={href} {...props} target="_blank" rel="noreferrer noopener" />
            ),
          img: ({ node: _node, src, ...props }) => (src ? <img src={src} {...props} loading="lazy" /> : null),
        }}
      >
        {markdown}
      </ReactMarkdown>
    </div>
  );
}

export const MarkdownView = memo(MarkdownViewImpl);
