import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

afterEach(() => cleanup());

// jsdom lacks these; components use them
// evaluates (min-width|max-width: Npx) against jsdom's 1024px-wide window; everything else is false
if (!window.matchMedia) {
  window.matchMedia = (q: string) =>
    ({ matches: evalQuery(q), media: q, onchange: null, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {}, dispatchEvent: () => false }) as unknown as MediaQueryList;
}
class RO { observe() {} unobserve() {} disconnect() {} }
(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver ??= RO;
Element.prototype.scrollIntoView ??= function () {};

function evalQuery(q: string): boolean {
  const w = window.innerWidth;
  const min = /min-width:\s*(\d+)px/.exec(q);
  const max = /max-width:\s*(\d+)px/.exec(q);
  if (min) return w >= Number(min[1]);
  if (max) return w <= Number(max[1]);
  return false;
}
