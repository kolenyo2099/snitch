"""Before/after/overlay chips. Both frames use identical stretch parameters,
computed once, and the values are printed on the image (spec §13.3)."""
from __future__ import annotations
import io

import numpy as np
from PIL import Image, ImageDraw

RGB = ("B04", "B03", "B02")
MAX_PX = 768


def display_bands(recipe: dict) -> tuple:
    """Three bands to draw. Optical recipes get true colour when B04/B03/B02 are
    all in the recipe; otherwise the loaded bands stand in, in recipe order — radar
    recipes therefore get VV/VH/VV as a false-colour composite. Legible either way,
    and labelled on the chip so nobody mistakes it for a photograph. The result is
    always exactly three bands: a one- or two-band frame cannot be rendered as a PNG."""
    bands = [x for x in recipe["bands"] if x != "SCL"]
    chosen = [x for x in RGB if x in bands]
    chosen += [x for x in bands if x not in chosen]
    chosen = chosen[:3]
    while chosen and len(chosen) < 3:
        chosen.append(chosen[0])
    return tuple(chosen) or RGB


def stretch_from(data: dict, bands=RGB, lo_pct=2.0, hi_pct=98.0) -> dict:
    out = {}
    for b in bands:
        a = data.get(b)
        if a is None:
            continue
        f = a[np.isfinite(a)]
        out[b] = ((float(np.percentile(f, lo_pct)), float(np.percentile(f, hi_pct)))
                  if f.size else (0.0, 1.0))
    return out


def _rgb(data: dict, stretch: dict, bands=RGB) -> np.ndarray:
    planes = []
    for b in bands:
        a = data.get(b)
        if a is None:                       # fall back to a grey composite
            a = next(v for k, v in data.items() if not k.startswith("_"))
        lo, hi = stretch.get(b, (0.0, 0.3))
        planes.append(np.clip((np.nan_to_num(a, nan=lo) - lo) / max(hi - lo, 1e-6), 0, 1))
    return (np.stack(planes, -1) * 255).astype("uint8")


def _fit(img: Image.Image) -> Image.Image:
    if max(img.size) <= MAX_PX:
        return img
    s = MAX_PX / max(img.size)
    return img.resize((max(1, int(img.width * s)), max(1, int(img.height * s))),
                      Image.NEAREST)


def _label(img: Image.Image, text: str) -> Image.Image:
    d = ImageDraw.Draw(img)
    w = d.textlength(text)
    d.rectangle([0, img.height - 14, w + 8, img.height], fill=(0, 0, 0))
    d.text((4, img.height - 13), text, fill=(255, 255, 255))
    return img


def _png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def render(before: dict, after: dict, change_mask: np.ndarray, stretch: dict,
           before_date: str, after_date: str, bands=RGB) -> dict[str, bytes]:
    """Returns {'before':png, 'after':png, 'overlay':png}. One stretch, both frames."""
    st = " ".join(f"{b}:{lo:.3f}-{hi:.3f}" for b, (lo, hi) in sorted(stretch.items()))
    st = f"{'/'.join(bands)} {st}" if tuple(bands) != RGB else st
    b_img = _fit(Image.fromarray(_rgb(before, stretch, bands)))
    a_img = _fit(Image.fromarray(_rgb(after, stretch, bands)))
    ov = np.array(a_img).copy()
    m = np.array(_fit(Image.fromarray((change_mask * 255).astype("uint8")))) > 127
    ov[m] = (0.45 * ov[m] + 0.55 * np.array([255, 60, 60])).astype("uint8")
    return {
        "before": _png(_label(b_img, f"{before_date}  stretch {st}")),
        "after": _png(_label(a_img, f"{after_date}  stretch {st}")),
        "overlay": _png(_label(Image.fromarray(ov), f"change {after_date}")),
    }
