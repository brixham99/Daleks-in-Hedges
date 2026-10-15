#!/usr/bin/env python3
"""Build Archie's in-game sprites (assets/archie_{se,sw,nw,ne}.png) from the
larger source art in tools/art/, with clean antialiased edges.

    python3 tools/make_archie.py

- archie_se_source.png: Nathan's 3/4 front view (facing SE). SW is its mirror
  (the hoodie is symmetric enough: central pocket, two drawstrings).
- Back views: tools/art/archie_ne_source.png if present (NW is its mirror);
  until then the old pixel-art backs (archie_n?_old.png) go through the same
  smooth downscale so all four facings match in softness.

Edges: art on a white backdrop is unpremultiplied (no white halo); fully
transparent pixels take the nearest solid colour; and scaling is done on premultiplied alpha with a
Lanczos filter, then a light unsharp mask.
"""
import os

import numpy as np
from PIL import Image, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
ART = os.path.join(HERE, "art")
OUT = os.path.join(HERE, "..", "assets")
SPRITE_H = 74      # game.SPRITE_H: the files are made exactly this tall (no runtime rescale)
FIGURE_H = 68      # the figure itself, as tall as the old sprites' figure at this size
PAD_BOTTOM = 3


def defringe(img: Image.Image, white_bg: bool = True) -> Image.Image:
    """Soft edges without a halo: a background-white composite is
    unpremultiplied, then edge pixels borrow the colour of solid neighbours."""
    a = np.asarray(img.convert("RGBA")).astype(np.float64)
    rgb, alpha = a[..., :3], a[..., 3:] / 255.0
    if white_bg:
        # Edge pixels mixed with the white backdrop: C = a*F + (1-a)*255  ->  F
        part = (alpha > 0.02) & (alpha < 0.98)
        fixed = (rgb - (1 - alpha) * 255.0) / np.maximum(alpha, 0.02)
        rgb = np.where(part, np.clip(fixed, 0, 255), rgb)
    # Bleed solid colour outwards into soft/transparent pixels (a few passes).
    solid = alpha[..., 0] > 0.9
    col = rgb.copy()
    known = solid.copy()
    for _ in range(4):
        acc = np.zeros_like(col)
        cnt = np.zeros(known.shape)
        for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (1, 1), (-1, 1), (1, -1)):
            k = np.roll(np.roll(known, dy, 0), dx, 1)
            c = np.roll(np.roll(col, dy, 0), dx, 1)
            acc += c * k[..., None]
            cnt += k
        grow = (~known) & (cnt > 0)
        col[grow] = acc[grow] / cnt[grow][:, None]
        known = known | grow
    soft = alpha[..., 0] < 0.1  # (the soft outline itself keeps its own dark colour)
    rgb[soft] = col[soft]
    out = np.concatenate([rgb, alpha * 255.0], axis=2)
    return Image.fromarray(np.clip(out + 0.5, 0, 255).astype(np.uint8), "RGBA")


def to_sprite(img: Image.Image, sharpen: float = 60) -> Image.Image:
    """Crop to the figure, downscale (premultiplied Lanczos) to FIGURE_H,
    sharpen a touch, and place it on a SPRITE_H canvas, feet PAD_BOTTOM up."""
    img = img.crop(img.split()[3].getbbox())
    w = max(1, round(img.width * FIGURE_H / img.height))
    small = img.convert("RGBa").resize((w, FIGURE_H), Image.LANCZOS, reducing_gap=3.0).convert("RGBA")
    if sharpen:
        rgb = small.convert("RGB").filter(ImageFilter.UnsharpMask(radius=0.8, percent=sharpen, threshold=1))
        small = Image.merge("RGBA", (*rgb.split(), small.split()[3]))
    canvas = Image.new("RGBA", (w + 6, SPRITE_H), (0, 0, 0, 0))
    canvas.paste(small, (3, SPRITE_H - PAD_BOTTOM - FIGURE_H), small)
    return canvas


def main():
    # The supplied art is already cut out: its soft edge pixels are the dark
    # outline (measured darker than the figure, not whiter), so no white to remove.
    front = defringe(Image.open(os.path.join(ART, "archie_se_source.png")), white_bg=False)
    back_src = os.path.join(ART, "archie_ne_source.png")
    if os.path.exists(back_src):
        ne = to_sprite(defringe(Image.open(back_src), white_bg=True))
        nw = ne.transpose(Image.FLIP_LEFT_RIGHT)
    else:
        ne = to_sprite(defringe(Image.open(os.path.join(ART, "archie_ne_old.png")), white_bg=False), sharpen=0)
        nw = to_sprite(defringe(Image.open(os.path.join(ART, "archie_nw_old.png")), white_bg=False), sharpen=0)
    se = to_sprite(front)
    sprites = {"se": se, "sw": se.transpose(Image.FLIP_LEFT_RIGHT), "ne": ne, "nw": nw}
    for key, img in sprites.items():
        path = os.path.join(OUT, f"archie_{key}.png")
        img.save(path)
        print(f"archie_{key}.png  {img.size[0]}x{img.size[1]}")


if __name__ == "__main__":
    main()
