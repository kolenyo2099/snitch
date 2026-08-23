"""P7 radar path. The one that matters is `test_wishart_false_alarm_rate`: R7's whole
claim is that its threshold is a real false-alarm probability, so the p-values have to
come out uniform on data where nothing changed."""
import numpy as np
import pytest

from terrawatch import masks
from terrawatch.detectors import REGISTRY, S1RatioFlood, WishartOmnibus, _otsu

ENL = 4.4
RNG = np.random.default_rng(20260822)


def _speckle(sigma, shape, looks=ENL):
    """Multilook intensity of a homogeneous target: Gamma(L, sigma/L)."""
    return RNG.gamma(looks, sigma / looks, size=shape).astype("float32")


def _stack(n, sigma_vv=0.10, sigma_vh=0.02, shape=(60, 60)):
    return [(float(i * 12), {"VV": _speckle(sigma_vv, shape),
                             "VH": _speckle(sigma_vh, shape)}) for i in range(n)]


# --- R7 -------------------------------------------------------------------

def test_wishart_false_alarm_rate():
    det = WishartOmnibus()
    base = det.fit_baseline(_stack(12), {})
    _, target = _stack(1)[0]
    r = det.score(0.0, target, base, {"threshold": 0.01, "enl": ENL})
    p = r.score[r.valid]
    assert p.size > 3000
    far = float((p <= 0.01).mean())
    # A chi-square approximation with no small-sample correction: the rate must land
    # in the right order of magnitude, not merely "below 1".
    assert 0.002 < far < 0.04, far
    assert 0.3 < float(np.median(p)) < 0.7, "p-values should be roughly uniform"


def test_wishart_detects_a_real_change_and_its_direction():
    det = WishartOmnibus()
    base = det.fit_baseline(_stack(12), {})
    _, target = _stack(1)[0]
    target = {k: v.copy() for k, v in target.items()}
    target["VV"][20:40, 20:40] *= 6.0            # a bright new structure
    target["VH"][20:40, 20:40] *= 6.0
    r = det.score(0.0, target, base, {"threshold": 0.01, "enl": ENL})
    changed = r.score <= 0.01
    assert changed[20:40, 20:40].mean() > 0.8
    assert changed.mean() < 0.2
    # Speckle means a handful of pixels still fall the wrong way at 6x; the direction
    # is a majority statement over the changed area, which is why the pipeline reduces
    # it with a 0.6 fraction rather than demanding unanimity.
    assert r.aux["_loewner"]["increase"][25:35, 25:35].mean() > 0.9
    assert r.aux["_loewner"]["decrease"][25:35, 25:35].mean() < 0.1


def test_wishart_states_expected_false_positives():
    det = WishartOmnibus()
    base = det.fit_baseline(_stack(12), {})
    r = det.score(0.0, _stack(1)[0][1], base, {"threshold": 0.01, "enl": ENL})
    assert r.aux["expected_false_positives"] == round(0.01 * int(r.valid.sum()))
    assert any("expected to cross by chance" in n for n in r.notes)
    assert any("unfiltered" in n for n in r.notes)


def test_wishart_polarity_is_declared_so_the_pipeline_compares_the_right_way():
    assert WishartOmnibus.spec.score_polarity == "lower_is_more_change"
    assert WishartOmnibus.spec.default_threshold == 0.01


# --- R4 -------------------------------------------------------------------

def test_ratio_flood_finds_the_drop_not_the_rise():
    det = S1RatioFlood()
    base = det.fit_baseline(_stack(10), {})
    _, target = _stack(1)[0]
    target = {k: v.copy() for k, v in target.items()}
    target["VH"][10:30, 10:30] *= 0.1            # flooded: specular, return collapses
    target["VH"][40:50, 40:50] *= 8.0            # brighter: not a flood
    r = det.score(0.0, target, base, {"polarisation": "VH", "threshold": 1.25})
    flooded = r.score >= 1.25
    assert flooded[15:25, 15:25].mean() > 0.9
    assert flooded[42:48, 42:48].mean() < 0.05
    assert r.direction["vh_db"] < 0


def test_ratio_flood_baseline_median_ignores_one_flooded_reference_date():
    det = S1RatioFlood()
    stack = _stack(10)
    stack[4][1]["VH"][:] *= 0.05                 # one flooded date in the reference
    base = det.fit_baseline(stack, {})
    clean = det.fit_baseline(_stack(10), {})
    ratio = np.nanmedian(base["coef"]["VH"][0] / clean["coef"]["VH"][0])
    assert 0.7 < ratio < 1.4, ratio


def test_otsu_mode_derives_and_records_a_threshold():
    det = S1RatioFlood()
    base = det.fit_baseline(_stack(10), {})
    _, target = _stack(1)[0]
    target = {k: v.copy() for k, v in target.items()}
    target["VH"][:30] *= 0.08
    r = det.score(0.0, target, base, {"polarisation": "VH", "threshold_mode": "otsu"})
    assert "derived_threshold" in r.aux
    t = r.aux["derived_threshold"]
    assert 1.5 < t < 12.0, t
    assert (r.score >= t)[:30].mean() > 0.85
    assert (r.score >= t)[30:].mean() < 0.15


def test_otsu_falls_back_loudly_when_the_scene_is_unimodal():
    det = S1RatioFlood()
    base = det.fit_baseline(_stack(10), {})
    r = det.score(0.0, _stack(1)[0][1], base, {"threshold_mode": "otsu"})
    # a unimodal scene still yields a split; what must not happen is silence
    assert r.aux["threshold_mode"] == "otsu"
    assert "derived_threshold" in r.aux or any("Otsu" in n for n in r.notes)


def test_otsu_refuses_a_sample_too_small_to_split():
    assert _otsu(np.arange(10, dtype="float64")) is None


# --- masks and the orbit rule ---------------------------------------------

def test_unavailable_masks_are_reported_as_none_not_as_zero():
    data = {"VV": np.ones((8, 8), "float32"), "VH": np.ones((8, 8), "float32"),
            "_crs": "EPSG:32721", "_transform": None}
    invalid, summary, vf = masks.apply_chain(data, ["slope", "hand"], {})
    assert summary["hand"] is None                 # never computed in v1
    assert summary["slope"] is None                # no AOI/transform to fetch a DEM
    assert vf == 1.0 and not invalid.any()


def test_slope_mask_excludes_steep_ground(monkeypatch):
    slope = np.zeros((8, 8), "float32")
    slope[:3] = 20.0
    monkeypatch.setattr("terrawatch.dem.slope_deg", lambda *a, **k: slope)
    data = {"VV": np.ones((8, 8), "float32"),
            "_crs": "EPSG:32721", "_transform": object()}
    m = masks.slope(data, {"_aoi": {"type": "Point", "coordinates": [0, 0]},
                           "max_slope_deg": 5.0})
    assert m[:3].all() and not m[3:].any()


def test_orbit_rule_rejects_every_path_not_just_polling():
    from terrawatch.pipeline import _orbit_reason

    project = {"s1_relative_orbit": 10, "s1_pass_direction": "DESCENDING"}
    recipe = {"sensor": "S1"}
    ok = type("S", (), {"relative_orbit": 10, "pass_direction": "DESCENDING"})()
    wrong_orbit = type("S", (), {"relative_orbit": 24, "pass_direction": "DESCENDING"})()
    wrong_pass = type("S", (), {"relative_orbit": 10, "pass_direction": "ASCENDING"})()
    assert _orbit_reason(project, recipe, ok) is None
    assert "relative orbit" in _orbit_reason(project, recipe, wrong_orbit)
    assert "pass direction" in _orbit_reason(project, recipe, wrong_pass)
    assert _orbit_reason(project, {"sensor": "S2"}, wrong_orbit) is None


@pytest.mark.parametrize("rid", ["flood_radar", "site_activity_radar"])
def test_radar_recipes_resolve_to_a_registered_detector(rid):
    from terrawatch.recipes import REGISTRY as RECIPES

    r = RECIPES[rid]
    det = REGISTRY[r["detector"]]
    assert r["sensor"] == "S1" == det.spec.sensor
    assert set(det.spec.required_bands) <= set(r["bands"])
    assert r["defaults"]["threshold"] == det.spec.default_threshold
    assert any("orbit" in x for x in r["limitations"])
