import { useState } from "react";
import { api } from "../api";
import { useApi } from "../useApi";
import { Async, ErrorBox } from "../components/Async";
import { ConfigEditor } from "../components/ConfigEditor";
import { DetectorSpec, Recipe } from "../types";

const GB = (b: number) => (b / 1e9).toFixed(2) + " GB";

const QUEUE_LABEL: Record<string, string> = {
  queued: "waiting", leased: "running", done: "finished", failed: "failed",
};

export default function Settings() {
  const [pw, setPw] = useState(localStorage.getItem("snitch_password") || "");
  const [gcResult, setGc] = useState<any>();
  const [gcErr, setGcErr] = useState<string>();
  const [gcBusy, setGcBusy] = useState(false);

  const health = useApi<any>(() => api.healthFull(), []);
  const registry = useApi<{ recipes: Recipe[]; detectors: DetectorSpec[] }>(async () => {
    const [recipes, detectors] = await Promise.all([api.recipes(), api.detectors()]);
    return { recipes, detectors };
  }, []);

  const runGc = async (dry: boolean) => {
    if (!dry && !confirm("Permanently delete every artifact that no record references? "
                         + "This cannot be undone.")) return;
    setGcBusy(true); setGcErr(undefined);
    try { setGc(await api.gc(dry)); if (!dry) health.reload(); }
    catch (e: any) { setGcErr(e.message || "The cleanup request failed."); }
    finally { setGcBusy(false); }
  };

  return (
    <>
      <h1 className="page">Settings</h1>
      <p className="sub">Everything the <code>config.yaml</code> used to hold, editable
        right here. Environment overrides (<code>SNITCH_SECTION__KEY</code>) still win.</p>

      {/* Everything below reports on the server. If the server cannot be reached, say
          so once at the top — the panels used to render "not configured" and zeroes,
          which reads as a finding about the user's setup rather than a failed request. */}
      {health.error && (
        <ErrorBox error={health.error} what="Server status" onRetry={health.reload} />
      )}

      <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))" }}>
        <ConfigEditor onSaved={health.reload} />

        <div className="panel">
          <h3 className="card">Data sources</h3>
          <table>
            <thead><tr><th scope="col">Adapter</th><th scope="col">Reachable</th></tr></thead>
            <tbody>
              {(health.data?.adapters || []).map((a: any) => (
                <tr key={a.adapter}>
                  <th scope="row" className="mono" style={{ fontWeight: 400 }}>{a.adapter}</th>
                  <td>{a.ok ? <span style={{ color: "var(--green)" }}>ok</span>
                            : <span style={{ color: "var(--red)" }}>{a.error || a.status}</span>}</td>
                </tr>
              ))}
              {health.loading && !health.data &&
                <tr><td colSpan={2} className="muted">checking…</td></tr>}
              {health.data && !health.data.adapters?.length &&
                <tr><td colSpan={2} className="muted">No adapters are enabled.</td></tr>}
              {health.error && !health.data &&
                <tr><td colSpan={2} className="muted">Unknown — the server did not answer.</td></tr>}
            </tbody>
          </table>
          <button onClick={health.reload} disabled={health.loading} style={{ marginTop: 8 }}>
            {health.loading ? "Checking…" : "Re-check"}
          </button>
        </div>

        <div className="panel">
          <h3 className="card">Credentials</h3>
          <p className="tiny muted">Presence only. Values are never logged, exported, or
            returned by the API.</p>
          <table>
            <tbody>
              {Object.entries(health.data?.credentials || {}).flatMap(([svc, envs]: any) =>
                Object.entries(envs).map(([env, present]: any) => (
                  <tr key={env}>
                    <th scope="row" className="mono" style={{ fontWeight: 400 }}>{svc} / {env}</th>
                    <td>{present
                      ? <span style={{ color: "var(--green)" }}>set</span>
                      : <span className="muted">not set</span>}</td>
                  </tr>
                )))}
              {!health.data &&
                <tr><td colSpan={2} className="muted">
                  {health.error ? "Unknown — the server did not answer." : "checking…"}
                </td></tr>}
            </tbody>
          </table>
          <p className="tiny muted">
            Set these as environment variables on the API process (or in the{" "}
            <code>environment:</code> block of <code>docker-compose.yml</code>) and
            restart it. Without CDSE credentials, Snitch falls back to the open
            Earth Search catalogue, which is recorded on every run that uses it.
          </p>
        </div>

        <div className="panel">
          <h3 className="card">Google Earth Engine</h3>
          <p className="tiny">
            Optional accelerator for baseline fitting, backtests, and Cloud Score+ masks.
            Never required: deleting the credentials leaves every project working on the
            local path. Runs that used GEE are marked, because that is a
            reproducibility-relevant fact.
          </p>
          <table>
            <tbody>
              <tr><th scope="row">Configured</th><td>{!health.data
                ? <span className="muted">{health.error ? "unknown" : "checking…"}</span>
                : health.data.gee?.configured
                  ? <span style={{ color: "var(--green)" }}>yes</span>
                  : <span className="muted">no — projects stay on the local path</span>}</td></tr>
              <tr><th scope="row">Monthly EECU-hours</th><td>{health.data
                ? `${health.data.gee.monthly_used} of ${health.data.gee.monthly_budget} used`
                : "—"}</td></tr>
              <tr><th scope="row">Today</th><td>{health.data
                ? `${health.data.gee.daily_used} of ${health.data.gee.daily_cap} used`
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
          <h3 className="card">Storage and queue</h3>
          <table>
            <tbody>
              <tr><th scope="row">Artifacts</th>
                  <td>{health.data ? GB(health.data.storage.artifact_bytes) : "—"}</td></tr>
              <tr><th scope="row">Disk free</th>
                  <td>{health.data ? GB(health.data.storage.disk_free_bytes) : "—"}</td></tr>
              <tr><th scope="row">Background jobs</th>
                  <td>
                    {/* This was a raw JSON dump: {"done":6,"queued":2}. */}
                    {health.data
                      ? Object.entries(health.data.queue || {}).length
                        ? Object.entries(health.data.queue).map(([k, v]) =>
                            `${v} ${QUEUE_LABEL[k] || k}`).join(" · ")
                        : "none"
                      : "—"}
                    {health.data?.worker && !health.data.worker.ok && (
                      <div className="tiny" style={{ color: "var(--amber)" }}>
                        {health.data.worker.hint}
                      </div>
                    )}
                  </td></tr>
            </tbody>
          </table>
          <div className="row" style={{ marginTop: 8 }}>
            <button disabled={gcBusy} onClick={() => runGc(true)}>Preview cleanup</button>
            <button className="danger" disabled={gcBusy || !health.data?.storage?.gc_enabled}
                    title={health.data && !health.data.storage?.gc_enabled
                      ? "Enable storage.gc_enabled in the Configuration panel above first"
                      : undefined}
                    onClick={() => runGc(false)}>
              Delete unreferenced artifacts
            </button>
          </div>
          {gcErr && <ErrorBox error={gcErr} what="The cleanup" />}
          {health.data && !health.data.storage?.gc_enabled && (
            <p className="tiny muted">
              Deletion is disabled by <code>storage.gc_enabled: false</code> — flip it in
              the Configuration panel above and this button unlocks.
            </p>
          )}
          {gcResult && (
            <p className="tiny muted" role="status" aria-live="polite">
              {gcResult.dry_run ? "Would delete" : "Deleted"} {gcResult.count} artifacts
              ({GB(gcResult.bytes)}). Only artifacts referenced by zero records are ever removed.
            </p>
          )}
        </div>

        <div className="panel">
          <h3 className="card">Access</h3>
          <p className="tiny muted">If <code>ui.password</code> is set in config.yaml,
            enter it here to reach the API from this browser. Saving signs this
            browser in (a cookie, so imagery and export links work); the password
            itself is kept in this browser only.</p>
          <div className="row">
            <label className="tiny muted">
              API password
              <input type="password" value={pw} autoComplete="current-password"
                     aria-label="API password" style={{ display: "block" }}
                     onChange={(e) => setPw(e.target.value)} />
            </label>
            <button style={{ alignSelf: "flex-end" }}
                    onClick={async () => {
                      try { await api.login(pw); } catch { /* cookie is best-effort;
                             the header alone still authenticates API calls */ }
                      localStorage.setItem("snitch_password", pw);
                      location.reload();
                    }}>
              Save
            </button>
            <button className="tiny muted" style={{ alignSelf: "flex-end", background: "none" }}
                    onClick={async () => {
                      try { await api.logout(); } catch { /* already signed out */ }
                      localStorage.removeItem("snitch_password");
                      location.reload();
                    }}>
              Sign out
            </button>
          </div>
        </div>

        <div className="panel" style={{ gridColumn: "1 / -1" }}>
          <h3 className="card">Recipe registry</h3>
          <Async state={registry} what="The recipe registry">
            {({ recipes, detectors }) => (
              <div style={{ overflowX: "auto" }}>
                <table>
                  <thead><tr><th scope="col">Question</th><th scope="col">Method</th>
                    <th scope="col">What the threshold means</th>
                    <th scope="col">Reference</th></tr></thead>
                  <tbody>
                    {recipes.map((r) => {
                      const d = detectors.find((x) => x.id === r.detector);
                      return (
                        <tr key={r.id}>
                          <td>{r.plain_question}<div className="tiny muted">{r.id} v{r.version}</div></td>
                          <td className="tiny mono">{r.detector}
                            <div className="muted">{r.sensor} · {r.resolution_m} m</div></td>
                          <td className="tiny">{d?.threshold_semantics}</td>
                          <td className="tiny">
                            <a href={r.reference.url} target="_blank" rel="noreferrer"
                               title={r.reference.citation}>
                              {r.reference.citation.slice(0, 60)}… ↗
                            </a>
                          </td>
                        </tr>
                      );
                    })}
                    {!recipes.length &&
                      <tr><td colSpan={4} className="muted">No recipes are registered.</td></tr>}
                  </tbody>
                </table>
              </div>
            )}
          </Async>
        </div>
      </div>
    </>
  );
}
