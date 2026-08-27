import { useEffect, useState } from "react";
import { api } from "../api";
import { DetectorSpec, Recipe } from "../types";

const GB = (b: number) => (b / 1e9).toFixed(2) + " GB";

export default function Settings() {
  const [health, setHealth] = useState<any>();
  const [recipes, setRecipes] = useState<Recipe[]>([]);
  const [detectors, setDetectors] = useState<DetectorSpec[]>([]);
  const [gcResult, setGc] = useState<any>();
  const [pw, setPw] = useState(localStorage.getItem("tw_password") || "");

  const load = () => { api.healthFull().then(setHealth); };
  useEffect(() => { load(); api.recipes().then(setRecipes); api.detectors().then(setDetectors); }, []);

  return (
    <>
      <h2>Settings</h2>
      <p className="sub">Configuration lives in <code>config.yaml</code>; every key is
        overridable with a <code>TW_SECTION__KEY</code> environment variable.</p>

      <div className="grid" style={{ gridTemplateColumns: "1fr 1fr" }}>
        <div className="panel">
          <b>Data sources</b>
          <table style={{ marginTop: 8 }}>
            <thead><tr><th>Adapter</th><th>Reachable</th></tr></thead>
            <tbody>
              {(health?.adapters || []).map((a: any) => (
                <tr key={a.adapter}>
                  <td className="mono">{a.adapter}</td>
                  <td>{a.ok ? <span style={{ color: "var(--green)" }}>ok</span>
                            : <span style={{ color: "var(--red)" }}>{a.error || a.status}</span>}</td>
                </tr>
              ))}
              {!health?.adapters && <tr><td colSpan={2} className="muted">checking…</td></tr>}
            </tbody>
          </table>
          <button onClick={load} style={{ marginTop: 8 }}>Re-check</button>
        </div>

        <div className="panel">
          <b>Credentials</b>
          <p className="tiny muted">Presence only. Values are never logged, exported, or
            returned by the API.</p>
          <table>
            <tbody>
              {Object.entries(health?.credentials || {}).flatMap(([svc, envs]: any) =>
                Object.entries(envs).map(([env, present]: any) => (
                  <tr key={env}><td className="mono">{svc} / {env}</td>
                    <td>{present ? "set" : <span className="muted">not set</span>}</td></tr>
                )))}
            </tbody>
          </table>
        </div>

        <div className="panel">
          <b>Google Earth Engine</b>
          <p className="tiny">
            Optional accelerator for baseline fitting, backtests, and Cloud Score+ masks.
            Never required: deleting the credentials leaves every project working on the
            local path. Runs that used GEE are marked, because that is a
            reproducibility-relevant fact.
          </p>
          <table style={{ marginTop: 8 }}>
            <tbody>
              <tr><td>Configured</td><td>{health?.gee?.configured
                ? <span style={{ color: "var(--green)" }}>yes</span>
                : <span className="muted">no — projects stay on the local path</span>}</td></tr>
              <tr><td>Monthly EECU-hours</td><td>{health
                ? `${health.gee.monthly_used} of ${health.gee.monthly_budget} used`
                : "—"}</td></tr>
              <tr><td>Today</td><td>{health
                ? `${health.gee.daily_used} of ${health.gee.daily_cap} used`
                : "—"}</td></tr>
            </tbody>
          </table>
          <p className="tiny muted">
            A task estimated above the remaining budget is refused before submission,
            not truncated mid-run.
          </p>
          <p className="tiny muted">
            Noncommercial projects have a monthly EECU-hour quota that resets on the first
            of the month. Exceeding it degrades to restricted mode rather than cutting you
            off. A daily EECU cap can be set in the Cloud console, and noncommercial status
            requires annual reverification.{" "}
            <a href="https://developers.google.com/earth-engine/guides/noncommercial_tiers"
               target="_blank" rel="noreferrer">Tier guide ↗</a>
          </p>
        </div>

        <div className="panel">
          <b>Storage</b>
          <table style={{ marginTop: 8 }}>
            <tbody>
              <tr><td>Artifacts</td><td>{health ? GB(health.storage.artifact_bytes) : "—"}</td></tr>
              <tr><td>Disk free</td><td>{health ? GB(health.storage.disk_free_bytes) : "—"}</td></tr>
              <tr><td>Queue</td><td className="mono">{JSON.stringify(health?.queue || {})}</td></tr>
            </tbody>
          </table>
          <div className="row" style={{ marginTop: 8 }}>
            <button onClick={async () => setGc(await api.gc(true))}>Preview cleanup</button>
            <button onClick={async () => { setGc(await api.gc(false)); load(); }}>
              Delete unreferenced artifacts
            </button>
          </div>
          {health && !health.storage?.gc_enabled && (
            <p className="tiny muted">
              Deletion is disabled by <code>storage.gc_enabled: false</code> in
              config.yaml — the preview works, the delete button will be refused until
              you enable it.
            </p>
          )}
          {gcResult && (
            <p className="tiny muted">
              {gcResult.dry_run ? "Would delete" : "Deleted"} {gcResult.count} artifacts
              ({GB(gcResult.bytes)}). Only artifacts referenced by zero records are ever removed.
            </p>
          )}
        </div>

        <div className="panel">
          <b>Access</b>
          <p className="tiny muted">If <code>ui.password</code> is set in config.yaml,
            enter it here to reach the API from this browser.</p>
          <div className="row">
            <input type="password" value={pw} onChange={(e) => setPw(e.target.value)} />
            <button onClick={() => { localStorage.setItem("tw_password", pw); location.reload(); }}>
              Save
            </button>
          </div>
        </div>

        <div className="panel" style={{ gridColumn: "1 / -1" }}>
          <b>Recipe registry</b>
          <table style={{ marginTop: 8 }}>
            <thead><tr><th>Question</th><th>Method</th><th>Threshold means</th><th>Reference</th></tr></thead>
            <tbody>
              {recipes.map((r) => {
                const d = detectors.find((x) => x.id === r.detector);
                return (
                  <tr key={r.id}>
                    <td>{r.plain_question}<div className="tiny muted">{r.id} v{r.version}</div></td>
                    <td className="tiny mono">{r.detector}<div className="muted">{r.sensor} · {r.resolution_m} m</div></td>
                    <td className="tiny">{d?.threshold_semantics}</td>
                    <td className="tiny">
                      <a href={r.reference.url} target="_blank" rel="noreferrer">{r.reference.citation.slice(0, 60)}… ↗</a>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}
