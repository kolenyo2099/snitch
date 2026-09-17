import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { useApi } from "../useApi";
import { Async, ErrorBox } from "../components/Async";
import { Sparkline } from "../components/Sparkline";
import { StatusChip } from "../components/Chips";
import { describeCron } from "../format";
import { Project } from "../types";

type Row = Project & { scores: number[]; open_incidents: number; diags: number;
                       last: string | null };
type SortKey = "name" | "last" | "incidents" | "diags";

export default function Projects() {
  const [err, setErr] = useState<string>();
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 }>(
    { key: "last", dir: -1 });

  const state = useApi<Row[]>(async () => {
      const ps = (await api.projects()).items as Project[];
      return Promise.all(ps.map(async (p) => {
        const [runs, inc, h] = await Promise.all([
          // 60 covers the 40-point sparkline with polarity-filtering headroom;
          // the default 200 full rows was over-fetch for a ≤40-point polyline.
          api.runs(p.uuid, 60), api.incidents(p.uuid), api.projectHealth(p.uuid)]);
        // One sparkline per *methodology*, not one blended line: z-scores and p-values
        // on the same polyline is a chart that lies. The primary method's runs are the
        // project headline; the rest live on the Runs tab.
        const primary = p.methodologies?.[0];
        const mine = runs.items.filter(
          (r: any) => !primary || r.methodology_id === primary.id);
        return {
          ...p,
          // Polarity-correct headline where present: p-value detectors alert on a
          // *low* score, and plotting score_p99 alone inverted their story.
          scores: mine.slice(0, 40).reverse()
            .map((r: any) => r.summary?.score_headline ?? r.summary?.score_p99)
            .filter((v: any) => v != null),
          open_incidents: inc.items.filter((i: any) => i.state !== "closed").length,
          diags: Object.values(h.diagnostics || {}).reduce((a: number, b: any) => a + b, 0),
          last: h.last_usable_observation,
        };
      }));
  }, []);

  const rows = useMemo(() => {
    const data = state.data || [];
    const v = (r: Row) =>
      sort.key === "name" ? r.name.toLowerCase()
        : sort.key === "last" ? (r.last || "")
        : sort.key === "incidents" ? r.open_incidents
        : r.diags;
    return data.slice().sort((a, b) => {
      const av = v(a), bv = v(b);
      return (av < bv ? -1 : av > bv ? 1 : 0) * sort.dir;
    });
  }, [state.data, sort]);

  const flip = (key: SortKey) =>
    setSort((s) => s.key === key ? { key, dir: s.dir === 1 ? -1 : 1 }
                                  : { key, dir: key === "name" ? 1 : -1 });
  const arrow = (key: SortKey) => sort.key === key ? (sort.dir === 1 ? " ↑" : " ↓") : "";

  const remove = async (p: Project) => {
    // Soft delete: the row disappears from every list but the scored history and
    // evidence stay on disk, which is the whole point of an audit trail.
    if (!confirm(`Delete "${p.name}"? It stops watching and disappears from this `
                 + `list. Its recorded observations and alerts are kept on disk.`))
      return;
    try { await api.remove(p.uuid); await state.reload(); }
    catch (e: any) { setErr(e.message || "That project could not be deleted."); }
  };

  return (
    <>
      <div className="spread">
        <div>
          <h1 className="page">Projects</h1>
          <p className="sub">One monitor per area and question.</p>
        </div>
        <Link to="/new"><button className="primary">New monitor</button></Link>
      </div>
      {err && <ErrorBox error={err} what="That deletion" />}
      <Async state={state} what="Your monitors">
        {(all) => all.length === 0 ? (
          <div className="panel">
            <b>No monitors yet.</b>
            <p className="tiny muted">
              A monitor is one area plus one question — draw the area, pick what kind of
              change matters, and Snitch watches it from then on.
            </p>
            <Link to="/new"><button className="primary">Create your first monitor</button></Link>
          </div>
        ) : (
      <div className="panel" style={{ overflowX: "auto" }}>
        <table>
          <thead>
            <tr>
              <th scope="col"><button className="th-sort" onClick={() => flip("name")}>Name{arrow("name")}</button></th>
              <th scope="col">Recipe</th>
              <th scope="col">Recent scores</th>
              <th scope="col"><button className="th-sort" onClick={() => flip("last")}>Last observation{arrow("last")}</button></th>
              <th scope="col"><button className="th-sort" onClick={() => flip("incidents")}>Open incidents{arrow("incidents")}</button></th>
              <th scope="col"><button className="th-sort" onClick={() => flip("diags")}>Diagnostics{arrow("diags")}</button></th>
              <th scope="col">Status</th>
              <th scope="col"><span className="tiny">Actions</span></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.uuid}>
                <td><Link to={`/projects/${r.uuid}`}>{r.name}</Link>
                    <div className="tiny muted">{r.aoi_area_km2.toFixed(1)} km²</div></td>
                <td className="tiny">{r.recipe_id}<div className="muted">v{r.recipe_version}</div></td>
                <td><Sparkline values={r.scores} threshold={r.params?.threshold}
                               label={`${r.scores.length} recent scores, latest ${r.scores.length ? r.scores[r.scores.length - 1].toFixed(2) : "n/a"}`} /></td>
                <td className="tiny">{r.last?.slice(0, 10) || <span className="muted">never</span>}
                    <div className="tiny muted" title={r.schedule_cron}>
                      {describeCron(r.schedule_cron)}
                    </div></td>
                <td>{r.open_incidents || <span className="muted">0</span>}</td>
                {/* The count opens the grouped, clearable view — a number you cannot
                    act on is just an anxiety generator. */}
                <td>{r.diags
                  ? <Link to={`/projects/${r.uuid}?tab=Health`}>{r.diags}</Link>
                  : <span className="muted">0</span>}</td>
                <td><StatusChip status={r.status} /></td>
                <td><button className="danger" onClick={() => remove(r)}
                            aria-label={`Delete ${r.name}`}>Delete</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
        )}
      </Async>
    </>
  );
}
