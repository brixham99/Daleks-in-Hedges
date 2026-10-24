#!/usr/bin/env python3
"""Generate the small effect sprites in assets/fx/ as PNG files.

The game loads them with pygame.image.load(...).convert_alpha(), exactly like
the Archie, Dalek and TARDIS sprites. Run from the repository root:

    python3 tools/make_fx.py
"""
import os
import sys

import numpy as np

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
import pygame  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FX_DIR = os.path.join(ROOT, "assets", "fx")


def radial_glow(radius: int, colour, strength: float = 1.0) -> np.ndarray:
    """(H, W, 4) uint8 RGBA: colour fading out from a whiter centre, alpha to 0 at the rim."""
    size = radius * 2 + 1
    yy, xx = np.mgrid[0:size, 0:size]
    d = np.sqrt((xx - radius) ** 2 + (yy - radius) ** 2) / max(1, radius)
    fall = np.clip(1.0 - d, 0.0, 1.0)
    core = fall ** 3
    rgb = np.array(colour, np.float64)[None, None, :] * (1 - core[..., None]) + 255.0 * core[..., None]
    out = np.zeros((size, size, 4), np.uint8)
    out[..., :3] = rgb.clip(0, 255).astype(np.uint8)
    out[..., 3] = (255.0 * strength * fall ** 1.6).clip(0, 255).astype(np.uint8)
    return out


def save_png(arr: np.ndarray, name: str):
    h, w, _ = arr.shape
    surf = pygame.image.frombuffer(np.ascontiguousarray(arr).tobytes(), (w, h), "RGBA")
    path = os.path.join(FX_DIR, name)
    pygame.image.save(surf, path)
    print("wrote", os.path.relpath(path, ROOT), f"{w}x{h}")


SCALE = 2     # assets are drawn for the 1280x720 full-resolution view (2x the old pixels)
CLOAK_SS = 4  # supersampling per axis for the cloak badge (4x4 = 16 samples a pixel)


def chameleon_cloak(ss: int = CLOAK_SS):
    """Oval badge with a green gem and a charge slot along the bottom.

    Antialiased: every shape is tested at ss x ss points inside each pixel and
    the coverage is averaged (premultiplied), so the curved edges get soft
    partial-alpha pixels instead of hard stair-steps. The shapes and sizes are
    the same as the old hard-edged badge (pixel centres sit on whole numbers).
    """
    W, H = 48, 56
    # Sample coordinates in pixel units: pixel i spans i-0.5 .. i+0.5.
    # (in the badge's original 48x56 units; the output is SCALE times larger)
    sy = (np.arange(H * SCALE * ss) + 0.5) / (ss * SCALE) - 0.5
    sx = (np.arange(W * SCALE * ss) + 0.5) / (ss * SCALE) - 0.5
    yy, xx = np.meshgrid(sy, sx, indexing="ij")
    cx, cy = W / 2, H / 2 - 2
    rgb = np.zeros((H * SCALE * ss, W * SCALE * ss, 3), np.float64)
    alpha = np.zeros((H * SCALE * ss, W * SCALE * ss), np.float64)

    def paint(mask, colour):
        rgb[mask] = colour
        alpha[mask] = 1.0

    body = ((xx - cx) / 18) ** 2 + ((yy - cy) / 22) ** 2 <= 1.0
    rim = ((xx - cx) / 20) ** 2 + ((yy - cy) / 24) ** 2 <= 1.0
    paint(rim, (186, 150, 70))
    paint(body, (48, 58, 72))
    paint(((xx - cx) / 14) ** 2 + ((yy - cy) / 16) ** 2 <= 1.0, (36, 44, 56))
    paint((xx - cx) ** 2 + (yy - cy + 2) ** 2 <= 36, (70, 196, 110))
    paint((xx - cx) ** 2 + (yy - cy + 2) ** 2 <= 12, (180, 255, 200))

    def rect(x0, x1, y0, y1):  # pixel columns x0..x1-1, rows y0..y1-1 (whole pixels, crisp)
        return (xx >= x0 - 0.5) & (xx < x1 - 0.5) & (yy >= y0 - 0.5) & (yy < y1 - 0.5)

    paint(rect(10, W - 10, H - 10, H - 4), (20, 14, 10))
    paint(rect(11, W - 11, H - 9, H - 5), (40, 120, 60))
    for px, py in ((cx - 12, cy - 14), (cx + 12, cy - 14), (cx - 12, cy + 12), (cx + 12, cy + 12)):
        paint((xx - px) ** 2 + (yy - py) ** 2 <= 4, (212, 180, 90))
    # Box-filter down to W x H, premultiplied so edges don't pick up black.
    H, W = H * SCALE, W * SCALE
    pre = (rgb * alpha[..., None]).reshape(H, ss, W, ss, 3).mean(axis=(1, 3))
    a = alpha.reshape(H, ss, W, ss).mean(axis=(1, 3))
    out = np.zeros((H, W, 4), np.uint8)
    out[..., :3] = np.where(a[..., None] > 0, pre / np.maximum(a[..., None], 1e-9), 0).round().clip(0, 255)
    out[..., 3] = (a * 255.0).round().clip(0, 255)
    return out


def main() -> int:
    os.makedirs(FX_DIR, exist_ok=True)
    pygame.init()
    # The TARDIS roof-lamp glow (start and exit dematerialisation).
    save_png(radial_glow(11 * SCALE, (255, 236, 190)), "lamp_glow.png")
    save_png(chameleon_cloak(), "chameleon_cloak.png")
    pygame.quit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
