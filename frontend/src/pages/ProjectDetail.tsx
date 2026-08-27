import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, artifactUrl } from "../api";
import { AlertCard, DiagnosticCard } from "../components/AlertCard";
import { MapView } from "../components/MapView";
import { Timeline } from "../components/Timeline";
import { CaveatChips, RecipeLink } from "../components/Chips";
import { ImageryGallery } from "../components/Imagery";
import { Methodologies } from "../components/Methodologies";
import {
  Alert, Diagnostic, Incident, Observation, Project, ProjectHealth, Recipe, Run,
} from "../types";

const TABS = ["Map", "Timeline", "Imagery", "Alerts", "Methods", "Health"] as const;

export default function ProjectDetail() {
  const { uuid = "" } = useParams();
  const [tab, setTab] = useState<(typeof TABS)[number]>("Map");
  const [p, setP] = useState<Project>();
  const [recipe, setRecipe] = useState<Recipe>();
  const [allRecipes, setAllRecipes] = useState<Recipe[]>([]);
  const [obs, setObs] = useState<Observation[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [diags, setDiags] = useState<Diagnostic[]>([]);
  const [h, setH] = useState<ProjectHealth>();
  const [severityBreaks, setSeverityBreaks] = useState<number[]>([]);
  const [seasonMismatch, setSeasonMismatch] = useState(false);
  const [paramBusy, setParamBusy] = useState(false);
  const [paramMessage, setParamMessage] = useState<string>();
  const [paramError, setParamError] = useState<string>();

  const load = async () => {
    const proj = await api.project(uuid);
    setP(proj);
    const rs = await api.recipes();
    setAllRecipes(rs);
    setRecipe(rs.find((r: Recipe) => r.id === proj.recipe_id));
    setObs((await api.observations(uuid)).items);
    setRuns((await api.runs(uuid)).items);
    setAlerts((await api.alerts(uuid)).items);
    setIncidents((await api.incidents(uuid)).items);
    setDiags((await api.diagnostics(`?project=${uuid}`)).items);
    setH(await api.projectHealth(uuid));
  };
  useEffect(() => { load(); }, [uuid]);
  useEffect(() => {
    if (!p) return;
    setSeverityBreaks(p.params?.severity_breaks || [-250, -100, 100, 270, 440, 660]);
    setSeasonMismatch(!!p.params?.allow_season_mismatch);
  }, [p?.uuid]);

  const queueParameterChange = async (params: Record<string, any>, message: string) => {
    setParamBusy(true); setParamError(undefined); setParamMessage(undefined);
    try {
      await api.reanalyse(uuid, params);
      setParamMessage(message);
    } catch (e: any) {
      setParamError(e.message || "The parameter change could not be queued. Try again.");
    } finally { setParamBusy(false); }
  };

  const saveSeverityBreaks = async () => {
    if (severityBreaks.length !== 6 || severityBreaks.some((n) => !Number.isFinite(n)) ||
        severityBreaks.some((n, i) => i > 0 && n <= severityBreaks[i - 1])) {
      setParamError("Enter six severity boundaries in strictly increasing order.");
      return;
    }
    await queueParameterChange(
      { severity_breaks: severityBreaks },
      "Severity boundaries queued. Historical runs will be reanalysed append-only.",
    );
  };

  const latest = useMemo(
    () => alerts.slice().sort((a, b) => b.sensed_at.localeCompare(a.sensed_at))[0],
    [alerts]);
  const latestRun = useMemo(
    () => runs.filter(r => r.status === "ok")
      .sort((a, b) => b.started_at.localeCompare(a.started_at))[0], [runs]);

  if (!p) return <p className="muted">Loading…</p>;
  const threshold = p.params?.threshold ?? recipe?.defaults?.threshold ?? 0;

  return (
    <>
      <div className="spread">
        <div>
          <h2>{p.name}</h2>
          <p className="sub">
            {p.aoi_area_km2.toFixed(1)} km² · {p.analysis_crs} · <RecipeLink recipe={recipe} />
          </p>
        </div>
        <div className="row">
          <button onClick={async () => { await api.runNow(uuid); load(); }}>Run now</button>
          {p.status === "active"
            ? <button onClick={async () => { await api.pause(uuid); load(); }}>Pause</button>
            : <button className="primary" onClick={async () => { await api.activate(uuid); load(); }}>
                Activate
              </button>}
          <a href={`/api/v1/projects/${uuid}/export`}><button>Export evidence</button></a>
        </div>
      </div>

      <div className="tabs">
        {TABS.map((t) => (
          <button key={t} className={tab === t ? "active" : ""} onClick={() => setTab(t)}>
            {t}{t === "Alerts" && alerts.length ? ` (${alerts.length})` : ""}
          </button>
        ))}
      </div>

      {tab === "Map" && (
        <div className="grid" style={{ gridTemplateColumns: "1fr", gap: 14 }}>
          <MapView aoi={p.aoi_geojson} change={latest?.geometry}
                   maskArtifactId={latestRun?.mask_raster_id}
                   maskLayers={(latestRun?.summary?.aux as any)?.mask_layers} />
          {latest ? (
            <div className="panel">
              <div className="spread">
                <b>Latest change footprint · {latest.sensed_at.slice(0, 10)}</b>
                <Link className="tiny" to={`/alerts/${latest.uuid}`}>alert detail →</Link>
              </div>
              <p>{latest.explanation_text}</p>
              <CaveatChips codes={latest.caveats} />
            </div>
          ) : (
            <div className="panel muted">
              No alert has been raised for this area yet. The overlay appears here when one is.
            </div>
          )}
        </div>
      )}

      {tab === "Timeline" && (
        <div className="panel">
          <Timeline observations={obs} runs={runs} threshold={threshold}
                    units={latestRun?.summary?.units || ""}
                    polarity={latestRun?.summary?.polarity}
                    onSelect={(t) => t.run && (window.location.href = `/runs/${t.run.uuid}`)} />
          <p className="tiny muted" style={{ marginTop: 10 }}>
            Click a scored tick to open its run. Amber means the scene arrived but too
            little of it was usable to score; grey means nothing was acquired at all.
          </p>
        </div>
      )}

      {tab === "Methods" && (
        <Methodologies uuid={uuid} methodologies={p.methodologies || []}
                       recipes={allRecipes} runs={runs} onChange={load} />
      )}

      {tab === "Imagery" && (
        <ImageryGallery alerts={alerts} runs={runs} obs={obs}
                        acquired={obs.length}
                        processed={runs.filter((r) => r.status === "ok").length} />
      )}

      {tab === "Alerts" && (
        <div className="grid">
          {incidents.length > 0 && (
            <div className="panel">
              <b>Incidents</b>
              <table style={{ marginTop: 8 }}>
                <thead><tr><th>Title</th><th>Opened</th><th>State</th><th>Peak score</th>
                  <th>Cumulative area</th></tr></thead>
                <tbody>
                  {incidents.map((i) => (
                    <tr key={i.uuid}>
                      <td>{i.title}</td><td className="tiny">{i.opened_at.slice(0, 10)}</td>
                      <td><span className="chip">{i.state}</span></td>
                      <td className="mono">{i.peak_score.toFixed(2)}</td>
                      <td>{(i.cumulative_area_m2 / 10000).toFixed(1)} ha</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {alerts.map((a) => (
            <AlertCard key={a.uuid} a={{ ...a, project_name: p.name, project_uuid: p.uuid }}
                       onTriage={async (s) => { await api.triage(a.uuid, { user_status: s }); load(); }} />
          ))}
          {!alerts.length && <div className="panel muted">No alerts for this project.</div>}
        </div>
      )}

      {tab === "Health" && h && (
        <div className="grid" style={{ gridTemplateColumns: "1fr 1fr" }}>
          <div className="panel">
            <b>Status</b>
            <table style={{ marginTop: 8 }}>
              <tbody>
                <tr><td>Last usable observation</td>
                    <td>{h.last_usable_observation?.slice(0, 10) || "never"}
                      {h.days_since != null && <span className="muted"> ({h.days_since}d ago)</span>}</td></tr>
                <tr><td>Next scheduled poll</td>
                    <td>{h.next_poll ? new Date(h.next_poll).toLocaleString() : "not scheduled"}</td></tr>
                <tr><td>Schedule</td><td className="mono">{p.schedule_cron}</td></tr>
                <tr><td>Adapter preference</td><td className="mono">{h.adapter_preference.join(" → ")}</td></tr>
                <tr><td>Baseline</td>
                    <td>{p.baseline_start
                      ? <>{p.baseline_start.slice(0, 10)} → {p.baseline_end?.slice(0, 10)}</>
                      : <span className="muted">not fitted</span>}</td></tr>
                <tr><td>Compute backend</td>
                    <td>{latestRun?.compute_backend || "local"}</td></tr>
              </tbody>
            </table>
            <label className="tiny" style={{ display: "block", marginTop: 8 }}>
              <input type="checkbox" checked={!!(p.params || {}).gee_enabled}
                     onChange={async (e) => {
                       try { await api.setGee(p.uuid, e.target.checked); load(); }
                       catch (err: any) { alert(String(err.message || err)); }
                     }} />{" "}
              Use Google Earth Engine for baseline fitting and backtests
            </label>
            <p className="tiny muted">Off by default. Forward monitoring always runs
              locally, and every run records which backend served it.</p>
          </div>
          <div className="panel">
            <b>Parameters</b>
            <table style={{ marginTop: 8 }}>
              <tbody>
                {Object.entries(p.params || {}).filter(([k]) => !k.startsWith("_")).map(([k, v]) => (
                  <tr key={k}><td className="mono">{k}</td>
                    <td className="mono">{typeof v === "object" ? JSON.stringify(v) : String(v)}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
          {recipe?.detector === "dnbr" && (
            <div className="panel" style={{ gridColumn: "1 / -1" }}>
              <b>Fire severity boundaries</b>
              <p className="tiny muted">
                Six increasing scaled-dNBR boundaries define the seven published classes.
                Saving creates new historical runs; existing evidence is never overwritten.
              </p>
              <div className="row">
                {[
                  "High regrowth", "Low regrowth", "Unburned", "Low severity",
                  "Moderate-low", "Moderate-high",
                ].map((label, i) => (
                  <label className="tiny" key={label}>{label}<br />
                    <input type="number" value={severityBreaks[i] ?? ""}
                           aria-label={`${label} upper boundary`}
                           onChange={(e) => setSeverityBreaks(severityBreaks.map(
                             (v, j) => j === i ? Number(e.target.value) : v))} />
                  </label>
                ))}
                <button className="primary" disabled={paramBusy}
                        onClick={saveSeverityBreaks}>
                  {paramBusy ? "Queueing…" : "Save and reanalyse"}
                </button>
              </div>
            </div>
          )}
          {recipe?.detector === "irmad_cva" && (
            <div className="panel" style={{ gridColumn: "1 / -1" }}>
              <b>Season matching</b>
              <p className="tiny muted">
                Same-month baseline matching is on by default so ordinary vegetation
                cycles are less likely to look like change.
              </p>
              <label className="row">
                <input type="checkbox" checked={seasonMismatch}
                       onChange={(e) => setSeasonMismatch(e.target.checked)} />
                Allow comparison with the all-season baseline
              </label>
              {seasonMismatch && <div className="warn-box" style={{ marginTop: 10 }}>
                This can flag seasonal growth, harvest, snow, or soil moisture as change.
                The override is recorded on every new run.
              </div>}
              <button style={{ marginTop: 10 }} className="primary" disabled={paramBusy}
                      onClick={() => queueParameterChange(
                        { allow_season_mismatch: seasonMismatch },
                        "Season-matching choice queued. Historical runs will be reanalysed append-only.",
                      )}>
                {paramBusy ? "Queueing…" : "Save and reanalyse"}
              </button>
            </div>
          )}
          {(paramMessage || paramError) && (
            <div aria-live="polite" className={paramError ? "err-box" : "panel"}
                 style={{ gridColumn: "1 / -1" }}>
              {paramError || paramMessage}
            </div>
          )}
          <div className="panel" style={{ gridColumn: "1 / -1" }}>
            <b>Diagnostics</b>
            <div className="grid" style={{ marginTop: 8 }}>
              {diags.map((d) => <DiagnosticCard key={d.id} d={d}
                onAck={async () => { await api.ackDiagnostic(d.id); load(); }} />)}
              {!diags.length && <span className="muted">None recorded.</span>}
            </div>
          </div>
          {recipe && (
            <div className="panel" style={{ gridColumn: "1 / -1" }}>
              <b>What this method cannot do</b>
              <ul>{recipe.limitations.map((l: string) => <li key={l}>{l}</li>)}</ul>
              <p className="tiny muted">{recipe.reference.citation}{" "}
                <a href={recipe.reference.url} target="_blank" rel="noreferrer">source ↗</a></p>
            </div>
          )}
        </div>
      )}
    </>
  );
}
