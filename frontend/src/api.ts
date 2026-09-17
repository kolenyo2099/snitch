const BASE = "/api/v1";

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const pw = localStorage.getItem("snitch_password");
  const r = await fetch(BASE + path, {
    ...init,
    headers: {
      "content-type": "application/json",
      ...(pw ? { "x-snitch-password": pw } : {}),
      ...(init?.headers || {}),
    },
  });
  if (!r.ok) {
    const detail = (await r.json().catch(() => ({}))).detail || r.statusText;
    // A 401 is not an outage: the password is missing or wrong, and the fix is the
    // Access section of Settings — say so instead of "problem reaching the API".
    const msg = r.status === 401
      ? "Password required or incorrect — set it in Settings → Access."
      : detail;
    throw Object.assign(new Error(msg), { status: r.status });
  }
  return r.status === 204 ? (undefined as T) : r.json();
}

export const api = {
  login: (password: string) =>
    req<any>("/auth/login", { method: "POST", body: JSON.stringify({ password }) }),
  logout: () => req<any>("/auth/logout", { method: "POST" }),
  health: () => req<any>("/health"),
  healthFull: () => req<any>("/health?check_adapters=true"),
  aoiPreview: (aoi_geojson: any, signal?: AbortSignal) =>
    req<any>("/aoi/preview",
             { method: "POST", body: JSON.stringify({ aoi_geojson }), signal }),
  projects: () => req<{ items: any[] }>("/projects"),
  methodologies: (u: string) => req<{ items: any[] }>(`/projects/${u}/methodologies`),
  addMethodology: (u: string, recipe_id: string) =>
    req<any>(`/projects/${u}/methodologies`,
             { method: "POST", body: JSON.stringify({ recipe_id }) }),
  removeMethodology: (u: string, id: number) =>
    req<void>(`/projects/${u}/methodologies/${id}`, { method: "DELETE" }),
  project: (u: string) => req<any>(`/projects/${u}`),
  projectHealth: (u: string) => req<any>(`/projects/${u}/health`),
  createProject: (body: any) =>
    req<any>("/projects", { method: "POST", body: JSON.stringify(body) }),
  patchProject: (u: string, body: any) =>
    req<any>(`/projects/${u}`, { method: "PATCH", body: JSON.stringify(body) }),
  setGee: (u: string, enabled: boolean) =>
    req<any>(`/projects/${u}/gee`, { method: "POST", body: JSON.stringify({ enabled }) }),
  activate: (u: string) => req<any>(`/projects/${u}/activate`, { method: "POST" }),
  pause: (u: string) => req<any>(`/projects/${u}/pause`, { method: "POST" }),
  remove: (u: string) => req<void>(`/projects/${u}`, { method: "DELETE" }),
  jobs: (u: string) => req<{ items: any[] }>(`/projects/${u}/jobs`),
  runNow: (u: string) => req<any>(`/projects/${u}/run-now`, { method: "POST" }),
  // `m` addresses one methodology; omitted, the project's primary one is used.
  startBacktest: (u: string, years = 3, m?: number) =>
    req<any>(`/projects/${u}/backtest?years=${years}${m ? `&methodology=${m}` : ""}`,
             { method: "POST" }),
  backtest: (u: string, m?: number) =>
    req<any>(`/projects/${u}/backtest${m ? `?methodology=${m}` : ""}`),
  setThreshold: (u: string, body: any, m?: number) =>
    req<any>(`/projects/${u}/threshold${m ? `?methodology=${m}` : ""}`,
             { method: "POST", body: JSON.stringify(body) }),
  reanalyse: (u: string, params: any, m?: number) =>
    req<any>(`/projects/${u}/reanalyse${m ? `?methodology=${m}` : ""}`,
             { method: "POST", body: JSON.stringify(params) }),
  observations: (u: string) => req<{ items: any[] }>(`/projects/${u}/observations`),
  runs: (u: string, limit = 200) =>
    req<{ items: any[] }>(`/projects/${u}/runs?limit=${limit}`),
  run: (u: string) => req<any>(`/runs/${u}`),
  alerts: (u: string) => req<{ items: any[] }>(`/projects/${u}/alerts`),
  alertsPage: (u: string, cursor?: number) =>
    req<{ items: any[]; next_cursor: number | null }>(
      `/projects/${u}/alerts?limit=100${cursor ? `&cursor=${cursor}` : ""}`),
  incidentsPage: (u: string, cursor?: number) =>
    req<{ items: any[]; next_cursor: number | null }>(
      `/projects/${u}/incidents?limit=100${cursor ? `&cursor=${cursor}` : ""}`),
  alert: (u: string) => req<any>(`/alerts/${u}`),
  triage: (u: string, body: any) =>
    req<any>(`/alerts/${u}`, { method: "PATCH", body: JSON.stringify(body) }),
  triageBulk: (uuids: string[], user_status: string) =>
    req<{ ok: boolean; updated: number }>("/alerts/triage",
                                         { method: "POST",
                                           body: JSON.stringify({ uuids, user_status }) }),
  incidents: (u: string) => req<{ items: any[] }>(`/projects/${u}/incidents`),
  feed: (q = "") => req<{ items: any[]; next_cursor?: string | null }>(`/feed${q}`),
  diagnostics: (q = "") => req<{ items: any[]; total?: number }>(`/diagnostics${q}`),
  diagnosticsSummary: (project?: string) =>
    req<{ items: any[] }>(`/diagnostics/summary${project ? `?project=${project}` : ""}`),
  ackDiagnosticCode: (code: string, project?: string) =>
    req<{ acknowledged: number }>("/diagnostics/acknowledge",
                                  { method: "POST", body: JSON.stringify({ code, project }) }),
  ackDiagnostic: (id: number) =>
    req<any>(`/diagnostics/${id}/acknowledge`, { method: "POST" }),
  recipes: () => req<any[]>("/recipes"),
  detectors: () => req<any[]>("/detectors"),
  configGet: () =>
    req<{ path: string; keys: Record<string, any> }>("/config"),
  configPut: (values: Record<string, any>) =>
    req<{ applied: Record<string, string>; rejected: Record<string, string> }>(
      "/config", { method: "PUT", body: JSON.stringify({ values }) }),
  artifactOverlay: (id: number) => req<any>(`/artifacts/${id}/overlay`),
  gc: (dry = true) => req<any>(`/maintenance/gc?dry_run=${dry}`, { method: "POST" }),
};

export const artifactUrl = (id: number | null) =>
  id ? `${BASE}/artifacts/${id}/raw` : undefined;
