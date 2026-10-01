import { useCallback, useMemo, useRef, type ReactNode } from "react";
import { useBlockVirtualizer } from "./useBlockVirtualizer";

/** Text split at line breaks into slices of about `size` characters (joined they give back the text). */
export function splitLines(text: string, size: number): string[] {
  const out: string[] = [];
  let start = 0;
  while (start < text.length) {
    let end = Math.min(text.length, start + size);
    if (end < text.length) {
      const nl = text.indexOf("\n", end);
      end = nl < 0 ? text.length : nl + 1;
    }
    out.push(text.slice(start, end));
    start = end;
  }
  return out.length ? out : [""];
}

const SMALL = 48_000;
const LINE = 12 * 1.65;

/**
 * The Markdown source. A multi-megabyte <pre> is itself slow to lay out, so large sources are cut into slices and
 * only the slices near the viewport are mounted (the "複製 Markdown" button still copies the whole text).
 */
export function RawSource({ text, scrollRef }: { text: string; scrollRef: React.RefObject<HTMLElement | null> }) {
  if (text.length <= SMALL) {
    return <pre className="min-h-full bg-muted/30 px-5 py-4 font-mono text-[12px] leading-[1.65] whitespace-pre-wrap break-words">{text}</pre>;
  }
  return <RawSlices text={text} scrollRef={scrollRef} />;
}

function RawSlices({ text, scrollRef }: { text: string; scrollRef: React.RefObject<HTMLElement | null> }) {
  const slices = useMemo(() => splitLines(text, 16_000), [text]);
  const lines = useMemo(() => slices.map((s) => s.split("\n").length), [slices]);
  const bodyRef = useRef<HTMLDivElement>(null);
  const estimate = useCallback(
    (i: number, w: number) => {
      // a wrapped line is about (length / chars per line) lines
      const perLine = Math.max(20, w / 7.3);
      return Math.max(lines[i], Math.ceil(slices[i].length / perLine)) * LINE;
    },
    [slices, lines],
  );
  const v = useBlockVirtualizer({ count: slices.length, estimate, scrollRef, bodyRef, overscan: 1200 });
  const [lo, hi] = v.range;
  const items: ReactNode[] = [];
  if (lo > 0) items.push(<div key="before" style={{ height: v.tops[lo] }} aria-hidden />);
  for (let i = lo; i <= hi && i < slices.length; i++) {
    items.push(
      <pre key={i} data-block={i} ref={v.measureRef} className="m-0 font-mono text-[12px] leading-[1.65] whitespace-pre-wrap break-words">
        {slices[i]}
      </pre>,
    );
  }
  const after = hi >= 0 && hi < slices.length - 1 ? v.total - (v.tops[hi] + v.heights[hi]) : 0;
  if (after > 0) items.push(<div key="after" style={{ height: after }} aria-hidden />);
  return (
    <div className="min-h-full bg-muted/30 px-5 py-4">
      <div ref={bodyRef}>{items}</div>
    </div>
  );
}
