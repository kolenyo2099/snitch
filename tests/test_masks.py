"""Masks against fixtures with known pixel counts (spec §18)."""
import numpy as np

from snitch import masks


def _fixture():
    scl = np.zeros((20, 20), "float32")
    scl[0:2, 0:2] = 9        # a 2x2 cloud
    scl[10, 10] = 11         # one snow pixel
    return {"B08": np.ones((20, 20), "float32"),
            "B03": np.full((20, 20), 0.1, "float32"),
            "B11": np.full((20, 20), 0.3, "float32"),
            "SCL": scl}


def test_cloud_is_dilated_by_two_pixels():
    data = _fixture()
    m = masks.scl_shadow(data, {})
    assert m[0:2, 0:2].all()
    assert m[3, 3] and not m[5, 5]      # 2-px dilation reaches (3,3), not (5,5)


def test_snow_uses_scl_and_ndsi():
    data = _fixture()
    assert masks.snow(data, {})[10, 10]
    data["B03"] = np.full((20, 20), 0.9, "float32")   # NDSI 0.5 everywhere
    assert masks.snow(data, {}).all()


def test_chain_reports_per_mask_counts_and_valid_fraction():
    invalid, summary, vf = masks.apply_chain(_fixture(), ["scl_shadow", "snow"])
    assert set(summary) >= {"nodata", "scl_shadow", "snow"}
    assert summary["nodata"] == 0
    assert summary["scl_shadow"] > 0
    assert abs(vf - (1 - invalid.mean())) < 1e-9
    assert 0 < vf < 1


def test_nodata_masks_nan_pixels():
    data = _fixture()
    data["B08"][5, 5] = np.nan
    invalid, summary, vf = masks.apply_chain(data, [])
    assert invalid[5, 5] and summary["nodata"] == 1


def test_cloudscore_plus_masks_low_clear_sky_scores_and_wins_priority():
    data = _fixture()
    data["cs"] = np.full((20, 20), 0.9, "float32")
    data["cs"][5:8, 5:8] = 0.2
    data["cloud_probability"] = np.full((20, 20), 0.99, "float32")
    m = masks.cloudscore_plus(data, {"cloudscore_threshold": 0.60})
    assert m[5:8, 5:8].all() and not m[:3, :3].all()
    assert masks.s2cloudless(data, {}) is None


def test_s2cloudless_fallback_projects_shadows_without_wrapping_edges():
    data = _fixture()
    data["cloud_probability"] = np.zeros((20, 20), "float32")
    data["cloud_probability"][1, 1] = 90.0
    data["_solar_azimuth"] = 0.0
    data["_solar_zenith"] = 10.0
    data["_resolution_m"] = 10.0
    m = masks.s2cloudless(data, {"cloud_projection_height_m": 200,
                                 "max_shadow_projection_px": 5})
    assert m[1, 1]
    assert not m[-3:, -3:].any(), "edge clouds must not wrap to the opposite corner"


def test_unavailable_preferred_masks_are_substituted_from_scl_and_recorded():
    """CDSE's cloud-probability layers are absent from the other catalogues.
    Rather than reporting the masks as not applied, the SCL cloud/shadow classes
    stand in — identical to the declared scl_shadow — and the substitution is
    recorded on the data so the run can caveat it honestly."""
    data = _fixture()
    invalid, summary, _ = masks.apply_chain(
        data, ["cloudscore_plus", "s2cloudless", "scl_shadow"])
    assert summary["cloudscore_plus"] is not None
    assert summary["s2cloudless"] is not None
    assert invalid.any()
    # the explicit scl_shadow entry adds nothing: the two substitutions above
    # already removed exactly those pixels
    assert summary["scl_shadow"] == 0
    # counts are "newly removed pixels", so the union is their (deduped) sum
    assert invalid.sum() == summary["cloudscore_plus"] + summary["s2cloudless"] \
        + summary["scl_shadow"] + summary["nodata"]
    assert data["_mask_substitutes"] == {"cloudscore_plus": "scl_shadow",
                                         "s2cloudless": "scl_shadow"}


def test_mask_stays_absent_when_no_scl_exists_to_fall_back_on():
    data = {"B08": np.ones((8, 8), "float32")}
    invalid, summary, _ = masks.apply_chain(data, ["cloudscore_plus"])
    assert summary["cloudscore_plus"] is None
    assert data["_mask_substitutes"] == {}


def test_optional_permanent_water_layer_has_named_count():
    data = {"B03": np.ones((8, 8), "float32"),
            "permanent_water": np.zeros((8, 8), "float32")}
    data["permanent_water"][2:5, 1:4] = 1
    invalid, summary, fraction = masks.apply_chain(data, ["water_permanent"])
    assert summary["water_permanent"] == 9
    assert invalid.sum() == 9
    assert abs(fraction - 55 / 64) < 1e-9
