import { expect, test } from "vitest";
import { splitMarkdown } from "../splitMarkdown";

const para = (i: number, size = 400) => `Paragraph ${i} ` + "lorem ipsum ".repeat(Math.ceil(size / 12));
const join = (parts: string[]) => parts.join("\n\n") + "\n";

test("a small document is a single block holding the whole text", () => {
  const md = "# T\n\nhello\n";
  const b = splitMarkdown(md);
  expect(b).toHaveLength(1);
  expect(b[0].text).toBe(md);
});

test("blocks are contiguous slices: joined they give back the document exactly", () => {
  const md = join(Array.from({ length: 300 }, (_, i) => para(i)));
  const b = splitMarkdown(md, { target: 5000, first: 5000 });
  expect(b.length).toBeGreaterThan(10);
  expect(b.map((x) => x.text).join("")).toBe(md);
  expect(b[0].start).toBe(0);
  for (let i = 1; i < b.length; i++) expect(b[i].start).toBe(b[i - 1].end);
  // every cut lands at the start of a top-level block (after a blank line)
  for (const x of b.slice(1)) expect(md.slice(x.start - 2, x.start)).toBe("\n\n");
});

test("without page markers it splits by size near the target", () => {
  const md = join(Array.from({ length: 300 }, (_, i) => para(i)));
  const b = splitMarkdown(md, { target: 5000, first: 5000 });
  for (const x of b.slice(0, -1)) {
    expect(x.text.length).toBeGreaterThanOrEqual(5000);
    expect(x.text.length).toBeLessThan(5000 + 600);
  }
  expect(b.every((x) => x.pages.length === 0)).toBe(true);
});

test("the first block is smaller so the first screen renders fast", () => {
  const md = join(Array.from({ length: 300 }, (_, i) => para(i)));
  const b = splitMarkdown(md, { target: 20000, first: 3000 });
  expect(b[0].text.length).toBeLessThan(4000);
  expect(b[1].text.length).toBeGreaterThanOrEqual(20000);
});

test("never cuts inside fenced code, $$ math, HTML tables or GFM tables", () => {
  const big = (s: string) => Array.from({ length: 60 }, () => s);
  const md = join([
    para(1),
    "```js\n" + big("let x = 1;\n\nlet y = 2;").join("\n") + "\n```",
    para(2),
    "$$\n" + big("a + b \\\\\n").join("\n") + "\n$$",
    para(3),
    "<table>\n" + big("<tr><td>cell</td></tr>\n").join("\n") + "\n</table>",
    para(4),
    big("| a | b |").join("\n"),
    para(5),
  ]);
  const b = splitMarkdown(md, { target: 200, first: 200 });
  expect(b.map((x) => x.text).join("")).toBe(md);
  for (const x of b) {
    const fences = (x.text.match(/^```/gm) ?? []).length;
    expect(fences % 2).toBe(0);
    const maths = (x.text.match(/^\$\$$/gm) ?? []).length;
    expect(maths % 2).toBe(0);
    expect((x.text.match(/<table>/g) ?? []).length).toBe((x.text.match(/<\/table>/g) ?? []).length);
  }
  const tableBlock = b.find((x) => x.text.includes("| a | b |"))!;
  expect((tableBlock.text.match(/\| a \| b \|/g) ?? []).length).toBe(60);
});

test("page markers are preferred cut points and recorded with their position", () => {
  const pages = Array.from({ length: 6 }, (_, i) => `<!-- page: ${i + 1} -->\n\n${para(i, 3000)}`);
  const md = join(pages);
  const b = splitMarkdown(md, { target: 100000, first: 100000, minPage: 1000 });
  expect(b).toHaveLength(6);
  b.forEach((x, i) => {
    expect(x.text.startsWith(`<!-- page: ${i + 1} -->`)).toBe(true);
    expect(x.pages).toEqual([{ page: i + 1, at: 0 }]);
  });
});

test("small pages are merged until minPage; big pages are still cut by size (30 markers / 1192 pages)", () => {
  const md = join(Array.from({ length: 40 }, (_, i) => `<!-- page: ${i + 1} -->\n\n${para(i, 500)}`));
  const b = splitMarkdown(md, { target: 100000, first: 100000, minPage: 2000 });
  expect(b.length).toBeGreaterThan(5);
  expect(b.length).toBeLessThan(15);
  expect(b.flatMap((x) => x.pages.map((p) => p.page))).toEqual(Array.from({ length: 40 }, (_, i) => i + 1));
  for (const x of b) for (const p of x.pages) expect(x.text.slice(Math.round(p.at * x.text.length)).startsWith("<!-- page:")).toBe(true);

  const few = join([`<!-- page: 1 -->`, ...Array.from({ length: 100 }, (_, i) => para(i)), `<!-- page: 2 -->`, para(200)]);
  const c = splitMarkdown(few, { target: 4000, first: 4000, minPage: 1000 });
  expect(c.length).toBeGreaterThan(8);
  expect(c.flatMap((x) => x.pages.map((p) => p.page))).toEqual([1, 2]);
});

test("inline page markers are recorded but do not cut; markers inside code are ignored", () => {
  const md = join([para(1), "more <!-- page: 3 --> inline", "```\n<!-- page: 9 -->\n```", para(2)]);
  const b = splitMarkdown(md, { target: 50, first: 50, minPage: 0 });
  const pages = b.flatMap((x) => x.pages.map((p) => p.page));
  expect(pages).toEqual([3]);
});

test("an unclosed fence does not stop splitting for the rest of a huge document", () => {
  const md = join(["```", ...Array.from({ length: 400 }, (_, i) => para(i))]);
  const b = splitMarkdown(md, { target: 4000, first: 4000, maxOpen: 16000 });
  expect(b.length).toBeGreaterThan(5);
  expect(b.map((x) => x.text).join("")).toBe(md);
});

test("CRLF documents split the same way", () => {
  const md = join(Array.from({ length: 200 }, (_, i) => para(i))).replace(/\n/g, "\r\n");
  const b = splitMarkdown(md, { target: 5000, first: 5000 });
  expect(b.length).toBeGreaterThan(5);
  expect(b.map((x) => x.text).join("")).toBe(md);
});

test("images are counted for the height estimate", () => {
  const b = splitMarkdown("![a](assets/a.png)\n\ntext ![b](assets/b.png)\n\n<img src=x>\n");
  expect(b[0].images).toBe(3);
});
