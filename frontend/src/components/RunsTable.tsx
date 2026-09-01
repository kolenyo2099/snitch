import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { describeDuration } from "../format";
import { DetectorSpec, Recipe, Run } from "../types";

const head = (r: Run) => r.summary?.score_headline ?? r.summary?.score_p99;

/** Runs were reachable only by clicking timeline ticks, and failed runs were not
 *  reachable at all — they rendered amber, indistinguishable from a cloudy scene.
 *  This is the run collection the data always had. */
export function RunsTable({ runs, recipes, detectors }: {
  runs: Run[]; recipes: Recipe[]; detectors?: DetectorSpec[];
}) {
  const [status, setStatus] = useState("");
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<{ key: "started" | "score" | "took";
                                    dir: 1 | -1 }>({ key: "started", dir: -1 });

  const byId = useMemo(() => new Map(runs.map((r) => [r.id, r])), [runs]);
  const unitsFor = (r: Run) =>
    detectors?.find((d) => d.id === r.detector_id)?.score_units
      ?? r.summary?.units ?? "";

  const shown = useMemo(() => {
    const needle = q.trim().toLowerCase();
    const filtered = runs.filter((r) => {
      if (status && r.status !== status) return false;
      if (needle && !`${r.uuid} ${r.detector_id} ${r.kind}`.toLowerCase().includes(needle))
        return false;
      return true;
    });
    const took = (r: Run) =>
      r.finished_at ? Date.parse(r.finished_at) - Date.parse(r.started_at) : -1;
    return filtered.sort((a, b) => {
      if (sort.key === "started") return sort.dir * b.started_at.localeCompare(a.started_at);
      if (sort.key === "score")
        return sort.dir * ((head(b) ?? -Infinity) - (head(a) ?? -Infinity));
      return sort.dir * (took(a) - took(b));
    });
  }, [runs, status, q, sort]);

  const arrow = (key: string) =>
    sort.key === key ? (sort.dir === -1 ? " ↓" : " ↑") : "";
  const flip = (key: "started" | "score" | "took") =>
    setSort((s) => s.key === key ? { key, dir: s.dir === -1 ? 1 : -1 }
                                  : { key, dir: key === "score" ? -1 : -1 });

  return (
    <div className="panel">
      <div className="spread">
        <h3 className="card" style={{ margin: 0 }}>
          Runs ({shown.length}{shown.length !== runs.length ? ` of ${runs.length}` : ""})
        </h3>
        <div className="row">
          <input aria-label="Search runs" placeholder="Search uuid, detector…"
                 value={q} onChange={(e) => setQ(e.target.value)} />
          <select aria-label="Filter by status" value={status}
                  onChange={(e) => setStatus(e.target.value)}>
            <option value="">Any status</option>
            <option value="ok">Ok</option>
            <option value="failed">Failed</option>
            <option value="skipped">Skipped</option>
          </select>
        </div>
      </div>
      <div style={{ overflowX: "auto", marginTop: 8 }}>
        <table>
          <thead>
            <tr>
              <th scope="col"><button className="th-sort" onClick={() => flip("started")}>
                Started{arrow("started")}</button></th>
              <th scope="col">Type</th>
              <th scope="col">Sensed</th>
              <th scope="col">Method</th>
              <th scope="col"><button className="th-sort" onClick={() => flip("score")}>
                Score{arrow("score")}</button></th>
              <th scope="col"><button className="th-sort" onClick={() => flip("took")}>
                Took{arrow("took")}</button></th>
              <th scope="col">Status</th>
              <th scope="col">Supersedes</th>
              <th scope="col"><span className="tiny">Actions</span></th>
            </tr>
          </thead>
          <tbody>
            {shown.map((r) => {
              const recipe = recipes.find((x) => x.id === r.detector_id.split("_")[0])
                ?? recipes.find((x) => x.detector === r.detector_id);
              const prev = r.supersedes_run_id != null ? byId.get(r.supersedes_run_id) : undefined;
              return (
                <tr key={r.uuid} className={r.status === "failed" ? "row-failed" : ""}>
                  <td className="tiny">{r.started_at.replace("T", " ").slice(0, 16)}</td>
                  <td className="tiny">{r.kind}</td>
                  <td className="tiny">{r.summary?.sensed_at
                    ? String(r.summary.sensed_at).slice(0, 10)
                    : <span className="muted">—</span>}</td>
                  <td className="tiny">{recipe?.plain_question
                    ? <span title={`${r.detector_id} v${r.detector_version}`}>
                        {recipe.plain_question}</span>
                    : <span className="mono tiny">{r.detector_id}</span>}</td>
                  <td className="mono tiny">{head(r) != null
                    ? `${head(r)!.toFixed(2)} ${unitsFor(r)}`
                    : <span className="muted">—</span>}</td>
                  <td className="tiny" title={describeDuration(r.started_at, r.finished_at).title}>
                    {describeDuration(r.started_at, r.finished_at).text}</td>
                  <td>
                    <span className={`chip run-${r.status}`}
                          title={r.status === "failed"
                            ? (r.error?.error || "failed")
                            : r.skip_reason || undefined}>
                      {r.status === "ok" ? "ok"
                        : r.status === "failed" ? "failed"
                        : `skipped: ${r.skip_reason || "gate"}`}
                    </span>
                  </td>
                  <td className="tiny">
                    {prev
                      ? <Link to={`/runs/${prev.uuid}`} className="tiny"
                              title="Re-analysis replaced this run's result">
                          run #{r.supersedes_run_id}</Link>
                      : r.supersedes_run_id != null
                        ? <span className="muted tiny">#{r.supersedes_run_id}</span>
                        : <span className="muted">—</span>}
                  </td>
                  <td><Link to={`/runs/${r.uuid}`} className="tiny">open →</Link></td>
                </tr>
              );
            })}
            {!shown.length && (
              <tr><td colSpan={9} className="muted">
                No runs match. Clear the search or status filter.
              </td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
