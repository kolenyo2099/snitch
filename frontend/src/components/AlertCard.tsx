import { Link } from "react-router-dom";
import { Alert, Diagnostic, UserStatus } from "../types";
import { CaveatChips, SeverityChip } from "./Chips";
import { artifactUrl } from "../api";

const ha = (m2: number) => (m2 / 10000).toFixed(1);

export function AlertCard({ a, onTriage }:
  { a: Alert; onTriage?: (s: UserStatus) => void }) {
  return (
    <div className={`alert-card ${a.confidence === "provisional" ? "provisional" : ""}`}>
      {a.overlay_chip_id
        ? <img className="thumb" src={artifactUrl(a.overlay_chip_id)} alt="change overlay" />
        : <div className="thumb" />}
      <div className="body">
        <div className="spread">
          <div className="row">
            {a.project_uuid
              ? <Link to={`/projects/${a.project_uuid}`}><b>{a.project_name}</b></Link>
              : null}
            <span className="muted">{a.sensed_at.slice(0, 10)}</span>
            <SeverityChip a={a} />
            {a.user_status !== "new" && <span className="chip">{a.user_status}</span>}
          </div>
          <Link className="tiny" to={`/alerts/${a.uuid}`}>open →</Link>
        </div>
        <div className="expl">{a.explanation_text}</div>
        <div className="row" style={{ gap: 6, marginBottom: 8 }}>
          <span className="chip">{ha(a.changed_area_m2)} ha</span>
          <span className="chip">{a.n_components} patch{a.n_components === 1 ? "" : "es"}</span>
          <span className="chip mono">score {a.score.toFixed(2)} / thr {a.threshold}</span>
          <CaveatChips codes={a.caveats} />
        </div>
        {onTriage && (
          <div className="row">
            {(["true", "false", "unclear", "acknowledged"] as UserStatus[]).map((s) => (
              <button key={s} onClick={() => onTriage(s)}
                      className={a.user_status === s ? "primary" : ""}>
                {s === "true" ? "Real change" : s === "false" ? "False alarm"
                  : s === "unclear" ? "Unclear" : "Acknowledge"}
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

export function DiagnosticCard({ d, onAck }: { d: Diagnostic; onAck?: () => void }) {
  return (
    <div className="alert-card diag-card">
      <div className="body">
        <div className="spread">
          <div className="row">
            <span className="chip mono">{d.code}</span>
            <span className="chip sev-high">{d.severity}</span>
            <span className="muted tiny">{d.occurred_at.replace("T", " ").slice(0, 16)}</span>
          </div>
          {onAck && !d.acknowledged && <button onClick={onAck}>Acknowledge</button>}
        </div>
        <div className="expl">{d.message}</div>
        {d.detail?.remedy && (
          <div className="diagnostic-remedy">
            <b>What to do:</b> {d.detail.remedy}
          </div>
        )}
      </div>
    </div>
  );
}
