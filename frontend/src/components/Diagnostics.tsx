import { useState } from "react";
import { api } from "../api";
import { useApi } from "../useApi";
import { Async, ErrorBox } from "./Async";
import { DiagnosticCard } from "./AlertCard";
import { Diagnostic } from "../types";

/**
 * A repeated fault is one problem, not N. This project's Health tab used to render
 * 609 near-identical ADAPTER_FALLBACK cards, each with its own Acknowledge button,
 * capped at 100 by the API with nothing on screen admitting the other 509 existed.
 *
 * So: one row per code, with the count and the time range, expandable to the actual
 * messages, and a single button that clears the whole code.
 */
export function DiagnosticsPanel({ project }: { project?: string }) {
  const [open, setOpen] = useState<string>();
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string>();

  const groups = useApi<any[]>(
    async () => (await api.diagnosticsSummary(project)).items, [project]);

  const ackAll = async (code: string, n: number) => {
    if (!confirm(`Acknowledge all ${n} open "${code}" diagnostic${n === 1 ? "" : "s"}? `
                 + `They stay on record but stop counting as open.`)) return;
    setBusy(true); setErr(undefined);
    try { await api.ackDiagnosticCode(code, project); await groups.reload(); }
    catch (e: any) { setErr(e.message || "Could not acknowledge those."); }
    finally { setBusy(false); }
  };

  return (
    <div className="panel">
      <h3 className="card">Diagnostics</h3>
      {err && <ErrorBox error={err} what="The acknowledgement" />}
      <Async state={groups} what="Diagnostics">
        {(items) => !items.length
          ? <span className="muted">No open diagnostics.</span>
          : (
            <div className="grid" style={{ gap: 8 }}>
              {items.map((g) => (
                <div className="panel" key={`${g.code}-${g.severity}`}
                     style={{ background: "var(--panel2)" }}>
                  <div className="spread diag-group">
                    <div className="row" style={{ gap: 8 }}>
                      <span className="count">{g.n}×</span>
                      <span className="chip mono">{g.code}</span>
                      <span className={`chip sev-${g.severity === "error" ? "high"
                        : g.severity === "warning" ? "medium" : "low"}`}>{g.severity}</span>
                      <span className="tiny muted">
                        {g.first_at.slice(0, 10) === g.last_at.slice(0, 10)
                          ? `on ${g.last_at.slice(0, 10)}`
                          : `${g.first_at.slice(0, 10)} → ${g.last_at.slice(0, 10)}`}
                      </span>
                    </div>
                    <div className="row" style={{ gap: 6 }}>
                      <button aria-expanded={open === g.code}
                              onClick={() => setOpen(open === g.code ? undefined : g.code)}>
                        {open === g.code ? "Hide" : `Show ${Math.min(g.n, 20)}`}
                      </button>
                      <button disabled={busy} onClick={() => ackAll(g.code, g.n)}>
                        Acknowledge all {g.n}
                      </button>
                    </div>
                  </div>
                  {open === g.code && <GroupDetail code={g.code} project={project} n={g.n} />}
                </div>
              ))}
            </div>
          )}
      </Async>
    </div>
  );
}

function GroupDetail({ code, project, n }: { code: string; project?: string; n: number }) {
  const LIMIT = 20;
  const q = `?code=${encodeURIComponent(code)}&acknowledged=false&limit=${LIMIT}`
          + (project ? `&project=${project}` : "");
  const state = useApi<{ items: Diagnostic[]; total?: number }>(
    () => api.diagnostics(q), [q]);
  return (
    <div className="grid" style={{ gap: 8, marginTop: 10 }}>
      <Async state={state} what="These diagnostics">
        {(d) => (
          <>
            {d.items.map((x) => <DiagnosticCard key={x.id} d={x} />)}
            {n > d.items.length && (
              <p className="tiny muted" style={{ margin: 0 }}>
                Showing the {d.items.length} most recent of {n}. They repeat the same
                fault — acknowledging the code clears all of them.
              </p>
            )}
          </>
        )}
      </Async>
    </div>
  );
}
