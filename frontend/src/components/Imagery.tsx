import { Link } from "react-router-dom";
import { artifactUrl } from "../api";
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
    });
  }
  return sets
    .filter((s) => s.before || s.after || s.overlay)
    .sort((a, b) => b.sensed_at.localeCompare(a.sensed_at));
}

function Frame({ id, label }: { id?: number | null; label: string }) {
  if (!id) return null;
  return (
    <figure className="imagery-frame">
      <a href={artifactUrl(id)} target="_blank" rel="noreferrer">
        <img src={artifactUrl(id)} alt={label} loading="lazy" />
      </a>
      <figcaption className="tiny muted">{label}</figcaption>
    </figure>
  );
}

export function ImageryGallery(
  { alerts, runs, obs, acquired, processed }:
  { alerts: Alert[]; runs: Run[]; obs: Observation[]; acquired: number; processed: number },
) {
  const sets = collectImagery(alerts, runs, obs);

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
      <p className="tiny muted" style={{ margin: 0 }}>
        {sets.length} rendered frame set{sets.length === 1 ? "" : "s"} from {acquired}{" "}
        acquired scene{acquired === 1 ? "" : "s"}. Before and after use an identical
        stretch, so the comparison cannot manufacture change.
      </p>
      {sets.map((s) => (
        <div className="panel" key={s.key}>
          <div className="spread">
            <div className="row" style={{ gap: 8 }}>
              <b>{s.sensed_at.slice(0, 10)}</b>
              <span className="chip">{s.origin === "alert" ? "alert" : "calibration"}</span>
              {s.detector && <span className="chip mono">{s.detector}</span>}
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
    </div>
  );
}
