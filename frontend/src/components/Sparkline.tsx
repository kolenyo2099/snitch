export function Sparkline({ values, threshold, label }: {
  values: number[]; threshold?: number; label?: string;
}) {
  if (!values.length) return <span className="muted tiny">no runs</span>;
  const W = 110, H = 26, max = Math.max(...values, threshold ?? 0, 1);
  const pts = values.map((v, i) =>
    `${(i * W) / Math.max(values.length - 1, 1)},${H - (v / max) * (H - 3) - 1}`).join(" ");
  return (
    <svg width={W} height={H} role="img" aria-label={label || "sparkline of recent scores"}>
      {threshold != null && (
        <line x1={0} x2={W} y1={H - (threshold / max) * (H - 3) - 1}
              y2={H - (threshold / max) * (H - 3) - 1} stroke="var(--red)"
              strokeDasharray="3 3" />
      )}
      <polyline points={pts} fill="none" stroke="var(--accent)" strokeWidth="1.4" />
    </svg>
  );
}
