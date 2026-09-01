import { useState } from "react";
import { api } from "../api";
import { useApi } from "../useApi";
import { Async, ErrorBox } from "../components/Async";
import { AlertCard, DiagnosticCard } from "../components/AlertCard";
import { HealthStrip } from "../components/HealthStrip";
import { Alert, Diagnostic, Project, ProjectHealth } from "../types";

export default function Dashboard() {
  const [filter, setFilter] = useState({ severity: "", user_status: "", project: "",
                                         date_from: "", date_to: "" });
  const [actionError, setActionError] = useState<string>();

  const health = useApi<{ p: Project; h: ProjectHealth }[]>(async () => {
    const ps = (await api.projects()).items as Project[];
    return Promise.all(ps.map(async (p) => ({ p, h: await api.projectHealth(p.uuid) })));
  }, []);

  const q = Object.entries(filter).filter(([, v]) => v)
    .map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join("&");
  const feed = useApi<(Alert | Diagnostic)[]>(
    async () => (await api.feed(q ? `?${q}` : "")).items, [q]);

  const act = async (fn: () => Promise<unknown>) => {
    setActionError(undefined);
    try { await fn(); feed.reload(); health.reload(); }
    catch (e: any) { setActionError(e.message || "That action failed."); }
  };

  const filtered = !!q;

  return (
    <>
      <h1 className="page">Dashboard</h1>
      <p className="sub">Alerts and errors across every monitor, newest first.</p>

      {health.error
        ? <ErrorBox error={health.error} what="Monitor health" onRetry={health.reload} />
        : <HealthStrip items={health.data || []} />}

      {actionError && <ErrorBox error={actionError} what="That action" />}

      <div className="row" style={{ marginBottom: 12 }}>
        <label className="tiny muted">Monitor<br />
          <select value={filter.project}
                  onChange={(e) => setFilter({ ...filter, project: e.target.value })}>
            <option value="">All projects</option>
            {(health.data || []).map(({ p }) =>
              <option key={p.uuid} value={p.uuid}>{p.name}</option>)}
          </select>
        </label>
        <label className="tiny muted">Severity<br />
          <select value={filter.severity}
                  onChange={(e) => setFilter({ ...filter, severity: e.target.value })}>
            <option value="">Any severity</option>
            <option value="high">High</option><option value="medium">Medium</option>
            <option value="low">Low</option>
          </select>
        </label>
        <label className="tiny muted">Triage status<br />
          <select value={filter.user_status}
                  onChange={(e) => setFilter({ ...filter, user_status: e.target.value })}>
            <option value="">Any status</option>
            <option value="new">New</option><option value="acknowledged">Acknowledged</option>
            <option value="true">Real change</option><option value="false">False alarm</option>
            <option value="unclear">Unclear</option>
          </select>
        </label>
        <label className="tiny muted">From<br />
          <input type="date" aria-label="From date (inclusive)" value={filter.date_from}
                 onChange={(e) => setFilter({ ...filter, date_from: e.target.value })} />
        </label>
        <label className="tiny muted">To<br />
          <input type="date" aria-label="To date (inclusive)" value={filter.date_to}
                 onChange={(e) => setFilter({ ...filter, date_to: e.target.value })} />
        </label>
        <button style={{ alignSelf: "flex-end" }} onClick={feed.reload}>Refresh</button>
        {filtered && (
          <button style={{ alignSelf: "flex-end" }}
                  onClick={() => setFilter({ severity: "", user_status: "", project: "",
                                             date_from: "", date_to: "" })}>
            Clear filters
          </button>
        )}
      </div>

      <Async state={feed} what="The feed">
        {(items) => (
          <div className="grid">
            {items.length === 0 && (
              <div className="panel muted">
                {filtered ? (
                  <>
                    <b>No alerts or errors match these filters.</b>
                    <p className="tiny" style={{ marginBottom: 0 }}>
                      Clear them to see everything the monitors have raised.
                    </p>
                  </>
                ) : (
                  <>
                    <b>Nothing to show — and that is the expected state.</b>
                    <p className="tiny" style={{ marginBottom: 0 }}>
                      This feed only carries threshold crossings and errors that need you.
                      A monitor that is running normally and seeing no change is silent
                      here. Use the cards above to confirm each one is still receiving
                      imagery.
                    </p>
                  </>
                )}
              </div>
            )}
            {items.map((it) =>
              it._type === "diagnostic"
                ? <DiagnosticCard key={`d${it.id}`} d={it as Diagnostic}
                                  onAck={() => act(() => api.ackDiagnostic(it.id))} />
                : <AlertCard key={`a${it.id}`} a={it as Alert}
                             onTriage={(s) => act(() =>
                               api.triage((it as Alert).uuid, { user_status: s }))} />
            )}
          </div>
        )}
      </Async>
    </>
  );
}
