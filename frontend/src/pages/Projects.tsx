import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { Sparkline } from "../components/Sparkline";
import { StatusChip } from "../components/Chips";
import { Project } from "../types";

type Row = Project & { scores: number[]; open_incidents: number; diags: number;
                       last: string | null };

export default function Projects() {
  const [rows, setRows] = useState<Row[]>([]);
  const [err, setErr] = useState<string>();

  const load = async () => {
      const ps = (await api.projects()).items as Project[];
      setRows(await Promise.all(ps.map(async (p) => {
        const [runs, inc, h] = await Promise.all([
          api.runs(p.uuid), api.incidents(p.uuid), api.projectHealth(p.uuid)]);
        return {
          ...p,
          scores: runs.items.slice(0, 40).reverse()
            .map((r: any) => r.summary?.score_p99).filter((v: any) => v != null),
          open_incidents: inc.items.filter((i: any) => i.state !== "closed").length,
          diags: Object.values(h.diagnostics || {}).reduce((a: number, b: any) => a + b, 0),
          last: h.last_usable_observation,
        };
      })));
  };
  useEffect(() => { load(); }, []);

  const remove = async (p: Project) => {
    // Soft delete: the row disappears from every list but the scored history and
    // evidence stay on disk, which is the whole point of an audit trail.
    if (!confirm(`Delete "${p.name}"? It stops watching and disappears from this `
                 + `list. Its recorded observations and alerts are kept on disk.`))
      return;
    try { await api.remove(p.uuid); await load(); }
    catch (e: any) { setErr(e.message || "That project could not be deleted."); }
  };

  return (
    <>
      <div className="spread">
        <div>
          <h2>Projects</h2>
          <p className="sub">One monitor per area and question.</p>
        </div>
        <Link to="/new"><button className="primary">New monitor</button></Link>
      </div>
      {err && <div className="err-box" style={{ marginBottom: 12 }}>{err}</div>}
      <div className="panel">
        <table>
          <thead>
            <tr><th>Name</th><th>Recipe</th><th>Recent scores</th><th>Last observation</th>
                <th>Open incidents</th><th>Diagnostics</th><th>Status</th><th></th></tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.uuid}>
                <td><Link to={`/projects/${r.uuid}`}>{r.name}</Link>
                    <div className="tiny muted">{r.aoi_area_km2.toFixed(1)} km²</div></td>
                <td className="tiny">{r.recipe_id}<div className="muted">v{r.recipe_version}</div></td>
                <td><Sparkline values={r.scores} threshold={r.params?.threshold} /></td>
                <td className="tiny">{r.last?.slice(0, 10) || <span className="muted">never</span>}</td>
                <td>{r.open_incidents || <span className="muted">0</span>}</td>
                <td>{r.diags || <span className="muted">0</span>}</td>
                <td><StatusChip status={r.status} /></td>
                <td><button className="danger" onClick={() => remove(r)}
                            aria-label={`Delete ${r.name}`}>Delete</button></td>
              </tr>
            ))}
            {!rows.length && <tr><td colSpan={8} className="muted">No projects yet.</td></tr>}
          </tbody>
        </table>
      </div>
    </>
  );
}
