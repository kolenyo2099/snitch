"""Detector contract and local detector implementations. Pure array code, no I/O."""
from __future__ import annotations
import warnings
from contextlib import contextmanager
from dataclasses import dataclass, field

import numpy as np

YEAR = 365.25


@dataclass(frozen=True)
class DetectorSpec:
    id: str
    version: str
    display_name: str
    sensor: str
    required_bands: list[str]
    needs_baseline: bool
    min_baseline_observations: int
    score_units: str
    score_polarity: str
    default_threshold: float
    threshold_semantics: str
    reference: dict
    param_schema: dict = field(default_factory=dict)
    kernel_radius_px: int = 0


@dataclass
class DetectorResult:
    score: np.ndarray
    valid: np.ndarray
    direction: dict | None
    aux: dict
    notes: list[str]


def index(data: dict, a: str, b: str) -> np.ndarray:
    """Normalised difference (a-b)/(a+b) with NaN propagation."""
    x, y = data[a], data[b]
    with np.errstate(invalid="ignore", divide="ignore"):
        return (x - y) / (x + y)


def _design(days: np.ndarray, harmonics: int) -> np.ndarray:
    cols = [np.ones_like(days), days / YEAR]
    for k in range(1, harmonics + 1):
        w = 2 * np.pi * k * days / YEAR
        cols += [np.cos(w), np.sin(w)]
    return np.stack(cols, axis=1)


class HarmonicResidual:
    """CCDC-style: fit a harmonic model per pixel per index over the baseline,
    score new observations as residual / robust sigma (Zhu & Woodcock 2014)."""

    spec = DetectorSpec(
        id="harmonic_residual", version="1.0",
        display_name="Harmonic residual (CCDC-style)", sensor="S2",
        required_bands=["B04", "B08", "B11", "B12"],
        needs_baseline=True, min_baseline_observations=30,
        score_units="z", score_polarity="higher_is_more_change",
        default_threshold=3.0,
        threshold_semantics=("Robust standard deviations of the seasonal model "
                             "residual. 3.0 means the observation is 3 MAD-sigmas "
                             "away from what this pixel normally does on this date."),
        reference={"citation": "Zhu, Z. & Woodcock, C.E. (2014), RSE 144, 152-171.",
                   "url": "https://doi.org/10.1016/j.rse.2014.01.011"},
        param_schema={"harmonics": {"type": "integer", "default": 2},
                      "threshold": {"type": "number", "default": 3.0}},
    )

    INDICES = {"NDVI": ("B08", "B04"), "NBR": ("B08", "B12")}

    def fit_baseline(self, stack: list[tuple[float, dict]], params: dict) -> dict:
        """stack: list of (day_number, {band: 2D array}) sorted by date."""
        harmonics = params.get("harmonics", 2)
        days = np.array([d for d, _ in stack], dtype="float64")
        X = _design(days, harmonics)
        out = {"harmonics": harmonics, "n_obs": len(stack), "day0": days[0],
               "coef": {}, "sigma": {}}
        for name, (a, b) in self.INDICES.items():
            Y = np.stack([index(d, a, b) for _, d in stack])       # (t, y, x)
            t, ny, nx = Y.shape
            Yf = Y.reshape(t, -1)
            good = np.isfinite(Yf)
            Yz = np.where(good, Yf, 0.0)
            # least squares per pixel, ignoring NaNs via weights
            coef = np.empty((X.shape[1], Yf.shape[1]), dtype="float32")
            for j in range(Yf.shape[1]):
                g = good[:, j]
                if g.sum() < X.shape[1] + 2:
                    coef[:, j] = np.nan
                    continue
                coef[:, j] = np.linalg.lstsq(X[g], Yz[g, j], rcond=None)[0]
            resid = Yf - X @ coef
            resid[~good] = np.nan
            mad = np.nanmedian(np.abs(resid - np.nanmedian(resid, 0)), 0)
            sigma = np.where(np.isfinite(mad) & (mad > 0), 1.4826 * mad, np.nan)
            out["coef"][name] = coef.reshape((X.shape[1], ny, nx))
            out["sigma"][name] = sigma.reshape(ny, nx).astype("float32")
        return out

    def alignment_pair(self, target: dict, baseline: dict):
        """Comparable rasters for the stage-6 phase-correlation check: the model's
        mean-level plane and the observed index it predicts."""
        name = "NDVI"
        a, b = self.INDICES[name]
        return baseline["coef"][name][0], index(target, a, b)

    def score(self, day: float, target: dict, baseline: dict,
              params: dict) -> DetectorResult:
        notes: list[str] = []
        if baseline["n_obs"] < self.spec.min_baseline_observations:
            notes.append(f"BASELINE_TOO_SHORT: fitted on {baseline['n_obs']} "
                         f"observations, {self.spec.min_baseline_observations} "
                         "recommended; residual scale is unreliable.")
        X = _design(np.array([day]), baseline["harmonics"])[0]
        zs, direction, residuals = [], {}, {}
        for name, (a, b) in self.INDICES.items():
            obs = index(target, a, b)
            coef, sigma = baseline["coef"][name], baseline["sigma"][name]
            pred = np.tensordot(X, coef, axes=(0, 0))
            resid = obs - pred
            with np.errstate(invalid="ignore", divide="ignore"):
                zs.append(resid / sigma)
            residuals[name.lower()] = resid
            direction[name.lower()] = float(np.nanmedian(resid))
        z = np.stack(zs)
        # change-consistent direction only: a *drop* in NDVI/NBR
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            score = np.nanmax(-z, axis=0)
        valid = np.isfinite(score)
        score = np.where(valid, score, np.nan).astype("float32")
        if valid.sum() and np.nanmean(np.abs(np.stack(zs))[np.isfinite(np.stack(zs))]) > 10:
            notes.append("Residuals are unusually large everywhere; suspect an "
                         "unmasked cloud or a radiometric mismatch.")
        return DetectorResult(score, valid, direction,
                              {"baseline_n_obs": baseline["n_obs"],
                               "_residuals": residuals}, notes)


# --- radar detectors -------------------------------------------------------
#
# Both take **linear gamma0 power** as loaded by the RTC adapter. Neither speckle
# filters: the ratio detector gains little from it once an MMU is applied, and the
# Wishart test is invalid on filtered data because filtering destroys the very
# per-pixel statistics the likelihood ratio is built on (spec §8, R7 limitations).


@contextmanager
def _quiet():
    """A pixel that is NaN on every date is normal (RTC layover, AOI corners); numpy's
    all-NaN-slice warning for it is noise in the worker log, not information."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        yield


def _pols(params: dict, default: str) -> tuple[str, ...]:
    p = params.get("polarisation", default)
    return tuple(x.strip().upper() for x in (p if isinstance(p, list) else [p]))


def _median_baseline(stack, pols) -> dict:
    """Per-pixel temporal median of linear power. Median, not mean, because a single
    flooded or wind-roughened date in the reference window must not move the
    reference it is later compared against."""
    out = {"harmonics": 0, "n_obs": len(stack), "day0": stack[0][0],
           "coef": {}, "sigma": {}}
    for pol in pols:
        Y = np.stack([d[pol] for _, d in stack])
        with _quiet():
            out["coef"][pol] = np.nanmedian(Y, axis=0)[None].astype("float32")
            out["sigma"][pol] = np.nanstd(Y, axis=0).astype("float32")
    return out


def _db(x):
    with np.errstate(invalid="ignore", divide="ignore"):
        return 10.0 * np.log10(np.where(np.asarray(x) > 0, x, np.nan))


def _otsu(values: np.ndarray, bins: int = 256) -> float | None:
    """Otsu (1979) on a finite 1-D sample. Returns the threshold value."""
    v = values[np.isfinite(values)]
    if v.size < 64:
        return None
    hist, edges = np.histogram(v, bins=bins)
    w = hist.cumsum()
    centres = (edges[:-1] + edges[1:]) / 2
    m = (hist * centres).cumsum()
    total_w, total_m = w[-1], m[-1]
    w1 = total_w - w
    ok = (w > 0) & (w1 > 0)
    if not ok.any():
        return None
    with np.errstate(invalid="ignore", divide="ignore"):
        between = (total_m * w - m * total_w) ** 2 / (w * w1 * total_w ** 2)
    between = np.where(ok, between, -np.inf)
    return float(centres[int(np.argmax(between))])


def _otsu_bimodal(values: np.ndarray) -> float | None:
    """Return an Otsu cut only when it separates two materially distinct groups."""
    t = _otsu(values)
    v = values[np.isfinite(values)]
    if t is None:
        return None
    left, right = v[v <= t], v[v > t]
    if min(left.size, right.size) < max(16, int(0.02 * v.size)):
        return None
    separation = float(right.mean() - left.mean())
    within = float(np.sqrt((left.var() + right.var()) / 2.0))
    return t if separation > max(3.0 * within, 0.05) else None


def _robust_baseline(stack, variables: dict[str, callable]) -> dict:
    """Store a per-pixel median and MAD scale for each derived variable."""
    out = {"harmonics": 0, "n_obs": len(stack), "day0": stack[0][0],
           "coef": {}, "sigma": {}}
    for name, fn in variables.items():
        Y = np.stack([fn(d) for _, d in stack])
        with _quiet():
            med = np.nanmedian(Y, axis=0)
            mad = np.nanmedian(np.abs(Y - med), axis=0)
        out["coef"][name] = med[None].astype("float32")
        out["sigma"][name] = np.where(mad > 1e-6, 1.4826 * mad, np.nan).astype(
            "float32")
    return out


def _seasonal_baseline(stack, variables: dict[str, callable]) -> dict:
    """Twelve monthly robust medians plus an all-season fallback plane."""
    out = {"harmonics": 0, "n_obs": len(stack), "day0": stack[0][0],
           "coef": {}, "sigma": {}}
    month_ids = np.array([int((day % YEAR) // (YEAR / 12)) for day, _ in stack])
    shape = next(v.shape for k, v in stack[0][1].items() if not k.startswith("_"))
    counts = np.bincount(month_ids, minlength=12).astype("float32")
    out["coef"]["_SEASON_COUNT"] = np.stack(
        [np.full(shape, n, "float32") for n in counts])
    out["sigma"]["_SEASON_COUNT"] = np.zeros(shape, "float32")
    for name, fn in variables.items():
        Y = np.stack([fn(data) for _, data in stack])
        with _quiet():
            global_med = np.nanmedian(Y, axis=0)
            mad = np.nanmedian(np.abs(Y - global_med), axis=0)
        planes = []
        for month in range(12):
            selected = Y[month_ids == month]
            with _quiet():
                planes.append(np.nanmedian(selected, axis=0)
                              if len(selected) else global_med)
        planes.extend([global_med, Y[-1]])  # all-season fallback, then latest clear
        out["coef"][name] = np.stack(planes).astype("float32")
        out["sigma"][name] = np.where(mad > 1e-6, 1.4826 * mad, np.nan).astype(
            "float32")
    return out


def _season_plane(baseline: dict, day: float, allow_mismatch: bool = False):
    month = int((day % YEAR) // (YEAR / 12))
    counts = baseline["coef"].get("_SEASON_COUNT")
    available = counts is not None and float(counts[month, 0, 0]) > 0
    return (12 if allow_mismatch or not available else month), month, available


def _gaussian_pdf(x, mean, sigma):
    sigma = np.maximum(sigma, 0.15)
    with np.errstate(invalid="ignore", divide="ignore", over="ignore"):
        return np.exp(-0.5 * ((x - mean) / sigma) ** 2) / sigma


class RaddProbabilistic:
    """RADD-style forest disturbance probability from dual-pol radar backscatter."""

    spec = DetectorSpec(
        id="radd_probabilistic", version="1.0",
        display_name="All-weather forest disturbance probability", sensor="S1",
        required_bands=["VV", "VH"], needs_baseline=True,
        min_baseline_observations=12, score_units="probability",
        score_polarity="higher_is_more_change", default_threshold=0.75,
        threshold_semantics=("Posterior probability that backscatter belongs to the "
                             "lower-return disturbance component after stable-forest "
                             "median normalisation."),
        reference={"citation": "Reiche et al. (2021), ERL 16, 024005.",
                   "url": "https://doi.org/10.1088/1748-9326/abd0a8"},
        param_schema={"flag_probability": {"type": "number", "default": 0.75},
                      "confirmation_probability": {"type": "number", "default": 0.975},
                      "disturbance_prior": {"type": "number", "default": 0.05},
                      "threshold": {"type": "number", "default": 0.75}},
    )
    POLS = ("VV", "VH")

    def fit_baseline(self, stack, params):
        out = {"harmonics": 0, "n_obs": len(stack), "day0": stack[0][0],
               "coef": {}, "sigma": {}}
        for pol in self.POLS:
            Y = np.stack([_db(d[pol]) for _, d in stack])
            with _quiet():
                q25, q75 = np.nanpercentile(Y, [25, 75], axis=0)
                spread = np.nanstd(Y, axis=0)
            low, high = q25, q75
            s_low = s_high = np.maximum(spread * 0.75, 0.5)
            for _ in range(20):
                p0 = _gaussian_pdf(Y, low, s_low)
                p1 = _gaussian_pdf(Y, high, s_high)
                den = p0 + p1 + 1e-12
                r0, r1 = p0 / den, p1 / den
                w0, w1 = np.nansum(r0, 0), np.nansum(r1, 0)
                low = np.nansum(r0 * Y, 0) / np.maximum(w0, 1e-6)
                high = np.nansum(r1 * Y, 0) / np.maximum(w1, 1e-6)
                s_low = np.sqrt(np.nansum(r0 * (Y - low) ** 2, 0) /
                                np.maximum(w0, 1e-6))
                s_high = np.sqrt(np.nansum(r1 * (Y - high) ** 2, 0) /
                                 np.maximum(w1, 1e-6))
            swap = low > high
            lo, hi = np.where(swap, high, low), np.where(swap, low, high)
            slo = np.where(swap, s_high, s_low)
            shi = np.where(swap, s_low, s_high)
            # Component scales travel as coefficient planes because the baseline COG
            # contract reserves `sigma` for one 2-D plane per variable.
            out["coef"][pol] = np.stack([lo, hi, slo, shi]).astype("float32")
            out["sigma"][pol] = np.maximum(spread, 0.15).astype("float32")
        return out

    def alignment_pair(self, target, baseline):
        return baseline["coef"]["VH"][1], _db(target["VH"])

    def score(self, day, target, baseline, params):
        # A monitored forest starts with a conservative disturbance prior; both
        # polarisations then update those odds from the fitted class likelihoods.
        previous = params.get("_previous_probability")
        if previous is None:
            prior = float(params.get("disturbance_prior", 0.05))
            prior = float(np.clip(prior, 1e-6, 1 - 1e-6))
            log_odds = np.full_like(target["VH"], np.log(prior / (1 - prior)),
                                    dtype="float64")
        else:
            previous = np.clip(np.asarray(previous, dtype="float64"), 1e-6, 1 - 1e-6)
            if previous.shape != target["VH"].shape:
                raise ValueError("previous RADD probability grid does not match target")
            log_odds = np.log(previous / (1 - previous))
        offsets = {}
        valid = np.ones_like(target["VH"], dtype=bool)
        for pol in self.POLS:
            x = _db(target[pol])
            forest = baseline["coef"][pol][1]
            finite = np.isfinite(x) & np.isfinite(forest)
            offset = float(np.nanmedian(x[finite] - forest[finite])) if finite.any() else 0.0
            offset = float(np.clip(offset, -1.5, 1.5))
            x = x - offset
            low, high, slo, shi = baseline["coef"][pol]
            # A shared scale keeps the posterior monotonic below the component
            # midpoint. Separate tiny EM variances can otherwise call an extreme
            # backscatter collapse "forest" merely because both densities underflow.
            shared = np.maximum((slo + shi) / 2.0, 0.35)
            log_low = -0.5 * ((x - low) / shared) ** 2
            log_high = -0.5 * ((x - high) / shared) ** 2
            log_odds += log_low - log_high
            valid &= np.isfinite(x) & np.isfinite(log_odds)
            offsets[f"{pol.lower()}_normalisation_db"] = offset
        clipped = np.clip(log_odds, -80, 80)
        probability = 1.0 / (1.0 + np.exp(-clipped))
        probability = np.where(valid, probability, np.nan).astype("float32")
        notes = []
        if baseline["n_obs"] < self.spec.min_baseline_observations:
            notes.append("BASELINE_TOO_SHORT: radar mixture components are unstable.")
        return DetectorResult(probability, valid, {"backscatter": "decrease"},
                              {**offsets, "baseline_n_obs": baseline["n_obs"],
                               "confirmation_probability": float(params.get(
                                   "confirmation_probability", 0.975)),
                               "previous_probability_used": previous is not None}, notes)


class DNBR:
    """Key & Benson scaled differenced Normalized Burn Ratio."""

    spec = DetectorSpec(
        id="dnbr", version="1.0", display_name="Burn extent and dNBR severity",
        sensor="S2", required_bands=["B8A", "B12"], needs_baseline=True,
        min_baseline_observations=6, score_units="scaled_dNBR",
        score_polarity="higher_is_more_change", default_threshold=100.0,
        threshold_semantics=("Pre-fire minus post-fire NBR, multiplied by 1000. "
                             "100 is the published low-severity boundary."),
        reference={"citation": "Key & Benson (2006), FIREMON RMRS-GTR-164-CD.",
                   "url": "https://www.fs.usda.gov/research/treesearch/24066"},
        param_schema={"threshold": {"type": "number", "default": 100.0}},
    )
    CLASSES = [(-np.inf, -250, "enhanced_regrowth_high"),
               (-250, -100, "enhanced_regrowth_low"),
               (-100, 100, "unburned"), (100, 270, "low"),
               (270, 440, "moderate_low"), (440, 660, "moderate_high"),
               (660, np.inf, "high")]

    @staticmethod
    def _nbr(data):
        return index(data, "B8A", "B12")

    def fit_baseline(self, stack, params):
        return _seasonal_baseline(stack, {"NBR": self._nbr})

    def alignment_pair(self, target, baseline):
        return baseline["coef"]["NBR"][12], self._nbr(target)

    def score(self, day, target, baseline, params):
        plane, month, available = _season_plane(baseline, day)
        if not available:
            plane = 13
        dnbr = 1000.0 * (baseline["coef"]["NBR"][plane] - self._nbr(target))
        valid = np.isfinite(dnbr)
        breaks = params.get("severity_breaks", [-250, -100, 100, 270, 440, 660])
        if len(breaks) != 6 or list(breaks) != sorted(breaks):
            raise ValueError("severity_breaks must contain six increasing numbers")
        names = [x[2] for x in self.CLASSES]
        edges = [-np.inf, *[float(x) for x in breaks], np.inf]
        counts = {name: int(((dnbr >= lo) & (dnbr < hi) & valid).sum())
                  for lo, hi, name in zip(edges[:-1], edges[1:], names)}
        notes = ([] if available else [
            f"No baseline observation exists for seasonal month {month + 1}; "
            "the most recent clear baseline observation was used instead."])
        return DetectorResult(np.where(valid, dnbr, np.nan).astype("float32"), valid,
                              {"nbr": float(np.nanmedian(-dnbr / 1000.0))
                               if valid.any() else 0.0},
                              {"severity_pixel_counts": counts,
                               "severity_breaks": [float(x) for x in breaks],
                               "reference_season_month": month + 1,
                               "seasonal_reference_available": available,
                               "baseline_n_obs": baseline["n_obs"]}, notes)


class MNDWIExtent:
    """Season-matched optical water classification and extent change."""

    spec = DetectorSpec(
        id="mndwi_extent", version="1.0", display_name="Seasonal water extent change",
        sensor="S2", required_bands=["B03", "B08", "B11"], needs_baseline=True,
        min_baseline_observations=12, score_units="fraction",
        score_polarity="higher_is_more_change", default_threshold=0.10,
        threshold_semantics=("Absolute water-area change divided by the seasonal "
                             "baseline water area. 0.10 means a ten-percent change."),
        reference={"citation": "Xu (2006), IJRS 27(14), 3025-3033.",
                   "url": "https://doi.org/10.1080/01431160600589179"},
        param_schema={"threshold": {"type": "number", "default": 0.10},
                      "fixed_mndwi_threshold": {"type": "number", "default": 0.0}},
    )

    @staticmethod
    def _mndwi(data):
        return index(data, "B03", "B11")

    def fit_baseline(self, stack, params):
        waters = []
        for day, data in stack:
            m = self._mndwi(data)
            t = _otsu_bimodal(m)
            waters.append((int((day % YEAR) // (YEAR / 12)), m > (0.0 if t is None else t)))
        shape = waters[0][1].shape
        all_water = np.stack([w for _, w in waters])
        global_extent = np.nanmedian(all_water.astype("float32"), 0)
        months = []
        for month in range(12):
            selected = [w for m, w in waters if m == month]
            months.append(np.nanmedian(np.stack(selected).astype("float32"), 0)
                          if selected else global_extent)
        return {"harmonics": 0, "n_obs": len(stack), "day0": stack[0][0],
                "coef": {"WATER": np.stack(months).astype("float32")},
                "sigma": {"WATER": np.zeros(shape, "float32")}}

    def alignment_pair(self, target, baseline):
        return baseline["coef"]["WATER"][0], self._mndwi(target)

    def score(self, day, target, baseline, params):
        mndwi = self._mndwi(target)
        derived = _otsu_bimodal(mndwi)
        notes = []
        if derived is None:
            derived = float(params.get("fixed_mndwi_threshold", 0.0))
            notes.append("MNDWI histogram was not separable; fixed 0.0 threshold used.")
        target_water = mndwi > derived
        month = int((day % YEAR) // (YEAR / 12))
        baseline_water = baseline["coef"]["WATER"][month] >= 0.5
        valid = np.isfinite(mndwi)
        b_area = max(int((baseline_water & valid).sum()), 1)
        signed = (int((target_water & valid).sum()) - b_area) / b_area
        changed = (target_water != baseline_water) & valid
        score = np.where(changed, abs(signed), 0.0).astype("float32")
        return DetectorResult(score, valid,
                              {"water_extent": "increase" if signed > 0 else "decrease",
                               "fraction": float(signed)},
                              {"mndwi_threshold": float(derived),
                               "season_month": month + 1,
                               "baseline_water_pixels": b_area,
                               "target_water_pixels": int((target_water & valid).sum())},
                              notes)


class BuiltupDual:
    """Optical and radar built-up evidence fused on the shared analysis grid."""

    spec = DetectorSpec(
        id="builtup_dual", version="1.0", display_name="Built-up expansion evidence",
        sensor="S2", required_bands=["B02", "B03", "B04", "B08", "B11", "B12"],
        needs_baseline=True, min_baseline_observations=12, score_units="z",
        score_polarity="higher_is_more_change", default_threshold=2.5,
        threshold_semantics=("The weaker of the NDBI and visible-brightness robust "
                             "z-scores; both optical signals must rise."),
        reference={"citation": "Zha, Gao & Ni (2003), IJRS 24(3), 583-594.",
                   "url": "https://doi.org/10.1080/01431160304987"},
        param_schema={"threshold": {"type": "number", "default": 2.5},
                      "optical_z_threshold": {"type": "number", "default": 2.5},
                      "radar_z_threshold": {"type": "number", "default": 2.5},
                      "radar_alpha": {"type": "number", "default": 0.01},
                      "enl": {"type": "number", "default": 4.4}},
    )

    @staticmethod
    def _ndbi(data):
        return index(data, "B11", "B08")

    @staticmethod
    def _brightness(data):
        return np.nanmean(np.stack([data[b] for b in ("B02", "B03", "B04")]), 0)

    def fit_baseline(self, stack, params):
        return _seasonal_baseline(stack, {"NDBI": self._ndbi,
                                          "BRIGHTNESS": self._brightness})

    def fit_radar_baseline(self, stack):
        wishart = WishartOmnibus().fit_baseline(stack, {})
        return {"harmonics": 0, "n_obs": wishart["n_obs"],
                "day0": wishart["day0"],
                "coef": {f"RADAR_{p}": wishart["coef"][p]
                         for p in WishartOmnibus.POLS},
                "sigma": {f"RADAR_{p}": wishart["sigma"][p]
                          for p in WishartOmnibus.POLS}}

    def alignment_pair(self, target, baseline):
        return baseline["coef"]["NDBI"][12], self._ndbi(target)

    def score(self, day, target, baseline, params):
        plane, month, season_available = _season_plane(baseline, day)
        values = {"ndbi": self._ndbi(target), "brightness": self._brightness(target)}
        zs = {}
        for key, value in values.items():
            name = key.upper()
            with np.errstate(invalid="ignore", divide="ignore"):
                zs[key] = (value - baseline["coef"][name][plane]) / baseline["sigma"][name]
        optical = np.minimum(zs["ndbi"], zs["brightness"])
        radar = None
        if all(p in target and f"RADAR_{p}" in baseline["coef"]
               for p in WishartOmnibus.POLS):
            from scipy.stats import norm
            radar_n = int(baseline["coef"].get(
                "_RADAR_COUNT", np.zeros((1, 1, 1)))[0, 0, 0])
            radar_base = {
                "n_obs": radar_n, "harmonics": 0, "day0": baseline["day0"],
                "coef": {p: baseline["coef"][f"RADAR_{p}"]
                         for p in WishartOmnibus.POLS},
                "sigma": {p: baseline["sigma"][f"RADAR_{p}"]
                          for p in WishartOmnibus.POLS},
            }
            wishart = WishartOmnibus().score(
                day, {p: target[p] for p in WishartOmnibus.POLS}, radar_base,
                {"enl": params.get("enl", 4.4),
                 "threshold": params.get("radar_alpha", 0.01)})
            increase = wishart.aux["_loewner"]["increase"]
            with np.errstate(invalid="ignore"):
                significance_z = norm.isf(np.clip(wishart.score, 1e-12, 1 - 1e-12))
            radar = np.where(increase, significance_z, 0.0)
            zs["radar_vv"] = radar
        score = np.fmax(optical, radar) if radar is not None else optical
        valid = np.isfinite(score)
        agreement = ((optical >= float(params.get("optical_z_threshold", 2.5))) &
                     (radar >= float(params.get("radar_z_threshold", 2.5)))
                     if radar is not None else np.zeros_like(valid))
        notes = ([] if radar is not None else [
            "No same-orbit radar observation was available near this optical date; "
            "confirmation therefore requires three optical observations."])
        return DetectorResult(np.where(valid, score, np.nan).astype("float32"), valid,
                              {k: float(np.nanmedian(v)) for k, v in zs.items()
                               if np.isfinite(v).any()},
                              {"streams": (["optical", "radar"] if radar is not None
                                           else ["optical"]),
                               "_cross_stream_agreement": agreement,
                               "radar_baseline_n_obs": (int(baseline["coef"]
                                   ["_RADAR_COUNT"][0, 0, 0])
                                   if "_RADAR_COUNT" in baseline["coef"] else 0),
                               "radar_method": ("wishart_increase_significance"
                                                if radar is not None else None),
                               "reference_season_month": month + 1,
                               "seasonal_reference_available": season_available,
                               "baseline_n_obs": baseline["n_obs"]}, notes)


class IrmadCva:
    """Regularised IR-MAD with a CVA direction summary."""

    BANDS = ("B02", "B03", "B04", "B08", "B11", "B12")
    spec = DetectorSpec(
        id="irmad_cva", version="1.0", display_name="Unsupervised bitemporal change",
        sensor="S2", required_bands=list(BANDS), needs_baseline=True,
        min_baseline_observations=2, score_units="p_value",
        score_polarity="lower_is_more_change", default_threshold=0.05,
        threshold_semantics=("Chi-squared probability that the standardised MAD "
                             "variates are consistent with no change."),
        reference={"citation": "Nielsen (2007), IEEE TIP 16(2), 463-478.",
                   "url": "https://doi.org/10.1109/TIP.2006.888195"},
        param_schema={"threshold": {"type": "number", "default": 0.05},
                      "max_iterations": {"type": "integer", "default": 50},
                      "convergence": {"type": "number", "default": 0.001}},
    )

    def fit_baseline(self, stack, params):
        return _seasonal_baseline(stack, {b: (lambda d, band=b: d[band])
                                          for b in self.BANDS})

    def alignment_pair(self, target, baseline):
        return baseline["coef"]["B04"][12], target["B04"]

    def score(self, day, target, baseline, params):
        from scipy.stats import chi2
        allow_mismatch = bool(params.get("allow_season_mismatch", False))
        plane, month, available = _season_plane(baseline, day, allow_mismatch)
        ref = np.stack([baseline["coef"][b][plane] for b in self.BANDS], -1)
        obs = np.stack([target[b] for b in self.BANDS], -1)
        valid = np.isfinite(ref).all(-1) & np.isfinite(obs).all(-1)
        X, Y = ref[valid].astype("float64"), obs[valid].astype("float64")
        if len(X) < len(self.BANDS) * 4:
            return DetectorResult(np.full(valid.shape, np.nan, "float32"),
                                  np.zeros_like(valid), None, {},
                                  ["IR-MAD needs more valid pixels to estimate covariance."])
        weights = np.ones(len(X))
        last_rho = np.zeros(len(self.BANDS))
        iterations = 0
        for iterations in range(1, int(params.get("max_iterations", 50)) + 1):
            w = weights / max(weights.sum(), 1e-12)
            mx, my = w @ X, w @ Y
            xc, yc = X - mx, Y - my
            sxx = (xc * w[:, None]).T @ xc
            syy = (yc * w[:, None]).T @ yc
            sxy = (xc * w[:, None]).T @ yc
            ridge = 1e-6 * max(float(np.trace(sxx) + np.trace(syy)), 1.0)
            sxx += np.eye(sxx.shape[0]) * ridge
            syy += np.eye(syy.shape[0]) * ridge
            def invsqrt(cov):
                vals, vecs = np.linalg.eigh(cov)
                floor = max(float(vals.max()) * 1e-8, 1e-12)
                return (vecs * (1.0 / np.sqrt(np.maximum(vals, floor)))) @ vecs.T
            wx, wy = invsqrt(sxx), invsqrt(syy)
            U, rho, Vt = np.linalg.svd(wx @ sxy @ wy)
            rho = np.clip(rho, 0, 1 - 1e-9)
            A, B = wx @ U, wy @ Vt.T
            mad = xc @ A - yc @ B
            var = np.maximum(2.0 * (1.0 - rho), 1e-8)
            stat = np.sum(mad ** 2 / var, axis=1)
            weights = np.maximum(chi2.sf(stat, len(self.BANDS)), 1e-8)
            if np.max(np.abs(rho - last_rho)) < float(params.get("convergence", 0.001)):
                break
            last_rho = rho
        p = chi2.sf(stat, len(self.BANDS))
        score = np.full(valid.shape, np.nan, "float32")
        score[valid] = p.astype("float32")
        ndvi_ref = index({"B08": ref[..., 3], "B04": ref[..., 2]}, "B08", "B04")
        ndvi_obs = index(target, "B08", "B04")
        nbr_ref = index({"B08": ref[..., 3], "B12": ref[..., 5]}, "B08", "B12")
        nbr_obs = index(target, "B08", "B12")
        direction = {"ndvi": float(np.nanmedian(ndvi_obs - ndvi_ref)),
                     "nbr": float(np.nanmedian(nbr_obs - nbr_ref))}
        notes = []
        if allow_mismatch:
            notes.append("Same-season matching was explicitly overridden; phenology "
                         "may be scored as change.")
        elif not available:
            notes.append(f"No baseline observation exists for seasonal month "
                         f"{month + 1}; the all-season median was used and phenology "
                         "may be scored as change.")
        return DetectorResult(score, valid, direction,
                              {"iterations": iterations,
                               "canonical_correlations": rho.tolist(),
                               "reference_season_month": month + 1,
                               "seasonal_reference_available": available,
                               "season_mismatch_override": allow_mismatch}, notes)


class S1RatioFlood:
    """R4. Reference-to-target backscatter ratio on Sentinel-1. Open water is a
    specular reflector, so flooding shows up as a large *drop* in return; the score is
    reference / target, which is above 1 exactly where backscatter fell."""

    spec = DetectorSpec(
        id="s1_ratio_flood", version="1.0",
        display_name="Flood extent (S1 backscatter ratio)", sensor="S1",
        required_bands=["VV", "VH"],
        needs_baseline=True, min_baseline_observations=8,
        score_units="ratio", score_polarity="higher_is_more_change",
        default_threshold=1.25,
        threshold_semantics=("Ratio of the reference backscatter to this "
                             "observation's, in linear power. 1.25 is the UN-SPIDER "
                             "recommended value: the return fell to 80% of normal. "
                             "In `otsu` mode the threshold is instead derived from "
                             "this scene's own histogram and recorded on the run."),
        reference={"citation": ("UN-SPIDER Recommended Practice: Flood Mapping and "
                                "Damage Assessment Using Sentinel-1 SAR Data in "
                                "Google Earth Engine; Otsu, N. (1979), IEEE TSMC "
                                "9(1), 62-66."),
                   "url": "https://doi.org/10.1109/TSMC.1979.4310076"},
        param_schema={"polarisation": {"type": "string", "default": "VH",
                                       "enum": ["VH", "VV"]},
                      "threshold_mode": {"type": "string", "default": "fixed",
                                         "enum": ["fixed", "otsu"]},
                      "threshold": {"type": "number", "default": 1.25},
                      "max_slope_deg": {"type": "number", "default": 5.0}},
    )

    def fit_baseline(self, stack: list[tuple[float, dict]], params: dict) -> dict:
        return _median_baseline(stack, ("VV", "VH"))

    def alignment_pair(self, target: dict, baseline: dict):
        pol = _pols({}, "VH")[0]
        return _db(baseline["coef"][pol][0]), _db(target[pol])

    def score(self, day: float, target: dict, baseline: dict,
              params: dict) -> DetectorResult:
        pol = _pols(params, "VH")[0]
        notes: list[str] = []
        if baseline["n_obs"] < self.spec.min_baseline_observations:
            notes.append(f"BASELINE_TOO_SHORT: reference median from "
                         f"{baseline['n_obs']} observations, "
                         f"{self.spec.min_baseline_observations} recommended.")
        ref, obs = baseline["coef"][pol][0], target[pol]
        with np.errstate(invalid="ignore", divide="ignore"):
            ratio = ref / obs
        ratio = np.where(np.isfinite(ratio) & (ratio > 0), ratio, np.nan)
        valid = np.isfinite(ratio)
        aux: dict = {"polarisation": pol, "baseline_n_obs": baseline["n_obs"],
                     "threshold_mode": params.get("threshold_mode", "fixed")}
        if params.get("threshold_mode") == "otsu":
            # Otsu separates in dB, where the water and land modes are closer to
            # Gaussian than they are in linear power, then the cut is converted back.
            t_db = _otsu(_db(ratio))
            if t_db is None:
                notes.append("Otsu could not find a bimodal split in this scene; the "
                             "fixed threshold was used instead.")
            else:
                aux["derived_threshold"] = float(10 ** (t_db / 10.0))
                aux["derived_threshold_db"] = float(t_db)
        drop_db = _db(obs) - _db(ref)
        return DetectorResult(
            ratio.astype("float32"), valid,
            {f"{pol.lower()}_db": float(np.nanmedian(drop_db))
             if np.isfinite(drop_db).any() else 0.0},
            aux, notes + [
                "SAR reads smooth dry surfaces — tarmac, sand, dry riverbeds — as "
                "water, under-detects flooding beneath vegetation canopy and in dense "
                "urban areas because of double bounce, and loses open water entirely "
                "when wind roughens the surface."],
            )


class WishartOmnibus:
    """R7. Omnibus likelihood-ratio test for equality of the (diagonal) dual-pol
    covariance matrix between the baseline period and this observation. The score is
    a real p-value, so the threshold is a per-pixel false-alarm rate rather than an
    arbitrary number (Conradsen et al. 2016)."""

    spec = DetectorSpec(
        id="wishart_omnibus", version="1.0",
        display_name="Site activity (Wishart omnibus change test)", sensor="S1",
        required_bands=["VV", "VH"],
        needs_baseline=True, min_baseline_observations=8,
        score_units="p_value", score_polarity="lower_is_more_change",
        default_threshold=0.01,
        threshold_semantics=("Per-pixel probability of seeing this much covariance "
                             "difference when nothing changed. At 0.01, one pixel in "
                             "a hundred is expected to cross by chance alone; the run "
                             "summary states that expected count for this AOI."),
        reference={"citation": ("Conradsen, K., Nielsen, A.A. & Skriver, H. (2016). "
                                "Determining the points of change in time series of "
                                "polarimetric SAR data. IEEE TGRS 54(5), 3007-3024."),
                   "url": "https://doi.org/10.1109/TGRS.2015.2510160"},
        param_schema={"alpha": {"type": "number", "default": 0.01},
                      "enl": {"type": "number", "default": 4.4},
                      "threshold": {"type": "number", "default": 0.01}},
    )

    POLS = ("VV", "VH")

    def fit_baseline(self, stack: list[tuple[float, dict]], params: dict) -> dict:
        """The sufficient statistic for a Wishart period is the mean intensity per
        channel; the period's equivalent number of looks is ENL times the number of
        observations that went into it."""
        out = {"harmonics": 0, "n_obs": len(stack), "day0": stack[0][0],
               "coef": {}, "sigma": {}}
        for pol in self.POLS:
            Y = np.stack([d[pol] for _, d in stack])
            with _quiet():
                out["coef"][pol] = np.nanmean(Y, axis=0)[None].astype("float32")
                out["sigma"][pol] = np.isfinite(Y).sum(0).astype("float32")
        return out

    def alignment_pair(self, target: dict, baseline: dict):
        return _db(baseline["coef"]["VV"][0]), _db(target["VV"])

    def score(self, day: float, target: dict, baseline: dict,
              params: dict) -> DetectorResult:
        from scipy.stats import chi2

        enl = float(params.get("enl", 4.4))
        notes: list[str] = []
        if baseline["n_obs"] < self.spec.min_baseline_observations:
            notes.append(f"BASELINE_TOO_SHORT: reference period has "
                         f"{baseline['n_obs']} observations, "
                         f"{self.spec.min_baseline_observations} recommended; the "
                         "chi-square approximation degrades on short periods.")
        mu_b = {p: baseline["coef"][p][0] for p in self.POLS}
        mu_t = {p: target[p] for p in self.POLS}
        # per-pixel counts of contributing baseline observations -> equivalent looks
        n1 = enl * np.nanmax(np.stack([baseline["sigma"][p] for p in self.POLS]), 0)
        n2 = enl
        n = n1 + n2

        with np.errstate(invalid="ignore", divide="ignore"):
            det_b = np.prod([mu_b[p] for p in self.POLS], axis=0)
            det_t = np.prod([mu_t[p] for p in self.POLS], axis=0)
            mu_p = {p: (n1 * mu_b[p] + n2 * mu_t[p]) / n for p in self.POLS}
            det_p = np.prod([mu_p[p] for p in self.POLS], axis=0)
            # -2lnQ for equality of two complex Wishart scale matrices, diagonal case
            stat = 2.0 * (n * np.log(det_p) - n1 * np.log(det_b) - n2 * np.log(det_t))
        # f = (k-1) * q, with q = 2 free parameters in a diagonal 2x2 matrix
        f = (2 - 1) * len(self.POLS)
        stat = np.where(np.isfinite(stat) & (stat > 0), stat, np.nan)
        p_value = chi2.sf(stat, f).astype("float32")
        valid = np.isfinite(p_value)
        p_value = np.where(valid, p_value, 1.0).astype("float32")   # 1.0 = no evidence

        # Loewner order: is the change an increase in both channels, a decrease in
        # both, or neither? "Indefinite" is a real and common answer and saying so
        # beats forcing a direction the data does not support.
        up = np.all([mu_t[p] > mu_b[p] for p in self.POLS], axis=0)
        down = np.all([mu_t[p] < mu_b[p] for p in self.POLS], axis=0)
        alpha = float(params.get("threshold", params.get("alpha", 0.01)))
        aux = {"baseline_n_obs": baseline["n_obs"], "enl": enl, "dof": f,
               "alpha": alpha,
               "expected_false_positives": int(round(alpha * int(valid.sum()))),
               "_loewner": {"increase": up, "decrease": down}}
        notes.append(f"At alpha={alpha:g} over {int(valid.sum())} valid pixels, "
                     f"{aux['expected_false_positives']} pixels are expected to cross "
                     "by chance alone; the minimum mapping unit is what suppresses "
                     "them.")
        notes.append("The Wishart model assumes unfiltered multilook intensities. "
                     "Speckle filtering this input would invalidate the p-value.")
        return DetectorResult(p_value, valid, None, aux, notes)


REGISTRY = {d.spec.id: d for d in [
    HarmonicResidual(), RaddProbabilistic(), DNBR(), S1RatioFlood(), MNDWIExtent(),
    BuiltupDual(), WishartOmnibus(), IrmadCva(),
]}
