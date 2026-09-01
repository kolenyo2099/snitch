import { useMemo, useRef, useState } from "react";
import { Run } from "../types";
import { ChipImage } from "./Chip";

/**
 * The core interaction (§13.2 step 3): a histogram of every historical score at this
 * site, a draggable threshold, and the real past dates that would have alerted.
 * The user tunes by looking at their own site, never by understanding a z-score.
 */
export function Calibrate({ runs, threshold, units, semantics, years, polarity,
                            onChange }: {
  runs: Run[]; threshold: number; units: string; semantics?: string;
  years: number; polarity?: string; onChange: (t: number) => void;
}) {
  const wrap = useRef<HTMLDivElement>(null);
  const [drag, setDrag] = useState(false);

  /* Not every detector agrees that a big number is bad. A p-value detector alerts
   * when the score falls *below* the threshold, so the comparison, the shading and
   * the axis all have to run the other way. Getting this wrong would show a user a
   * confident histogram of exactly the wrong dates. */
  const lower = polarity === "lower_is_more_change";
  const scoreOf = (r: Run): number | null | undefined =>
    (r.summary as any)?.score_headline ?? r.summary?.score_p99;
  const crosses = (v: number | null | undefined) =>
    v != null && (lower ? v <= threshold : v >= threshold);

  const scores = useMemo(
    () => runs.map(scoreOf).filter((v): v is number => v != null),
    [runs]);
  const max = lower ? 1 : Math.max(...scores, threshold * 1.3, 1);
  const bins = useMemo(() => {
    const n = 34, out = new Array(n).fill(0);
    scores.forEach((s) => { out[Math.min(n - 1, Math.floor((s / max) * n))]++; });
    return out;
  }, [scores, max]);
  const peak = Math.max(...bins, 1);

  const would = useMemo(
    () => runs.filter((r) => crosses(scoreOf(r))
                          && (r.summary?.changed_area_m2 ?? 0) > 0)
              .sort((a, b) => b.started_at.localeCompare(a.started_at)),
    [runs, threshold]);
  const perWeeks = would.length ? (years * 52) / would.length : 0;

  const H = 170;
  const step = max / 200;
  const clamp = (v: number) => Math.min(max, Math.max(0, v));
  const nudge = (delta: number) =>
    onChange(lower ? +clamp(threshold + delta).toPrecision(2)
                   : Math.round(clamp(threshold + delta) * 100) / 100);
  const setFromX = (clientX: number) => {
    const el = wrap.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    const f = Math.min(1, Math.max(0, (clientX - rect.left) / rect.width));
    onChange(lower ? +(f * max).toPrecision(2) : Math.round(f * max * 100) / 100);
  };

  if (!scores.length)
    return (
      <div className="panel muted">
        <b>No historical scores yet.</b>
        <p className="tiny" style={{ marginBottom: 0 }}>
          The threshold is tuned against this site's own past scores, so calibration has
          to finish before this histogram can appear. Until then the monitor uses the
          recipe's conservative default.
        </p>
      </div>
    );

  return (
    <div>
      {/* Pointer events, not mouse events: the histogram was mouse-only, so on a
          tablet the product's central interaction did nothing at all. role=slider
          plus the key handler make it reachable without a pointer too. */}
      <div ref={wrap} className="panel" role="slider" tabIndex={0}
           aria-label="Alert threshold"
           aria-valuemin={0} aria-valuemax={+max.toFixed(2)} aria-valuenow={threshold}
           aria-valuetext={`${threshold} ${units}, ${would.length} alerts in ${years} years`}
           style={{ position: "relative", padding: "12px 12px 26px", cursor: "ew-resize",
                    userSelect: "none", touchAction: "none" }}
           onPointerDown={(e) => {
             (e.target as Element).setPointerCapture?.(e.pointerId);
             setDrag(true); setFromX(e.clientX);
           }}
           onPointerMove={(e) => drag && setFromX(e.clientX)}
           onPointerUp={() => setDrag(false)}
           onPointerCancel={() => setDrag(false)}
           onKeyDown={(e) => {
             const big = max / 20;
             const d = e.key === "ArrowRight" || e.key === "ArrowUp" ? step
                     : e.key === "ArrowLeft" || e.key === "ArrowDown" ? -step
                     : e.key === "PageUp" ? big : e.key === "PageDown" ? -big : 0;
             if (d) { e.preventDefault(); nudge(d); return; }
             if (e.key === "Home") { e.preventDefault(); onChange(0); }
             if (e.key === "End") { e.preventDefault(); onChange(+max.toFixed(2)); }
           }}>
        <div style={{ display: "flex", alignItems: "flex-end", height: H, gap: 2 }}>
          {bins.map((b, i) => {
            const v = ((i + 0.5) / bins.length) * max;
            return (
              <div key={i} title={`${b} observations near ${v.toFixed(2)} ${units}`}
                   style={{
                     flex: 1, height: `${(b / peak) * 100}%`, minHeight: b ? 2 : 0,
                     background: crosses(v) ? "var(--red)" : "var(--accent)",
                     opacity: crosses(v) ? 0.9 : 0.6, borderRadius: "2px 2px 0 0",
                   }} />
            );
          })}
        </div>
        <div style={{ position: "absolute", top: 6, bottom: 20,
                      left: `${(threshold / max) * 100}%`, width: 2,
                      background: "var(--text)" }}>
          <div className="chip" style={{ position: "absolute", top: -4, left: 6,
                                         whiteSpace: "nowrap" }}>
            {lower ? threshold.toPrecision(2) : threshold.toFixed(2)} {units}
          </div>
        </div>
        <div className="tiny muted" style={{ position: "absolute", bottom: 6, left: 12 }}>0</div>
        <div className="tiny muted" style={{ position: "absolute", bottom: 6, right: 12 }}>
          {max.toFixed(1)} {units}
        </div>
      </div>

      <div className="row" style={{ margin: "12px 0" }}>
        <label className="tiny muted" style={{ flex: 1 }}>
          Threshold
          <input type="range" min={0} max={max} step={max / 400} value={threshold}
                 aria-label="Alert threshold" style={{ width: "100%", display: "block" }}
                 onChange={(e) => onChange(+e.target.value)} />
        </label>
        <label className="tiny muted">
          Exact value
          <input type="number" step={lower ? 0.001 : 0.01} value={threshold}
                 min={0} max={+max.toFixed(2)} aria-label="Alert threshold, exact value"
                 style={{ width: 100, display: "block" }}
                 onChange={(e) => onChange(+e.target.value)} />
        </label>
      </div>

      <div className="panel" style={{ marginBottom: 12 }}>
        <b>At this threshold you would have received {would.length} alert
          {would.length === 1 ? "" : "s"} in {years} years</b>
        <div className="muted">
          {would.length
            ? `roughly one every ${perWeeks < 1 ? "week or less" : `${perWeeks.toFixed(0)} weeks`}.`
            : `nothing at this site would ever have alerted — the threshold may be too ${lower ? "low" : "high"}.`}
        </div>
        {semantics && <p className="tiny muted" style={{ marginBottom: 0 }}>{semantics}</p>}
      </div>

      <div className="grid">
        {would.slice(0, 12).map((r) => (
          <div key={r.uuid} className="panel calibration-event">
            {(r.summary?.aux as any)?.calibration_chips && (
              <div className="calibration-pair" aria-label="Before and after satellite images">
                <ChipImage id={(r.summary?.aux as any).calibration_chips.before}
                           alt={`Before ${r.started_at.slice(0, 10)}`} />
                <ChipImage id={(r.summary?.aux as any).calibration_chips.after}
                           alt={`After ${r.started_at.slice(0, 10)}`} />
              </div>
            )}
            <div>
              <b>{r.started_at.slice(0, 10)}</b>
              <div className="tiny muted">
                {((r.summary?.changed_area_m2 ?? 0) / 10000).toFixed(2)} ha across{" "}
                {r.summary?.n_components} patch{r.summary?.n_components === 1 ? "" : "es"}
              </div>
            </div>
            <span className="mono calibration-score">
              {lower ? scoreOf(r)?.toPrecision(2) : scoreOf(r)?.toFixed(2)} {units}
            </span>
          </div>
        ))}
        {would.length > 12 && (
          <p className="tiny muted">…and {would.length - 12} more.</p>
        )}
      </div>
    </div>
  );
}
