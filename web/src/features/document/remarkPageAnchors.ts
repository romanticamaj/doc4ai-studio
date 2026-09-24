import type { Root, Html, Parent } from "mdast";
import { visit, SKIP } from "unist-util-visit";

const PAGE_RE = /^<!--\s*page:\s*(\d+)\s*-->$/;

/**
 * `<!-- page: N -->` → an empty `<div data-page="N" class="page-anchor">` (spec §6 page 4). react-markdown drops
 * HTML comments, so without this the Document view could not line Markdown up with the source pages.
 * The anchor is a custom mdast node with hName/hProperties, so no raw HTML is needed for it.
 */
export function remarkPageAnchors(options: { nonce?: string } = {}) {
  return (tree: Root) => {
    visit(tree, "html", (node: Html, index, parent: Parent | undefined) => {
      const m = PAGE_RE.exec(node.value.trim());
      if (!m || !parent || index == null) return;
      const inline = parent.type === "paragraph";
      const anchor = {
        type: "pageAnchor",
        data: {
          hName: inline ? "span" : "div",
          // with a nonce (MarkdownView) the class is the nonce and is replaced after sanitizing; without one the
          // final classes are emitted directly
          hProperties: {
            dataPage: Number(m[1]),
            className: options.nonce ? [options.nonce] : inline ? ["page-anchor", "page-anchor-inline"] : ["page-anchor"],
          },
        },
      };
      (parent.children as unknown[]).splice(index, 1, anchor);
      return [SKIP, index + 1];
    });
  };
}
