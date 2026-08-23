export type Severity = "low" | "medium" | "high";
export type Confidence = "provisional" | "confirmed";
export type UserStatus = "new" | "acknowledged" | "true" | "false" | "unclear";

export interface Project {
  id: number; uuid: string; name: string; description: string | null;
  aoi_geojson: any; aoi_area_km2: number; analysis_crs: string;
  recipe_id: string; recipe_version: string; params: Record<string, any>;
  adapter_preference: string[]; s1_relative_orbit: number | null;
  s1_pass_direction: string | null; schedule_cron: string;
  status: "draft" | "calibrating" | "active" | "paused" | "failed" | "deleted";
  baseline_start: string | null; baseline_end: string | null;
  baseline_artifact_id: number | null; next_poll?: string | null;
  /** Every methodology this project runs over the AOI. The fields above mirror the
   *  first one, so single-methodology callers need no change. */
  methodologies?: Methodology[];
}

export interface Methodology {
  id: number; project_id: number; recipe_id: string; recipe_version: string;
  params: Record<string, any>;
  baseline_start: string | null; baseline_end: string | null;
  baseline_artifact_id: number | null; status: string;
  created_at: string; updated_at: string;
}

export interface Observation {
  id: number; scene_id: string; platform: string; collection: string;
  adapter: string; sensed_at: string; valid_fraction: number;
  cloud_fraction: number | null; mask_summary: Record<string, number | null>;
  status: "usable" | "rejected" | "partial"; rejection_reason: string | null;
  source_uri: string; relative_orbit: number | null; pass_direction: string | null;
}

export interface RunSummary {
  score_p50: number | null; score_p90: number | null; score_p95: number | null;
  score_p99: number | null; score_max: number | null;
  /** Polarity-correct extreme, and which way the score runs. A p-value detector is
   *  worst at its minimum, so nothing should rank runs by score_max alone. */
  score_headline?: number | null; polarity?: string;
  valid_pixels: number; valid_fraction: number; changed_pixels: number;
  changed_area_m2: number; n_components: number; largest_component_m2: number;
  direction: Record<string, number> | null; aux: Record<string, any>;
  notes: string[]; threshold: number; units: string;
  mask_summary: Record<string, number | null>;
  no_alert_reason: string | null; no_alert_reason_text: string | null;
}

export interface Run {
  id: number; uuid: string; kind: string; detector_id: string;
  methodology_id: number | null;
  detector_version: string; params: Record<string, any>;
  target_observation_id: number | null; started_at: string;
  finished_at: string | null; status: "ok" | "skipped" | "failed";
  skip_reason: string | null; error?: any; score_raster_id: number | null;
  mask_raster_id: number | null; summary: RunSummary | null;
  supersedes_run_id: number | null; compute_backend: "local" | "gee";
  observations?: Observation[];
}

export interface Alert {
  id: number; uuid: string; project_id: number; run_id: number;
  methodology_id: number | null;
  incident_id: number | null; raised_at: string; sensed_at: string;
  severity: Severity; confidence: Confidence; score: number; threshold: number;
  changed_area_m2: number; changed_fraction: number; n_components: number;
  largest_component_m2: number; geometry: any; direction: Record<string, number> | null;
  explanation_text: string; explanation_llm: string | null;
  before_chip_id: number | null; after_chip_id: number | null;
  overlay_chip_id: number | null; caveats: string[];
  user_status: UserStatus; user_note: string | null;
  project_name?: string; project_uuid?: string; run?: Run; recipe?: Recipe;
  _type?: "alert";
}

export interface Incident {
  id: number; uuid: string; opened_at: string; closed_at: string | null;
  state: "open" | "cooling" | "closed"; peak_score: number;
  cumulative_area_m2: number; title: string;
}

export interface Diagnostic {
  id: number; project_id: number | null; code: string;
  severity: "info" | "warning" | "error"; message: string;
  detail: any; occurred_at: string; resolved_at: string | null;
  acknowledged: number; _type?: "diagnostic";
}

export interface Recipe {
  id: string; version: string; display_name: string; plain_question: string;
  sensor: "S1" | "S2"; secondary_sensor?: "S1" | "S2"; detector: string;
  resolution_m: number; bands: string[]; radar_bands?: string[];
  indices?: string[]; mask_chain: string[]; min_valid_fraction?: number;
  baseline?: { strategy: string; min_years: number; min_observations: number };
  defaults: Record<string, any>;
  default_provenance: Record<string, "literature" | "statistical" | "heuristic">;
  reference: { citation: string; url: string; note?: string };
  limitations: string[];
}

export interface DetectorSpec {
  id: string; version: string; display_name: string; sensor: string;
  required_bands: string[]; needs_baseline: boolean;
  min_baseline_observations: number; score_units: string;
  score_polarity: string; default_threshold: number;
  threshold_semantics: string; reference: { citation: string; url: string };
  param_schema: Record<string, any>;
}

export interface ProjectHealth {
  last_usable_observation: string | null; days_since: number | null;
  diagnostics: Record<string, number>; next_poll: string | null;
  status: string; adapter_preference: string[];
}
