type PdfLike = {
  numPages: number;
  getPage: (n: number) => Promise<{ getViewport: (o: { scale: number }) => { width: number; height: number } }>;
};

/** height / width of the first `count` pages, loaded in parallel (each costs a few small range requests). */
export async function firstRatios(doc: PdfLike, count: number): Promise<Map<number, number>> {
  const out = new Map<number, number>();
  const pages = Array.from({ length: Math.min(count, doc.numPages) }, (_, i) => i + 1);
  await Promise.all(
    pages.map((n) =>
      doc.getPage(n).then((p) => {
        const vp = p.getViewport({ scale: 1 });
        out.set(n, vp.height / vp.width);
      }),
    ),
  );
  return out;
}

/**
 * height / width of every page: the real ratio of each page loaded so far, and for the others the ratio of the
 * nearest loaded page before them (pages of one PDF can differ in size and orientation, M5); A4 without any.
 */
export function estimateRatios(numPages: number, known: Map<number, number>): number[] {
  const out = new Array<number>(numPages);
  let first = 1.4142;
  for (let n = 1; n <= numPages; n++) {
    const r = known.get(n);
    if (r != null) {
      first = r;
      break;
    }
  }
  let cur = first;
  for (let n = 1; n <= numPages; n++) {
    const r = known.get(n);
    if (r != null) cur = r;
    out[n - 1] = cur;
  }
  return out;
}
