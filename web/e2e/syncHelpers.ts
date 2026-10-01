import type { Page } from "@playwright/test";

export type Pane = "原始檔" | "轉換結果";

type PaneState = { top: number | null; anchorTop: number | null; height: number };

/** In the pane's scroll container: the mounted `[data-page]` with the greatest page number whose top is at or above
 *  the container's top + 25 % of its height (null when no page anchor is mounted); plus where page `n` starts. */
async function paneState(page: Page, pane: Pane, n?: number): Promise<PaneState> {
  return page.evaluate(
    ([label, target]) => {
      const sec = document.querySelector(`section[aria-label="${label}"]`);
      const anchors = sec ? Array.from(sec.querySelectorAll<HTMLElement>("[data-page]")) : [];
      if (!sec || !anchors.length) return { top: null, anchorTop: null, height: 0 };
      let c: HTMLElement | null = anchors[0].parentElement;
      while (c && c !== sec && !/(auto|scroll)/.test(getComputedStyle(c).overflowY)) c = c.parentElement;
      const box = (c ?? (sec as HTMLElement)).getBoundingClientRect();
      const limit = box.top + 0.25 * box.height;
      let best: number | null = null;
      let anchorTop: number | null = null;
      for (const a of anchors) {
        const p = Number(a.dataset.page);
        const t = a.getBoundingClientRect().top;
        if (t <= limit && (best === null || p > best)) best = p;
        if (target != null && p === target && anchorTop === null) anchorTop = t - box.top;
      }
      return { top: best, anchorTop, height: box.height };
    },
    [pane, n ?? null] as const,
  );
}

export async function topPage(page: Page, pane: Pane): Promise<number | null> {
  return (await paneState(page, pane)).top;
}

/** Deterministic (mulberry32), unique, sorted page numbers in 1..n. */
export function pickPages(n: number, count = 20, seed = 20261001): number[] {
  let a = seed >>> 0;
  const rnd = () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
  const out = new Set<number>();
  const want = Math.min(count, n);
  while (out.size < want) out.add(1 + Math.floor(rnd() * n));
  return [...out].sort((x, y) => x - y);
}

async function stable(page: Page, panes: Pane[], ms = 300, timeout = 10_000): Promise<void> {
  const start = Date.now();
  let last = "";
  let since = Date.now();
  while (Date.now() - start < timeout) {
    const now = JSON.stringify(await Promise.all(panes.map((p) => topPage(page, p))));
    if (now !== last) {
      last = now;
      since = Date.now();
    } else if (Date.now() - since >= ms) return;
    await page.waitForTimeout(50);
  }
}

/** 「跳至頁」: type the page number + Enter, then wait until both panes have been still for 300 ms. */
export async function goTo(page: Page, n: number): Promise<void> {
  const input = page.locator('[aria-label="跳至頁碼"]');
  await input.fill(String(n));
  await input.press("Enter");
  await stable(page, ["原始檔", "轉換結果"]);
}

/** Scrolls only `pane` with the mouse wheel (user-style) until page `n` starts at the top of its view, then waits
 *  until the other pane is still for 300 ms. Pages are mounted lazily, so the target cannot be looked up first. */
export async function scrollPaneTo(page: Page, pane: Pane, n: number): Promise<void> {
  const box = await page.locator(`section[aria-label="${pane}"]`).boundingBox();
  if (!box) throw new Error(`pane ${pane} not found`);
  await page.mouse.move(box.x + box.width / 2, box.y + box.height * 0.6);
  for (let step = 0; step < 200; step++) {
    const s = await paneState(page, pane, n);
    if (s.top === n && s.anchorTop !== null && s.anchorTop <= 8 && s.anchorTop > -0.2 * s.height) break;
    let dy: number;
    if (s.anchorTop !== null && Math.abs(s.anchorTop) < 4 * s.height) dy = s.anchorTop - 2;   // page n is mounted: land on it
    else {
      const diff = n - (s.top ?? 1);
      dy = Math.sign(diff || 1) * Math.min(1500, Math.max(200, Math.abs(diff) * 300));
    }
    await page.mouse.wheel(0, dy);
    await page.waitForTimeout(80);
  }
  const other: Pane = pane === "原始檔" ? "轉換結果" : "原始檔";
  await stable(page, [pane, other]);
}
