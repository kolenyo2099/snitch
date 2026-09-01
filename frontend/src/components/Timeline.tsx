import { useMemo } from "react";
import { Observation, Run } from "../types";

/**
 * Distinct states per date (§9.1): green observed+scored, amber observed but
 * insufficient valid pixels, grey no acquisition — and a fourth the data makes real:
 * red for a run that errored, which used to masquerade as "too cloudy".
 */
type Tick = {
  date: string; state: "scored" | "gated" | "none" | "error";
  score: number | null; run?: Run; obs?: Observation;
  /** Position in the current run of consecutive above-threshold *scores*. This is a
   *  readable proxy, not the confirmation rule itself — the alert engine counts
   *  spatially overlapping crossings, which chips and alerts carry, not ticks. */
  streak?: number;
};

const COLOR = {
  scored: "var(--green)", gated: "var(--amber)", none: "var(--grey)",
  error: "var(--red)",
};

const pctile = (xs: number[], p: number) => {
  const s = xs.slice().sort((a, b) => a - b);
  return s[Math.min(s.length - 1, Math.floor(p * (s.length - 1)))];
};

export function Timeline({ observations, runs, threshold, units, polarity,
                           onSelect }: {
  observations: Observation[]; runs: Run[]; threshold: number;
  units: string; polarity?: string;
  onSelect?: (t: Tick, shift: boolean) => void;
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
          state: scored ? "scored"
            : run?.status === "failed" ? "error" : "gated",
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
    // Count each scored observation's position in its streak of consecutive
    // crossings. This is the same rule the alert engine applies — made visible
    // while it is still pending rather than only in hindsight.
    let streak = 0;
    for (const t of out) {
      if (t.state === "scored" && t.score != null && crosses(t.score)) streak += 1;
      else streak = 0;
      if (t.state === "scored" && streak) t.streak = streak;
    }
    return out.sort((a, b) => a.date.localeCompare(b.date));
  }, [observations, runs, threshold, lower]);

  if (!ticks.length)
    return (
      <div className="muted">
        <b>No observations yet.</b>
        <p className="tiny" style={{ marginBottom: 0 }}>
          Nothing has been acquired for this area. If the monitor is active, the next
          scheduled poll will look for imagery; if it is calibrating, history is still
          being fetched.
        </p>
      </div>
    );

  const W = Math.max(760, ticks.length * 13), H = 216, PAD = 34, TRACK = 172;
  const scores = ticks.map(t => t.score).filter((s): s is number => s != null);
  const maxY = Math.max(threshold * 1.4, ...scores, 1);
  const x = (i: number) => PAD + (i * (W - PAD - 14)) / Math.max(ticks.length - 1, 1);
  const y = (v: number) => TRACK - (v / maxY) * (TRACK - 14);
  const line = ticks.filter(t => t.score != null)
    .map((t) => `${x(ticks.indexOf(t))},${y(t.score!)}`).join(" ");
  const band = scores.length >= 8
    ? { lo: pctile(scores, 0.5), hi: pctile(scores, 0.95) } : null;
  const longest = Math.max(0, ...ticks.map(t => t.streak ?? 0));

  const title = (t: Tick) => {
    if (t.state === "scored")
      return `${t.date} — scored, ${t.score?.toFixed(2)} ${units}, `
        + `${((t.obs!.valid_fraction) * 100).toFixed(0)}% usable`
        + (t.streak && t.streak > 1
          ? ` · ${t.streak} scored dates in a row above threshold`
          : "");
    if (t.state === "error")
      return `${t.date} — run failed: ${t.run?.error?.error || t.run?.skip_reason || "see run detail"}`;
    if (t.state === "gated")
      return `${t.date} — observed but not scored: ${t.obs?.rejection_reason || "below the valid-pixel gate"}`;
    return `${t.date} — no acquisition covering this site`;
  };

  return (
    <div className="timeline">
      <svg width={W} height={H} role="group" aria-label="observation timeline">
        {/* The distribution of past scores, so "above threshold" has context. */}
        {band && (
          <>
            <rect x={PAD} y={y(band.hi)} width={W - PAD - 14}
                  height={Math.max(2, y(band.lo) - y(band.hi))}
                  fill="var(--accent)" opacity={0.08} />
            <text x={PAD + 6} y={y(band.hi) - 4} fill="var(--faint)" fontSize={10}>
              typical range, p50–p95 of past scores
            </text>
          </>
        )}
        {[0, 0.5, 1].map((f) => (
          <g key={f}>
            <line x1={PAD} x2={W - 10} y1={y(maxY * f)} y2={y(maxY * f)} stroke="var(--line)" />
            <text x={4} y={y(maxY * f) + 4} fill="var(--faint)" fontSize="10">
              {(maxY * f).toFixed(1)}{f === 1 && units ? ` ${units}` : ""}
            </text>
          </g>
        ))}
        <line x1={PAD} x2={W - 10} y1={y(threshold)} y2={y(threshold)}
              stroke="var(--red)" strokeDasharray="5 4" />
        <text x={W - 8} y={y(threshold) - 4} fill="var(--red)" fontSize="10"
              textAnchor="end">threshold {threshold} {units}</text>
        <polyline points={line} fill="none" stroke="var(--accent)" strokeWidth="1.5" />
        {ticks.map((t, i) => (
          <g key={t.date + i}
             role={t.run?.status === "ok" ? "button" : undefined}
             tabIndex={t.run?.status === "ok" ? 0 : undefined}
             aria-label={title(t)}
             className={`tick ${t.run?.status === "ok" ? "" : "tick-dead"}`}
             onClick={(e) => t.run?.status === "ok" && onSelect?.(t, e.shiftKey)}
             onKeyDown={(e) => {
               if (t.run?.status === "ok" && (e.key === "Enter" || e.key === " ")) {
                 e.preventDefault(); onSelect?.(t, e.shiftKey);
               }
             }}>
            <title>{title(t)}</title>
            {/* The hit band is the whole column: an 8px rect next to a 13px pitch
                made the timeline feel broken to click. */}
            <rect className="tick-band" x={x(i) - 6.5} y={6} width={13} height={H - 12}
                  fill="transparent" />
            {t.score != null && (
              <circle cx={x(i)} cy={y(t.score)} r={crosses(t.score) ? 4 : 2.5}
                      fill={crosses(t.score) ? "var(--red)" : "var(--accent)"}
                      stroke={t.streak && t.streak > 1 ? "var(--red)" : "none"}
                      strokeWidth={t.streak && t.streak > 1 ? 1.5 : 0} />
            )}
            {t.streak != null && t.streak > 1 && (
              <text x={x(i)} y={TRACK + 2} fontSize={9} fill="var(--red)"
                    textAnchor="middle">{Math.min(t.streak, 9)}</text>
            )}
            <rect x={x(i) - 4} y={TRACK + 8} width={8} height={t.state === "none" ? 8 : 16}
                  rx={2} fill={COLOR[t.state]}
                  opacity={t.state === "none" ? 0.55 : 1}
                  stroke={t.state === "none" ? "var(--line)" : "none"}
                  strokeDasharray={t.state === "none" ? "2 2" : undefined} />
          </g>
        ))}
        {/* One date at each end told you nothing about the middle. */}
        {ticks.map((t, i) => {
          const every = Math.max(1, Math.ceil(ticks.length / 8));
          if (i % every || i > ticks.length - every / 2) return null;
          return (
            <g key={`lab${i}`}>
              <line x1={x(i)} x2={x(i)} y1={TRACK + 26} y2={TRACK + 30} stroke="var(--line)" />
              <text x={x(i)} y={H - 4} fill="var(--faint)" fontSize="10" textAnchor="middle">
                {t.date.slice(0, 7)}
              </text>
            </g>
          );
        })}
        <text x={W - 10} y={H - 4} fill="var(--faint)" fontSize="10" textAnchor="end">
          {ticks[ticks.length - 1].date}
        </text>
      </svg>
      <div className="legend">
        <span><i className="swatch" style={{ background: COLOR.scored }} />observed and scored</span>
        <span><i className="swatch" style={{ background: COLOR.gated }} />observed, too few valid pixels</span>
        <span><i className="swatch" style={{ background: COLOR.none, opacity: .55 }} />no acquisition</span>
        <span><i className="swatch" style={{ background: COLOR.error }} />run failed</span>
        {longest > 1 && (
          <span style={{ color: "var(--red)" }}>
            ring + number = {longest} scored dates in a row above threshold
          </span>
        )}
      </div>
    </div>
  );
}
