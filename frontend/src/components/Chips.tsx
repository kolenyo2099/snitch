import { Alert, Recipe } from "../types";

/** Caveat codes rendered as visible chips — never buried in a details pane (§9.3). */
export const CAVEAT_LABEL: Record<string, string> = {
  LOW_VALID_FRACTION: "partly obscured",
  PERSISTENT_OCCLUSION: "site obscured for weeks",
  MISREGISTRATION: "images slightly misaligned",
  BASELINE_TOO_SHORT: "short baseline",
  BASELINE_SPANS_CONSTELLATION_EVENT: "baseline spans a constellation change",
  PROVENANCE_DISCONTINUITY: "mixed data sources",
  ADAPTER_FALLBACK: "fallback data source",
  SATURATION: "index saturated",
  DETECTOR_ASSUMPTION: "method assumption unverified",
  ORBIT_MISMATCH: "radar geometry differs",
};

export const CAVEAT_HELP: Record<string, string> = {
  BASELINE_SPANS_CONSTELLATION_EVENT:
    "The baseline period covers a known Sentinel constellation change, which can shift measured values independently of the ground.",
  LOW_VALID_FRACTION: "Part of the area was masked on this date, so the footprint may be incomplete.",
  MISREGISTRATION: "Feature edges can register as false change when frames are misaligned.",
  DETECTOR_ASSUMPTION: "The method reported an assumption it could not fully verify.",
};

export function CaveatChips({ codes }: { codes: string[] }) {
  if (!codes?.length) return null;
  return (
    <span className="row" style={{ gap: 6 }}>
      {codes.map((c) => (
        <span key={c} className="chip caveat" title={CAVEAT_HELP[c] || c}>
          ⚠ {CAVEAT_LABEL[c] || c}
        </span>
      ))}
    </span>
  );
}

export function SeverityChip({ a }: { a: Alert }) {
  return (
    <>
      <span className={`chip sev-${a.severity}`}>{a.severity}</span>
      <span className={`chip ${a.confidence === "provisional" ? "provisional" : ""}`}
            title={a.confidence === "provisional"
              ? "Seen once. This recipe requires repeat observation before confirming."
              : "Confirmed by the required number of consecutive observations."}>
        {a.confidence}
      </span>
    </>
  );
}

/** Every recipe name links to its reference (§13.3). */
export function RecipeLink({ recipe }: { recipe?: Recipe }) {
  if (!recipe) return null;
  return (
    <a className="chip" href={recipe.reference.url} target="_blank" rel="noreferrer"
       title={recipe.reference.citation}>
      {recipe.display_name} ↗
    </a>
  );
}

export function ScoreValue({ value, units, semantics, threshold }:
  { value: number | null; units?: string; semantics?: string; threshold?: number }) {
  if (value == null) return <span className="muted">—</span>;
  return (
    <span title={semantics || ""} className="mono">
      {value.toFixed(2)} {units || ""}
      {threshold != null && <span className="muted"> / thr {threshold}</span>}
    </span>
  );
}
