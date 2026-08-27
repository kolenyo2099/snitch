"""Chip rendering for every recipe. The vegetation recipe's band list has only two of
B04/B03/B02, which used to return a two-band frame that crashed the PNG encoder — and
no alert of the flagship recipe could ever be raised. This is the regression suite for
that: display_bands must hand back exactly three bands per recipe, and render must
produce three valid PNGs for each."""
import numpy as np

from terrawatch import chips
from terrawatch.recipes import REGISTRY


def test_display_bands_is_always_exactly_three():
    for rid, recipe in REGISTRY.items():
        bands = chips.display_bands(recipe)
        assert len(bands) == 3, f"{rid}: display_bands returned {bands}"
        assert len(set(bands)) >= 1


def test_vegetation_recipe_pads_to_three_without_rgb_blue():
    bands = chips.display_bands(REGISTRY["vegetation_loss_optical"])
    assert bands == ("B04", "B03", "B08")


def test_every_recipe_renders_before_after_overlay_pngs():
    for rid, recipe in REGISTRY.items():
        bands = chips.display_bands(recipe)
        rng = np.random.default_rng(len(bands))
        data = {b: (rng.random((32, 32)) * 0.6).astype("float32") for b in bands}
        stretch = chips.stretch_from(data, bands)
        mask = np.zeros((32, 32), bool)
        mask[8:16, 8:16] = True
        pngs = chips.render(data, data, mask, stretch,
                            "2025-01-01", "2025-06-01", bands)
        assert set(pngs) == {"before", "after", "overlay"}, rid
        for name, blob in pngs.items():
            assert blob[:8] == b"\x89PNG\r\n\x1a\n", f"{rid}/{name} is not a PNG"
