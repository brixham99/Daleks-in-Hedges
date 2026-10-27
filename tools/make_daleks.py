"""Build the four per-level Dalek designs (Original, Movie, 2005, 2010).

    python3 tools/make_daleks.py

Same method as the game's original Dalek (assets/dalek_se.png / _ne.png):
glossy painted isometric art on a flat magenta matte, cut out with the
matte un-mixed from the edges. The Original IS that first Dalek; Movie,
2005 and 2010 were painted in its style from Nathan's reference sheet
(the raw paintings are tools/art/daleks/*.jpg, cut-outs *_se/_ne.png).

Writes assets/daleks/<design>_<se|ne>.png, <design>_<se|ne>_lit.png (just
the dome lights lit, with a soft halo, blitted on top while it speaks) and
daleks.json (gun tip, eye tip, height scale). SW/NW are mirrored in game.
"""
import json
import os

import numpy as np
from PIL import Image, ImageFilter
from scipy import ndimage

HERE = os.path.dirname(os.path.abspath(__file__))
ART = os.path.join(HERE, "art", "daleks")
OUT = os.path.join(HERE, "..", "assets", "daleks")
PAD = 16   # room above/around for the lamp halo

DESIGNS = {
    #  name: (label, source dir/prefix, height vs the Original, lamp-on colour, lamp rule)
    "original": ("Original", os.path.join(HERE, "..", "assets", "dalek_"), 1.00, (255, 250, 215), "knobs"),
    "movie": ("Movie", os.path.join(ART, "movie_"), 1.00, (255, 70, 40), "red"),
    "2005": ("2005", os.path.join(ART, "2005_"), 1.02, (255, 252, 230), "clear"),
    "2010": ("2010", os.path.join(ART, "2010_"), 1.12, (235, 245, 255), "clear"),
}
# The Original's dome lights (its ear knobs) by hand, in its 160 px source: (x, y, r).
ORIGINAL_KNOBS = {"se": [(64.0, 2.5, 3.6), (31.5, 14.0, 4.2)], "ne": [(55.6, 24.4, 3.8)]}


def lamp_mask(rule, face, rgb, a, eye):
    h_rows = np.nonzero((a > 64).any(1))[0]
    top, h = h_rows[0], h_rows[-1] - h_rows[0]
    ys, xs = np.mgrid[0:a.shape[0], 0:a.shape[1]]
    if rule == "knobs":
        m = np.zeros(a.shape, float)
        for x, y, r in ORIGINAL_KNOBS[face]:
            m = np.maximum(m, np.clip(r + 0.5 - np.hypot(xs - x - PAD, ys - y - PAD), 0, 1))
        return m * (a / 255.0)
    r, g, b = (rgb[..., i].astype(int) for i in range(3))
    if rule == "red":
        m = (r > 140) & (r - g > 60) & (r - b > 60)
    else:
        spread = np.maximum(np.maximum(r, g), b) - np.minimum(np.minimum(r, g), b)
        m = (spread < 45) & (np.minimum(np.minimum(r, g), b) > 150)
    m &= (ys < top + 0.13 * h) & (a > 100)
    m &= np.hypot(xs - eye[0], ys - eye[1]) > 0.11 * h * (1.6 if rule == "clear" and h > 600 else 1.0)
    lab, n = ndimage.label(m)
    sizes = ndimage.sum(m, lab, range(1, n + 1))
    m = np.isin(lab, [i + 1 for i, s in enumerate(sizes) if s >= 20])
    # take in the antialiased rim of each lamp
    m = ndimage.binary_dilation(m, iterations=2) & (a > 0)
    return ndimage.gaussian_filter(m.astype(float), 0.7) * (a / 255.0)


def tips(a):
    rows = np.nonzero((a > 64).any(1))[0]
    top, h = rows[0], rows[-1] - rows[0]

    def rightmost(y0, y1):
        y0, y1 = int(top + y0 * h), int(top + y1 * h)
        ys, xs = np.nonzero(a[y0:y1] > 100)
        i = xs.argmax()
        return [float(xs[i]) + 0.5, float(ys[i] + y0) + 0.5]
    return rightmost(0.0, 0.22), rightmost(0.30, 0.58)


def main():
    os.makedirs(OUT, exist_ok=True)
    meta = {"order": list(DESIGNS), "designs": {}}
    for name, (label, src, hscale, on, rule) in DESIGNS.items():
        meta["designs"][name] = {"label": label, "height": hscale}
        for face in ("se", "ne"):
            im = np.asarray(Image.open(f"{src}{face}.png").convert("RGBA"))
            im = np.pad(im, ((PAD, PAD), (PAD, PAD), (0, 0)))
            rgb, a = im[..., :3].astype(float), im[..., 3].astype(float)
            if name != "original":
                # matte-tinted shadow pixels the painting left under the base
                tint = (rgb[..., 0] - rgb[..., 1] > 20) & (rgb[..., 2] - rgb[..., 1] > 8)
                a = np.where(tint, 0, a)
                # edge pixels take the colour of the nearest solid pixel: no matte fringe
                _, (iy, ix) = ndimage.distance_transform_edt(a < 250, return_indices=True)
                edge = (a > 0) & (a < 250)
                rgb[edge] = rgb[iy[edge], ix[edge]]
                a = np.where(a < 40, 0, a)
                im = np.dstack([rgb, a]).astype(np.uint8)
            eye, gun = tips(a)
            m = lamp_mask(rule, face, rgb, a, eye)
            # lit: the lamp pixels pushed towards the lamp colour, plus a soft halo
            scale = a.shape[0] / 300.0
            lum = rgb.mean(-1, keepdims=True) / 255.0
            core = np.clip(np.array(on) * (0.75 + 0.5 * lum) + 40 * lum, 0, 255)
            halo = np.asarray(Image.fromarray((m * 255).astype(np.uint8)).filter(
                ImageFilter.GaussianBlur(4.5 * scale))).astype(float) / 255.0
            a_halo = np.clip(halo * 2.0, 0, 0.7) * (1 - m)
            alpha = m + a_halo
            with np.errstate(all="ignore"):
                col = np.where(alpha[..., None] > 0,
                               (core * m[..., None] + np.array(on) * a_halo[..., None]) / alpha[..., None], 0)
            lit = np.dstack([np.clip(col, 0, 255), np.clip(alpha, 0, 1) * 255]).astype(np.uint8)
            Image.fromarray(im).save(os.path.join(OUT, f"{name}_{face}.png"))
            Image.fromarray(lit).save(os.path.join(OUT, f"{name}_{face}_lit.png"))
            meta["designs"][name][face] = {"eye": eye, "gun": gun, "size": [im.shape[1], im.shape[0]]}
            print(name, face, im.shape[1::-1], "lamp px", int(m.sum()))
    with open(os.path.join(OUT, "daleks.json"), "w") as fh:
        json.dump(meta, fh, indent=1)


if __name__ == "__main__":
    main()
