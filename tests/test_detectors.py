"""Detectors against synthetic arrays with known injected change (spec §18)."""
import numpy as np
import pytest

from snitch.detectors import HarmonicResidual


def _scene(ndvi: np.ndarray) -> dict:
    """Build B04/B08/B12 that yield the requested NDVI and a matching NBR."""
    b08 = np.full_like(ndvi, 0.3)
    b04 = b08 * (1 - ndvi) / (1 + ndvi)
    return {"B04": b04, "B08": b08, "B12": b04}


def _series(n=48, shape=(32, 32), seed=0):
    rng = np.random.default_rng(seed)
    out = []
    for i in range(n):
        day = i * 12.0
        season = 0.7 + 0.15 * np.sin(2 * np.pi * day / 365.25)
        ndvi = np.full(shape, season) + rng.normal(0, 0.01, shape)
        out.append((day, ndvi))
    return out


def test_detects_injected_clearing_and_leaves_the_rest_alone():
    det = HarmonicResidual()
    series = _series()
    baseline = [(d, _scene(n)) for d, n in series[:40]]
    b = det.fit_baseline(baseline, {"harmonics": 2})

    day, ndvi = series[40]
    ndvi = ndvi.copy()
    ndvi[8:20, 8:20] -= 0.45                   # a clearing in the middle
    r = det.score(day, _scene(ndvi), b, {})

    changed = r.score[8:20, 8:20]
    unchanged = np.concatenate([r.score[:8].ravel(), r.score[24:].ravel()])
    assert np.nanmedian(changed) > 5, "injected clearing must score well above 3 sigma"
    assert np.nanpercentile(unchanged, 99) < 3, "unchanged pixels must not fire"
    assert r.direction["ndvi"] < 0


def test_no_change_scores_below_threshold():
    det = HarmonicResidual()
    series = _series(seed=1)
    b = det.fit_baseline([(d, _scene(n)) for d, n in series[:40]], {})
    day, ndvi = series[41]
    r = det.score(day, _scene(ndvi), b, {})
    assert np.nanpercentile(r.score, 99) < 3


def test_nan_input_propagates_rather_than_filling():
    det = HarmonicResidual()
    series = _series(seed=2)
    b = det.fit_baseline([(d, _scene(n)) for d, n in series[:40]], {})
    day, ndvi = series[41]
    ndvi = ndvi.copy()
    ndvi[0:4, :] = np.nan
    r = det.score(day, _scene(ndvi), b, {})
    assert np.isnan(r.score[0:4, :]).all()
    assert not r.valid[0:4, :].any()


def test_short_baseline_emits_a_note():
    det = HarmonicResidual()
    series = _series(n=10, seed=3)
    b = det.fit_baseline([(d, _scene(n)) for d, n in series], {})
    r = det.score(200.0, _scene(series[-1][1]), b, {})
    assert any("BASELINE_TOO_SHORT" in n for n in r.notes)
