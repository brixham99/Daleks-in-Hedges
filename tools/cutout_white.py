#!/usr/bin/env python3
"""Cut a figure out of a white (JPEG) background with soft, halo-free edges.

    python3 tools/cutout_white.py in.jpg out.png

- Background = near-white pixels connected to the border (flood fill), so
  light colours inside the figure (grey hoodie, white soles) stay solid.
- JPEG speckle: only the largest connected figure is kept.
- Edge pixels get alpha from how far they are from white, and their colour
  is unpremultiplied from the white backdrop (no white halo).
"""
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

BG_T = 18        # "white" for the flood fill (max channel distance from 255)
SOFT_LO, SOFT_HI = 10, 70   # edge alpha ramp over this distance from white


def cutout(img: Image.Image) -> Image.Image:
    rgb = np.asarray(img.convert("RGB")).astype(np.float64)
    h, w = rgb.shape[:2]
    d = (255.0 - rgb).max(axis=2)
    # 1. Background: near-white and reachable from the border.
    m = Image.fromarray(np.where(d < BG_T, 255, 0).astype(np.uint8))
    m = Image.fromarray(np.pad(np.asarray(m), 1, constant_values=255)).copy()  # (copy: writable for floodfill)
    ImageDraw.floodfill(m, (0, 0), 128)
    bg = (np.asarray(m)[1:-1, 1:-1] == 128)
    # 2. Keep only the largest blob of figure (drops JPEG specks).
    fig = ~bg
    ys, xs = np.nonzero(fig)
    lab = Image.fromarray(np.where(fig, 255, 0).astype(np.uint8)).copy()
    best, best_n, seen = None, 0, np.zeros_like(fig)
    for y, x in zip(ys[:: max(1, len(ys) // 400)], xs[:: max(1, len(xs) // 400)]):
        if seen[y, x]:
            continue
        tmp = lab.copy()
        ImageDraw.floodfill(tmp, (int(x), int(y)), 100)
        comp = np.asarray(tmp) == 100
        seen |= comp
        if comp.sum() > best_n:
            best, best_n = comp, comp.sum()
    fig = best
    # 3. Soft edge: a 2 px band at the boundary takes alpha from distance to white.
    edge = np.asarray(Image.fromarray((~fig * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(5))) > 0
    alpha = np.where(fig, 1.0, 0.0)
    ramp = np.clip((d - SOFT_LO) / (SOFT_HI - SOFT_LO), 0.0, 1.0)
    near = edge & (fig | (np.asarray(Image.fromarray((fig * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(3))) > 0))
    alpha = np.where(near, ramp, alpha)
    # 4. Unpremultiply the white out of the edge colour.
    a = alpha[..., None]
    col = np.where(a > 0.02, (rgb - (1.0 - a) * 255.0) / np.maximum(a, 0.02), 0.0)
    out = np.concatenate([np.clip(col, 0, 255), alpha[..., None] * 255.0], axis=2)
    res = Image.fromarray((out + 0.5).astype(np.uint8), "RGBA")
    return res.crop(res.split()[3].getbbox())


if __name__ == "__main__":
    cutout(Image.open(sys.argv[1])).save(sys.argv[2])
