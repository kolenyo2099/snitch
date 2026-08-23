"""P8 detector checks with synthetic changes and unchanged controls."""
import numpy as np

from terrawatch.detectors import (
    BuiltupDual, DNBR, IrmadCva, MNDWIExtent, RaddProbabilistic, REGISTRY,
)
from terrawatch.recipes import REGISTRY as RECIPES


RNG = np.random.default_rng(8222026)


def _from_index(value, a=0.3):
    return a * (1 - value) / (1 + value)


def _dnbr_scene(nbr, shape=(40, 40)):
    n = np.full(shape, nbr, dtype="float32") if np.isscalar(nbr) else nbr
    return {"B8A": np.full(shape, 0.3, "float32"),
            "B12": _from_index(n).astype("float32")}


def test_dnbr_detects_burn_and_reports_all_severity_classes():
    det = DNBR()
    base = det.fit_baseline([(float(i), _dnbr_scene(0.72)) for i in range(8)], {})
    post = np.full((40, 40), 0.72, "float32")
    post[10:30, 10:30] = 0.12
    r = det.score(100, _dnbr_scene(post), base, {})
    assert np.nanmedian(r.score[12:28, 12:28]) > 550
    assert np.nanmax(np.abs(r.score[:8])) < 1
    assert r.aux["severity_pixel_counts"]["moderate_high"] == 400
    assert sum(r.aux["severity_pixel_counts"].values()) == 1600


def test_dnbr_uses_the_matching_season_and_accepts_custom_class_breaks():
    det = DNBR()
    stack = []
    for year in range(2):
        stack += [(15 + year * 365.25, _dnbr_scene(0.72)),
                  (197 + year * 365.25, _dnbr_scene(0.42))]
    base = det.fit_baseline(stack, {})
    r = det.score(197, _dnbr_scene(0.30), base,
                  {"severity_breaks": [-300, -120, 80, 250, 400, 600]})
    assert 115 < np.nanmedian(r.score) < 125
    assert r.aux["reference_season_month"] == 7
    assert r.aux["seasonal_reference_available"] is True
    assert r.aux["severity_breaks"][2] == 80


def _water_scene(water):
    mndwi = np.where(water, 0.65, -0.35).astype("float32")
    return {"B03": np.full(water.shape, 0.3, "float32"),
            "B08": np.full(water.shape, 0.2, "float32"),
            "B11": _from_index(mndwi).astype("float32")}


def test_mndwi_scores_relative_seasonal_extent_change_on_changed_pixels():
    det = MNDWIExtent()
    water = np.zeros((40, 40), bool)
    water[10:30, 10:30] = True
    stack = [(15.0 + i * 365.25, _water_scene(water)) for i in range(2)]
    base = det.fit_baseline(stack, {})
    expanded = water.copy()
    expanded[5:10, 10:30] = True
    r = det.score(15.0, _water_scene(expanded), base, {})
    assert 0.24 < r.direction["fraction"] < 0.26
    assert np.allclose(r.score[5:10, 10:30], 0.25)
    assert not r.score[:5].any()
    assert r.aux["target_water_pixels"] == 500


def test_mndwi_reports_fixed_fallback_for_unimodal_scene():
    det = MNDWIExtent()
    water = np.zeros((40, 40), bool)
    base = det.fit_baseline([(15.0, _water_scene(water)),
                             (380.0, _water_scene(water))], {})
    flat = {"B03": np.full((40, 40), 0.2, "float32"),
            "B08": np.full((40, 40), 0.2, "float32"),
            "B11": np.full((40, 40), 0.2, "float32")}
    r = det.score(15.0, flat, base, {})
    assert r.aux["mndwi_threshold"] == 0.0
    assert any("not separable" in note for note in r.notes)


def _built_scene(shape=(36, 36), changed=False):
    noise = RNG.normal(0, 0.006, shape).astype("float32")
    d = {"B02": 0.13 + noise, "B03": 0.14 + noise,
         "B04": 0.15 + noise, "B08": 0.34 + noise,
         "B11": 0.20 + noise, "B12": 0.18 + noise}
    if changed:
        for b in ("B02", "B03", "B04"):
            d[b] = d[b].copy(); d[b][10:26, 10:26] += 0.12
        d["B11"] = d["B11"].copy(); d["B11"][10:26, 10:26] += 0.22
    return d


def test_builtup_requires_both_ndbi_and_brightness_to_rise():
    det = BuiltupDual()
    base = det.fit_baseline([(float(i), _built_scene()) for i in range(16)], {})
    changed = det.score(20, _built_scene(changed=True), base, {})
    unchanged = det.score(21, _built_scene(), base, {})
    assert np.nanmedian(changed.score[12:24, 12:24]) > 5
    assert (unchanged.score >= 2.5).mean() < 0.03
    assert changed.aux["streams"] == ["optical"]


def test_builtup_fuses_radar_and_marks_cross_stream_agreement():
    det = BuiltupDual()
    base = det.fit_baseline([(float(i), _built_scene()) for i in range(16)], {})
    radar_stack = [(float(i), {
        "VV": RNG.gamma(4.4, 0.08 / 4.4, (36, 36)).astype("float32"),
        "VH": RNG.gamma(4.4, 0.02 / 4.4, (36, 36)).astype("float32"),
    }) for i in range(16)]
    radar = det.fit_radar_baseline(radar_stack)
    base["coef"].update(radar["coef"]); base["sigma"].update(radar["sigma"])
    base["coef"]["_RADAR_COUNT"] = np.full((1, 36, 36), 16, "float32")
    base["sigma"]["_RADAR_COUNT"] = np.zeros((36, 36), "float32")
    target = _built_scene(changed=True)
    target["VV"] = RNG.gamma(4.4, 0.08 / 4.4, (36, 36)).astype("float32")
    target["VH"] = RNG.gamma(4.4, 0.02 / 4.4, (36, 36)).astype("float32")
    target["VV"][10:26, 10:26] *= 8
    target["VH"][10:26, 10:26] *= 8
    r = det.score(20, target, base, {})
    agreement = r.aux["_cross_stream_agreement"]
    assert agreement[12:24, 12:24].mean() > 0.95
    assert r.aux["streams"] == ["optical", "radar"]
    assert r.aux["radar_baseline_n_obs"] == 16
    assert r.aux["radar_method"] == "wishart_increase_significance"


def _radar_stack(n=16, shape=(42, 42)):
    out = []
    for i in range(n):
        out.append((float(i), {
            "VV": RNG.lognormal(np.log(0.10), 0.12, shape).astype("float32"),
            "VH": RNG.lognormal(np.log(0.025), 0.12, shape).astype("float32"),
        }))
    return out


def test_radd_probability_finds_backscatter_collapse_only():
    det = RaddProbabilistic()
    base = det.fit_baseline(_radar_stack(), {})
    assert all(s.ndim == 2 for s in base["sigma"].values())
    target = _radar_stack(1)[0][1]
    target = {k: v.copy() for k, v in target.items()}
    for pol in ("VV", "VH"):
        target[pol][12:30, 12:30] *= 0.15
    r = det.score(30, target, base, {})
    assert np.nanmedian(r.score[14:28, 14:28]) > 0.95
    outside = np.concatenate([r.score[:8].ravel(), r.score[34:].ravel()])
    assert np.nanpercentile(outside, 95) < 0.75


def test_radd_accumulates_the_previous_probability_as_bayesian_prior():
    det = RaddProbabilistic()
    base = det.fit_baseline(_radar_stack(), {})
    target = _radar_stack(1)[0][1]
    for pol in ("VV", "VH"):
        target[pol][12:30, 12:30] *= 0.55
    first = det.score(30, target, base, {})
    second = det.score(36, target, base, {"_previous_probability": first.score})
    a = np.nanmedian(first.score[14:28, 14:28])
    b = np.nanmedian(second.score[14:28, 14:28])
    assert b > a
    assert second.aux["previous_probability_used"] is True


def _multiband_scene(latent, changed=False):
    bands = {}
    for i, band in enumerate(IrmadCva.BANDS):
        own = latent[..., i]
        shared = latent[..., (i + 1) % len(IrmadCva.BANDS)]
        bands[band] = (0.08 + (i + 1) * 0.025 + own * 0.012 + shared * 0.004).astype(
            "float32")
    if changed:
        for i, band in enumerate(IrmadCva.BANDS):
            bands[band] = bands[band].copy()
            bands[band][14:30, 14:30] += (-0.12 if band in ("B08",) else 0.10)
    return bands


def test_irmad_localises_change_and_ignores_global_linear_radiometry():
    det = IrmadCva()
    latent = RNG.normal(0, 1, (44, 44, len(IrmadCva.BANDS)))
    stack = [(float(i), _multiband_scene(latent + RNG.normal(0, 0.04, latent.shape)))
             for i in range(5)]
    base = det.fit_baseline(stack, {})
    target = _multiband_scene(latent, changed=True)
    for band in target:
        target[band] = target[band] * 1.08 + 0.01
    r = det.score(40, target, base, {})
    assert (r.score[16:28, 16:28] <= 0.05).mean() > 0.75
    outside = np.concatenate([r.score[:10].ravel(), r.score[34:].ravel()])
    assert (outside <= 0.05).mean() < 0.15
    assert 1 <= r.aux["iterations"] <= 50
    assert r.direction["ndvi"] < 0


def test_irmad_season_override_is_loud_and_traceable():
    det = IrmadCva()
    latent = RNG.normal(0, 1, (32, 32, len(IrmadCva.BANDS)))
    base = det.fit_baseline([(15.0, _multiband_scene(latent)),
                             (380.0, _multiband_scene(latent))], {})
    r = det.score(197.0, _multiband_scene(latent), base,
                  {"allow_season_mismatch": True, "max_iterations": 3})
    assert r.aux["season_mismatch_override"] is True
    assert any("explicitly overridden" in note for note in r.notes)


def test_all_eight_recipes_resolve_to_compatible_detectors():
    assert len(RECIPES) == 8
    for recipe in RECIPES.values():
        det = REGISTRY[recipe["detector"]]
        assert recipe["sensor"] == det.spec.sensor
        assert set(det.spec.required_bands) <= set(recipe["bands"])
        assert recipe["defaults"]["threshold"] == det.spec.default_threshold
