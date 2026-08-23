import { useEffect, useState } from "react";
import { api } from "../api";

const LABEL: Record<string, string> = {
  poll: "Checking for new imagery",
  run: "Processing a scene",
  baseline: "Fitting the baseline",
  backtest: "Calibrating on this site's history",
  reanalyse: "Re-scoring history with the new parameters",
  export: "Building the evidence export",
};

/** One bar. Determinate when the job knows its denominator, sweeping when it does
 *  not — a search has no page count up front and a fake percentage is worse than
 *  none. */
export function ProgressBar({ label, done, total, note }: {
  label: string; done?: number; total?: number | null; note?: string | null;
}) {
  const pct = total ? Math.min(100, Math.round((done || 0) * 100 / total)) : null;
  return (
    <div role="status" aria-live="polite">
      <div className="spread">
        <b>{label}…</b>
        {pct != null && <span className="tiny mono">{pct}%</span>}
      </div>
      <div className="progress-track" role="progressbar" aria-label={label}
           aria-valuenow={pct ?? undefined} aria-valuemin={0} aria-valuemax={100}>
        <div className={`progress-fill${pct != null ? " det" : ""}`}
             style={pct != null ? { width: `${pct}%` } : undefined} />
      </div>
      <p className="tiny muted" style={{ margin: 0 }}>
        {note || "Working…"}
        {total ? ` · ${done || 0} of ${total}` : ""}
      </p>
    </div>
  );
}

/** Polls a project's job queue and shows a bar for whatever is running right now.
 *  Renders nothing when the queue is idle, so it can sit unconditionally in a page. */
export function ProjectProgress({ uuid, onIdle }: {
  uuid: string; onIdle?: () => void;
}) {
  const [active, setActive] = useState<any[]>([]);

  useEffect(() => {
    let alive = true, wasBusy = false;
    const tick = async () => {
      try {
        const js = (await api.jobs(uuid)).items
          .filter((j: any) => j.status === "queued" || j.status === "leased");
        if (!alive) return;
        setActive(js);
        if (wasBusy && !js.length) onIdle?.();
        wasBusy = js.length > 0;
      } catch { /* a dropped poll is not worth a visible error */ }
    };
    tick();
    const t = setInterval(tick, 3000);
    return () => { alive = false; clearInterval(t); };
  }, [uuid]);

  if (!active.length) return null;
  return (
    <div className="panel grid" style={{ gap: 12 }}>
      {active.map((j) => (
        <ProgressBar key={j.id} label={LABEL[j.kind] || j.kind}
                     done={j.progress_done} total={j.progress_total}
                     note={j.status === "queued"
                       ? "Queued — waiting for a free worker."
                       : j.progress_note} />
      ))}
    </div>
  );
}
