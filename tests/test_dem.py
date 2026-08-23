import numpy as np

from terrawatch.dem import hand_from_elevation


def test_hand_routes_downhill_and_is_zero_on_drainage():
    # A valley falling south: the centre column accumulates the surrounding hillsides.
    y = np.arange(9)[:, None]
    x = np.arange(9)[None, :]
    z = (100 - y * 2 + np.abs(x - 4) * 5).astype("float32")
    hand = hand_from_elevation(z, pixel_area_m2=100, drainage_area_m2=500)
    assert np.all(hand[2:, 4] == 0)
    assert np.nanmedian(hand[:, 0]) > np.nanmedian(hand[:, 2]) > 0
    assert (hand >= 0).all()
