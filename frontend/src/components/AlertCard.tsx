import { Link } from "react-router-dom";
import { Alert, Diagnostic, UserStatus } from "../types";
import { CaveatChips, ScoreValue, SeverityChip } from "./Chips";
import { ChipImage } from "./Chip";

const ha = (m2: number) => (m2 / 10000).toFixed(1);

/** Score units live with the detector, not the alert, so callers that know the
 *  project's recipe pass them in; §13.3 forbids a bare number. */
export function AlertCard({ a, units, semantics, selected, onSelect, onTriage }:
  { a: Alert; units?: string; semantics?: string; selected?: boolean;
    onSelect?: (v: boolean) => void; onTriage?: (s: UserStatus) => void }) {
  return (
    <div className={`alert-card ${a.confidence === "provisional" ? "provisional" : ""}`}>
      <ChipImage id={a.overlay_chip_id} alt="Change overlay" className="thumb" />
      <div className="body">
        <div className="spread">
          <div className="row">
            {onSelect && (
              <input type="checkbox" checked={!!selected}
                     aria-label={`Select the ${a.sensed_at.slice(0, 10)} alert from ${a.project_name || "this monitor"}`}
                     onChange={(e) => onSelect(e.target.checked)} />
            )}
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
          <span className="chip">
            <ScoreValue value={a.score} units={units} semantics={semantics}
                        threshold={a.threshold} />
          </span>
          {a.incident_id != null && (
            <span className="chip" title="Part of a grouped episode — see the Alerts tab">
              incident #{a.incident_id}
            </span>
          )}
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

export function DiagnosticCard({ d, codeCount, onAck, onAckCode }: {
  d: Diagnostic; codeCount?: number; onAck?: () => void;
  onAckCode?: () => void;
}) {
  return (
    <div className="alert-card diag-card">
      <div className="body">
        <div className="spread">
          <div className="row">
            {/* Which monitor this came from. Without it a mixed feed is unreadable. */}
            {d.project_uuid && (
              <Link to={`/projects/${d.project_uuid}?tab=Health`}><b>{d.project_name}</b></Link>
            )}
            <span className="chip mono">{d.code}</span>
            <span className={`chip sev-${d.severity}`}>{d.severity}</span>
            <span className="muted tiny">{d.occurred_at.replace("T", " ").slice(0, 16)}</span>
          </div>
          {onAck && !d.acknowledged && (
            <div className="row" style={{ gap: 6 }}>
              {(codeCount ?? 0) > 1 && onAckCode && (
                <button onClick={onAckCode}
                        title="Acknowledges every still-open diagnostic sharing this code, not just the ones shown">
                  Clear all of this code
                </button>
              )}
              <button onClick={onAck}>Acknowledge</button>
            </div>
          )}
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
