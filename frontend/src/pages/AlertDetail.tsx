import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, artifactUrl } from "../api";
import { CaveatChips, SeverityChip, RecipeLink } from "../components/Chips";
import { MapView, SwipeCompare } from "../components/MapView";
import { Alert, UserStatus } from "../types";

export default function AlertDetail() {
  const { uuid = "" } = useParams();
  const [a, setA] = useState<Alert>();
  const [note, setNote] = useState("");
  const load = () => api.alert(uuid).then((x) => { setA(x); setNote(x.user_note || ""); });
  useEffect(() => { load(); }, [uuid]);
  if (!a) return <p className="muted">Loading…</p>;

  return (
    <>
      <div className="spread">
        <div>
          <h2>Alert · {a.sensed_at.slice(0, 10)}</h2>
          <p className="sub row">
            <SeverityChip a={a} /> <RecipeLink recipe={a.recipe} />
            <Link className="tiny" to={`/runs/${a.run?.uuid}`}>run detail →</Link>
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
          <b>Before / after</b>
          <SwipeCompare before={artifactUrl(a.before_chip_id)} after={artifactUrl(a.after_chip_id)} />
        </div>
        <div className="panel">
          <b>Change footprint</b>
          <MapView aoi={a.geometry} change={a.geometry} height={330}
                   maskArtifactId={a.run?.mask_raster_id}
                   maskLayers={(a.run?.summary?.aux as any)?.mask_layers} />
        </div>
        <div className="panel">
          <b>Measurements</b>
          <table style={{ marginTop: 8 }}>
            <tbody>
              <tr><td>Changed area</td><td>{(a.changed_area_m2 / 10000).toFixed(2)} ha
                ({(a.changed_fraction * 100).toFixed(2)}% of AOI)</td></tr>
              <tr><td>Patches</td><td>{a.n_components}, largest {(a.largest_component_m2 / 10000).toFixed(2)} ha</td></tr>
              <tr><td title={(a.recipe as any)?.threshold_semantics}>Score</td>
                  <td className="mono">{a.score.toFixed(3)} (threshold {a.threshold})</td></tr>
              <tr><td>Direction</td><td className="mono">{JSON.stringify(a.direction)}</td></tr>
            </tbody>
          </table>
        </div>
        <div className="panel">
          <b>Triage</b>
          <div className="row" style={{ margin: "8px 0" }}>
            {(["true", "false", "unclear", "acknowledged"] as UserStatus[]).map((s) => (
              <button key={s} className={a.user_status === s ? "primary" : ""}
                      onClick={async () => { await api.triage(uuid, { user_status: s }); load(); }}>
                {s === "true" ? "Real change" : s === "false" ? "False alarm"
                  : s === "unclear" ? "Unclear" : "Acknowledge"}
              </button>
            ))}
          </div>
          <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={4}
                    style={{ width: "100%" }} placeholder="Notes on what you found…" />
          <button onClick={async () => { await api.triage(uuid, { user_note: note }); load(); }}>
            Save note
          </button>
        </div>
        {a.recipe && (
          <div className="panel" style={{ gridColumn: "1 / -1" }}>
            <b>Method and its limits</b>
            <p className="tiny">{a.recipe.reference.citation}{" "}
              <a href={a.recipe.reference.url} target="_blank" rel="noreferrer">source ↗</a></p>
            <ul>{a.recipe.limitations.map((l: string) => <li key={l}>{l}</li>)}</ul>
          </div>
        )}
      </div>
    </>
  );
}
