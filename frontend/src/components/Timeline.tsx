import { useMemo } from "react";
import { Observation, Run } from "../types";

/**
 * Three distinct states per date (§9.1): green observed+scored, amber observed but
 * insufficient valid pixels, grey no acquisition. Amber and grey never render alike.
 */
type Tick = {
  date: string; state: "scored" | "gated" | "none";
  score: number | null; run?: Run; obs?: Observation;
};

const COLOR = { scored: "var(--green)", gated: "var(--amber)", none: "var(--grey)" };

export function Timeline({ observations, runs, threshold, units, polarity, onSelect }: {
  observations: Observation[]; runs: Run[]; threshold: number;
  units: string; polarity?: string; onSelect?: (t: Tick) => void;
}) {
  /* Not every detector alerts on a high score: p-value detectors alert on a score at
   * or *below* the threshold, so the crossing test runs the other way for them. */
  const lower = polarity === "lower_is_more_change";
  const crosses = (s: number) => (lower ? s <= threshold : s >= threshold);
  const ticks = useMemo<Tick[]>(() => {
    const byObs = new Map(runs.filter(r => r.target_observation_id)
      .map(r => [r.target_observation_id!, r]));
    const rows: Tick[] = observations
      .slice().sort((a, b) => a.sensed_at.localeCompare(b.sensed_at))
      .map((o) => {
        const run = byObs.get(o.id);
        const scored = run?.status === "ok";
        return {
          date: o.sensed_at.slice(0, 10),
          state: scored ? "scored" : "gated",
          score: scored ? run!.summary?.score_p99 ?? null : null,
          run, obs: o,
        };
      });
    // expected-but-absent acquisition slots become explicit grey ticks
    const out: Tick[] = [];
    for (let i = 0; i < rows.length; i++) {
      out.push(rows[i]);
      if (i + 1 < rows.length) {
        const gap = (Date.parse(rows[i + 1].date) - Date.parse(rows[i].date)) / 864e5;
        for (let k = 1; k * 5 < gap - 2 && k < 12; k++) {
          const d = new Date(Date.parse(rows[i].date) + k * 5 * 864e5);
          out.push({ date: d.toISOString().slice(0, 10), state: "none", score: null });
        }
      }
    }
    return out.sort((a, b) => a.date.localeCompare(b.date));
  }, [observations, runs]);

  if (!ticks.length) return <p className="muted">No observations yet.</p>;

  const W = Math.max(760, ticks.length * 13), H = 210, PAD = 34, TRACK = 172;
  const scores = ticks.map(t => t.score).filter((s): s is number => s != null);
  const maxY = Math.max(threshold * 1.4, ...scores, 1);
  const x = (i: number) => PAD + (i * (W - PAD - 14)) / Math.max(ticks.length - 1, 1);
  const y = (v: number) => TRACK - (v / maxY) * (TRACK - 14);
  const line = ticks.filter(t => t.score != null)
    .map((t) => `${x(ticks.indexOf(t))},${y(t.score!)}`).join(" ");

  return (
    <div className="timeline">
      <svg width={W} height={H} role="img" aria-label="observation timeline">
        {[0, 0.5, 1].map((f) => (
          <g key={f}>
            <line x1={PAD} x2={W - 10} y1={y(maxY * f)} y2={y(maxY * f)} stroke="var(--line)" />
            <text x={4} y={y(maxY * f) + 4} fill="var(--faint)" fontSize="10">
              {(maxY * f).toFixed(1)}
            </text>
          </g>
        ))}
        <line x1={PAD} x2={W - 10} y1={y(threshold)} y2={y(threshold)}
              stroke="var(--red)" strokeDasharray="5 4" />
        <text x={W - 8} y={y(threshold) - 4} fill="var(--red)" fontSize="10"
              textAnchor="end">threshold {threshold} {units}</text>
        {line && <polyline points={line} fill="none" stroke="var(--accent)" strokeWidth="1.5" />}
        {ticks.map((t, i) => (
          <g key={t.date + i} className="tick" onClick={() => onSelect?.(t)}>
            <title>
              {t.date} — {t.state === "scored"
                ? `scored, ${t.score?.toFixed(2)} ${units}, ${(t.obs!.valid_fraction * 100).toFixed(0)}% usable`
                : t.state === "gated"
                  ? `observed but not scored: ${t.obs?.rejection_reason || "below the valid-pixel gate"}`
                  : "no acquisition covering this site"}
            </title>
            {t.score != null && (
              <circle cx={x(i)} cy={y(t.score)} r={crosses(t.score) ? 4 : 2.5}
                      fill={crosses(t.score) ? "var(--red)" : "var(--accent)"} />
            )}
            <rect x={x(i) - 4} y={TRACK + 8} width={8} height={t.state === "none" ? 8 : 16}
                  rx={2} fill={COLOR[t.state]}
                  opacity={t.state === "none" ? 0.55 : 1} />
          </g>
        ))}
        <text x={PAD} y={H - 4} fill="var(--faint)" fontSize="10">{ticks[0].date}</text>
        <text x={W - 10} y={H - 4} fill="var(--faint)" fontSize="10" textAnchor="end">
          {ticks[ticks.length - 1].date}
        </text>
      </svg>
      <div className="legend">
        <span><i className="swatch" style={{ background: COLOR.scored }} />observed and scored</span>
        <span><i className="swatch" style={{ background: COLOR.gated }} />observed, too few valid pixels</span>
        <span><i className="swatch" style={{ background: COLOR.none, opacity: .55 }} />no acquisition</span>
      </div>
    </div>
  );
}
