import { useEffect, useState } from "react";
import { api } from "../api";
import { AlertCard, DiagnosticCard } from "../components/AlertCard";
import { HealthStrip } from "../components/HealthStrip";
import { Alert, Diagnostic, Project, ProjectHealth } from "../types";

export default function Dashboard() {
  const [health, setHealth] = useState<{ p: Project; h: ProjectHealth }[]>([]);
  const [items, setItems] = useState<(Alert | Diagnostic)[]>([]);
  const [filter, setFilter] = useState({ severity: "", user_status: "", project: "" });
  const [err, setErr] = useState<string>();

  const load = async () => {
    try {
      const ps = (await api.projects()).items as Project[];
      setHealth(await Promise.all(ps.map(async (p) =>
        ({ p, h: await api.projectHealth(p.uuid) }))));
      const q = Object.entries(filter).filter(([, v]) => v)
        .map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join("&");
      setItems((await api.feed(q ? `?${q}` : "")).items);
    } catch (e: any) { setErr(e.message); }
  };
  useEffect(() => { load(); }, [JSON.stringify(filter)]);

  return (
    <>
      <h2>Dashboard</h2>
      <p className="sub">Alerts and errors across every monitor, newest first.</p>
      {err && <div className="err-box" style={{ marginBottom: 12 }}>{err}</div>}
      <HealthStrip items={health} />

      <div className="row" style={{ marginBottom: 12 }}>
        <select value={filter.project}
                onChange={(e) => setFilter({ ...filter, project: e.target.value })}>
          <option value="">All projects</option>
          {health.map(({ p }) => <option key={p.uuid} value={p.uuid}>{p.name}</option>)}
        </select>
        <select value={filter.severity}
                onChange={(e) => setFilter({ ...filter, severity: e.target.value })}>
          <option value="">Any severity</option>
          <option value="high">High</option><option value="medium">Medium</option>
          <option value="low">Low</option>
        </select>
        <select value={filter.user_status}
                onChange={(e) => setFilter({ ...filter, user_status: e.target.value })}>
          <option value="">Any status</option>
          <option value="new">New</option><option value="acknowledged">Acknowledged</option>
          <option value="true">Real change</option><option value="false">False alarm</option>
          <option value="unclear">Unclear</option>
        </select>
        <button onClick={load}>Refresh</button>
      </div>

      <div className="grid">
        {items.length === 0 && (
          <div className="panel muted">
            Nothing to show. That means every monitor ran and saw no threshold crossing —
            check the health strip above to confirm they are still seeing data.
          </div>
        )}
        {items.map((it) =>
          it._type === "diagnostic"
            ? <DiagnosticCard key={`d${it.id}`} d={it as Diagnostic}
                              onAck={async () => { await api.ackDiagnostic(it.id); load(); }} />
            : <AlertCard key={`a${it.id}`} a={it as Alert}
                         onTriage={async (s) => {
                           await api.triage((it as Alert).uuid, { user_status: s });
                           load();
                         }} />
        )}
      </div>
    </>
  );
}
