import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, artifactUrl } from "../api";
import { useApi } from "../useApi";
import { Async, ErrorBox } from "../components/Async";
import { CaveatChips, SeverityChip, RecipeLink } from "../components/Chips";
import { MapView, SwipeCompare } from "../components/MapView";
import { describeDirection } from "../format";
import { Alert, UserStatus } from "../types";

export default function AlertDetail() {
  const { uuid = "" } = useParams();
  const state = useApi<Alert>(() => api.alert(uuid), [uuid]);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState<string>();
  const [err, setErr] = useState<string>();
  const a = state.data;
  useEffect(() => { if (a) setNote(a.user_note || ""); }, [a?.uuid]);

  const triage = async (body: any, done: string) => {
    setBusy(true); setErr(undefined); setSaved(undefined);
    try { await api.triage(uuid, body); setSaved(done); await state.reload(); }
    catch (e: any) { setErr(e.message || "That could not be saved."); }
    finally { setBusy(false); }
  };

  if (!a) return <Async state={state} what="This alert">{() => null}</Async>;

  const direction = describeDirection(a.direction);

  return (
    <>
      <div className="spread">
        <div>
          <p className="tiny">
            {a.project_uuid
              ? <Link to={`/projects/${a.project_uuid}`}>← {a.project_name || "Back to monitor"}</Link>
              : <Link to="/">← Dashboard</Link>}
          </p>
          <h1 className="page">Alert · {a.sensed_at.slice(0, 10)}</h1>
          <p className="sub row">
            <SeverityChip a={a} /> <RecipeLink recipe={a.recipe} />
            {a.run?.uuid && <Link className="tiny" to={`/runs/${a.run.uuid}`}>run detail →</Link>}
          </p>
        </div>
        <a href={`/api/v1/alerts/${a.uuid}/export`}>
          <button className="primary">Export evidence bundle</button>
        </a>
      </div>

      <div className="panel" style={{ marginBottom: 14 }}>
        <p style={{ margin: 0, lineHeight: 1.6 }}>{a.explanation_text}</p>
        <div className="row" style={{ marginTop: 10 }}>
          <CaveatChips codes={a.caveats} />
        </div>
      </div>

      {a.explanation_llm && (
        <div className="llm-box" style={{ marginBottom: 14 }}>
          <b className="tiny">Machine-generated, unverified</b>
          <p style={{ marginBottom: 4 }}>{a.explanation_llm}</p>
          <p className="tiny muted">
            Vision models are unreliable on 10 m multispectral imagery. This text does not
            affect severity, confidence, or whether this alert was raised, and is excluded
            from evidence exports.
          </p>
        </div>
      )}

      <div className="grid" style={{ gridTemplateColumns: "1fr 1fr" }}>
        <div className="panel">
          <h3 className="card">Before / after</h3>
          <SwipeCompare before={artifactUrl(a.before_chip_id)} after={artifactUrl(a.after_chip_id)} />
        </div>
        <div className="panel">
          <h3 className="card">Change footprint</h3>
          <MapView aoi={a.geometry} change={a.geometry} height={330}
                   maskArtifactId={a.run?.mask_raster_id}
                   maskLayers={(a.run?.summary?.aux as any)?.mask_layers} />
        </div>
        <div className="panel">
          <h3 className="card">Measurements</h3>
          <table style={{ marginTop: 8 }}>
            <tbody>
              <tr><th scope="row" style={{ fontWeight: 400 }}>Changed area</th><td>{(a.changed_area_m2 / 10000).toFixed(2)} ha
                ({(a.changed_fraction * 100).toFixed(2)}% of AOI)</td></tr>
              <tr><th scope="row" style={{ fontWeight: 400 }}>Patches</th><td>{a.n_components}, largest {(a.largest_component_m2 / 10000).toFixed(2)} ha</td></tr>
              <tr><th scope="row" style={{ fontWeight: 400 }} title={(a.recipe as any)?.threshold_semantics}>Score</th>
                  <td className="mono">{a.score.toFixed(3)} {a.run?.summary?.units || ""}
                    {" "}(threshold {a.threshold} {a.run?.summary?.units || ""})</td></tr>
              <tr><th scope="row" style={{ fontWeight: 400 }}>Which way each signal moved</th>
                  <td>{direction.length
                    ? direction.map((d) => (
                        <div key={d.label}>
                          {d.label} <span className="mono">{d.value}</span>
                        </div>))
                    : <span className="muted">—</span>}</td></tr>
            </tbody>
          </table>
        </div>
        <div className="panel">
          <h3 className="card">Triage</h3>
          <div className="row" style={{ margin: "8px 0" }} role="group"
               aria-label="Was this a real change?">
            {(["true", "false", "unclear", "acknowledged"] as UserStatus[]).map((k) => (
              <button key={k} disabled={busy} aria-pressed={a.user_status === k}
                      className={a.user_status === k ? "primary" : ""}
                      onClick={() => triage({ user_status: k }, "Triage saved.")}>
                {k === "true" ? "Real change" : k === "false" ? "False alarm"
                  : k === "unclear" ? "Unclear" : "Acknowledge"}
              </button>
            ))}
          </div>
          <label className="tiny muted" style={{ display: "block" }}>
            Notes
            <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={4}
                      aria-label="Triage notes" style={{ width: "100%", display: "block" }}
                      placeholder="Notes on what you found…" />
          </label>
          <button disabled={busy} style={{ marginTop: 6 }}
                  onClick={() => triage({ user_note: note }, "Note saved.")}>
            {busy ? "Saving…" : "Save note"}
          </button>
          {err && <ErrorBox error={err} what="Your triage" />}
          {saved && <p className="tiny" role="status" aria-live="polite"
                       style={{ color: "var(--accent)" }}>{saved}</p>}
        </div>
        {a.recipe && (
          <div className="panel" style={{ gridColumn: "1 / -1" }}>
            <h3 className="card">Method and its limits</h3>
            <p className="tiny">{a.recipe.reference.citation}{" "}
              <a href={a.recipe.reference.url} target="_blank" rel="noreferrer">source ↗</a></p>
            <ul>{a.recipe.limitations.map((l: string) => <li key={l}>{l}</li>)}</ul>
          </div>
        )}
      </div>
    </>
  );
}
