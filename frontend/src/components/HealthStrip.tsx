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
        return (
          <Link key={p.uuid} className={`health-card ${cls}`} to={`/projects/${p.uuid}`}>
            <div className="name">{p.name}</div>
            <div className="meta">
              {h.last_usable_observation
                ? <>Last seen {h.last_usable_observation.slice(0, 10)} · {h.days_since}d ago</>
                : <>No usable observation yet</>}
              <br />
              {errs ? <span style={{ color: "var(--red)" }}>{errs} error{errs > 1 ? "s" : ""}</span> : null}
              {errs && warns ? " · " : null}
              {warns ? <span style={{ color: "var(--amber)" }}>{warns} warning{warns > 1 ? "s" : ""}</span> : null}
              {!errs && !warns ? "No open diagnostics" : null}
              <br />
              {p.status === "active"
                ? <>Next poll {h.next_poll ? new Date(h.next_poll).toLocaleString() : "unscheduled"}</>
                : <>Status: {p.status}</>}
            </div>
          </Link>
        );
      })}
    </div>
  );
}
