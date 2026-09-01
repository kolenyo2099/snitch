import { useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceLine, Legend } from "recharts";
import { api, artifactUrl } from "../api";
import { useApi } from "../useApi";
import { Async } from "../components/Async";
import { ChipImage } from "../components/Chip";
import { describeDirection, describeDuration, describeParam, describeProvenance } from "../format";
import { Run } from "../types";

const SCENES_SHOWN = 15;

/** A calibration run keeps its before/after pair in aux; an alert run has no chips of
 *  its own (they hang off the alert). Absent is rendered as absent, never as blank. */
function RunChips({ run, label }: { run: Run; label: string }) {
  const chips = (run.summary?.aux as any)?.calibration_chips;
  return (
    <div className="panel">
      <div className="spread">
        <b>{label}</b>
        {run.summary?.sensed_at && (
          <span className="tiny muted">sensed {String(run.summary.sensed_at).slice(0, 10)}</span>
        )}
      </div>
      {chips ? (
        <div className="imagery-row">
          <figure className="imagery-frame">
            <a href={artifactUrl(chips.before)} target="_blank" rel="noreferrer">
              <ChipImage id={chips.before} alt={`${label} before`} />
            </a>
            <figcaption className="tiny muted">Before</figcaption>
          </figure>
          <figure className="imagery-frame">
            <a href={artifactUrl(chips.after)} target="_blank" rel="noreferrer">
              <ChipImage id={chips.after} alt={`${label} after`} />
            </a>
            <figcaption className="tiny muted">After</figcaption>
          </figure>
        </div>
      ) : (
        <p className="tiny muted" style={{ marginBottom: 0 }}>
          No rendered pair for this run.
        </p>
      )}
    </div>
  );
}

export default function RunDetail() {
  const { uuid = "" } = useParams();
  const [sp, setSp] = useSearchParams();
  const compareUuid = sp.get("compare");
  const state = useApi<Run>(() => api.run(uuid), [uuid]);
  // The other half of a comparison; absent param, absent fetch.
  const cmp = useApi<Run | undefined>(async () =>
    compareUuid ? api.run(compareUuid) : undefined, [compareUuid, uuid]);
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
              {r.supersedes_uuid &&
                <Link className="chip" to={`/runs/${r.supersedes_uuid}`}
                      title="Re-analysis replaced this run's result — open the superseded one">
                  supersedes run #{r.supersedes_run_id}
                </Link>}
              {!compareUuid && (
                <button className="tiny"
                        title="Put this run beside another: before/after pairs and results side by side"
                        onClick={() => {
                          const n = new URLSearchParams(sp);
                          // First entry compares with the superseded run, which is the
                          // usual question: what did the re-analysis change?
                          if (r.supersedes_uuid) n.set("compare", r.supersedes_uuid);
                          setSp(n);
                        }}>
                  Compare…
                </button>
              )}
            </p>

            {compareUuid && (
              <div className="spread" style={{ marginBottom: 10 }}>
                <p className="tiny muted" style={{ margin: 0 }}>
                  Comparing with{" "}
                  <span className="mono">{compareUuid.slice(0, 8)}</span>. Both sides use
                  each frame's recorded stretch — nothing is re-stretched to manufacture
                  a difference.
                </p>
                <button className="tiny" onClick={() => {
                  const n = new URLSearchParams(sp); n.delete("compare"); setSp(n);
                }}>Close comparison</button>
              </div>
            )}

            {/* The run→alert direction finally exists: this is the alert this run raised. */}
            {r.alert && (
              <div className="panel" style={{ marginBottom: 14, borderLeft: "3px solid var(--red)" }}>
                <div className="spread">
                  <b>This run raised an alert</b>
                  <Link className="tiny" to={`/alerts/${r.alert.uuid}`}>alert detail →</Link>
                </div>
                <p className="tiny" style={{ margin: "6px 0 0" }}>
                  severity <span className={`chip sev-${r.alert.severity}`}>{r.alert.severity}</span>{" "}
                  sensed {r.alert.sensed_at.slice(0, 10)} · score{" "}
                  <span className="mono">{r.alert.score.toFixed(2)} / thr {r.alert.threshold}</span>
                  {r.alert.user_status !== "new" && <> · triaged <span className="chip">{r.alert.user_status}</span></>}
                </p>
              </div>
            )}

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

            {/* Side-by-side: the two runs' pairs and their key numbers. */}
            {compareUuid && cmp.data && cmp.data.uuid !== r.uuid && (
              <div className="grid" style={{ gridTemplateColumns: "1fr 1fr", gap: 14, marginBottom: 14 }}>
                <RunChips run={r} label="This run" />
                <RunChips run={cmp.data} label="Compared run" />
                <div className="panel" style={{ gridColumn: "1 / -1" }}>
                  <h3 className="card">Difference</h3>
                  <table>
                    <thead><tr><th scope="col"></th><th scope="col">This run</th>
                      <th scope="col">Compared run</th></tr></thead>
                    <tbody>
                      <tr><th scope="row">Headline score</th>
                          <td className="mono">{(r.summary?.score_headline ?? r.summary?.score_p99)
                            ? `${(r.summary!.score_headline ?? r.summary!.score_p99)!.toFixed(3)} ${r.summary?.units || ""}`
                            : "—"}</td>
                          <td className="mono">{(cmp.data.summary?.score_headline ?? cmp.data.summary?.score_p99)
                            ? `${(cmp.data.summary!.score_headline ?? cmp.data.summary!.score_p99)!.toFixed(3)} ${cmp.data.summary?.units || ""}`
                            : "—"}</td></tr>
                      <tr><th scope="row">Changed area</th>
                          <td>{((r.summary?.changed_area_m2 ?? 0) / 10000).toFixed(2)} ha</td>
                          <td>{((cmp.data.summary?.changed_area_m2 ?? 0) / 10000).toFixed(2)} ha</td></tr>
                      <tr><th scope="row">Patches</th>
                          <td>{r.summary?.n_components ?? "—"}</td>
                          <td>{cmp.data.summary?.n_components ?? "—"}</td></tr>
                      <tr><th scope="row">Usable pixels</th>
                          <td>{((r.summary?.valid_fraction ?? 0) * 100).toFixed(1)}%</td>
                          <td>{((cmp.data.summary?.valid_fraction ?? 0) * 100).toFixed(1)}%</td></tr>
                      <tr><th scope="row">Threshold</th>
                          <td className="mono">{r.summary?.threshold} {r.summary?.units}</td>
                          <td className="mono">{cmp.data.summary?.threshold} {cmp.data.summary?.units}</td></tr>
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(340px, 1fr))" }}>
              <div className="panel">
                <h3 className="card">Score distribution ({s?.units})</h3>
                <p className="tiny muted">
                  Percentiles of this scene's per-pixel scores. The dashed red line is the
                  alert threshold.
                  {r.score_raster_id != null && <> The full per-pixel{" "}
                    <a href={artifactUrl(r.score_raster_id)} target="_blank" rel="noreferrer">
                      score raster</a> is downloadable.</>}
                  {r.mask_raster_id != null && <> The{" "}
                    <a href={artifactUrl(r.mask_raster_id)} target="_blank" rel="noreferrer">
                      valid-pixel mask</a> shows exactly what was usable.</>}
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
