import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceLine, Legend } from "recharts";
import { api } from "../api";
import { useApi } from "../useApi";
import { Async } from "../components/Async";
import { describeDirection, describeDuration, describeParam, describeProvenance } from "../format";
import { Run } from "../types";

const SCENES_SHOWN = 15;

export default function RunDetail() {
  const { uuid = "" } = useParams();
  const state = useApi<Run>(() => api.run(uuid), [uuid]);
  const [allScenes, setAllScenes] = useState(false);
  const [rawParams, setRawParams] = useState(false);

  return (
    <Async state={state} what="This run">
      {(r) => {
        const s = r.summary;
        const hist = s ? [
          { name: "p50", v: s.score_p50 }, { name: "p90", v: s.score_p90 },
          { name: "p95", v: s.score_p95 }, { name: "p99", v: s.score_p99 },
          { name: "max", v: s.score_max },
        ].filter((d) => d.v != null) : [];
        const timing = describeDuration(r.started_at, r.finished_at);
        const direction = describeDirection(s?.direction);
        const scenes = r.observations || [];
        const shown = allScenes ? scenes : scenes.slice(0, SCENES_SHOWN);
        // Pixel counts mean nothing without a denominator, and the caption already
        // promised "how much of the AOI".
        const totalPx = (s?.valid_pixels ?? 0)
          + Object.values(s?.mask_summary || {})
              .reduce((a: number, b: any) => a + (typeof b === "number" ? b : 0), 0);

        return (
          <>
            <p className="tiny">
              {r.project_uuid
                ? <Link to={`/projects/${r.project_uuid}`}>← {r.project_name || "Back to monitor"}</Link>
                : <Link to="/projects">← All monitors</Link>}
            </p>
            <h1 className="page">
              {r.kind === "backtest" ? "Calibration run" : "Run"}
              {s?.sensed_at ? ` · ${String(s.sensed_at).slice(0, 10)}` : ""}
            </h1>
            <p className="sub row" style={{ gap: 6 }}>
              <span className="mono tiny" title={r.uuid}>{r.uuid.slice(0, 8)}</span>
              <span>{r.kind} · {r.detector_id} v{r.detector_version}</span>
              <span className={r.compute_backend === "gee" ? "chip gee" : "chip"}
                    title="Which backend computed this run">
                {r.compute_backend}
              </span>
              {r.supersedes_run_id != null &&
                <span className="chip">supersedes run #{r.supersedes_run_id}</span>}
            </p>

            {r.status === "failed" && (
              <div className="err-box" role="alert" style={{ marginBottom: 14 }}>
                <b>This run failed and produced no alert.</b>
                <pre className="tiny" style={{ whiteSpace: "pre-wrap", overflowX: "auto" }}>
                  {r.error?.traceback || r.error?.error}
                </pre>
              </div>
            )}
            {r.skip_reason && s?.no_alert_reason_text && (
              <div className="warn-box" style={{ marginBottom: 14 }}>
                <b>No alert raised.</b> {s.no_alert_reason_text}
              </div>
            )}

            <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(340px, 1fr))" }}>
              <div className="panel">
                <h3 className="card">Score distribution ({s?.units})</h3>
                <p className="tiny muted">
                  Percentiles of this scene's per-pixel scores. The dashed red line is the
                  alert threshold.
                </p>
                <div style={{ height: 200, marginTop: 8 }}>
                  <ResponsiveContainer>
                    <BarChart data={hist} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
                      <XAxis dataKey="name" stroke="var(--dim)" fontSize={11} />
                      <YAxis stroke="var(--dim)" fontSize={11}
                             label={{ value: s?.units, angle: -90, position: "insideLeft",
                                      fill: "var(--dim)", fontSize: 11 }} />
                      <Tooltip contentStyle={{ background: "var(--panel)", border: "1px solid var(--line)" }}
                               formatter={(v: any) => [`${Number(v).toFixed(3)} ${s?.units || ""}`, "Score"]} />
                      {s && <ReferenceLine y={s.threshold} stroke="var(--red)" strokeDasharray="4 4"
                                           label={{ value: `threshold ${s.threshold}`, position: "insideTopRight",
                                                    fill: "var(--red)", fontSize: 10 }} />}
                      <Bar dataKey="v" fill="var(--accent)" name="Score" />
                      <Legend wrapperStyle={{ fontSize: 11, color: "var(--dim)" }} />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              </div>

              <div className="panel">
                <h3 className="card">Result</h3>
                <table>
                  <tbody>
                    <tr><th scope="row">Changed area</th>
                        <td>{((s?.changed_area_m2 ?? 0) / 10000).toFixed(2)} ha
                          {s?.changed_fraction != null &&
                            <span className="muted"> ({(s.changed_fraction * 100).toFixed(2)}% of the area)</span>}
                        </td></tr>
                    <tr><th scope="row">Patches above the minimum size</th><td>{s?.n_components}</td></tr>
                    <tr><th scope="row">Largest patch</th>
                        <td>{((s?.largest_component_m2 ?? 0) / 10000).toFixed(2)} ha</td></tr>
                    <tr><th scope="row">Usable pixels</th>
                        <td>{((s?.valid_fraction ?? 0) * 100).toFixed(1)}% of the area
                          <div className="tiny muted">The rest was cloud, shadow, or outside the scene.</div>
                        </td></tr>
                    <tr><th scope="row">Threshold</th><td className="mono">
                      {s?.threshold} {s?.units}
                      {s?.polarity === "lower_is_more_change" && (
                        <div className="tiny muted">A score at or below this counts as change.</div>
                      )}
                    </td></tr>
                    {s?.aux?.derived_threshold != null && (
                      <tr><th scope="row">Threshold derived from this scene</th><td className="mono">
                        {Number(s.aux.derived_threshold).toFixed(2)} {s?.units}
                        {s.aux.derived_threshold_db != null &&
                          ` (${Number(s.aux.derived_threshold_db).toFixed(2)} dB)`}
                        <div className="tiny muted">
                          Otsu mode: this run was judged against its own histogram, not
                          against the recipe default.
                        </div>
                      </td></tr>
                    )}
                    {s?.aux?.expected_false_positives != null && (
                      <tr><th scope="row">Expected false positives</th><td className="mono">
                        {s.aux.expected_false_positives} px
                        <div className="tiny muted">
                          alpha {s.aux.alpha} × {s.valid_pixels.toLocaleString()} valid pixels.
                          {" "}{s.changed_pixels} pixels crossed. Only the minimum mapping
                          unit separates the two.
                        </div>
                      </td></tr>
                    )}
                    <tr><th scope="row">Which way each signal moved</th>
                        <td>
                          {direction.length ? (
                            <table style={{ margin: 0 }}>
                              <tbody>
                                {direction.map((d) => (
                                  <tr key={d.label}>
                                    <td style={{ border: 0, padding: "2px 8px 2px 0" }}>{d.label}</td>
                                    <td className="mono" style={{ border: 0, padding: "2px 0" }}>{d.value}</td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          ) : <span className="muted">—</span>}
                          <div className="tiny muted">
                            Mean change in each input across the flagged pixels.
                          </div>
                        </td></tr>
                    <tr><th scope="row">Took</th>
                        <td title={timing.title}>{timing.text}</td></tr>
                  </tbody>
                </table>
              </div>

              <div className="panel">
                <h3 className="card">Mask breakdown</h3>
                <p className="tiny muted">How much of the area each mask removed on this date.</p>
                <table>
                  <thead><tr><th scope="col">Mask</th><th scope="col">Removed</th></tr></thead>
                  <tbody>
                    {Object.entries(s?.mask_summary || {}).map(([k, v]) => (
                      <tr key={k}><td className="mono">{k}</td>
                        <td>{v == null
                          ? <span className="muted">declared, not applied locally</span>
                          : (
                            <>
                              {totalPx
                                ? <b>{((Number(v) / totalPx) * 100).toFixed(1)}%</b>
                                : <b>—</b>}
                              <span className="muted tiny"> ({Number(v).toLocaleString()} px)</span>
                            </>
                          )}</td></tr>
                    ))}
                    {!Object.keys(s?.mask_summary || {}).length &&
                      <tr><td colSpan={2} className="muted">No masks were applied.</td></tr>}
                  </tbody>
                </table>
              </div>

              <div className="panel">
                <h3 className="card">Input scenes ({scenes.length})</h3>
                <div style={{ overflowX: "auto" }}>
                  <table>
                    <thead><tr><th scope="col">Scene</th><th scope="col">Sensed</th>
                      <th scope="col">Adapter</th><th scope="col">Source</th></tr></thead>
                    <tbody>
                      {shown.map((o: any) => (
                        <tr key={o.id}>
                          <td className="mono tiny">{o.scene_id}</td>
                          <td className="tiny">{o.sensed_at.slice(0, 10)}</td>
                          <td className="tiny">{o.adapter}</td>
                          <td><a className="tiny" href={o.source_uri} target="_blank" rel="noreferrer">STAC ↗</a></td>
                        </tr>
                      ))}
                      {!scenes.length &&
                        <tr><td colSpan={4} className="muted">No scenes recorded for this run.</td></tr>}
                    </tbody>
                  </table>
                </div>
                {scenes.length > SCENES_SHOWN && (
                  <button style={{ marginTop: 8 }} onClick={() => setAllScenes(!allScenes)}>
                    {allScenes ? "Show fewer"
                      : `Show all ${scenes.length} scenes`}
                  </button>
                )}
              </div>

              <div className="panel" style={{ gridColumn: "1 / -1" }}>
                <div className="spread">
                  <h3 className="card" style={{ margin: 0 }}>Parameters and provenance</h3>
                  <div className="row" style={{ gap: 6 }}>
                    <button aria-pressed={rawParams} onClick={() => setRawParams(!rawParams)}>
                      {rawParams ? "Show as a table" : "Show raw JSON"}
                    </button>
                    <a href={`/api/v1/runs/${r.uuid}/export`}><button>Download run bundle</button></a>
                  </div>
                </div>
                <p className="tiny muted">
                  Exactly what this run was configured with. The bundle contains these
                  values verbatim.
                </p>
                {rawParams ? (
                  <pre className="tiny" style={{ whiteSpace: "pre-wrap", overflowX: "auto" }}>
                    {JSON.stringify(r.params, null, 2)}
                  </pre>
                ) : (
                  <table>
                    <tbody>
                      {Object.entries(r.params || {})
                        .filter(([k]) => !k.startsWith("_") && k !== "threshold_provenance")
                        .map(([k, v]) => {
                          const d = describeParam(k, v);
                          return (
                            <tr key={k}>
                              <th scope="row" style={{ fontWeight: 400 }}>
                                <span title={d.help || k}
                                      style={{ borderBottom: d.help ? "1px dotted var(--faint)" : undefined }}>
                                  {d.label}
                                </span>
                                <div className="tiny muted mono">{k}</div>
                              </th>
                              <td className="mono">
                                {d.text || JSON.stringify(d.raw)}
                                {d.unit ? <span className="muted"> {d.unit}</span> : null}
                              </td>
                            </tr>
                          );
                        })}
                      {(r.params as any)?.threshold_provenance && (
                        <tr><th scope="row" style={{ fontWeight: 400 }}>Where the threshold came from</th>
                            <td>{describeProvenance((r.params as any).threshold_provenance)}</td></tr>
                      )}
                    </tbody>
                  </table>
                )}
                {!!s?.notes?.length && (
                  <div className="warn-box" style={{ marginTop: 10 }}>
                    <b>Method notes</b>
                    <ul>{s.notes.map((n: string) => <li key={n}>{n}</li>)}</ul>
                  </div>
                )}
              </div>
            </div>
          </>
        );
      }}
    </Async>
  );
}
