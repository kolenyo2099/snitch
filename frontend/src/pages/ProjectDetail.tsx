import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api } from "../api";
import { useApi } from "../useApi";
import { Async, ErrorBox } from "../components/Async";
import { AlertCard } from "../components/AlertCard";
import { DiagnosticsPanel } from "../components/Diagnostics";
import { MapView } from "../components/MapView";
import { Timeline } from "../components/Timeline";
import { CaveatChips, RecipeLink, STATUS_HELP } from "../components/Chips";
import { DateCompare, ImageryGallery, collectImagery } from "../components/Imagery";
import { Methodologies } from "../components/Methodologies";
import { ProjectProgress } from "../components/Progress";
import { RunsTable } from "../components/RunsTable";
import { describeCron, describeParam, describeProvenance } from "../format";
import {
  Alert, DetectorSpec, Incident, Observation, Project, ProjectHealth, Recipe, Run,
} from "../types";

const TABS = ["Map", "Timeline", "Runs", "Imagery", "Alerts", "Methods", "Health"] as const;
type Tab = (typeof TABS)[number];

type Bundle = {
  p: Project; recipe?: Recipe; allRecipes: Recipe[]; detectors: DetectorSpec[];
  obs: Observation[]; runs: Run[]; alerts: Alert[]; incidents: Incident[];
  h: ProjectHealth;
};

export default function ProjectDetail() {
  const { uuid = "" } = useParams();
  const navigate = useNavigate();
  // The tab lives in the URL: it can be linked, bookmarked, and undone with Back.
  const [sp, setSp] = useSearchParams();
  const raw = sp.get("tab");
  const tab: Tab = (TABS as readonly string[]).includes(raw || "") ? (raw as Tab) : "Map";
  const setTab = (t: Tab) => setSp((prev) => {
    const next = new URLSearchParams(prev); next.set("tab", t); return next;
  }, { replace: true });
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([]);

  const [severityBreaks, setSeverityBreaks] = useState<number[]>([]);
  const [seasonMismatch, setSeasonMismatch] = useState(false);
  const [paramBusy, setParamBusy] = useState(false);
  const [paramMessage, setParamMessage] = useState<string>();
  const [paramError, setParamError] = useState<string>();
  const [actionBusy, setActionBusy] = useState<string>();
  const [actionMessage, setActionMessage] = useState<string>();
  const [actionError, setActionError] = useState<string>();
  // Shift-click compare on the Timeline: first pick is remembered, second opens
  // the two runs side by side on the run page.
  const [cmpPick, setCmpPick] = useState<string | null>(null);
  const [alertSev, setAlertSev] = useState("");
  const [alertTriage, setAlertTriage] = useState("");
  // With several methodologies on one AOI, each has its own threshold and score
  // units — a blended timeline plots incompatible quantities on one axis.
  const [methSel, setMethSel] = useState<number | "">("");

  // Follow the keyset cursor instead of silently rendering page one forever —
  // bounded at 500 so a project with fifty thousand alerts cannot stall the page.
  const allPages = async (
    fetchPage: (cursor?: number) => Promise<{ items: any[]; next_cursor: number | null }>,
  ) => {
    let out: any[] = [], cursor: number | null | undefined, pages = 0;
    do {
      const r = await fetchPage(cursor ?? undefined);
      out = out.concat(r.items);
      cursor = r.next_cursor;
      pages += 1;
    } while (cursor && pages < 5);
    return out;
  };

  const state = useApi<Bundle>(async () => {
    const p = await api.project(uuid);
    const [allRecipes, detectors, obs, runs, alerts, incidents, h] = await Promise.all([
      api.recipes(), api.detectors(), api.observations(uuid), api.runs(uuid),
      allPages((c) => api.alertsPage(uuid, c)),
      allPages((c) => api.incidentsPage(uuid, c)),
      api.projectHealth(uuid),
    ]);
    return {
      p, allRecipes, detectors,
      recipe: allRecipes.find((r: Recipe) => r.id === p.recipe_id),
      obs: obs.items, runs: runs.items, alerts,
      incidents, h,
    };
  }, [uuid]);

  const p = state.data?.p;
  useEffect(() => {
    if (!p) return;
    setSeverityBreaks(p.params?.severity_breaks || [-250, -100, 100, 270, 440, 660]);
    setSeasonMismatch(!!p.params?.allow_season_mismatch);
  }, [p?.uuid]);

  const act = async (key: string, fn: () => Promise<unknown>, done: string) => {
    setActionBusy(key); setActionError(undefined); setActionMessage(undefined);
    try { await fn(); setActionMessage(done); await state.reload(); }
    catch (e: any) { setActionError(e.message || "That action failed."); }
    finally { setActionBusy(undefined); }
  };

  const queueParameterChange = async (params: Record<string, any>, message: string) => {
    setParamBusy(true); setParamError(undefined); setParamMessage(undefined);
    try { await api.reanalyse(uuid, params); setParamMessage(message); }
    catch (e: any) {
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

  const onTabKey = (e: React.KeyboardEvent, i: number) => {
    const d = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1
            : e.key === "Home" ? -i : e.key === "End" ? TABS.length - 1 - i : 0;
    if (!d) return;
    e.preventDefault();
    const next = (i + d + TABS.length) % TABS.length;
    setTab(TABS[next]);
    tabRefs.current[next]?.focus();
  };

  return (
    <Async state={state} what="This monitor">
      {({ p, recipe, allRecipes, detectors, obs, runs, alerts, incidents, h }) => {
        const latest = alerts.slice()
          .sort((a, b) => b.sensed_at.localeCompare(a.sensed_at))[0];
        const latestRun = runs.filter((r) => r.status === "ok")
          .sort((a, b) => b.started_at.localeCompare(a.started_at))[0];
        const threshold = p.params?.threshold ?? recipe?.defaults?.threshold ?? 0;
        const scored = runs
          .map((r) => r.summary?.score_p99).filter((v): v is number => v != null);
        const best = scored.length ? Math.max(...scored) : null;
        const calibrating = p.status === "calibrating";
        const spec = detectors.find((d) => d.id === recipe?.detector);
        const units = latestRun?.summary?.units || spec?.score_units || "";

        return (
          <>
            <div className="spread">
              <div>
                <h1 className="page">{p.name}</h1>
                <p className="sub row" style={{ gap: 6 }}>
                  <span>{p.aoi_area_km2.toFixed(1)} km² · {p.analysis_crs}</span>
                  <RecipeLink recipe={recipe} />
                  <span className="chip" title={STATUS_HELP[p.status] || p.status}>
                    {p.status}
                  </span>
                </p>
              </div>
              <div className="row">
                <button disabled={!!actionBusy}
                        onClick={() => act("run", () => api.runNow(uuid),
                          "Queued a check for new imagery.")}>
                  {actionBusy === "run" ? "Queueing…" : "Run now"}
                </button>
                {/* "Activate" used to show whenever status !== active, so a calibrating
                    project looked paused. Only a paused one offers activation. */}
                {p.status === "active" ? (
                  <button disabled={!!actionBusy}
                          onClick={() => act("pause", () => api.pause(uuid),
                            "Paused. No new imagery will be checked.")}>
                    {actionBusy === "pause" ? "Pausing…" : "Pause"}
                  </button>
                ) : calibrating ? (
                  <button disabled title={STATUS_HELP.calibrating}>Calibrating…</button>
                ) : (
                  <button className="primary" disabled={!!actionBusy}
                          onClick={() => act("activate", () => api.activate(uuid),
                            "Watching. New imagery is checked on the schedule.")}>
                    {actionBusy === "activate" ? "Activating…" : "Activate"}
                  </button>
                )}
                <a href={`/api/v1/projects/${uuid}/export`}><button>Export evidence</button></a>
              </div>
            </div>

            {actionError && <ErrorBox error={actionError} what="That action" />}
            {actionMessage && (
              <p className="tiny muted" role="status" aria-live="polite">{actionMessage}</p>
            )}

            {/* A queued or running job is the answer to "why is nothing happening?",
                so it sits above the tabs rather than inside one of them. */}
            <div style={{ margin: "12px 0" }}>
              <ProjectProgress uuid={uuid} onIdle={state.reload} />
            </div>

            {calibrating && (
              <div className="warn-box" style={{ marginBottom: 14 }} role="status">
                <b>Calibrating on this site's history.</b>{" "}
                <span className="tiny">{STATUS_HELP.calibrating}</span>
              </div>
            )}

            <div className="tabs" role="tablist" aria-label="Monitor sections">
              {TABS.map((t, i) => (
                <button key={t} role="tab" id={`tab-${t}`}
                        ref={(el) => { tabRefs.current[i] = el; }}
                        aria-selected={tab === t} aria-controls={`panel-${t}`}
                        tabIndex={tab === t ? 0 : -1}
                        onKeyDown={(e) => onTabKey(e, i)}
                        onClick={() => setTab(t)}>
                  {t}{t === "Alerts" && alerts.length ? ` (${alerts.length})` : ""}
                </button>
              ))}
            </div>

            <div role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`}>
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
                    <NoAlerts runs={runs} best={best} threshold={threshold}
                              units={units} onCalibrate={() => setTab("Methods")} />
                  )}
                  {/* The spec's swipe comparator, minus the tiler: chips from this
                      project share one baseline stretch, so any two dates can swipe. */}
                  <DateCompare sets={collectImagery(alerts, runs, obs)} />
                </div>
              )}

              {tab === "Timeline" && (() => {
                const meth = p.methodologies?.find((m) => m.id === methSel);
                const tlRuns = meth ? runs.filter((r) => r.methodology_id === meth.id) : runs;
                const tlThreshold = meth?.params?.threshold ?? threshold;
                const tlUnits = meth
                  ? detectors.find((d) => d.id ===
                      allRecipes.find((r) => r.id === meth.recipe_id)?.detector)?.score_units
                    || ""
                  : units;
                return (
                <div className="panel">
                  {(p.methodologies?.length ?? 0) > 1 && (
                    <div className="row" style={{ marginBottom: 10 }}>
                      <label className="tiny muted">Methodology<br />
                        <select value={methSel}
                                onChange={(e) =>
                                  setMethSel(e.target.value === "" ? "" : Number(e.target.value))}>
                          <option value="">All methodologies</option>
                          {p.methodologies!.map((m) => (
                            <option key={m.id} value={m.id}>
                              {allRecipes.find((r) => r.id === m.recipe_id)?.plain_question
                                || m.recipe_id}
                            </option>
                          ))}
                        </select>
                      </label>
                      <p className="tiny muted" style={{ alignSelf: "flex-end", margin: 0 }}>
                        Each methodology scores in its own units — never blended.
                      </p>
                    </div>
                  )}
                  {cmpPick && (
                    <p className="tiny" role="status" style={{ marginTop: 0 }}>
                      Picked <span className="mono">{cmpPick.slice(0, 8)}</span> —
                      shift-click a second scored tick to see the two dates side by
                      side, or{" "}
                      <button className="link" onClick={() => setCmpPick(null)}>cancel</button>.
                    </p>
                  )}
                  <Timeline observations={obs} runs={tlRuns} threshold={tlThreshold}
                            units={tlUnits}
                            polarity={latestRun?.summary?.polarity ?? spec?.score_polarity}
                            onSelect={(t, shift) => {
                              if (!t.run) return;
                              if (shift && cmpPick && cmpPick !== t.run.uuid) {
                                navigate(`/runs/${t.run.uuid}?compare=${cmpPick}`);
                                setCmpPick(null);
                              } else if (shift) {
                                setCmpPick(t.run.uuid);
                              } else {
                                navigate(`/runs/${t.run.uuid}`);
                              }
                            }} />
                  <p className="tiny muted" style={{ marginTop: 10 }}>
                    Click a scored tick to open its run; shift-click two ticks to compare
                    their dates side by side. Amber means the scene arrived but too little
                    of it was usable to score; grey (dashed) means nothing was acquired;
                    red means the run itself failed.
                  </p>
                </div>
                );
              })()}

              {tab === "Runs" && (
                <RunsTable runs={runs} recipes={allRecipes} detectors={detectors}
                           methodologies={p.methodologies} />
              )}

              {tab === "Methods" && (
                <Methodologies uuid={uuid} methodologies={p.methodologies || []}
                               recipes={allRecipes} detectors={detectors} runs={runs}
                               onChange={state.reload}
                               onShowRuns={() => setTab("Runs")} />
              )}

              {tab === "Imagery" && (
                <ImageryGallery alerts={alerts} runs={runs} obs={obs}
                                acquired={obs.length}
                                processed={runs.filter((r) => r.status === "ok").length} />
              )}

              {tab === "Alerts" && (
                <div className="grid">
                  <div className="row" role="group" aria-label="Filter alerts">
                    <label className="tiny muted">Severity<br />
                      <select value={alertSev} onChange={(e) => setAlertSev(e.target.value)}>
                        <option value="">Any severity</option>
                        <option value="high">High</option><option value="medium">Medium</option>
                        <option value="low">Low</option>
                      </select>
                    </label>
                    <label className="tiny muted">Triage status<br />
                      <select value={alertTriage} onChange={(e) => setAlertTriage(e.target.value)}>
                        <option value="">Any status</option>
                        <option value="new">New</option>
                        <option value="acknowledged">Acknowledged</option>
                        <option value="true">Real change</option>
                        <option value="false">False alarm</option>
                        <option value="unclear">Unclear</option>
                      </select>
                    </label>
                    {(alertSev || alertTriage) && (
                      <button style={{ alignSelf: "flex-end" }}
                              onClick={() => { setAlertSev(""); setAlertTriage(""); }}>
                        Clear
                      </button>
                    )}
                  </div>

                  {incidents.length > 0 && (
                    <div className="panel">
                      <h3 className="card">
                        Incidents ({incidents.length}) — one episode, however many alerts
                      </h3>
                      {/* Expandable in place: an incident's member alerts are the
                          reason it exists, so hiding them behind no route at all was
                          the graph's most valuable dead end. */}
                      {incidents.map((i) => (
                        <IncidentRow key={i.uuid} incident={i}
                                     alerts={alerts.filter((a) => a.incident_id === i.id)} />
                      ))}
                    </div>
                  )}
                  {alerts
                    .filter((a) => (!alertSev || a.severity === alertSev)
                                 && (!alertTriage || a.user_status === alertTriage))
                    .map((a) => (
                      <AlertCard key={a.uuid} a={{ ...a, project_name: p.name, project_uuid: p.uuid }}
                                 units={units} semantics={spec?.threshold_semantics}
                                 onTriage={(s) => act(`triage-${a.uuid}`,
                                   () => api.triage(a.uuid, { user_status: s }), "Triage saved.")} />
                    ))}
                  {!alerts.length && (
                    <NoAlerts runs={runs} best={best} threshold={threshold}
                              units={units}
                              onCalibrate={() => setTab("Methods")} />
                  )}
                </div>
              )}

              {tab === "Health" && (
                <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))" }}>
                  <div className="panel">
                    <h3 className="card">Status</h3>
                    <table>
                      <tbody>
                        <tr><th scope="row">Last usable observation</th>
                            <td>{h.last_usable_observation?.slice(0, 10) || "never"}
                              {h.days_since != null && <span className="muted"> ({h.days_since}d ago)</span>}</td></tr>
                        <tr><th scope="row">Next scheduled poll</th>
                            <td>{h.next_poll ? new Date(h.next_poll).toLocaleString() : "not scheduled"}</td></tr>
                        <tr><th scope="row">Schedule</th>
                            <td>{describeCron(p.schedule_cron) || "—"}
                              <div className="tiny muted mono" title="cron expression">
                                {p.schedule_cron}
                              </div></td></tr>
                        <tr><th scope="row">Adapter preference</th>
                            <td className="mono">{h.adapter_preference.join(" → ")}</td></tr>
                        <tr><th scope="row">Baseline</th>
                            <td>{p.baseline_start
                              ? <>{p.baseline_start.slice(0, 10)} → {p.baseline_end?.slice(0, 10)}</>
                              : <span className="muted">not fitted</span>}</td></tr>
                        <tr><th scope="row">Compute backend</th>
                            <td>{latestRun?.compute_backend || "local"}</td></tr>
                      </tbody>
                    </table>
                    <label className="tiny" style={{ display: "block", marginTop: 8 }}>
                      <input type="checkbox" checked={!!(p.params || {}).gee_enabled}
                             onChange={(e) => act("gee",
                               () => api.setGee(p.uuid, e.target.checked),
                               e.target.checked ? "Earth Engine enabled for this monitor."
                                                : "Earth Engine disabled; runs stay local.")} />{" "}
                      Use Google Earth Engine for baseline fitting and backtests
                    </label>
                    <p className="tiny muted">Off by default. Forward monitoring always runs
                      locally, and every run records which backend served it.</p>
                  </div>

                  <div className="panel">
                    <h3 className="card">Parameters</h3>
                    <p className="tiny muted">
                      What this method is currently using. Hover a name for what it means.
                    </p>
                    <table>
                      <tbody>
                        {Object.entries(p.params || {})
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
                                  {d.text || <span className="muted">see below</span>}
                                  {d.unit ? <span className="muted"> {d.unit}</span> : null}
                                </td>
                              </tr>
                            );
                          })}
                        {p.params?.threshold_provenance && (
                          <tr><th scope="row" style={{ fontWeight: 400 }}>Where the threshold came from</th>
                              <td>{describeProvenance(p.params.threshold_provenance)}</td></tr>
                        )}
                      </tbody>
                    </table>
                  </div>

                  {recipe?.detector === "dnbr" && (
                    <div className="panel" style={{ gridColumn: "1 / -1" }}>
                      <h3 className="card">Fire severity boundaries</h3>
                      <p className="tiny muted">
                        Six increasing scaled-dNBR boundaries define the seven published classes.
                        Saving creates new historical runs; existing evidence is never overwritten.
                      </p>
                      <div className="row">
                        {["High regrowth", "Low regrowth", "Unburned", "Low severity",
                          "Moderate-low", "Moderate-high"].map((label, i) => (
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
                      <h3 className="card">Season matching</h3>
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

                  {paramError && <div style={{ gridColumn: "1 / -1" }}>
                    <ErrorBox error={paramError} what="The parameter change" />
                  </div>}
                  {paramMessage && (
                    <div aria-live="polite" className="panel" style={{ gridColumn: "1 / -1" }}>
                      {paramMessage}
                    </div>
                  )}

                  <div style={{ gridColumn: "1 / -1" }}>
                    <DiagnosticsPanel project={uuid} />
                  </div>

                  {recipe && (
                    <div className="panel" style={{ gridColumn: "1 / -1" }}>
                      <h3 className="card">What this method cannot do</h3>
                      <ul>{recipe.limitations.map((l: string) => <li key={l}>{l}</li>)}</ul>
                      <p className="tiny muted">{recipe.reference.citation}{" "}
                        <a href={recipe.reference.url} target="_blank" rel="noreferrer">source ↗</a></p>
                    </div>
                  )}
                </div>
              )}
            </div>
          </>
        );
      }}
    </Async>
  );
}

/** "No alerts for this project." is true and useless. The user's next question is
 *  always "so is it broken, or has nothing happened?" — which the scores answer. */
function NoAlerts({ runs, best, threshold, units, onCalibrate }: {
  runs: Run[]; best: number | null; threshold: number;
  units?: string; onCalibrate: () => void;
}) {  const scored = runs.filter((r) => r.status === "ok").length;
  return (
    <div className="panel">
      <b>No alert has been raised here yet.</b>
      {scored === 0 ? (
        <p className="tiny muted" style={{ marginBottom: 0 }}>
          Nothing has been scored yet either, so there is nothing to compare against a
          threshold. Calibrate the method to score this site's history first.
        </p>
      ) : (
        <>
          <p className="tiny muted">
            {scored} observation{scored === 1 ? " has" : "s have"} been scored.
            {best != null && (
              <> The highest score seen was <b className="mono">{best.toFixed(2)} {units}</b>,
                {" "}against an alert threshold of <b className="mono">{threshold} {units}</b>
                {best >= threshold
                  ? " — crossings exist but have not yet met the confirmation rule."
                  : " — nothing has come close, so either this site really is quiet or the "
                    + "threshold is set higher than the changes you care about."}
              </>
            )}
          </p>
          <button onClick={onCalibrate}>Tune the threshold</button>
        </>
      )}
    </div>
  );
}

/** One incident, expandable to the alerts that make it up. An incident *is* its member
 *  alerts grouped in time; a closed table hid exactly that. */
function IncidentRow({ incident, alerts }: { incident: Incident; alerts: Alert[] }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="incident">
      <button className="incident-head" aria-expanded={open}
              onClick={() => setOpen(!open)}>
        <span className="row" style={{ gap: 8, flex: 1, textAlign: "left" }}>
          <b>{incident.title || `Incident ${incident.uuid.slice(0, 8)}`}</b>
          <span className="chip">{incident.state}</span>
          <span className="tiny muted">opened {incident.opened_at.slice(0, 10)}</span>
          <span className="tiny mono">peak {incident.peak_score.toFixed(2)}</span>
          <span className="tiny muted">{(incident.cumulative_area_m2 / 10000).toFixed(1)} ha cumulative</span>
        </span>
        <span className="tiny">{alerts.length} alert{alerts.length === 1 ? "" : "s"}</span>
        <span aria-hidden>{open ? "▾" : "▸"}</span>
      </button>
      {open && (
        <div className="incident-body">
          {alerts.length ? alerts.map((a) => (
            <AlertCard key={a.uuid}
                       a={{ ...a, project_uuid: undefined, project_name: undefined }} />
          )) : (
            <p className="tiny muted" style={{ margin: 0 }}>
              Its alerts are not in the loaded page — open the monitor's Alerts tab
              around {incident.opened_at.slice(0, 10)} to see them.
            </p>
          )}
        </div>
      )}
    </div>
  );
}
