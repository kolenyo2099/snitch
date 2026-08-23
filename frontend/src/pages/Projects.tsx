import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { Sparkline } from "../components/Sparkline";
import { Project } from "../types";

type Row = Project & { scores: number[]; open_incidents: number; diags: number;
                       last: string | null };

export default function Projects() {
  const [rows, setRows] = useState<Row[]>([]);

  useEffect(() => {
    (async () => {
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
    })();
  }, []);

  return (
    <>
      <div className="spread">
        <div>
          <h2>Projects</h2>
          <p className="sub">One monitor per area and question.</p>
        </div>
        <Link to="/new"><button className="primary">New monitor</button></Link>
      </div>
      <div className="panel">
        <table>
          <thead>
            <tr><th>Name</th><th>Recipe</th><th>Recent scores</th><th>Last observation</th>
                <th>Open incidents</th><th>Diagnostics</th><th>Status</th></tr>
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
                <td><span className="chip">{r.status}</span></td>
              </tr>
            ))}
            {!rows.length && <tr><td colSpan={7} className="muted">No projects yet.</td></tr>}
          </tbody>
        </table>
      </div>
    </>
  );
}
