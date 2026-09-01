import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../api";
import { useApi } from "../useApi";
import { ErrorBox } from "../components/Async";
import { AlertCard, DiagnosticCard } from "../components/AlertCard";
import { HealthStrip } from "../components/HealthStrip";
import { Alert, Diagnostic, Project, ProjectHealth } from "../types";

type FeedItem = Alert | Diagnostic;
const EMPTY = { severity: "", user_status: "", project: "", date_from: "", date_to: "" };
const POLL_MS = 25_000;

export default function Dashboard() {
  // Filters live in the URL: a triage queue like "high severity, untriaged, this
  // month" can be bookmarked, shared, and undone with Back.
  const [sp, setSp] = useSearchParams();
  const filter = {
    severity: sp.get("severity") || "", user_status: sp.get("user_status") || "",
    project: sp.get("project") || "", date_from: sp.get("date_from") || "",
    date_to: sp.get("date_to") || "",
  };
  const setFilter = (next: typeof EMPTY) => {
    setSp((prev) => {
      const n = new URLSearchParams(prev);
      for (const k of Object.keys(EMPTY)) {
        if (next[k as keyof typeof EMPTY]) n.set(k, next[k as keyof typeof EMPTY]);
        else n.delete(k);
      }
      return n;
    });
  };

  const [actionError, setActionError] = useState<string>();

  const health = useApi<{ p: Project; h: ProjectHealth }[]>(async () => {
    const ps = (await api.projects()).items as Project[];
    return Promise.all(ps.map(async (p) => ({ p, h: await api.projectHealth(p.uuid) })));
  }, []);

  // Score units are a detector fact, not an alert fact; resolve them once so every
  // card can carry its units (§13.3).
  const unitsByRecipe = useApi<Record<string, { units: string; semantics: string }>>(
    async () => {
      const [recipes, detectors] = await Promise.all([api.recipes(), api.detectors()]);
      const map: Record<string, { units: string; semantics: string }> = {};
      for (const r of recipes as any[]) {
        const d = (detectors as any[]).find((x) => x.id === r.detector);
        if (d) map[r.id] = { units: d.score_units, semantics: d.threshold_semantics };
      }
      return map;
    }, []);

  // The feed owns its own pagination state: the cursor the API returns is kept and
  // spent by "Load more" instead of being dropped.
  const [items, setItems] = useState<FeedItem[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [feedError, setFeedError] = useState<string>();
  const [feedLoading, setFeedLoading] = useState(true);
  const [moreLoading, setMoreLoading] = useState(false);
  const seq = useRef(0);
  const paged = useRef(false);
  const q = Object.entries(filter).filter(([, v]) => v)
    .map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join("&");

  const load = useCallback(async (mode: "replace" | "append", spendCursor?: string | null) => {
    const mine = ++seq.current;
    if (mode === "replace") { setFeedLoading(true); paged.current = false; }
    else { setMoreLoading(true); paged.current = true; }
    try {
      const qs = [q, ...(mode === "append" && spendCursor ? [`cursor=${spendCursor}`] : [])]
        .filter(Boolean).join("&");
      const page = await api.feed(qs ? `?${qs}` : "");
      if (mine !== seq.current) return;
      setCursor(page.next_cursor ?? null);
      setItems((prev) => mode === "append" ? [...prev, ...page.items] : page.items);
      setFeedError(undefined);
    } catch (e: any) {
      if (mine === seq.current) setFeedError(e?.message || "Could not reach the API.");
    } finally {
      if (mine === seq.current) { setFeedLoading(false); setMoreLoading(false); }
    }
  }, [q]);

  useEffect(() => { load("replace"); }, [load]);

  // New alerts must arrive on their own; a monitoring dashboard that only moves when
  // you hit Refresh is a report, not a monitor. Polling replaces the first page only
  // while the user is still on it — once they page into history, refreshing would
  // shuffle items under their eyes.
  useEffect(() => {
    const t = setInterval(() => {
      if (document.visibilityState === "visible" && !paged.current) load("replace");
    }, POLL_MS);
    return () => clearInterval(t);
  }, [load]);

  const filtered = !!q;

  // Optimistic triage: the card updates immediately and rolls back if the API
  // refuses, so the primary loop costs one click, not one refetch of the world.
  const triage = async (a: Alert, status: string) => {
    const prev = items;
    setItems((cur) => cur.map((it) =>
      it === a ? { ...a, user_status: status as Alert["user_status"] } : it));
    try { await api.triage(a.uuid, { user_status: status }); }
    catch (e: any) { setItems(prev); setActionError(e.message || "That action failed."); }
  };
  const ackDiag = async (d: Diagnostic, fn: () => Promise<unknown>) => {
    setActionError(undefined);
    try { await fn(); setItems((cur) => cur.filter((it) => it !== d)); health.reload(); }
    catch (e: any) { setActionError(e.message || "That action failed."); }
  };

  // Every diagnostic in the feed is error-severity: filtering by anything else
  // hides them rather than silently ignoring the filter.
  const showDiags = filter.severity === "" || filter.severity === "high";
  const visible = items.filter((it) =>
    it._type !== "diagnostic" || showDiags);
  const diagCount = items.length - visible.length;
  const codeCounts = new Map<string, number>();
  for (const it of items) if (it._type === "diagnostic")
    codeCounts.set((it as Diagnostic).code, (codeCounts.get((it as Diagnostic).code) || 0) + 1);

  return (
    <>
      <h1 className="page">Dashboard</h1>
      <p className="sub">Alerts and errors across every monitor, newest first.</p>

      {health.error
        ? <ErrorBox error={health.error} what="Monitor health" onRetry={health.reload} />
        : <HealthStrip items={health.data || []} />}

      {actionError && <ErrorBox error={actionError} what="That action" />}

      <div className="row" style={{ marginBottom: 12 }}>
        <label className="tiny muted">Monitor<br />
          <select value={filter.project}
                  onChange={(e) => setFilter({ ...filter, project: e.target.value })}>
            <option value="">All projects</option>
            {(health.data || []).map(({ p }) =>
              <option key={p.uuid} value={p.uuid}>{p.name}</option>)}
          </select>
        </label>
        <label className="tiny muted">Severity<br />
          <select value={filter.severity}
                  onChange={(e) => setFilter({ ...filter, severity: e.target.value })}>
            <option value="">Any severity</option>
            <option value="high">High</option><option value="medium">Medium</option>
            <option value="low">Low</option>
          </select>
        </label>
        <label className="tiny muted">Triage status<br />
          <select value={filter.user_status}
                  onChange={(e) => setFilter({ ...filter, user_status: e.target.value })}>
            <option value="">Any status</option>
            <option value="new">New</option><option value="acknowledged">Acknowledged</option>
            <option value="true">Real change</option><option value="false">False alarm</option>
            <option value="unclear">Unclear</option>
          </select>
        </label>
        <label className="tiny muted">From<br />
          <input type="date" aria-label="From date (inclusive)" value={filter.date_from}
                 onChange={(e) => setFilter({ ...filter, date_from: e.target.value })} />
        </label>
        <label className="tiny muted">To<br />
          <input type="date" aria-label="To date (inclusive)" value={filter.date_to}
                 onChange={(e) => setFilter({ ...filter, date_to: e.target.value })} />
        </label>
        <button style={{ alignSelf: "flex-end" }}
                onClick={() => load("replace")}
                disabled={feedLoading}>Refresh</button>
        {filtered && (
          <button style={{ alignSelf: "flex-end" }} onClick={() => setFilter(EMPTY)}>
            Clear filters
          </button>
        )}
      </div>

      {feedError && <ErrorBox error={feedError} what="The feed" onRetry={() => load("replace")} />}
      {!feedLoading && !feedError && diagCount > 0 && (
        <p className="tiny muted" role="note" style={{ marginTop: 0 }}>
          {diagCount} error diagnostic{diagCount === 1 ? " is" : "s are"} hidden while
          filtering by a severity below high — diagnostics are always errors.{" "}
          <button className="link" onClick={() => setFilter({ ...filter, severity: "" })}>
            Show them
          </button>
        </p>
      )}

      {feedLoading ? (
        <p className="muted" role="status">Loading the feed…</p>
      ) : visible.length === 0 ? (
        <div className="panel muted">
          {filtered ? (
            <>
              <b>No alerts or errors match these filters.</b>
              <p className="tiny" style={{ marginBottom: 0 }}>
                Clear them to see everything the monitors have raised.
              </p>
            </>
          ) : (
            <>
              <b>Nothing to show — and that is the expected state.</b>
              <p className="tiny" style={{ marginBottom: 0 }}>
                This feed only carries threshold crossings and errors that need you.
                A monitor that is running normally and seeing no change is silent
                here. Use the cards above to confirm each one is still receiving
                imagery.
              </p>
            </>
          )}
        </div>
      ) : (
        <div className="grid">
          {visible.map((it) =>
            it._type === "diagnostic"
              ? <DiagnosticCard key={`d${it.id}`} d={it as Diagnostic}
                                codeCount={codeCounts.get((it as Diagnostic).code)}
                                onAck={() => ackDiag(it as Diagnostic,
                                  () => api.ackDiagnostic((it as Diagnostic).id))}
                                onAckCode={() => ackDiag(it as Diagnostic, () =>
                                  api.ackDiagnosticCode((it as Diagnostic).code,
                                    (it as Diagnostic).project_uuid || undefined))} />
              : <AlertCard key={`a${it.id}`} a={it as Alert}
                           units={unitsByRecipe.data?.[
                             health.data?.find(({ p }) => p.uuid === (it as Alert).project_uuid)
                               ?.p.recipe_id || ""]?.units}
                           semantics={unitsByRecipe.data?.[
                             health.data?.find(({ p }) => p.uuid === (it as Alert).project_uuid)
                               ?.p.recipe_id || ""]?.semantics}
                           onTriage={(s) => triage(it as Alert, s)} />
          )}
        </div>
      )}

      {/* Older items exist: the cursor was always returned, it was just dropped. */}
      {cursor && !feedLoading && visible.length > 0 && (
        <button style={{ marginTop: 12 }} disabled={moreLoading}
                onClick={() => load("append", cursor)}>
          {moreLoading ? "Loading…" : "Load older items"}
        </button>
      )}
    </>
  );
}
