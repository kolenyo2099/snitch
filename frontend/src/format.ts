/** Turning internals into English. Everything here was previously rendered raw. */

const CRON_DOW = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
                  "Saturday"];

/**
 * A cron expression means nothing to most people. Handles the shapes the wizard
 * emits and falls back to the raw expression rather than guessing at an exotic one.
 */
export function describeCron(cron?: string | null): string | null {
  if (!cron) return null;
  const parts = cron.trim().split(/\s+/);
  if (parts.length < 5) return cron;
  const [min, hour, dom, mon, dow] = parts;
  const at = /^\d+$/.test(hour) && /^\d+$/.test(min)
    ? ` at ${hour.padStart(2, "0")}:${min.padStart(2, "0")} UTC` : "";
  if (mon !== "*") return cron;
  if (dom === "*" && dow === "*") return `Every day${at}`;
  const every = dom.match(/^\*\/(\d+)$/);
  if (every && dow === "*") return `Every ${every[1]} days${at}`;
  if (dom === "*" && /^\d$/.test(dow)) return `Every ${CRON_DOW[Number(dow)]}${at}`;
  return cron;
}

/**
 * Two full ISO timestamps side by side are a duration wearing a disguise. Show the
 * duration; keep the exact instants in the tooltip.
 */
export function describeDuration(start?: string | null, end?: string | null) {
  if (!start) return { text: "—", title: "" };
  const title = end ? `${start} to ${end}` : start;
  if (!end) return { text: "still running", title };
  const ms = Date.parse(end) - Date.parse(start);
  if (!Number.isFinite(ms) || ms < 0) return { text: "—", title };
  const s = ms / 1000;
  const dur = s < 1 ? `${ms} ms`
    : s < 90 ? `${s.toFixed(s < 10 ? 1 : 0)} s`
    : s < 5400 ? `${(s / 60).toFixed(1)} min`
    : `${(s / 3600).toFixed(1)} h`;
  return { text: `${dur} · ${new Date(start).toLocaleString()}`, title };
}

const BAND_LABEL: Record<string, string> = {
  ndbi: "NDBI (built-up index)",
  ndvi: "NDVI (vegetation index)",
  ndwi: "NDWI (water index)",
  nbr: "NBR (burn ratio)",
  brightness: "Brightness",
  radar_vv: "Radar VV backscatter",
  radar_vh: "Radar VH backscatter",
};

/** A detector reports which way each input moved. It was being dumped as JSON. */
export function describeDirection(d: any): { label: string; value: string }[] {
  if (d == null) return [];
  if (typeof d !== "object") return [{ label: "", value: String(d) }];
  return Object.entries(d).map(([k, v]) => ({
    label: BAND_LABEL[k] || k,
    value: typeof v === "number" ? (v > 0 ? `+${v.toFixed(3)}` : v.toFixed(3)) : String(v),
  }));
}

/**
 * Parameter names as shipped are detector jargon. Label and explain the ones a user
 * can act on; anything unlisted still renders, under its own name.
 */
export const PARAM_META: Record<string, { label: string; unit?: string; help: string }> = {
  threshold: { label: "Alert threshold", help:
    "A scene scoring at or beyond this raises an alert. Tune it on the Methods tab." },
  optical_z_threshold: { label: "Optical threshold", unit: "z", help:
    "How many standard deviations from this site's optical baseline count as change." },
  radar_z_threshold: { label: "Radar threshold", unit: "z", help:
    "How many standard deviations from this site's radar baseline count as change." },
  radar_alpha: { label: "Radar false-positive rate", help:
    "Probability of calling an unchanged pixel changed. 0.01 means one in a hundred." },
  enl: { label: "Equivalent number of looks", help:
    "Radar speckle averaging in the source product. Sets how noisy a single pixel is." },
  min_mapping_unit_m2: { label: "Smallest patch reported", unit: "m2", help:
    "Changed patches below this area are discarded as noise." },
  consecutive_confirmations: { label: "Observations before confirming", help:
    "How many consecutive crossings are required before an alert stops being provisional." },
  exit_ratio: { label: "Incident close ratio", help:
    "An open incident closes once the score falls to this fraction of the threshold." },
  cloudscore_threshold: { label: "Cloud Score+ cutoff", help:
    "Pixels scoring below this on Cloud Score+ are masked as cloud." },
  s2cloudless_threshold: { label: "s2cloudless cutoff", help:
    "Cloud probability above this is masked when Cloud Score+ is unavailable." },
  severity_breaks: { label: "Fire severity boundaries", help:
    "Scaled-dNBR class boundaries." },
  allow_season_mismatch: { label: "Allow all-season baseline", help:
    "Off by default so ordinary seasonal cycles are less likely to look like change." },
  gee_enabled: { label: "Use Google Earth Engine", help:
    "Optional accelerator for baseline fitting and backtests." },
};

export function describeParam(key: string, value: any) {
  const meta = PARAM_META[key];
  const label = meta?.label
    || key.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
  let text: string;
  if (value == null) text = "—";
  else if (typeof value === "boolean") text = value ? "on" : "off";
  else if (Array.isArray(value)) text = value.join(", ");
  else if (typeof value === "object") text = "";
  else text = String(value);
  return { label, text, unit: meta?.unit, help: meta?.help, raw: value };
}

/** Threshold provenance was rendered as escaped JSON in a table cell. */
export function describeProvenance(p: any): string | null {
  if (!p || typeof p !== "object") return null;
  const when = p.at ? new Date(p.at).toLocaleDateString() : null;
  const how = p.source === "default"
    ? `the recipe's ${p.default_kind || "built-in"} default`
    : p.source === "backtest"
      ? "your calibration against this site's history"
      : String(p.source);
  return `Set from ${how}${when ? ` on ${when}` : ""}.`;
}

export const pct = (n: number) => `${(n * 100).toFixed(1)}%`;
export const ha = (m2: number) => (m2 / 10000).toFixed(2);
