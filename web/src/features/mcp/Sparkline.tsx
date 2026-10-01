export function Sparkline({ values, width = 120, height = 28, tone = "info", label }: { values: number[]; width?: number; height?: number; tone?: "info" | "warn" | "danger"; label: string }) {
  const max = Math.max(1, ...values);
  const n = Math.max(1, values.length - 1);
  const points = values.map((v, i) => `${((i / n) * (width - 2) + 1).toFixed(1)},${(height - 2 - (v / max) * (height - 4) + 1).toFixed(1)}`).join(" ");
  const color = tone === "danger" ? "var(--color-danger)" : tone === "warn" ? "var(--color-warn)" : "var(--color-info)";
  return (
    <svg role="img" aria-label={label} width={width} height={height} viewBox={`0 0 ${width} ${height}`} className="shrink-0">
      <polyline fill="none" stroke={color} strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round" points={points} />
    </svg>
  );
}
