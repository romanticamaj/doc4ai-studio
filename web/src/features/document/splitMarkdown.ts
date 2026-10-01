/**
 * Splits a Markdown document into contiguous slices that each parse on their own, so a 5 MB document can be
 * rendered a block at a time. Cuts only at the start of a top-level block (a non-indented line after a blank line,
 * or a `<!-- page: N -->` line), never inside fenced code, `$$` math, an HTML `<table>` or a GFM table (the same
 * atomic units as src/aidoc/chunk.py). Page markers are the preferred cut points; pages smaller than `minPage` are
 * merged, and long stretches (with few or no markers) are cut by size near `target`.
 *
 * Known limits of rendering slices separately: footnotes and reference-style link definitions only resolve within
 * their own block, and an ordered list cut in two restarts at its next item's own number.
 */
export type MdBlock = {
  start: number;
  end: number;
  text: string;
  /** page markers in this block, `at` = position as a fraction (0..1) of the block's text */
  pages: { page: number; at: number }[];
  images: number;
};

export type SplitOptions = { target?: number; first?: number; minPage?: number; maxOpen?: number };

const MARKER_LINE = /^<!--\s*page:\s*(\d+)\s*-->$/;
const MARKER_ANY = /<!--\s*page:\s*(\d+)\s*-->/g;
const FENCE = /^ {0,3}(`{3,}|~{3,})/;
const IMAGES = /!\[[^\]\n]*\]\(|<img\b/gi;

export function splitMarkdown(md: string, opts: SplitOptions = {}): MdBlock[] {
  const target = opts.target ?? 24_000;
  const first = opts.first ?? 8_000;
  const minPage = opts.minPage ?? 8_000;
  const maxOpen = opts.maxOpen ?? 256_000;
  const blocks: MdBlock[] = [];
  let blockStart = 0;
  let marks: { page: number; offset: number }[] = [];

  let fence: { ch: string; len: number } | null = null;
  let math = false;
  let html = false;
  let openedAt = 0;
  let prevBlank = true;

  const close = (end: number) => {
    const text = md.slice(blockStart, end);
    const len = Math.max(1, text.length);
    blocks.push({
      start: blockStart,
      end,
      text,
      pages: marks.map((m) => ({ page: m.page, at: (m.offset - blockStart) / len })),
      images: (text.match(IMAGES) ?? []).length,
    });
    blockStart = end;
    marks = [];
  };

  let pos = 0;
  const n = md.length;
  while (pos < n) {
    let nl = md.indexOf("\n", pos);
    if (nl < 0) nl = n;
    const raw = md.slice(pos, nl);
    const line = raw.endsWith("\r") ? raw.slice(0, -1) : raw;
    const trimmed = line.trim();
    const limit = blocks.length === 0 ? first : target;

    // a construct left open far too long (unclosed fence / $$ / <table>) stops protecting the rest of the document
    if ((fence || math || html) && pos - openedAt > maxOpen) {
      fence = null;
      math = false;
      html = false;
    }
    const open = !!(fence || math || html);
    const marker = !open && MARKER_LINE.test(trimmed);
    const boundary = !open && trimmed !== "" && !/^\s/.test(line) && (marker || prevBlank);
    if (boundary && pos > blockStart) {
      const size = pos - blockStart;
      if ((marker && size >= minPage) || size >= limit) close(pos);
    }

    // advance the construct state with this line
    if (fence) {
      const f = FENCE.exec(line);
      if (f && f[1][0] === fence.ch && f[1].length >= fence.len && line.trim() === f[1]) fence = null;
    } else if (math) {
      if (trimmed.includes("$$")) math = false;
    } else if (html) {
      if (trimmed.toLowerCase().includes("</table>")) html = false;
    } else {
      const f = FENCE.exec(line);
      if (f) {
        fence = { ch: f[1][0], len: f[1].length };
        openedAt = pos;
      } else if (trimmed.startsWith("$$") && (trimmed === "$$" || !trimmed.slice(2).includes("$$"))) {
        math = true;
        openedAt = pos;
      } else if (trimmed.toLowerCase().startsWith("<table") && !trimmed.toLowerCase().includes("</table>")) {
        html = true;
        openedAt = pos;
      }
      if (!fence && !math) {
        MARKER_ANY.lastIndex = 0;
        for (let m = MARKER_ANY.exec(line); m; m = MARKER_ANY.exec(line)) marks.push({ page: Number(m[1]), offset: pos + m.index });
      }
    }
    prevBlank = trimmed === "";
    pos = nl + 1;
  }
  if (blockStart < n || blocks.length === 0) close(n);
  return blocks;
}
