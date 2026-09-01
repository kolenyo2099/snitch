import { useState } from "react";
import { api } from "../api";
import { ErrorBox } from "./Async";
import { Calibrate } from "./Calibrate";
import { DetectorSpec, Methodology, Recipe, Run } from "../types";

/**
 * A project may watch one AOI with several methodologies at once. Each carries its own
 * baseline, threshold and score history — two methods measure different quantities, so
 * nothing here is ever summed or averaged across them.
 */
export function Methodologies(
  { uuid, methodologies, recipes, detectors, runs, onChange, onShowRuns }:
  {
    uuid: string; methodologies: Methodology[]; recipes: Recipe[];
    detectors?: DetectorSpec[]; runs: Run[]; onChange: () => void;
    /** Jumps to the Runs tab — a count you cannot click is a dead end. */
    onShowRuns?: () => void;
  },
) {
  // Tuning was only reachable from the creation wizard, so an existing monitor could
  // never be retuned without making a new one.
  const [tuning, setTuning] = useState<Methodology>();
  const [draft, setDraft] = useState(0);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string>();
  const [note, setNote] = useState<string>();
  const [adding, setAdding] = useState("");

  const used = new Set(methodologies.map((m) => m.recipe_id));
  const available = recipes.filter((r) => !used.has(r.id));

  const act = async (fn: () => Promise<unknown>, done?: string) => {
    setBusy(true); setErr(undefined); setNote(undefined);
    try { await fn(); setNote(done); onChange(); }
    catch (e: any) { setErr(e.message); }
    finally { setBusy(false); }
  };

  /* Calibrating downloads and re-scores three years of imagery for this AOI. It used
   * to fire on a single click with no confirmation and no visible response — the only
   * evidence anything had happened was the header button changing. */
  const calibrate = (m: Methodology, question: string) => {
    if (!confirm(
      `Calibrate "${question}"?\n\n`
      + `This downloads and re-scores three years of imagery for this area. It runs in `
      + `the background and can take from several minutes to a few hours depending on `
      + `the area, and no alerts are sent while it runs.`)) return;
    act(() => api.startBacktest(uuid, 3, m.id),
        "Calibration queued. Progress appears above the tabs; the monitor's status "
        + "stays \u201ccalibrating\u201d until it finishes.");
  };

  return (
    <div className="panel">
      <div className="spread">
        <h3 className="card">Methodologies ({methodologies.length})</h3>
        {methodologies.length > 1 && (
          <span className="tiny muted">
            Scored separately; baselines and thresholds are never shared.
          </span>
        )}
      </div>

      {err && <ErrorBox error={err} what="That change" />}
      {note && <p className="tiny" role="status" aria-live="polite"
                  style={{ color: "var(--accent)" }}>{note}</p>}

      <div style={{ overflowX: "auto" }}>
      <table style={{ marginTop: 8 }}>
        <thead>
          <tr>
            <th scope="col">Question</th><th scope="col">Detector</th>
            <th scope="col">Baseline</th><th scope="col">Threshold</th>
            <th scope="col">Runs</th><th scope="col"><span className="tiny">Actions</span></th>
          </tr>
        </thead>
        <tbody>
          {methodologies.map((m, i) => {
            const r = recipes.find((x) => x.id === m.recipe_id);
            const spec = detectors?.find((d) => d.id === r?.detector);
            const mine = runs.filter((run) => run.methodology_id === m.id);
            return (
              <tr key={m.id}>
                <td>
                  {r?.plain_question || m.recipe_id}
                  <div className="tiny muted">
                    {m.recipe_id} v{m.recipe_version}
                    {i === 0 && <span className="chip" style={{ marginLeft: 6 }}>primary</span>}
                  </div>
                </td>
                <td className="tiny">{r?.detector || "—"}</td>
                <td className="tiny">
                  {m.baseline_artifact_id
                    ? <>
                        {(m.baseline_start || "").slice(0, 10)} → {(m.baseline_end || "").slice(0, 10)}
                        <div><a className="tiny" href={`/api/v1/artifacts/${m.baseline_artifact_id}/raw`}
                                target="_blank" rel="noreferrer"
                                title="The fitted baseline every score of this method is measured against">
                          baseline artifact ↗</a></div>
                      </>
                    : <span className="muted">not fitted</span>}
                </td>
                <td className="tiny mono">
                  {m.params?.threshold ?? "—"}
                  {m.params?.threshold != null && spec?.score_units
                    ? <span className="muted"> {spec.score_units}</span> : null}
                </td>
                <td className="tiny">{mine.length
                  ? <a href="#" onClick={(e) => { e.preventDefault(); onShowRuns?.(); }}>
                      {mine.length} runs</a>
                  : <span className="muted">0</span>}</td>
                <td>
                  <div className="row" style={{ gap: 6 }}>
                    <button disabled={busy}
                            onClick={() => {
                              setDraft(m.params?.threshold ?? 0);
                              setTuning(tuning?.id === m.id ? undefined : m);
                            }}
                            aria-expanded={tuning?.id === m.id}>
                      Tune threshold
                    </button>
                    <button disabled={busy}
                            title="Re-score three years of history so you can pick a threshold"
                            onClick={() => calibrate(m, r?.plain_question || m.recipe_id)}>
                      {busy ? "Working…" : m.baseline_artifact_id ? "Re-calibrate" : "Calibrate"}
                    </button>
                    {methodologies.length > 1 && (
                      <button disabled={busy || mine.length > 0}
                              title={mine.length
                                ? "This methodology has scored runs; removing it would orphan them."
                                : "Remove this methodology"}
                              onClick={() => {
                                if (!confirm(`Remove "${r?.plain_question || m.recipe_id}" `
                                             + "from this monitor?")) return;
                                act(() => api.removeMethodology(uuid, m.id), "Removed.");
                              }}>
                        Remove
                      </button>
                    )}
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      </div>

      {tuning && (() => {
        const spec = detectors?.find(
          (d) => d.id === recipes.find((r) => r.id === tuning.recipe_id)?.detector);
        const mine = runs.filter((run) => run.methodology_id === tuning.id);
        return (
          <div className="panel" style={{ marginTop: 12 }}>
            <div className="spread">
              <h3 className="card" style={{ margin: 0 }}>
                Alert threshold ·{" "}
                {recipes.find((r) => r.id === tuning.recipe_id)?.plain_question}
              </h3>
              <button onClick={() => setTuning(undefined)}>Close</button>
            </div>
            <p className="tiny muted">
              Every past score at this site. Drag the line, or use the arrow keys, to see
              which real dates would have alerted.
            </p>
            <Calibrate runs={mine} threshold={draft} units={spec?.score_units || ""}
                       polarity={spec?.score_polarity}
                       semantics={spec?.threshold_semantics} years={3}
                       onChange={setDraft} />
            <div className="row" style={{ marginTop: 10 }}>
              <button className="primary" disabled={busy || draft === tuning.params?.threshold}
                      onClick={() => act(async () => {
                        await api.setThreshold(uuid, { threshold: draft,
                                                       source: "calibrated" }, tuning.id);
                        setTuning(undefined);
                      }, `Threshold saved as ${draft}. It applies to future runs.`)}>
                {busy ? "Saving…" : "Save threshold"}
              </button>
              {draft === tuning.params?.threshold && (
                <span className="tiny muted">Unchanged — move the line to save a new value.</span>
              )}
            </div>
          </div>
        );
      })()}

      {available.length > 0 && (
        <div className="row" style={{ marginTop: 10, gap: 8 }}>
          <select value={adding} onChange={(e) => setAdding(e.target.value)}>
            <option value="">Add another question…</option>
            {available.map((r) => (
              <option key={r.id} value={r.id}>{r.plain_question}</option>
            ))}
          </select>
          <button className="primary" disabled={!adding || busy}
                  onClick={() => act(async () => {
                    await api.addMethodology(uuid, adding);
                    setAdding("");
                  }, "Added. Calibrate it before it can score.")}>
            Add
          </button>
          <span className="tiny muted">
            A new methodology starts with no baseline — calibrate it before it can score.
          </span>
        </div>
      )}
    </div>
  );
}
