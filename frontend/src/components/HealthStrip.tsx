import { Link } from "react-router-dom";
import { Project, ProjectHealth } from "../types";

/** A project that has silently stopped seeing data must be as loud as one alerting (§9.4). */
export function HealthStrip({ items }: { items: { p: Project; h: ProjectHealth }[] }) {
  if (!items.length) return null;
  return (
    <div className="health-strip">
      {items.map(({ p, h }) => {
        const errs = h.diagnostics?.error || 0;
        const warns = h.diagnostics?.warning || 0;
        const stale = h.days_since != null && h.days_since > 20;
        const cls = errs || p.status === "failed" ? "bad"
          : warns || stale || p.status === "paused" ? "warn" : "ok";
        const label = errs ? `${errs} error${errs > 1 ? "s" : ""}`
          : warns ? `${warns} warning${warns > 1 ? "s" : ""}` : null;
        return (
          <Link key={p.uuid} className={`health-card ${cls}`} to={`/projects/${p.uuid}`}>
            <div className="name">{p.name}</div>
            <div className="meta">
              {h.last_usable_observation
                ? <>Last seen {h.last_usable_observation.slice(0, 10)} · {h.days_since}d ago</>
                : <>No usable observation yet</>}
              <br />
              {/* These are grouped by code on the Health tab; the raw count is a
                  count of repeats, not of distinct problems. */}
              {errs ? <span style={{ color: "var(--red)" }}>{errs} error{errs > 1 ? "s" : ""}</span> : null}
              {errs && warns ? " · " : null}
              {warns ? <span style={{ color: "var(--amber)" }}>{warns} warning{warns > 1 ? "s" : ""}</span> : null}
              {label ? <span className="muted"> open</span> : "No open diagnostics"}
              <br />
              {p.status === "active"
                ? <>Next poll {h.next_poll ? new Date(h.next_poll).toLocaleString() : "unscheduled"}</>
                : p.status === "calibrating"
                  ? <span style={{ color: "var(--amber)" }}>Calibrating — no alerts while this runs</span>
                  : <>Status: {p.status}</>}
              {stale && p.status === "active" && (
                <><br /><span style={{ color: "var(--amber)" }}>
                  No usable imagery for {h.days_since} days
                </span></>
              )}
            </div>
          </Link>
        );
      })}
    </div>
  );
}
