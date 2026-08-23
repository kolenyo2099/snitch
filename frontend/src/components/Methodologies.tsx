import { useState } from "react";
import { api } from "../api";
import { Methodology, Recipe, Run } from "../types";

/**
 * A project may watch one AOI with several methodologies at once. Each carries its own
 * baseline, threshold and score history — two methods measure different quantities, so
 * nothing here is ever summed or averaged across them.
 */
export function Methodologies(
  { uuid, methodologies, recipes, runs, onChange }:
  {
    uuid: string; methodologies: Methodology[]; recipes: Recipe[]; runs: Run[];
    onChange: () => void;
  },
) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string>();
  const [adding, setAdding] = useState("");

  const used = new Set(methodologies.map((m) => m.recipe_id));
  const available = recipes.filter((r) => !used.has(r.id));

  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true); setErr(undefined);
    try { await fn(); onChange(); }
    catch (e: any) { setErr(e.message); }
    finally { setBusy(false); }
  };

  return (
    <div className="panel">
      <div className="spread">
        <b>Methodologies ({methodologies.length})</b>
        {methodologies.length > 1 && (
          <span className="tiny muted">
            Scored separately; baselines and thresholds are never shared.
          </span>
        )}
      </div>

      {err && <div className="err-box" style={{ margin: "8px 0" }}>{err}</div>}

      <table style={{ marginTop: 8 }}>
        <thead>
          <tr>
            <th>Question</th><th>Detector</th><th>Baseline</th>
            <th>Threshold</th><th>Runs</th><th />
          </tr>
        </thead>
        <tbody>
          {methodologies.map((m, i) => {
            const r = recipes.find((x) => x.id === m.recipe_id);
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
                    ? `${(m.baseline_start || "").slice(0, 10)} → ${(m.baseline_end || "").slice(0, 10)}`
                    : <span className="muted">not fitted</span>}
                </td>
                <td className="tiny mono">{m.params?.threshold ?? "—"}</td>
                <td className="tiny">{mine.length || <span className="muted">0</span>}</td>
                <td>
                  <div className="row" style={{ gap: 6 }}>
                    <button disabled={busy}
                            onClick={() => act(() => api.startBacktest(uuid, 3, m.id))}>
                      Calibrate
                    </button>
                    {methodologies.length > 1 && (
                      <button disabled={busy || mine.length > 0}
                              title={mine.length
                                ? "This methodology has scored runs; removing it would orphan them."
                                : "Remove this methodology"}
                              onClick={() => act(() => api.removeMethodology(uuid, m.id))}>
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
                  })}>
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
