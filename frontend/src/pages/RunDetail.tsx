import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceLine } from "recharts";
import { api } from "../api";
import { Run } from "../types";

export default function RunDetail() {
  const { uuid = "" } = useParams();
  const [r, setR] = useState<Run>();
  useEffect(() => { api.run(uuid).then(setR); }, [uuid]);
  if (!r) return <p className="muted">Loading…</p>;
  const s = r.summary;

  const hist = s ? [
    { name: "p50", v: s.score_p50 }, { name: "p90", v: s.score_p90 },
    { name: "p95", v: s.score_p95 }, { name: "p99", v: s.score_p99 },
    { name: "max", v: s.score_max },
  ].filter((d) => d.v != null) : [];

  return (
    <>
      <h2>Run {r.uuid.slice(0, 8)}</h2>
      <p className="sub">
        {r.kind} · {r.detector_id} v{r.detector_version} ·{" "}
        <span className={r.compute_backend === "gee" ? "chip gee" : "chip"}>
          {r.compute_backend}
        </span>
        {r.supersedes_run_id && <span className="chip"> supersedes run #{r.supersedes_run_id}</span>}
      </p>

      {r.status === "failed" && (
        <div className="err-box" style={{ marginBottom: 14 }}>
          <b>This run failed and produced no alert.</b>
          <pre className="tiny" style={{ whiteSpace: "pre-wrap" }}>
            {r.error?.traceback || r.error?.error}
          </pre>
        </div>
      )}
      {r.skip_reason && s?.no_alert_reason_text && (
        <div className="warn-box" style={{ marginBottom: 14 }}>
          <b>No alert raised.</b> {s.no_alert_reason_text}
        </div>
      )}

      <div className="grid" style={{ gridTemplateColumns: "1fr 1fr" }}>
        <div className="panel">
          <b>Score distribution ({s?.units})</b>
          <div style={{ height: 190, marginTop: 8 }}>
            <ResponsiveContainer>
              <BarChart data={hist}>
                <XAxis dataKey="name" stroke="var(--dim)" fontSize={11} />
                <YAxis stroke="var(--dim)" fontSize={11} />
                <Tooltip contentStyle={{ background: "var(--panel)", border: "1px solid var(--line)" }} />
                {s && <ReferenceLine y={s.threshold} stroke="var(--red)" strokeDasharray="4 4" />}
                <Bar dataKey="v" fill="var(--accent)" />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>

        <div className="panel">
          <b>Result</b>
          <table style={{ marginTop: 8 }}>
            <tbody>
              <tr><td>Changed area</td><td>{((s?.changed_area_m2 ?? 0) / 10000).toFixed(2)} ha</td></tr>
              <tr><td>Patches above MMU</td><td>{s?.n_components}</td></tr>
              <tr><td>Largest patch</td><td>{((s?.largest_component_m2 ?? 0) / 10000).toFixed(2)} ha</td></tr>
              <tr><td>Valid fraction</td><td>{((s?.valid_fraction ?? 0) * 100).toFixed(1)}%</td></tr>
              <tr><td>Threshold</td><td className="mono">
                {s?.threshold} {s?.units}
                {s?.polarity === "lower_is_more_change" && (
                  <span className="tiny muted"> (a score at or below this is change)</span>
                )}
              </td></tr>
              {s?.aux?.derived_threshold != null && (
                <tr><td>Threshold derived from this scene</td><td className="mono">
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
                <tr><td>Expected false positives</td><td className="mono">
                  {s.aux.expected_false_positives} px
                  <div className="tiny muted">
                    alpha {s.aux.alpha} × {s.valid_pixels.toLocaleString()} valid pixels.
                    {" "}{s.changed_pixels} pixels crossed. Only the minimum mapping
                    unit separates the two.
                  </div>
                </td></tr>
              )}
              <tr><td>Direction</td><td className="mono">{JSON.stringify(s?.direction)}</td></tr>
              <tr><td>Timing</td><td className="tiny">{r.started_at} → {r.finished_at}</td></tr>
            </tbody>
          </table>
        </div>

        <div className="panel">
          <b>Mask breakdown</b>
          <p className="tiny muted">How much of the AOI each mask removed on this date.</p>
          <table>
            <thead><tr><th>Mask</th><th>Pixels removed</th></tr></thead>
            <tbody>
              {Object.entries(s?.mask_summary || {}).map(([k, v]) => (
                <tr key={k}><td className="mono">{k}</td>
                  <td>{v == null ? <span className="muted">declared, not applied locally</span> : v}</td></tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="panel">
          <b>Input scenes</b>
          <table>
            <thead><tr><th>Scene</th><th>Sensed</th><th>Adapter</th><th>Source</th></tr></thead>
            <tbody>
              {(r.observations || []).map((o: any) => (
                <tr key={o.id}>
                  <td className="mono tiny">{o.scene_id}</td>
                  <td className="tiny">{o.sensed_at.slice(0, 10)}</td>
                  <td className="tiny">{o.adapter}</td>
                  <td><a className="tiny" href={o.source_uri} target="_blank" rel="noreferrer">STAC ↗</a></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="panel" style={{ gridColumn: "1 / -1" }}>
          <div className="spread">
            <b>Parameters and provenance</b>
            <a href={`/api/v1/runs/${r.uuid}/export`}><button>Download run bundle</button></a>
          </div>
          <pre className="tiny" style={{ whiteSpace: "pre-wrap" }}>
            {JSON.stringify(r.params, null, 2)}
          </pre>
          {!!s?.notes?.length && (
            <div className="warn-box">
              <b>Method notes</b>
              <ul>{s.notes.map((n: string) => <li key={n}>{n}</li>)}</ul>
            </div>
          )}
        </div>
      </div>
    </>
  );
}
