import { unified, type Processor } from "unified";
import remarkParse from "remark-parse";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import remarkRehype from "remark-rehype";
import rehypeRaw from "rehype-raw";
import rehypeSanitize, { defaultSchema, type Options as SanitizeSchema } from "rehype-sanitize";
import rehypeKatex from "rehype-katex";
import { visit } from "unist-util-visit";
import type { Element, Root } from "hast";
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type VFile = any;
import { remarkPageAnchors } from "./remarkPageAnchors";

/**
 * Markdown block → sanitized hast. Runs in the markdown Web Worker (or on the main thread when workers are not
 * available); the result is plain data. URLs are rewritten afterwards on the main thread (markdownUrl), because
 * the asset token lives in the page's localStorage.
 *
 * Converted documents are untrusted input: raw HTML is allowed only because MinerU writes tables as <table>, and
 * everything passes through the sanitizer (no scripts, handlers or styles).
 */
export function sanitizeSchema(nonce: string): SanitizeSchema {
  return {
    ...defaultSchema,
    tagNames: [...(defaultSchema.tagNames ?? []), "div", "span", "caption", "colgroup", "col"],
    attributes: {
      ...defaultSchema.attributes,
      div: [...(defaultSchema.attributes?.div ?? []), "dataPage", ["className", nonce]],
      span: [...(defaultSchema.attributes?.span ?? []), "dataPage", ["className", nonce]],
      code: [["className", /^language-./, "math-inline", "math-display"]],
      td: [...(defaultSchema.attributes?.td ?? []), "rowSpan", "colSpan"],
      th: [...(defaultSchema.attributes?.th ?? []), "rowSpan", "colSpan"],
    },
  };
}

/**
 * rehype-raw re-parses the whole tree through parse5, which is by far the slowest step; it is only needed when the
 * block actually contains raw HTML.
 */
function rehypeRawIfNeeded() {
  const raw = rehypeRaw() as unknown as (tree: Root, file: VFile) => Root;
  return (tree: Root, file: VFile) => {
    let found = false;
    visit(tree, (n) => {
      if ((n as { type: string }).type === "raw") {
        found = true;
        return false;
      }
    });
    return found ? raw(tree, file) : tree;
  };
}

/** After sanitizing: our anchors (per-load nonce class) get their real classes; data-page anywhere else is removed. */
function rehypeFinalizeAnchors(options: { nonce: string }) {
  return (tree: Root) => {
    visit(tree, (n) => {
      delete (n as { position?: unknown }).position;
      if (n.type !== "element") return;
      const el = n as Element;
      const cls = el.properties?.className;
      const ours = Array.isArray(cls) && cls.includes(options.nonce);
      if (ours) {
        el.properties.className = el.tagName === "span" ? ["page-anchor", "page-anchor-inline"] : ["page-anchor"];
      } else if (el.properties && "dataPage" in el.properties) {
        delete el.properties.dataPage;
      }
    });
  };
}

let cached: { nonce: string; proc: Processor<any, any, any, any, any> } | null = null;

function processor(nonce: string) {
  if (cached?.nonce === nonce) return cached.proc;
  const proc = unified()
    .use(remarkParse)
    .use(remarkGfm)
    .use(remarkMath)
    .use(remarkPageAnchors, { nonce })
    .use(remarkRehype, { allowDangerousHtml: true })
    .use(rehypeRawIfNeeded)
    .use(rehypeSanitize, sanitizeSchema(nonce))
    .use(rehypeFinalizeAnchors, { nonce })
    .use(rehypeKatex);
  cached = { nonce, proc: proc as unknown as Processor<any, any, any, any, any> };
  return cached.proc;
}

export function processBlock(markdown: string, nonce: string): Root {
  const proc = processor(nonce);
  return proc.runSync(proc.parse(markdown)) as Root;
}
