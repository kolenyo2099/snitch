import { useEffect, useState, useRef } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api";
import { DrawMap } from "../components/MapView";
import { Calibrate } from "../components/Calibrate";
import { ProjectProgress } from "../components/Progress";
import { STATUS_HELP } from "../components/Chips";
import { ErrorBox } from "../components/Async";
import { describeCron } from "../format";
import { DetectorSpec, Recipe, Run } from "../types";

const STEPS = ["Where", "What", "Calibrate", "Watch"];
const BACKTEST_YEARS = 3;

function geometryFromKml(text: string) {
  const xml = new DOMParser().parseFromString(text, "application/xml");
  if (xml.querySelector("parsererror")) throw new Error("That file is not valid KML.");
  const rings = [...xml.querySelectorAll("Polygon outerBoundaryIs LinearRing coordinates")]
    .map((node) => (node.textContent || "").trim().split(/\s+/).map((tuple) => {
      const [lon, lat] = tuple.split(",").map(Number);
      if (!Number.isFinite(lon) || !Number.isFinite(lat))
        throw new Error("KML coordinates are invalid.");
      return [lon, lat];
    })).filter((ring) => ring.length >= 4);
  if (!rings.length) throw new Error("No polygon was found in that KML file.");
  return rings.length === 1
    ? { type: "Polygon", coordinates: [rings[0]] }
    : { type: "MultiPolygon", coordinates: rings.map((ring) => [ring]) };
}

export default function Wizard() {
  const nav = useNavigate();
  const [step, setStep] = useState(0);
  const [aoi, setAoi] = useState<any>(null);
  const [paste, setPaste] = useState("");
  const [preview, setPreview] = useState<any>();
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string>();

  const [recipes, setRecipes] = useState<Recipe[]>([]);
  const [detectors, setDetectors] = useState<DetectorSpec[]>([]);
  // A project may watch this AOI with several methodologies at once. The first
  // picked stays the primary: it drives the calibration step and the summary copy.
  const [recipeIds, setRecipeIds] = useState<string[]>([]);
  const recipeId = recipeIds[0];
  const toggleRecipe = (id: string) =>
    setRecipeIds((cur) => cur.includes(id) ? cur.filter((r) => r !== id) : [...cur, id]);
  const [orbit, setOrbit] = useState<any>(null);

  const [uuid, setUuid] = useState<string>();
  const [methId, setMethId] = useState<number>();
  const [backtestArtifact, setBacktestArtifact] = useState<number | null>(null);
  const [runs, setRuns] = useState<Run[]>([]);
  const [threshold, setThreshold] = useState(0);
  const [usedDefault, setUsedDefault] = useState(false);
  const [backtesting, setBacktesting] = useState(false);

  const [name, setName] = useState("");
  const [cron, setCron] = useState("");
  // Where the user searched, so an unnamed monitor still gets a name someone can
  // recognise in a list. Four projects called "Untitled monitor" is not a list.
  const [placeName, setPlaceName] = useState("");
  const fallbackName = () =>
    [placeName, recipe?.display_name].filter(Boolean).join(" — ")
    || (preview ? `${preview.area_km2.toFixed(0)} km2 monitor` : "Untitled monitor");

  useEffect(() => { api.recipes().then(setRecipes); api.detectors().then(setDetectors); }, []);
  const recipe = recipes.find((r) => r.id === recipeId);
  const chosen = recipeIds.map((id) => recipes.find((r) => r.id === id)!).filter(Boolean);
  const spec = detectors.find((d) => d.id === recipe?.detector);
  const needsRadarOrbit = chosen.some(
    (r) => r.sensor === "S1" || r.secondary_sensor === "S1");
  const mixesSensors = new Set(chosen.map((r) => r.sensor)).size > 1;

  // Every edit fires a fresh coverage check, and they do not come back in order.
  // Without this guard a slow reply for an older shape lands after a newer one and
  // overwrites it, which is why the observation count appeared to drift up and down.
  const previewSeq = useRef(0);
  const previewAbort = useRef<AbortController>();

  const runPreview = async (g: any) => {
    previewAbort.current?.abort();
    const seq = ++previewSeq.current;
    setAoi(g); setPreview(undefined); setErr(undefined);
    if (!g) { setBusy(false); return; }
    const ac = new AbortController();
    previewAbort.current = ac;
    setBusy(true);
    try {
      const p = await api.aoiPreview(g, ac.signal);
      if (seq !== previewSeq.current) return;   // a newer shape already won
      setPreview(p);
    } catch (e: any) {
      if (ac.signal.aborted || seq !== previewSeq.current) return;
      setErr(e.message);
    } finally {
      if (seq === previewSeq.current) setBusy(false);
    }
  };

  // step 3: create the project and immediately start the backtest
  const beginCalibration = async () => {
    setBusy(true); setErr(undefined);
    try {
      const p = await api.createProject({
        name: name.trim() || fallbackName(), aoi_geojson: aoi, recipe_ids: recipeIds,
        s1_relative_orbit: orbit?.relative_orbit ?? null,
        s1_pass_direction: orbit?.pass_direction ?? null,
        // a mixed-sensor project needs a source list that can serve both
        ...(mixesSensors
          ? { adapter_preference: ["cdse", "earthsearch", "planetary"] } : {}),
      });
      setUuid(p.uuid);
      const primary = (p.methodologies as any[] | undefined)?.[0];
      setMethId(primary?.id);
      setThreshold(p.params.threshold ?? recipe?.defaults.threshold ?? 0);
      setCron(p.schedule_cron);
      await api.startBacktest(p.uuid, BACKTEST_YEARS, primary?.id);
      setBacktesting(true);
      setStep(2);
    } catch (e: any) { setErr(e.message); }
    finally { setBusy(false); }
  };

  // honest progress: poll the backtest until the job leaves the queue
  useEffect(() => {
    if (!uuid || !backtesting) return;
    const t = setInterval(async () => {
      const b = await api.backtest(uuid, methId);
      setRuns(b.runs || []);
      // The newest scored raster stands in for "the calibration": recording it with
      // the threshold is what makes method.md's provenance section truthful.
      const ids: (number | null)[] = (b.runs || []).map((r: Run) => r.score_raster_id);
      setBacktestArtifact(ids.filter((v): v is number => v != null).pop() ?? null);
      if (b.job && ["done", "failed"].includes(b.job.status)) {
        setBacktesting(false);
        if (b.job.status === "failed") setErr(b.job.last_error?.split("\n").pop());
      }
    }, 3000);
    return () => clearInterval(t);
  }, [uuid, methId, backtesting]);

  const activate = async () => {
    if (!uuid) return;
    setBusy(true);
    try {
      await api.setThreshold(uuid, {
        threshold, source: usedDefault ? "default" : "calibrated",
        ...(backtestArtifact ? { backtest_artifact_id: backtestArtifact } : {}),
      }, methId);
      await api.patchProject(uuid, { name: name.trim() || fallbackName(),
                                    schedule_cron: cron });
      await api.activate(uuid);
      nav(`/projects/${uuid}`);
    } catch (e: any) { setErr(e.message); }
    finally { setBusy(false); }
  };

  return (
    <>
      <h1 className="page">New monitor</h1>
      <p className="sub">
        Four steps. You can change everything later except the recipe.
        {" "}The monitor is created at step 3, when calibration starts.
      </p>
      <ol className="wizard-steps" aria-label="Progress">
        {STEPS.map((s, i) => (
          <li key={s} className={i === step ? "on" : i < step ? "done" : ""}
              aria-current={i === step ? "step" : undefined}>
            {i < step ? "\u2713" : i + 1}. {s}
          </li>
        ))}
      </ol>
      {err && <ErrorBox error={err} what="This step" />}

      {step === 0 && (
        <div className="grid">
          <div className="panel">
            <label>Name this monitor
              <input value={name} style={{ width: "100%" }} autoFocus
                     onChange={(e) => setName(e.target.value)}
                     placeholder="Northern block" />
            </label>
            <p className="tiny muted">
              Just for you — how it will be listed. Rename any time.
              {!name.trim() && (placeName || preview) &&
                <> Left blank, it will be called <b>{fallbackName()}</b>.</>}
            </p>
          </div>
          <DrawMap onChange={runPreview} onPlace={setPlaceName} />
          {busy && (
            <div className="panel coverage-progress" role="status" aria-live="polite">
              <b>Checking what imagery exists here…</b>
              <div className="progress-track"
                   role="progressbar" aria-label="Checking coverage">
                <div className="progress-fill" />
              </div>
              <p className="tiny muted">
                Searching two years of Sentinel-2 and 90 days of Sentinel-1 over this
                shape. The counts below are final once this finishes.
              </p>
            </div>
          )}
          {preview && (
            <div className="panel aoi-summary">
              <b>{preview.area_km2.toFixed(1)} km² · analysis CRS {preview.analysis_crs}</b>
              <span className="chip settled" title="This shape has been fully checked.">
                ✓ coverage checked
              </span>
              {preview.slow_warning && (
                <div className="warn-box" style={{ margin: "8px 0" }}>
                  Above 500 km². Runs over this area will be slow and each one downloads
                  more data. Consider splitting it into smaller monitors.
                </div>
              )}
              <table style={{ marginTop: 8 }}>
                <tbody>
                  <tr><td>Usable Sentinel-2 observations, last 2 years</td>
                      <td>{preview.s2
                        ? <>{preview.s2.usable_estimate} of {preview.s2.total}
                            <span className="muted"> (~{preview.s2.per_year}/yr, via {preview.s2.adapter})</span></>
                        : <span className="muted">could not check: {preview.errors?.s2}</span>}</td></tr>
                  <tr><td>Sentinel-1 orbits covering this area</td>
                      <td>{preview.s1_orbits.length
                        ? preview.s1_orbits.map((o: any) =>
                            `#${o.relative_orbit} ${o.pass_direction} (${o.scenes_90d} in 90d)`).join(", ")
                        : <span className="muted">none found</span>}</td></tr>
                </tbody>
              </table>
              {preview.s2 && preview.s2.usable_estimate < 30 && (
                <div className="warn-box" style={{ marginTop: 8 }}>
                  Fewer than 30 usable optical observations in two years. Seasonal methods
                  need roughly that many. A radar recipe will see this site more reliably.
                </div>
              )}
            </div>
          )}
          <div className="panel">
            <b>Or paste a GeoJSON geometry / Feature</b>
            <textarea rows={3} style={{ width: "100%", marginTop: 6 }} value={paste}
                      onChange={(e) => setPaste(e.target.value)}
                      placeholder='{"type":"Polygon","coordinates":[[[...]]]}' />
            <button onClick={() => {
              try {
                const g = JSON.parse(paste);
                runPreview(g.type === "Feature" ? g.geometry
                  : g.type === "FeatureCollection" ? g.features[0].geometry : g);
              } catch { setErr("That is not valid GeoJSON."); }
            }}>Use this geometry</button>
            <div className="geometry-divider"><span>or import</span></div>
            <label className="file-action">
              Choose a KML file
              <input type="file" accept=".kml,application/vnd.google-earth.kml+xml"
                     onChange={async (e) => {
                       const file = e.target.files?.[0];
                       if (!file) return;
                       try {
                         setErr(undefined);
                         await runPreview(geometryFromKml(await file.text()));
                       } catch (error: any) { setErr(error.message); }
                       finally { e.target.value = ""; }
                     }} />
            </label>
            <p className="tiny muted">
              Imports polygon boundaries; placemarks without an area are ignored.
            </p>
          </div>

          <div className="row">
            <button className="primary" disabled={!aoi || busy || !preview}
                    title={!aoi ? "Draw or paste an area first"
                      : busy ? "Waiting for the coverage check to finish" : undefined}
                    onClick={() => setStep(1)}>
              {busy ? "Checking coverage…" : "Next: what to watch for"}
            </button>
            {/* A disabled button with no reason next to it is a dead end. */}
            {(!aoi || busy || !preview) && (
              <span className="tiny muted">
                {!aoi
                  ? "Draw an area on the map, or paste a geometry, to continue."
                  : "Waiting for the coverage check to finish…"}
              </span>
            )}
          </div>
        </div>
      )}

      {step === 1 && (
        <div className="grid">
          <div className="panel">
            <b>Pick one or more questions</b>
            <p className="tiny muted" style={{ marginTop: 6 }}>
              Each one you pick is calibrated and scored separately over the same area,
              with its own baseline and threshold — two methods measure different
              quantities, so their scores are never pooled. Click again to unpick.
              {recipeIds.length > 1 &&
                ` Selected ${recipeIds.length}; ${recipeIds[0]} is the primary.`}
            </p>
            {mixesSensors && (
              <div className="warn-box" style={{ marginTop: 8 }}>
                This mixes optical and radar. Both source catalogues will be used, and
                the radar methods are held to the single relative orbit you pick below.
              </div>
            )}
          </div>
          <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fill,minmax(300px,1fr))" }}>
            {recipes.map((r) => (
              <button key={r.id}
                      className={`recipe-card ${recipeIds.includes(r.id) ? "sel" : ""}`}
                      aria-pressed={recipeIds.includes(r.id)}
                      onClick={() => toggleRecipe(r.id)}>
                <h4>
                  {recipeIds.includes(r.id) &&
                    <span className="chip settled" style={{ marginRight: 6 }}>
                      {recipeIds.indexOf(r.id) === 0 ? "primary" : "✓"}
                    </span>}
                  {r.plain_question}
                </h4>
                <div className="row" style={{ gap: 6, marginBottom: 8 }}>
                  <span className="chip">{r.secondary_sensor
                    ? "optical + radar"
                    : r.sensor === "S1" ? "radar, sees through cloud" : "optical"}</span>
                  <span className="chip">{r.resolution_m} m</span>
                  <span className="chip">{r.sensor === "S1" ? "~6 day revisit" : "~5 day revisit"}</span>
                </div>
                <div className="tiny muted">{r.display_name}</div>
                <a className="tiny" href={r.reference.url} target="_blank" rel="noreferrer"
                   onClick={(e) => e.stopPropagation()}>why this method ↗</a>
              </button>
            ))}
          </div>

          {needsRadarOrbit && (
            <div className="panel">
              <b>Pick one radar orbit</b>
              <p className="tiny muted">
                Radar brightness depends on the viewing geometry, so observations may only
                be compared within a single relative orbit and pass direction. Mixing them
                is the most common source of false radar change.
              </p>
              <div className="row">
                {(preview?.s1_orbits || []).map((o: any) => (
                  <button key={`${o.relative_orbit}${o.pass_direction}`}
                          className={orbit?.relative_orbit === o.relative_orbit ? "primary" : ""}
                          onClick={() => setOrbit(o)}>
                    #{o.relative_orbit} {o.pass_direction} · {o.scenes_90d} scenes/90d
                  </button>
                ))}
                {!preview?.s1_orbits?.length && <span className="muted">No orbits detected.</span>}
              </div>
            </div>
          )}

          {recipe && (
            <div className="panel">
              <b>What this method cannot do</b>
              <ul>{recipe.limitations.map((l: string) => <li key={l}>{l}</li>)}</ul>
            </div>
          )}

          <div className="row">
            <button onClick={() => setStep(0)}>Back</button>
            <button className="primary" disabled={!recipeIds.length || busy ||
                       (needsRadarOrbit && !orbit)}
                    title={!recipeIds.length ? "Pick at least one question"
                      : needsRadarOrbit && !orbit ? "Pick a radar orbit" : undefined}
                    onClick={beginCalibration}>
              {busy ? "Creating the monitor…" : "Next: calibrate on this site's history"}
            </button>
            {!busy && (!recipeIds.length || (needsRadarOrbit && !orbit)) && (
              <span className="tiny muted">
                {!recipeIds.length
                  ? "Pick at least one question above to continue."
                  : "Pick one radar orbit above to continue — the radar method needs it."}
              </span>
            )}
          </div>
          <p className="tiny muted">
            The next step creates the monitor and starts a {BACKTEST_YEARS}-year
            calibration over this area. It runs in the background and sends no alerts.
          </p>
        </div>
      )}

      {step === 2 && (
        <div className="grid">
          <div className="panel">
            <b>Set the alert threshold</b>
            <p className="tiny muted" style={{ margin: "4px 0 0" }}>
              We scored the last {BACKTEST_YEARS} years of this exact site with the method
              you picked. The histogram is every past observation by how much it changed.
              Drag the line: anything to the {spec?.score_polarity === "lower_is_more_change"
                ? "left" : "right"} of it would have raised an alert. The count and the real
              past dates below update as you drag, so you can pick a line that would have
              caught the changes you care about without firing every week. Unsure? Use the
              default below.
            </p>
          </div>
          {backtesting && (
            <>
              <div className="panel">
                <b>Calibrating</b>
                <p className="tiny muted" style={{ margin: "4px 0 0" }}>
                  {STATUS_HELP.calibrating}
                </p>
                <div className="muted" style={{ marginTop: 8 }}>
                  {runs.length} observation{runs.length === 1 ? "" : "s"} scored so far.
                </div>
              </div>
              {uuid && <ProjectProgress uuid={uuid} />}
            </>
          )}
          <Calibrate runs={runs} threshold={threshold} units={spec?.score_units || ""}
                    polarity={spec?.score_polarity}
                     semantics={spec?.threshold_semantics} years={BACKTEST_YEARS}
                     onChange={(t) => { setThreshold(t); setUsedDefault(false); }} />
          <div className="row">
            <button onClick={() => setStep(1)}>Back</button>
            <button onClick={() => {
              setThreshold(recipe?.defaults.threshold ?? threshold);
              setUsedDefault(true);
            }}>
              I don't know, use the {recipe?.default_provenance.threshold === "heuristic"
                ? "conservative app default" : "published default"}
            </button>
            <button className="primary" onClick={() => setStep(3)}>Next: schedule it</button>
          </div>
          {usedDefault && (
            <p className="tiny muted">
              Using the {recipe?.default_provenance.threshold === "heuristic"
                ? "app's heuristic" : "published"} default of {recipe?.defaults.threshold}.
              That provenance is recorded alongside the calibration.
            </p>
          )}
        </div>
      )}

      {step === 3 && (
        <div className="grid" style={{ maxWidth: 680 }}>
          {/* Last screen of a four-step flow. It used to show only a name and a
              schedule, with no restatement of what was actually configured. */}
          <div className="panel">
            <h3 className="card">Review</h3>
            <table>
              <tbody>
                <tr><th scope="row">Area</th>
                    <td>{preview ? `${preview.area_km2.toFixed(1)} km²` : "—"}
                      {preview?.analysis_crs &&
                        <span className="muted"> · {preview.analysis_crs}</span>}</td></tr>
                <tr><th scope="row">Watching for</th>
                    <td>{chosen.length
                      ? <ul style={{ margin: 0, paddingLeft: 18 }}>
                          {chosen.map((r, i) => (
                            <li key={r.id}>{r.plain_question}
                              {i === 0 && chosen.length > 1 &&
                                <span className="muted"> (primary)</span>}</li>
                          ))}
                        </ul>
                      : "—"}</td></tr>
                {orbit && (
                  <tr><th scope="row">Radar orbit</th>
                      <td className="mono">#{orbit.relative_orbit} {orbit.pass_direction}</td></tr>
                )}
                <tr><th scope="row">Alert threshold</th>
                    <td className="mono">{threshold} {spec?.score_units}
                      <div className="tiny muted">
                        {usedDefault || !runs.length
                          ? "The recipe default — recorded as such. Retune it on the "
                            + "Methods tab once calibration finishes."
                          : `Chosen from this site's ${BACKTEST_YEARS}-year history.`}
                      </div></td></tr>
                <tr><th scope="row">Observations scored</th>
                    <td>{runs.length}
                      {backtesting && <span className="muted"> · still calibrating</span>}</td></tr>
              </tbody>
            </table>
          </div>
          <div className="panel grid">
            <label>Name
              <input value={name} style={{ width: "100%" }}
                     onChange={(e) => setName(e.target.value)}
                     placeholder={fallbackName()} />
            </label>
            <label>Check for new imagery
              <select value={cron} style={{ width: "100%" }}
                      onChange={(e) => setCron(e.target.value)}>
                <option value={preview?.suggested_cron?.[recipe?.sensor || "S2"] || cron}>
                  Suggested — matches the real revisit at this latitude
                </option>
                <option value="0 3 * * *">Every day at 03:00</option>
                <option value="0 3 */2 * *">Every 2 days</option>
                <option value="0 3 * * 1">Weekly, Mondays</option>
              </select>
              <span className="tiny muted">
                {describeCron(cron) || "—"}
                <span className="mono"> ({cron})</span>
              </span>
            </label>
            <p className="tiny muted">
              Notification channels are configured once, in{" "}
              <Link to="/settings">Settings</Link>, and apply to every monitor.
            </p>
          </div>
          <div className="row">
            <button onClick={() => setStep(2)}>Back</button>
            <button className="primary" disabled={busy} onClick={activate}>
              {busy ? "Activating…" : "Activate monitor"}
            </button>
            {backtesting && (
              <span className="tiny muted">
                Calibration is still running. Activating now is fine — it finishes in the
                background and you can retune the threshold afterwards.
              </span>
            )}
          </div>
        </div>
      )}
    </>
  );
}
