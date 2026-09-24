import { memo } from "react";
import ReactMarkdown, { defaultUrlTransform } from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeRaw from "rehype-raw";
import rehypeSanitize, { defaultSchema } from "rehype-sanitize";
import rehypeKatex from "rehype-katex";
import "katex/dist/katex.min.css";
import { withToken } from "@/api/client";
import { remarkPageAnchors } from "./remarkPageAnchors";

// Converted documents are untrusted input: raw HTML is allowed only because MinerU writes tables as <table>,
// and everything passes through a sanitizer (no scripts, handlers or styles).
const schema = {
  ...defaultSchema,
  tagNames: [...(defaultSchema.tagNames ?? []), "div", "span", "caption", "colgroup", "col"],
  attributes: {
    ...defaultSchema.attributes,
    div: [...(defaultSchema.attributes?.div ?? []), "dataPage", ["className", "page-anchor"]],
    span: [...(defaultSchema.attributes?.span ?? []), "dataPage", ["className", "page-anchor", "page-anchor-inline"]],
    code: [["className", /^language-./, "math-inline", "math-display"]],
    td: [...(defaultSchema.attributes?.td ?? []), "rowSpan", "colSpan"],
    th: [...(defaultSchema.attributes?.th ?? []), "rowSpan", "colSpan"],
  },
};

function MarkdownViewImpl({ markdown, docId }: { markdown: string; docId: string }) {
  const urlTransform = (url: string) => {
    const u = url.replace(/^\.\//, "");
    if (u.startsWith("assets/")) return withToken(`/api/documents/${docId}/assets/${u.slice("assets/".length)}`);
    return defaultUrlTransform(url);
  };
  return (
    <div className="md-body">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath, remarkPageAnchors]}
        rehypePlugins={[rehypeRaw, [rehypeSanitize, schema], rehypeKatex]}
        urlTransform={urlTransform}
        components={{
          a: ({ node: _node, ...props }) => <a {...props} target="_blank" rel="noreferrer noopener" />,
          img: ({ node: _node, ...props }) => <img {...props} loading="lazy" />,
        }}
      >
        {markdown}
      </ReactMarkdown>
    </div>
  );
}

export const MarkdownView = memo(MarkdownViewImpl);
