import type { Anchor } from "./useScrollSync";

/**
 * Page anchors of a block-virtualized Markdown pane in scroll-content coordinates. A mounted block reports where
 * its anchors really are (`measured[i]`, px from the block top); for a block that is only a placeholder the anchor
 * is estimated from its position in the block's text.
 */
export function mdAnchors(
  blocks: { pages: { page: number; at: number }[] }[],
  tops: ArrayLike<number>,
  heights: ArrayLike<number>,
  measured: (number[] | undefined)[],
  base: number,
): Anchor[] {
  const out: Anchor[] = [];
  for (let i = 0; i < blocks.length; i++) {
    const pages = blocks[i].pages;
    if (!pages.length) continue;
    const m = measured[i];
    const exact = m && m.length === pages.length;
    for (let k = 0; k < pages.length; k++) {
      const off = exact ? m[k] : Math.round(pages[k].at * heights[i]);
      out.push({ page: pages[k].page, top: base + tops[i] + off });
    }
  }
  return out;
}
