import { expect, test } from "vitest";
import { unified } from "unified";
import remarkParse from "remark-parse";
import remarkRehype from "remark-rehype";
import rehypeStringify from "rehype-stringify";
import { remarkPageAnchors } from "../remarkPageAnchors";

test("page comments become data-page anchors", async () => {
  const out = await unified()
    .use(remarkParse)
    .use(remarkPageAnchors)
    .use(remarkRehype)
    .use(rehypeStringify)
    .process("<!-- page: 1 -->\n\n# T\n\n<!-- page: 12 -->\n\ntext");
  expect(String(out)).toContain('<div data-page="1" class="page-anchor"></div>');
  expect(String(out)).toContain('data-page="12"');
  expect(String(out)).not.toContain("<!--");
});

test("other HTML comments are left alone", async () => {
  const out = await unified().use(remarkParse).use(remarkPageAnchors).use(remarkRehype, { allowDangerousHtml: true }).use(rehypeStringify, { allowDangerousHtml: true })
    .process("<!-- image -->\n\ntext");
  expect(String(out)).toContain("<!-- image -->");
  expect(String(out)).not.toContain("data-page");
});
