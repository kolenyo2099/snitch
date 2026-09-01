import { useState } from "react";
import { Link } from "react-router-dom";
import { artifactUrl } from "../api";
import { ChipImage } from "./Chip";
import { SwipeCompare } from "./MapView";
import { Alert, Observation, Run } from "../types";

/** One rendered frame set for a single acquisition. */
export interface ImageSet {
  key: string;
  sensed_at: string;
  origin: "alert" | "calibration";
  before?: number | null;
  after?: number | null;
  overlay?: number | null;
  href: string;
  hrefLabel: string;
  detector?: string;
  note?: string;
  /** Polarity-correct headline score, so the gallery can answer "show me the most
   *  changed pairs" without opening every run. */
  score?: number | null;
  units?: string;
}

/**
 * Chips are rendered at two points in the pipeline: when a run raises an alert, and
 * when a backtest run crosses the threshold. Nothing else writes an image, so this
 * gathers exactly those rather than implying every acquisition has a picture.
 */
export function collectImagery(alerts: Alert[], runs: Run[], obs: Observation[]): ImageSet[] {
  const obsById = new Map(obs.map((o) => [o.id, o]));
  const sets: ImageSet[] = alerts.map((a) => ({
    key: `alert-${a.uuid}`,
    sensed_at: a.sensed_at,
    origin: "alert" as const,
    before: a.before_chip_id,
    after: a.after_chip_id,
    overlay: a.overlay_chip_id,
    href: `/alerts/${a.uuid}`,
    hrefLabel: "alert detail →",
    note: a.explanation_text,
    score: a.score,
  }));

  for (const r of runs) {
    const chips = (r.summary?.aux as any)?.calibration_chips;
    if (!chips) continue;
    const o = r.target_observation_id ? obsById.get(r.target_observation_id) : undefined;
    sets.push({
      key: `run-${r.uuid}`,
      sensed_at: o?.sensed_at || r.started_at,
      origin: "calibration",
      before: chips.before,
      after: chips.after,
      overlay: chips.overlay,
      href: `/runs/${r.uuid}`,
      hrefLabel: "run detail →",
      detector: r.detector_id,
      note: "Rendered during backtest calibration, not an alert.",
      score: r.summary?.score_headline ?? r.summary?.score_p99 ?? null,
      units: r.summary?.units,
    });
  }
  return sets
    .filter((s) => s.before || s.after || s.overlay)
    .sort((a, b) => b.sensed_at.localeCompare(a.sensed_at));
}

/* Every frame renders, present or not. Omitting the missing ones left rows that were
   silently blank, and a bare <img> onto a 404 shows the browser's broken-image glyph,
   which reads as a corrupted picture rather than an absent file. */
function Frame({ id, label }: { id?: number | null; label: string }) {
  return (
    <figure className="imagery-frame">
      {id
        ? <a href={artifactUrl(id)} target="_blank" rel="noreferrer">
            <ChipImage id={id} alt={label} />
          </a>
        : <ChipImage id={id} alt={label} />}
      <figcaption className="tiny muted">{label}</figcaption>
    </figure>
  );
}

const PAGE = 12;

export function ImageryGallery(
  { alerts, runs, obs, acquired, processed }:
  { alerts: Alert[]; runs: Run[]; obs: Observation[]; acquired: number; processed: number },
) {
  const all = collectImagery(alerts, runs, obs);
  const [origin, setOrigin] = useState<"all" | "alert" | "calibration">("all");
  const [sort, setSort] = useState<"new" | "old" | "score">("new");
  const [shown, setShown] = useState(PAGE);
  const keep = origin === "all" ? all : all.filter((s) => s.origin === origin);
  const sets = keep.slice().sort((a, b) => {
    if (sort === "score")
      return ((b.score ?? -Infinity) - (a.score ?? -Infinity))
        || b.sensed_at.localeCompare(a.sensed_at);
    return sort === "new"
      ? b.sensed_at.localeCompare(a.sensed_at)
      : a.sensed_at.localeCompare(b.sensed_at);
  });
  const page = sets.slice(0, shown);
  const nAlert = all.filter((s) => s.origin === "alert").length;

  if (!sets.length) {
    return (
      <div className="panel muted">
        <b>No rendered imagery yet.</b>
        <p className="tiny" style={{ marginTop: 6 }}>
          {acquired} scene{acquired === 1 ? "" : "s"} acquired, {processed} scored.
          Picture frames are written when a run raises an alert, and when a backtest
          run crosses the threshold — so a project that has seen no change has nothing
          to show here. The Timeline tab lists every acquisition either way.
        </p>
      </div>
    );
  }

  return (
    <div className="grid" style={{ gap: 14 }}>
      <div className="spread">
        <p className="tiny muted" style={{ margin: 0, maxWidth: "60ch" }}>
          {sets.length} rendered frame set{sets.length === 1 ? "" : "s"} from {acquired}{" "}
          acquired scene{acquired === 1 ? "" : "s"}. Before and after use an identical
          stretch, so the comparison cannot manufacture change.
        </p>
        <div className="row" role="group" aria-label="Sort frames">
          {([["new", "Newest"], ["old", "Oldest"], ["score", "Highest score"]] as const).map(
            ([k, label]) => (
              <button key={k} aria-pressed={sort === k}
                      className={sort === k ? "primary" : ""}
                      onClick={() => { setSort(k); setShown(PAGE); }}>
                {label}
              </button>
            ))}
        </div>
        <div className="row" role="group" aria-label="Filter frames">
          {(["all", "alert", "calibration"] as const).map((k) => (
            <button key={k} aria-pressed={origin === k}
                    className={origin === k ? "primary" : ""}
                    onClick={() => { setOrigin(k); setShown(PAGE); }}>
              {k === "all" ? `All (${all.length})`
                : k === "alert" ? `Alerts (${nAlert})`
                : `Calibration (${all.length - nAlert})`}
            </button>
          ))}
        </div>
      </div>
      {page.map((s) => (
        <div className="panel" key={s.key}>
          <div className="spread">
            <div className="row" style={{ gap: 8 }}>
              <b>{s.sensed_at.slice(0, 10)}</b>
              <span className="chip">{s.origin === "alert" ? "alert" : "calibration"}</span>
              {s.detector && <span className="chip mono">{s.detector}</span>}
              {s.score != null && (
                <span className="chip mono"
                      title="This frame's headline score — the same number its run shows">
                  {s.score.toFixed(2)} {s.units || ""}
                </span>
              )}
            </div>
            <Link className="tiny" to={s.href}>{s.hrefLabel}</Link>
          </div>
          <div className="imagery-row">
            <Frame id={s.before} label="Before" />
            <Frame id={s.after} label="After" />
            <Frame id={s.overlay} label="Change overlay" />
          </div>
          {s.note && <p className="tiny muted" style={{ marginTop: 8 }}>{s.note}</p>}
        </div>
      ))}
      {/* 99 frame sets used to render as 297 simultaneous image requests. */}
      {shown < sets.length && (
        <button onClick={() => setShown(shown + PAGE)}>
          Show {Math.min(PAGE, sets.length - shown)} more
          <span className="muted"> ({shown} of {sets.length} shown)</span>
        </button>
      )}
    </div>
  );
}

/** Any two acquisition dates, swiped. The spec's comparator was about two dates of
 *  the *same* site; chips from the same project share the baseline stretch, so
 *  cross-date swiping is legitimate — and it is the fastest way to see "what
 *  actually changed here between these visits". */
export function DateCompare({ sets }: { sets: ImageSet[] }) {
  const ordered = sets.slice().sort((a, b) => a.sensed_at.localeCompare(b.sensed_at));
  const [beforeKey, setBeforeKey] = useState(ordered[0]?.key ?? "");
  const [afterKey, setAfterKey] = useState(ordered[ordered.length - 1]?.key ?? "");
  if (ordered.length < 2) return null;
  const pick = (key: string) => ordered.find((s) => s.key === key);
  const before = pick(beforeKey) ?? ordered[0];
  const after = pick(afterKey) ?? ordered[ordered.length - 1];
  const frame = (s?: ImageSet) =>
    s ? artifactUrl(s.after ?? s.before ?? s.overlay ?? null) : undefined;
  const label = (s: ImageSet) =>
    `${s.sensed_at.slice(0, 10)}${s.score != null ? ` · ${s.score.toFixed(2)} ${s.units || ""}` : ""}`
    + ` · ${s.origin === "alert" ? "alert" : "calibration"}`;

  return (
    <div className="panel">
      <div className="spread">
        <h3 className="card" style={{ margin: 0 }}>Before / after, any two dates</h3>
        <div className="row">
          <label className="tiny muted">Before<br />
            <select aria-label="Before date" value={before.key}
                    onChange={(e) => setBeforeKey(e.target.value)}>
              {ordered.map((s) => <option key={s.key} value={s.key}>{label(s)}</option>)}
            </select>
          </label>
          <label className="tiny muted">After<br />
            <select aria-label="After date" value={after.key}
                    onChange={(e) => setAfterKey(e.target.value)}>
              {ordered.map((s) => <option key={s.key} value={s.key}>{label(s)}</option>)}
            </select>
          </label>
        </div>
      </div>
      <SwipeCompare before={frame(before)} after={frame(after)} />
      <p className="tiny">
        {before.href && <Link className="tiny" to={before.href}>{before.sensed_at.slice(0, 10)} →</Link>}
        {" · "}
        {after.href && <Link className="tiny" to={after.href}>{after.sensed_at.slice(0, 10)} →</Link>}
      </p>
    </div>
  );
}
