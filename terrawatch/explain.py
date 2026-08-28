"""Deterministic explanation. Always present, never optional (spec §12.1)."""
from __future__ import annotations

# Stable diagnostic metadata. Call sites still supply the event-specific message, while
# this registry keeps severity and recovery advice consistent across API and UI.
DIAGNOSTICS = {
    "NO_ACQUISITION": ("info", "Wait for the next scheduled pass or verify the AOI and date range."),
    "ACQUISITION_OVERDUE": ("warning", "Check source health; if optical scenes stay obscured, add a radar monitor."),
    "LOW_VALID_FRACTION": ("warning", "Wait for a clearer acquisition or lower the validity gate only after reviewing the mask."),
    "PERSISTENT_OCCLUSION": ("warning", "Pair this optical monitor with a radar recipe that can observe through clouds."),
    "MISREGISTRATION": ("warning", "Enable automatic co-registration or inspect the source imagery before retrying."),
    "ORBIT_MISMATCH": ("error", "Select the project's original relative orbit and pass direction."),
    "BASELINE_TOO_SHORT": ("error", "Extend the baseline window until the recipe has enough usable observations."),
    "BASELINE_SPANS_CONSTELLATION_EVENT": ("warning", "Refit the baseline on one side of the constellation event when practical."),
    "ADAPTER_FALLBACK": ("warning", "No action is required; verify source health if fallback use persists."),
    "PROVENANCE_DISCONTINUITY": ("warning", "Refit the baseline with the current adapter before treating small changes as real."),
    "SATURATION": ("warning", "Inspect the source bands and use another acquisition if clipping is widespread."),
    "DETECTOR_ASSUMPTION": ("warning", "Review the detector note and adjust the recipe or baseline before relying on the result."),
    "QUOTA_LOW": ("warning", "Reduce the requested GEE workload or increase the configured quota budget."),
    "QUOTA_EXHAUSTED": ("error", "Use the local backend or wait for the GEE quota window to reset."),
    "SOURCE_UNREACHABLE": ("error", "Check network and source credentials, then retry; configured fallbacks are attempted automatically."),
    "RUN_FAILED": ("error", "Review the recorded error details, correct the cause, and retry the run."),
    "MASK_UNAVAILABLE": ("warning", "Inspect the run's mask breakdown and use a source that publishes the missing mask when needed."),
    "SLOW_BACKTEST": ("info", "Let the job finish, or shorten the date range to get results sooner."),
}

CAVEAT_SENTENCES = {
    "LOW_VALID_FRACTION": "Part of the area was obscured on the target date, so the "
                          "change footprint may be incomplete.",
    "PERSISTENT_OCCLUSION": "This site has been obscured for several consecutive "
                            "acquisitions, so this change may have begun earlier than "
                            "the date shown.",
    "MISREGISTRATION": "The target image is slightly misaligned with the baseline; "
                       "edges of features may register as false change.",
    "BASELINE_TOO_SHORT": "The baseline has fewer observations than this method needs, "
                          "so the score scale is less reliable than usual.",
    "BASELINE_SPANS_CONSTELLATION_EVENT": "The baseline spans a known change in the "
                                          "satellite constellation, which can shift "
                                          "measured values independently of the ground.",
    "PROVENANCE_DISCONTINUITY": "The baseline and this observation came from different "
                                "data sources or processing levels.",
    "ADAPTER_FALLBACK": "The primary data source was unavailable; a fallback source "
                        "served this observation.",
    "SATURATION": "A large share of pixels sit at the index's extreme value, which "
                  "compresses the measured difference.",
    "DETECTOR_ASSUMPTION": "The detection method reported an assumption it could not "
                           "fully verify.",
    "ORBIT_MISMATCH": "Radar geometry differs from the baseline; backscatter is "
                      "geometry-dependent and this comparison is unreliable.",
}

DIRECTION_READINGS = [
    (lambda d: d.get("ndvi", 0) < -0.05 and d.get("nbr", 0) < -0.05,
     "a pattern consistent with vegetation removal or burning"),
    (lambda d: d.get("ndvi", 0) < -0.05,
     "a pattern consistent with loss of green vegetation"),
    (lambda d: d.get("ndvi", 0) > 0.05,
     "a pattern consistent with vegetation growth or regrowth"),
    (lambda d: d.get("mndwi", 0) > 0.05, "a pattern consistent with water spreading"),
    (lambda d: d.get("mndwi", 0) < -0.05, "a pattern consistent with water receding"),
    (lambda d: d.get("ndbi", 0) > 0.05,
     "a pattern consistent with new hard or bare surfaces"),
]


def _ordinal_history(score: float, history: list[float]) -> str:
    n = len(history) + 1
    if not history:
        return f"This is the first scored observation of this site."
    rank = sum(1 for h in history if h >= score) + 1
    if rank == 1:
        return f"This is the highest change score in {n} observations of this site."
    pct = 100 * (1 - rank / n)
    return (f"This is the {rank}{'st' if rank % 10 == 1 and rank != 11 else 'nd' if rank % 10 == 2 and rank != 12 else 'rd' if rank % 10 == 3 and rank != 13 else 'th'}"
            f" highest of {n} observations of this site ({pct:.0f}th percentile).")


def explain(*, target_date: str, baseline_desc: str, changed_area_m2: float,
            aoi_area_m2: float, n_components: int, largest_component_m2: float,
            direction: dict, score: float, history: list[float],
            valid_fraction: float, caveats: list[str],
            recipe_name: str) -> str:
    ha = changed_area_m2 / 10_000
    pct = 100 * changed_area_m2 / aoi_area_m2 if aoi_area_m2 else 0
    parts = [
        f"Comparing {target_date} against {baseline_desc}, {ha:.1f} hectares "
        f"({pct:.1f}% of the area) changed across {n_components} "
        f"{'patch' if n_components == 1 else 'patches'}, the largest being "
        f"{largest_component_m2 / 10_000:.1f} hectares."
    ]
    if direction:
        deltas = ", ".join(f"{k.upper()} {'fell' if v < 0 else 'rose'} by {abs(v):.2f}"
                           for k, v in direction.items())
        reading = next((txt for pred, txt in DIRECTION_READINGS if pred(direction)), None)
        parts.append(f"{deltas} across the changed area" +
                     (f", {reading}." if reading else "."))
    parts.append(_ordinal_history(score, history))
    parts.append(f"{valid_fraction * 100:.0f}% of the area was usable on the target date.")
    parts += [CAVEAT_SENTENCES.get(c, f"Caveat: {c}.") for c in caveats]
    return " ".join(parts)


def no_alert_reason(reason: str) -> str:
    return {
        "below_threshold": "Scored, but no pixel crossed the alerting threshold.",
        "below_mmu": "Pixels crossed the threshold but no patch was large enough to "
                     "meet the minimum mapping unit.",
        "awaiting_confirmation": "A crossing was seen but this recipe requires repeat "
                                 "confirmation before raising an alert.",
        "gated": "Not scored: too little of the area was usable on this date.",
        "hysteresis": "Still inside an existing alerting episode; no new alert raised.",
    }.get(reason, reason)
