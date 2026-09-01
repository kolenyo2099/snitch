import { useMemo, useState } from "react";
import { api } from "../api";
import { useApi } from "../useApi";
import { Async, ErrorBox } from "./Async";

type ConfigKey = {
  value: any; type: "bool" | "int" | "float" | "str" | "list";
  set: boolean | null; env: string | null; restart: boolean;
  readOnly: boolean; help: string;
};

const SECTION_ORDER = ["storage", "adapters", "scheduler", "alerts",
                       "explanations", "notifications", "ui"];
const SECTION_LABEL: Record<string, string> = {
  storage: "Storage", adapters: "Data sources", scheduler: "Scheduler",
  alerts: "Alerting", explanations: "Explanations (VLM)",
  notifications: "Notification channels", ui: "Interface",
};

/** config.yaml, editable. Edits are written to the file (original kept as .orig)
 *  and applied to the running API immediately; the separate worker process picks
 *  them up on restart, and anything it must refuse says so in place. */
export function ConfigEditor({ onSaved }: { onSaved?: () => void } = {}) {
  const state = useApi<{ path: string; keys: Record<string, ConfigKey> }>(
    () => api.configGet(), []);
  const [drafts, setDrafts] = useState<Record<string, any>>({});
  const [secrets, setSecrets] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<string>();
  const [err, setErr] = useState<string>();

  const sections = useMemo(() => {
    const by = new Map<string, [string, ConfigKey][]>();
    for (const [k, meta] of Object.entries(state.data?.keys || {})) {
      const s = k.split(".")[0];
      if (!by.has(s)) by.set(s, []);
      by.get(s)!.push([k, meta]);
    }
    return [...by.entries()].sort((a, b) => {
      const ia = SECTION_ORDER.indexOf(a[0]), ib = SECTION_ORDER.indexOf(b[0]);
      return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
    });
  }, [state.data]);

  const setDraft = (k: string, v: any) => setDrafts((d) => ({ ...d, [k]: v }));

  const shown = (k: string, meta: ConfigKey) => {
    if (k in drafts) return drafts[k];
    if (meta.type === "list") return (meta.value || []).join(", ");
    return meta.value ?? "";
  };

  const save = async (entries: [string, ConfigKey][]) => {
    setBusy(true); setErr(undefined); setResult(undefined);
    const values: Record<string, any> = {};
    for (const [k, meta] of entries) {
      if (meta.set != null) {          // write-only secret
        if (secrets[k]?.trim()) values[k] = secrets[k].trim();
        continue;
      }
      if (!(k in drafts) || meta.readOnly) continue;
      const v = drafts[k];
      values[k] = meta.type === "int" || meta.type === "float" ? Number(v) : v;
    }
    if (!Object.keys(values).length) { setResult("Nothing changed in this section."); setBusy(false); return; }
    try {
      const r = await api.configPut(values);
      const parts = [
        ...Object.entries(r.applied || {}).map(([k, how]) =>
          `${k} — ${how === "restart" ? "saved, applies after restart" : "applied"}`),
        ...Object.entries(r.rejected || {}).map(([k, why]) => `${k} — refused: ${why}`),
      ];
      setResult(parts.join(" · ") || "Nothing to save.");
      setDrafts((d) => {
        const n = { ...d };
        for (const k of Object.keys(r.applied || {})) delete n[k];
        return n;
      });
      setSecrets({});
      state.reload();
      onSaved?.();
    } catch (e: any) {
      setErr(e.message || "Could not save the configuration.");
    } finally { setBusy(false); }
  };

  const input = (k: string, meta: ConfigKey) => {
    const dis = meta.readOnly;
    if (meta.set != null)
      return <input type="password" autoComplete="new-password" disabled={dis}
                    placeholder={meta.set ? "set — leave blank to keep" : "not set"}
                    value={secrets[k] || ""}
                    onChange={(e) => setSecrets((s) => ({ ...s, [k]: e.target.value }))} />;
    if (meta.type === "bool")
      return <input type="checkbox" checked={!!shown(k, meta)} disabled={dis}
                    onChange={(e) => setDraft(k, e.target.checked)} />;
    if (meta.type === "int" || meta.type === "float")
      return <input type="number" step="any" disabled={dis}
                    value={shown(k, meta)} onChange={(e) => setDraft(k, e.target.value)} />;
    if (meta.type === "list")
      return <input type="text" disabled={dis}
                    placeholder="comma-separated"
                    value={shown(k, meta)} onChange={(e) => setDraft(k, e.target.value)} />;
    return <input type="text" disabled={dis}
                  placeholder={meta.value == null ? "not set" : ""}
                  value={shown(k, meta)} onChange={(e) => setDraft(k, e.target.value)} />;
  };

  return (
    <div className="panel" style={{ gridColumn: "1 / -1" }}>
      <div className="spread">
        <h3 className="card" style={{ margin: 0 }}>Configuration</h3>
        <span className="tiny muted mono">{state.data?.path}</span>
      </div>
      <p className="tiny muted">
        Every setting from <code>config.yaml</code>, editable right here. Changes are
        written to the file (the hand-edited original is kept as{" "}
        <code>config.yaml.orig</code>) and apply to the running API immediately — the
        background worker picks them up on its next restart. An environment override{" "}
        (<code>TW_SECTION__KEY</code>) always wins and can only be changed there.
      </p>
      <Async state={state} what="The configuration">
        {(data) => (
          <div className="cfg-sections">
            {sections.map(([section, entries]) => (
              <fieldset key={section} className="cfg-section">
                <legend>{SECTION_LABEL[section] || section}</legend>
                <div className="cfg-grid">
                  {entries.map(([k, meta]) => (
                    <div className="cfg-row" key={k}>
                      <div className="cfg-name">
                        <span className="mono tiny">{k}</span>
                        {meta.env && <span className="chip" title={`Set by ${meta.env}; it wins over this file`}>env</span>}
                        {meta.restart && <span className="chip" title="Takes effect when the API restarts">restart</span>}
                        {meta.readOnly && <span className="chip">read-only</span>}
                        {meta.help && <div className="tiny muted cfg-help">{meta.help}</div>}
                      </div>
                      <div className="cfg-input">{input(k, meta)}</div>
                    </div>
                  ))}
                </div>
                <div className="row" style={{ marginTop: 8 }}>
                  <button disabled={busy} onClick={() => save(entries)}>
                    {busy ? "Saving…" : `Save ${SECTION_LABEL[section] || section}`}
                  </button>
                </div>
              </fieldset>
            ))}
          </div>
        )}
      </Async>
      {err && <ErrorBox error={err} what="The configuration change" />}
      {result && (
        <p className="tiny" role="status" aria-live="polite"
           style={{ color: "var(--accent)", marginBottom: 0 }}>{result}</p>
      )}
    </div>
  );
}
