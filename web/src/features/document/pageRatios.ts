type PdfLike = {
  numPages: number;
  getPage: (n: number) => Promise<{ getViewport: (o: { scale: number }) => { width: number; height: number } }>;
};

/** height / width of every page (pages of one PDF can differ in size and orientation), 32 at a time. */
export async function pageRatios(doc: PdfLike): Promise<number[]> {
  const out = new Array<number>(doc.numPages);
  for (let start = 1; start <= doc.numPages; start += 32) {
    const batch = [];
    for (let n = start; n < Math.min(start + 32, doc.numPages + 1); n++) {
      batch.push(
        doc.getPage(n).then((p) => {
          const vp = p.getViewport({ scale: 1 });
          out[n - 1] = vp.height / vp.width;
        }),
      );
    }
    await Promise.all(batch);
  }
  return out;
}
