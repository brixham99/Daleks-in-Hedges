#!/usr/bin/env python3
"""Daleks in Hedges — a small isometric hedge-maze game in pygame.

Play:  python3 game.py
Test:  SDL_VIDEODRIVER=dummy python3 game.py --seed 1 --screenshot preview.png --frames 8

Left and right turn Archie. Up steps forward, down steps back.
The maze stays put; the camera scrolls so he stays centred.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
import threading
import time
import ssl
import urllib.error
import urllib.parse
import urllib.request

# Headless runs (dummy video) need no audio device either. Normal runs use
# the real sound card; if the mixer cannot start, the game just stays silent.
if os.environ.get("SDL_VIDEODRIVER") == "dummy":
    os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import numpy as np
import pygame

import net

# Classic 2:1 isometric projection. (tx, ty) is the top vertex of a tile.
#   screen_x = (col - row) * (TILE_W // 2)
#   screen_y = (col + row) * (TILE_H // 2)
TILE_W = 64
TILE_H = 32
HEDGE_H = 40
SPRITE_H = 74
MOVE_MS = 150
TURN_MS = 220
BUMP_MS = 110
WIN_W = 1100
WIN_H = 720
# The world (maze, dirt, Archie) is drawn to a smaller internal surface and
# scaled up to the window, so everything grows together. The HUD (hint,
# minimap, win panel) is drawn afterwards at full window resolution.
ZOOM = 2.0
ZOOM_SMOOTH = False  # True: smoothscale; False: nearest-neighbour scale
MAZE_SIZE = 41  # odd, so a perfect maze has a solid border
WALL, PATH, EXIT = 0, 1, 2

# Clockwise on the unrotated isometric screen: SE, SW, NW, NE.
DIR_SE = (1, 0)
DIR_SW = (0, 1)
DIR_NW = (-1, 0)
DIR_NE = (0, -1)
DIR_ORDER = (DIR_SE, DIR_SW, DIR_NW, DIR_NE)
FACING_SPRITE = {
    DIR_SE: "se",
    DIR_SW: "sw",
    DIR_NW: "nw",
    DIR_NE: "ne",
}

# One back-to-front pass. Archie is inserted when the tiles in front of him
# start, so a near hedge can cover his feet without being drawn twice.
BG_COLOUR = (34, 22, 14)
DIRT_EDGE = (68, 44, 26)
N_DIRT = 16
DIRT_SHADES = (0.90, 0.97, 1.03)

HERE = os.path.dirname(os.path.abspath(__file__))
# Four facing sprites per character (same keys FACING_SPRITE uses).
CHARACTERS = {
    "archie": {
        "name": "Archie",
        "files": {k: os.path.join(HERE, "assets", f"archie_{k}.png") for k in ("se", "sw", "nw", "ne")},
    },
    "holly": {
        "name": "Holly",
        "files": {k: os.path.join(HERE, "assets", f"holly_{k}.png") for k in ("se", "sw", "nw", "ne")},
    },
}
CHAR_ORDER = ("archie", "holly")
TITLE_DALEKS = 10             # decorative Daleks on the title screen

DALEK_FILES = {k: os.path.join(HERE, "assets", f"dalek_{k}.png") for k in ("se", "sw", "ne", "nw")}
TARDIS_FILE = os.path.join(HERE, "assets", "tardis.png")
LAMP_GLOW_FILE = os.path.join(HERE, "assets", "fx", "lamp_glow.png")  # tools/make_fx.py
CLOAK_FILE = os.path.join(HERE, "assets", "fx", "chameleon_cloak.png")

# --- Daleks, the TARDIS and the Chameleon Cloak (pre-zoom pixels, ms) ---
DALEK_H = 86                 # Archie is SPRITE_H = 74
TARDIS_H = 124
NUM_DALEKS = 3               # on level 1; each level adds two more ...
DALEKS_PER_LEVEL = 2
MAX_DALEKS = 15              # ... up to this many (the 41x41 maze stays fair)
DALEK_STEP_MS = 260          # per cell, gliding; Archie takes MOVE_MS = 150
DALEK_TURN_MS = 160          # pause when a Dalek changes direction
DALEK_SIGHT = 8              # cells along the corridor it is facing
DALEK_TELEGRAPH_MS = 350     # eye-stalk glow before the shot
DALEK_MIN_START_DIST = 12    # path distance from Archie's start
DALEK_MIN_EXIT_DIST = 10     # path distance from the exit
LASER_MS = 450               # how long the beam stays on screen
LASER_WIDTHS = (4, 3, 2, 1)  # px on the low-res view (nearest-neighbour 2x to the window)
LASER_COLOURS = ((30, 70, 190), (70, 140, 250), (160, 210, 255), (255, 255, 255))
LASER_HOT = ((40, 95, 225), (110, 175, 255), (200, 232, 255), (255, 255, 255))  # flicker frames
LASER_TIP_GLOW = ((70, 140, 250), (170, 220, 255), (255, 255, 255))  # solid circles, outside in
LASER_HIT_GLOW = ((60, 120, 235), (160, 210, 255), (255, 255, 255))
EYE_GLOW = ((80, 160, 250), (180, 228, 255), (255, 255, 255))
DEATH_FADE_MS = 900          # Archie flickers, whites out and fades
DEATH_MSG_MS = 1000          # then the EXTERMINATED! panel
DEATH_FADE_LEVELS = 16
# (No full-window flash on the shot. The beam and its glows are solid draw
# calls; everything is drawn into an opaque back buffer, never with alpha
# straight onto the window surface - see Game.draw.)
PUFF_LEVELS = 12
DISGUISE_MS = 4000
DISGUISE_COOLDOWN_MS = 3000
POOF_MS = 280
DISGUISE_SCALE = 0.86
# Gun tip and eye-stalk tip in the 160 px source images. SW/NW are exact
# mirror images of SE/NE, so their points are mirrored at load time.
DALEK_GUN_SRC = {"se": (95, 76), "ne": (103, 47)}
DALEK_EYE_SRC = {"se": (79, 30), "ne": (77, 5)}
DALEK_SPACING = (12, 10, 8, 6, 4, 2)  # wanted gap between Daleks, relaxed in turn
# The TARDIS that dropped Archie off dematerialises behind him at the start.
DEMAT_WAIT_MS = 5000         # it stands solid for 5 s before it starts to go
DEMAT_MS = 3600              # effect length with our own sound; the box is gone 300 ms before the end
DEMAT_MIN_MS = 3000          # with a user-supplied sound, the fade follows its length ...
DEMAT_MAX_MS = 15000         # ... clamped to 3-15 s
DEMAT_END_GAP_MS = 300       # the box has fully faded this long before the effect ends
DEMAT_PULSE_MS = 1000        # one fade pulse (and one wheeze-groan) per second (longer for long clips)
DEMAT_LEVELS = 24            # pre-faded copies of the TARDIS (no set_alpha on RGBA)
TARDIS_LAMP_SRC = (59, 11)   # roof lamp in the 119x200 source image
DISGUISE_KEYS = (pygame.K_h, pygame.K_LSHIFT, pygame.K_RSHIFT)
MUTE_KEY = pygame.K_m

# --- Reaching the TARDIS: Archie goes in, it dematerialises, next level ---
EXIT_WALK_MS = 500           # Archie walks on from his corridor into the TARDIS ...
EXIT_FADE_FROM = 0.3         # ... fading out from 30% of the way ...
EXIT_FADE_TO = 0.9           # ... to gone at 90% (normal depth order: hedges and the box hide him)
EXIT_DEMAT_AT = 900          # then the TARDIS dematerialises (same as the start)
EXIT_CARD_GAP_MS = 250       # short pause once it has gone
CARD_FADE_MS = 450           # fade to black, the new maze is made ...
CARD_HOLD_MS = 1000          # ... "Level N" on black ...
LEVEL_BANNER_MS = 2600       # "Level N" banner at the start of a level
COMPLETE_TEXT = "Level complete!"
COMPLETE_SIZE = 20           # plain white text on the low-res view (chunky when 2x scaled)
COMPLETE_Y = 0.2             # its top, as a fraction of the window height (clear of the TARDIS)
COMPLETE_FADE_IN_MS = 300
COMPLETE_FADE_OUT_MS = 400

# --- Sound effects (assets/sounds, made by tools/make_sounds.py) ---
SOUND_DIR = os.path.join(HERE, "assets", "sounds")
SOUND_FILES = {
    "ow": "ow.wav",
    "exterminate": "exterminate.wav",
    "laser": "laser.wav",
    "step1": "step1.wav",
    "step2": "step2.wav",
    "step3": "step3.wav",
}
SOUND_FILES.update({"rustle": "rustle.wav", "cloak_on": "cloak_on.wav", "cloak_off": "cloak_off.wav",
                    "tardis_demat": "tardis_demat.wav"})
# Optional user-supplied TARDIS sound (not included, git-ignored). The first of
# these that pygame can load replaces our synthesised tardis_demat.wav.
TARDIS_REAL_FILES = ("tardis_real.wav", "tardis_real.ogg", "tardis_real.mp3")
TARDIS_REAL_VOLUME = 0.7
SOUND_VOLUME = {
    "ow": 0.28, "exterminate": 0.9, "laser": 0.7,
    "step1": 0.45, "step2": 0.45, "step3": 0.45,
    "rustle": 0.30, "cloak_on": 0.5, "cloak_off": 0.5,
    "tardis_demat": 0.6,
}
STEP_SOUNDS = ("step1", "step2", "step3")
# Ambient Dalek callouts: now and then during play the nearest Dalek says one
# of these, as loud as its hum would be (HUM_FAR/HUM_NEAR/HUM_CURVE, straight-
# line distance), so a far-off Dalek is barely heard. "Exterminate!" is kept
# for the moment a Dalek spots Archie, so it always means danger.
TAUNT_FILES = {
    "dalek_human_detected": "dalek_human_detected.wav",
    "dalek_destroy": "dalek_destroy.wav",
    "dalek_find_the_human": "dalek_find_the_human.wav",
}
SOUND_FILES.update(TAUNT_FILES)
SOUND_VOLUME.update({name: 1.0 for name in TAUNT_FILES})  # loudness is set per play on the channel
TAUNT_SOUNDS = tuple(TAUNT_FILES)
TAUNT_MIN_MS = 30000         # a callout every 30 ..
TAUNT_MAX_MS = 45000         # .. 45 s (random each time), counted from the level start
TAUNT_MAX = 0.9              # channel volume with a Dalek right next to Archie (as loud as "Exterminate!")
TAUNT_RETRY_MS = 2500        # due while another voice is speaking or a Dalek is aiming: try again shortly
OW_BUMPS = 5                 # "ow" on the 5th hedge bump ...
OW_WINDOW_MS = 2500          # ... within 2.5 s; single bumps only rustle
OW_QUIET_MS = 3000           # after an "ow", bumps just rustle for a while
BUMP_COUNT_GAP_MS = 250      # held-key repeats count, but at most 4 a second
RUSTLE_GAP_MS = 450          # the soft rustle at most about twice a second
# Dalek proximity hum: one looping channel per Dalek, reserved so effects
# never steal it. Loud only when a Dalek is gliding close by; it fades out
# while the Dalek turns or stands still (aiming, firing, blocked).
HUM_FILE = "dalek_hum.wav"
TITLE_THEME_FILE = "title_theme.wav"
TITLE_THEME_VOLUME = 0.55
TITLE_COPYRIGHT = (
    "©2026 Nathan, Archie & Holly Anderson. Doctor Who, the TARDIS, the Daleks, "
    "and related sound effects are trademarks and copyright of the BBC/BBC Studios. "
    "Dalek is also associated with the estate of Terry Nation. All rights in those "
    "works remain with their respective owners."
)
HUM_CHANNELS = 4             # reserved hum channels, given to the loudest Daleks
HUM_MAX = 0.5                # channel volume when a Dalek is right next to Archie
HUM_FAR = 25.0               # silent at this effective distance (cells) ...
HUM_NEAR = 1.0               # ... full volume at this one or closer
HUM_CURVE = 1.0              # volume = closeness ** 1.0 (linear): faint but clear far off, louder up close
HUM_STOP_DIST = 28.0         # beyond this (and silent) its channel is stopped
HUM_PATH_WEIGHT = 0.0        # 0: straight-line distance only; heard through hedges, any route
HUM_SUM_MAX = 0.8            # several Daleks at once: all hums scale down so their sum stays below this
HUM_GAIN_SMOOTH_MS = 250     # (that scaling changes smoothly)
HUM_SWAP_MS = 120            # a channel crossfades to a louder Dalek over this long ...
HUM_SWAP_MARGIN = 1.3        # ... once the newcomer is this much louder than the quietest one
HUM_FADE_OUT_MS = 80         # quick fade when a Dalek stops to turn (pause is 160 ms)
HUM_FADE_IN_MS = 110         # and back up as it glides off again
HUM_EXIT_FADE_MS = 1000      # gentle fade as the Daleks freeze when Archie reaches the TARDIS
HUM_DIST_SMOOTH_MS = 100     # distance changes are smoothed too
HUM_PAN = 0.45               # subtle stereo: at most 45% off the far side
MIXER_FREQ = 22050
MIXER_BUFFER = 512           # ~23 ms at 22050 Hz: small, so sounds aren't laggy
RESTART_KEYS = (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_r, pygame.K_SPACE)

# Score: a point for every second spent on the move (stepping between cells,
# not standing, turning on the spot, cloaked or bumping a hedge), a bonus for
# each new level, and a bonus each time a Dalek spots you and shouts.
SCORE_PER_SECOND = 1
SCORE_PER_LEVEL = 50
SCORE_PER_SPOT = 20          # a Dalek sees you and shouts "Exterminate!"
MOVE_GRACE_MS = 400          # time still counts this long after a step lands (tap gaps)
BONUS_POP_MS = 1100          # the "+20" pop beside the Score box
NAME_MAX = 8                 # leaderboard names: letters, digits and spaces
TITLE_PANEL_MS = 7000        # title alternates: character choice <-> Top 10, 7 s each
TOP_N = 10
# Hosted leaderboard (dreamlo.com, free tier). Free dreamlo boards are
# client-side by design: the private code that adds scores ships in the game.
DREAMLO_PUBLIC = "6ac7b3018f40bc15a8400bbc"
DREAMLO_PRIVATE = "ywhrA7pmp0ivMlVZTqZoTwfNbQztSs2kSLvG6ZGQ53OQ"
DREAMLO_URL = "http://dreamlo.com/lb/"
DREAMLO_TIMEOUT = 4.0        # seconds; every request runs on a background thread
SAVE_DIR = os.path.join(os.path.expanduser("~"), ".daleks_in_hedges")
FLAG_DIR = os.path.join(SAVE_DIR, "flags")  # flag images, downloaded once each
COUNTRY_URLS = ("https://ipinfo.io/country", "https://ipapi.co/country/")  # no key; first that answers
COUNTRY_TIMEOUT = 2.0
FLAG_URL = "https://flagcdn.com/w20/{code}.png"  # 20x10 PNG: one low-res row tall
FLAG_TIMEOUT = 3.0
SAVE_FILE = os.path.join(SAVE_DIR, "scores.json")  # last name, local best, cached Top 10, unsent scores

# Two players on a local network (see net.py for the transport).
NET_KEY = pygame.K_n           # title screen: open the network lobby
NET_SAFE_RADIUS = 6            # path cells round the start TARDIS that Daleks never enter
NET_SMOOTH_MS = 60             # the other player and the Daleks ease towards each update
NET_WATCH_PAN_MS = 600         # camera glide to the player you are watching
NET_NOTICE_MS = 3500           # "Connection lost" stays up this long
NET_JOIN_WAIT_S = 6.0          # no answer from a host in this long: back to the list
DALEK_STATES = ("roam", "aim", "fire")

KEY_ACTIONS = {
    pygame.K_LEFT: ("turn", -1),
    pygame.K_a: ("turn", -1),
    pygame.K_RIGHT: ("turn", 1),
    pygame.K_d: ("turn", 1),
    pygame.K_UP: ("step", 1),
    pygame.K_w: ("step", 1),
    pygame.K_DOWN: ("step", -1),
    pygame.K_s: ("step", -1),
}


def daleks_for_level(level: int) -> int:
    """3 on level 1, two more each level, capped at MAX_DALEKS."""
    return min(MAX_DALEKS, NUM_DALEKS + DALEKS_PER_LEVEL * (max(1, level) - 1))


def ease(t: float) -> float:
    t = 0.0 if t < 0.0 else 1.0 if t > 1.0 else t
    return t * t * (3.0 - 2.0 * t)


def mix(colour, factor: float):
    return tuple(max(0, min(255, int(c * factor))) for c in colour)


def tile_origin(col: float, row: float):
    return (
        (col - row) * (TILE_W // 2),
        (col + row) * (TILE_H // 2),
    )


def diamond_points(tx: float, ty: float):
    hw = TILE_W // 2
    hh = TILE_H // 2
    return (
        (tx, ty - 1),
        (tx + hw + 1, ty + hh),
        (tx, ty + TILE_H + 1),
        (tx - hw - 1, ty + hh),
    )


def generate_maze(size: int, seed: int):
    """Perfect maze via iterative recursive backtracker.

    Odd cells are rooms; carving steps by two and opens the wall between.
    The border stays solid hedge. Start is (1, 1); the exit is the open
    cell at the opposite corner.
    """
    if size % 2 == 0 or size < 5:
        raise ValueError("maze size must be an odd integer >= 5")
    grid = [[WALL for _ in range(size)] for _ in range(size)]
    rng_state = seed & 0xFFFFFFFF

    def rnd() -> int:
        nonlocal rng_state
        rng_state = (rng_state * 1664525 + 1013904223) & 0xFFFFFFFF
        return rng_state

    def shuffled_dirs():
        dirs = [(2, 0), (-2, 0), (0, 2), (0, -2)]
        for i in range(3, 0, -1):
            j = rnd() % (i + 1)
            dirs[i], dirs[j] = dirs[j], dirs[i]
        return dirs

    grid[1][1] = PATH
    stack = [(1, 1)]
    while stack:
        col, row = stack[-1]
        carved = False
        for dc, dr in shuffled_dirs():
            nc, nr = col + dc, row + dr
            if 1 <= nc < size - 1 and 1 <= nr < size - 1 and grid[nr][nc] == WALL:
                grid[row + dr // 2][col + dc // 2] = PATH
                grid[nr][nc] = PATH
                stack.append((nc, nr))
                carved = True
                break
        if not carved:
            stack.pop()

    exit_c, exit_r = size - 2, size - 2
    grid[exit_r][exit_c] = EXIT
    return grid


def load_font(size: int, bold: bool = False) -> pygame.font.Font:
    try:
        font = pygame.font.SysFont("dejavusans", size, bold=bold)
        if font is not None:
            return font
    except (OSError, pygame.error):
        pass
    return pygame.font.Font(None, size)


def _tileable_noise(size: int, cells: int, rng: np.random.Generator) -> np.ndarray:
    grid = rng.random((cells, cells))
    ys = np.linspace(0, cells, size, endpoint=False)
    xs = np.linspace(0, cells, size, endpoint=False)
    x0 = np.floor(xs).astype(np.int32) % cells
    y0 = np.floor(ys).astype(np.int32) % cells
    xf = xs - np.floor(xs)
    yf = ys - np.floor(ys)
    xf = xf * xf * (3.0 - 2.0 * xf)
    yf = yf * yf * (3.0 - 2.0 * yf)
    x1 = (x0 + 1) % cells
    y1 = (y0 + 1) % cells
    g00 = grid[np.ix_(y0, x0)]
    g10 = grid[np.ix_(y0, x1)]
    g01 = grid[np.ix_(y1, x0)]
    g11 = grid[np.ix_(y1, x1)]
    top = g00 * (1.0 - xf)[None, :] + g10 * xf[None, :]
    bot = g01 * (1.0 - xf)[None, :] + g11 * xf[None, :]
    return top * (1.0 - yf)[:, None] + bot * yf[:, None]


def make_dirt_texture(size: int = 160) -> pygame.Surface:
    """One tileable mottled-soil texture: browns, clumps, pebbles, specks."""
    rng = np.random.default_rng(7)
    n1 = _tileable_noise(size, 6, rng)
    n2 = _tileable_noise(size, 14, rng)
    n3 = _tileable_noise(size, 28, rng)
    value = np.clip(0.52 * n1 + 0.30 * n2 + 0.18 * n3, 0.0, 1.0)
    dark = np.array([86, 56, 34], dtype=np.float32)
    mid = np.array([138, 94, 54], dtype=np.float32)
    light = np.array([172, 128, 76], dtype=np.float32)
    t = value[..., None]
    colour = dark * (1.0 - t) + mid * t
    peak = np.clip((value - 0.58) / 0.42, 0.0, 1.0)[..., None]
    colour = colour * (1.0 - 0.65 * peak) + light * (0.65 * peak)
    clump = _tileable_noise(size, 4, rng)
    damp = np.clip((clump - 0.55) / 0.45, 0.0, 1.0)[..., None]
    soil = np.array([58, 36, 22], dtype=np.float32)
    colour = colour * (1.0 - 0.45 * damp) + soil * (0.45 * damp)
    img = np.clip(colour, 0, 255).astype(np.uint8)
    for _ in range(220):
        x = int(rng.integers(0, size))
        y = int(rng.integers(0, size))
        if rng.random() < 0.55:
            tint = (
                int(rng.integers(108, 156)),
                int(rng.integers(102, 140)),
                int(rng.integers(88, 120)),
            )
            rad = 1 if rng.random() < 0.75 else 2
        else:
            tint = (
                int(rng.integers(70, 120)),
                int(rng.integers(46, 78)),
                int(rng.integers(28, 48)),
            )
            rad = 0
        for dy in range(-rad, rad + 1):
            for dx in range(-rad, rad + 1):
                if dx * dx + dy * dy > rad * rad:
                    continue
                img[(y + dy) % size, (x + dx) % size] = tint
    # Fine specks, wrapping so the texture still tiles.
    speck = rng.random((size, size))
    hi = speck > 0.992
    lo = (speck > 0.984) & ~hi
    img[hi] = np.array([186, 154, 110], dtype=np.uint8)
    img[lo] = np.array([64, 42, 28], dtype=np.uint8)
    surf = pygame.surfarray.make_surface(np.transpose(img, (1, 0, 2)))
    return surf.convert()


def _feet_anchor(image: pygame.Surface):
    rect = image.get_bounding_rect(min_alpha=16)
    band = max(4, rect.height // 8)
    xs = []
    y0 = max(rect.top, rect.bottom - band)
    for y in range(y0, rect.bottom):
        for x in range(rect.left, rect.right):
            if image.get_at((x, y)).a > 16:
                xs.append(x)
    ax = (sum(xs) / len(xs)) if xs else rect.centerx
    return ax, rect.bottom


def build_dirt_variants(texture: pygame.Surface):
    """Clip the dirt texture into diamond tiles with varied sample offsets."""
    tw, th = texture.get_size()
    sw, sh = 72, 40
    cx, cy = sw / 2, sh / 2
    hw = TILE_W // 2
    hh = TILE_H // 2
    raw = (
        (cx, cy - hh - 1),
        (cx + hw + 1, cy),
        (cx, cy + hh + 1),
        (cx - hw - 1, cy),
    )

    def grow(points, amount):
        out = []
        for x, y in points:
            dx, dy = x - cx, y - cy
            length = math.hypot(dx, dy) or 1.0
            out.append((x + dx / length * amount, y + dy / length * amount))
        return out

    fill_pts = grow(raw, 1.6)
    mask = _new_rgba((sw, sh))
    pygame.draw.polygon(mask, (255, 255, 255, 255), fill_pts)
    mask_alpha = pygame.surfarray.array_alpha(mask)
    variants = []
    for i in range(N_DIRT):
        ox = (i * 53 + 17) % tw
        oy = (i * 37 + 11) % th
        patch = pygame.Surface((sw + tw, sh + th))
        patch.blit(texture, (0, 0))
        patch.blit(texture, (tw, 0))
        patch.blit(texture, (0, th))
        patch.blit(texture, (tw, th))
        base = _new_rgba((sw, sh))
        base.blit(patch, (-ox, -oy))
        # The diamond's alpha copied in directly (no blend-mode blits).
        alpha = pygame.surfarray.pixels_alpha(base)
        alpha[:] = mask_alpha
        del alpha
        pygame.draw.polygon(base, DIRT_EDGE, raw, 1)
        shades = []
        for factor in DIRT_SHADES:
            shaded = base.copy()
            rgb = pygame.surfarray.pixels3d(shaded)
            rgb[:] = np.clip(rgb.astype(np.float32) * factor, 0, 255).astype(np.uint8)
            del rgb
            shades.append(_finish_rgba(shaded))
        variants.append(shades)
    return variants


N_HEDGE = 16
N_HEDGE_SHADES = 8
HEDGE_FOG_LO = 0.88  # same range as Game._fog
HEDGE_FOG_HI = 1.02
HEDGE_PAD_X = 6
HEDGE_PAD_TOP = 9
HEDGE_PAD_BOT = 4
HEDGE_SW = TILE_W + 2 * HEDGE_PAD_X
HEDGE_SH = HEDGE_PAD_TOP + HEDGE_H + TILE_H + HEDGE_PAD_BOT
HEDGE_OX = HEDGE_PAD_X + TILE_W // 2          # sprite x of the tile's top vertex
HEDGE_OY = HEDGE_PAD_TOP + HEDGE_H            # sprite y of the tile's top vertex
_SIDE = 32.0                                   # world units along one tile edge

# View-space normals (x right, y down, z toward the viewer) and the light.
_N_TOP = np.array([0.0, -0.866, 0.5])
_N_LEFT = np.array([-0.707, 0.354, 0.612])
_N_RIGHT = np.array([0.707, 0.354, 0.612])
_LIGHT = np.array([0.38, -0.72, 0.58])
_LIGHT = _LIGHT / np.linalg.norm(_LIGHT)


def _project(u, v, w):
    sx = HEDGE_OX + (u - v) * (TILE_W / 2) / _SIDE
    sy = HEDGE_OY + (u + v) * (TILE_H / 2) / _SIDE - w
    depth = 0.866 * (u + v) + 0.577 * w
    return sx, sy, depth


def _mounds(rng, count, amp):
    cu = rng.uniform(-4, _SIDE + 4, count)
    cv = rng.uniform(-4, _SIDE + 4, count)
    sig = rng.uniform(5.0, 10.0, count)
    a = rng.uniform(-0.4, 1.0, count) * amp

    def field(u, v):
        out = np.zeros_like(u)
        for i in range(count):
            out += a[i] * np.exp(-((u - cu[i]) ** 2 + (v - cv[i]) ** 2) / (2 * sig[i] ** 2))
        return out
    return field


def render_hedge_variant(index: int, seed: int = 4242) -> np.ndarray:
    """One leafy box-hedge block as an RGBA array (H, W, 4)."""
    rng = np.random.default_rng(seed + index * 7919)
    W, H = HEDGE_SW, HEDGE_SH
    # Per-variant tint: hue leans yellow or blue, brightness wobbles.
    warm = rng.uniform(-0.55, 0.55)
    bright = rng.uniform(0.955, 1.045)
    leaf_dark = np.array([34, 74, 30], dtype=np.float64)
    leaf_light = np.array([112, 166, 66], dtype=np.float64)
    leaf_dark += np.array([6, 2, -4]) * warm
    leaf_light += np.array([16, 6, -10]) * warm
    leaf_dark *= bright
    leaf_light *= bright

    top_bump = _mounds(rng, 5, 3.2)
    left_bump = _mounds(rng, 4, 1.8)
    right_bump = _mounds(rng, 4, 1.8)

    leaves = []  # (u, v, w, face, offset)

    def add(face, n):
        o = rng.uniform(-2.8, 1.6, n)
        if face == 0:     # top: plane w = HEDGE_H
            u = rng.uniform(-1.2, _SIDE + 1.2, n)
            v = rng.uniform(-1.2, _SIDE + 1.2, n)
            w = HEDGE_H + top_bump(u, v) + o
        elif face == 1:   # left (SW) face: plane v = SIDE
            u = rng.uniform(-0.8, _SIDE + 0.8, n)
            w = rng.uniform(-1.0, HEDGE_H + 0.5, n)
            v = _SIDE + left_bump(u, w) * 0.7 + o * 0.8
        else:             # right (SE) face: plane u = SIDE
            v = rng.uniform(-0.8, _SIDE + 0.8, n)
            w = rng.uniform(-1.0, HEDGE_H + 0.5, n)
            u = _SIDE + right_bump(v, w) * 0.7 + o * 0.8
        leaves.append((u, v, w, np.full(n, face), o))

    add(0, 330)
    add(1, 430)
    add(2, 430)
    u = np.concatenate([l[0] for l in leaves])
    v = np.concatenate([l[1] for l in leaves])
    w = np.concatenate([l[2] for l in leaves])
    face = np.concatenate([l[3] for l in leaves])
    off = np.concatenate([l[4] for l in leaves])
    n = u.size
    sx, sy, depth = _project(u, v, w)

    # Leaf ellipse shape.
    a = rng.uniform(1.7, 3.1, n)
    b = a * rng.uniform(0.45, 0.75, n)
    th = rng.uniform(0, math.pi, n)
    # Leaves on the side faces hang a little more vertically.
    side = face > 0
    th[side] = rng.normal(math.pi / 2, 0.6, side.sum())
    ct, st = np.cos(th), np.sin(th)

    R = 4
    dy, dx = np.mgrid[-R:R + 1, -R:R + 1]
    dx = dx.ravel()[None, :]
    dy = dy.ravel()[None, :]
    ix = np.floor(sx)[:, None].astype(np.int64) + dx
    iy = np.floor(sy)[:, None].astype(np.int64) + dy
    px = ix + 0.5 - sx[:, None]
    py = iy + 0.5 - sy[:, None]
    lx = px * ct[:, None] + py * st[:, None]
    ly = -px * st[:, None] + py * ct[:, None]
    d2 = (lx / a[:, None]) ** 2 + (ly / b[:, None]) ** 2
    inside = (d2 < 1.0) & (ix >= 0) & (ix < W) & (iy >= 0) & (iy < H)
    hz = np.sqrt(np.clip(1.0 - d2, 0.0, 1.0))
    # Ellipsoid normal in leaf space, rotated back to screen.
    gx = lx / a[:, None] ** 2
    gy = ly / b[:, None] ** 2
    nx = gx * ct[:, None] - gy * st[:, None]
    ny = gx * st[:, None] + gy * ct[:, None]
    nz = hz / b[:, None]
    face_n = np.stack([_N_TOP, _N_LEFT, _N_RIGHT])[face]          # (n, 3)
    nnorm = np.sqrt(nx * nx + ny * ny + nz * nz) + 1e-9
    k_leaf = 0.75
    Nx = face_n[:, 0:1] + k_leaf * nx / nnorm
    Ny = face_n[:, 1:2] + k_leaf * ny / nnorm
    Nz = face_n[:, 2:3] + k_leaf * nz / nnorm
    L = np.sqrt(Nx * Nx + Ny * Ny + Nz * Nz)
    ndl = (Nx * _LIGHT[0] + Ny * _LIGHT[1] + Nz * _LIGHT[2]) / L
    shade = 0.30 + 0.80 * np.clip(ndl, 0.0, 1.0)
    # Rim: the middle of each leaf a touch brighter, its edge darker.
    shade *= 0.80 + 0.20 * hz

    # Leaf colour.
    t = np.clip(rng.beta(2.2, 2.0, n), 0, 1)
    face_gain = np.where(face == 0, 1.04, np.where(face == 1, 0.70, 0.84))
    ao = 0.55 + 0.45 * np.clip((off + 2.8) / 4.4, 0, 1)
    ground = np.where(face > 0, 0.55 + 0.45 * np.clip(w / 16.0, 0, 1), 1.0)
    jitter = rng.uniform(0.9, 1.1, n)
    base = leaf_dark[None, :] * (1 - t[:, None]) + leaf_light[None, :] * t[:, None]
    # A few young, yellow-green leaves on top.
    young = (face == 0) & (rng.random(n) < 0.08)
    base[young] = base[young] * 0.6 + np.array([170, 196, 92]) * 0.4
    gain = (face_gain * ao * ground * jitter)[:, None] * shade
    cr = base[:, 0:1] * gain
    cg = base[:, 1:2] * gain
    cb = base[:, 2:3] * gain
    zd = depth[:, None] + hz * b[:, None] * 0.9

    # Resolve with a z-buffer: sort by depth, last write wins.
    m = inside.ravel()
    flat = (iy * W + ix).ravel()[m]
    zz = np.broadcast_to(zd, inside.shape).ravel()[m]
    rgb = np.stack([cr.ravel()[m], cg.ravel()[m], cb.ravel()[m]], axis=1)
    order = np.lexsort((zz, flat))
    flat_s = flat[order]
    last = np.ones(flat_s.size, dtype=bool)
    last[:-1] = flat_s[1:] != flat_s[:-1]
    pick = order[last]

    img = np.zeros((H * W, 4), dtype=np.float64)
    # Base: the dark interior of the hedge so gaps read as deep shade.
    yy, xx = np.mgrid[0:H, 0:W]
    xr = xx + 0.5 - HEDGE_OX
    yr = yy + 0.5 - HEDGE_OY
    # top plane
    su = (xr + 2 * (yr + HEDGE_H)) * _SIDE / TILE_W * 1.0
    sv = (-xr + 2 * (yr + HEDGE_H)) * _SIDE / TILE_W * 1.0
    top_in = (su >= 0) & (su <= _SIDE) & (sv >= 0) & (sv <= _SIDE)
    # left face v = SIDE: x = (u - SIDE) * 32/SIDE
    lu = xr * _SIDE / (TILE_W / 2) + _SIDE
    lw = (lu + _SIDE) * (TILE_H / 2) / _SIDE - yr
    left_in = (lu >= 0) & (lu <= _SIDE) & (lw >= 0) & (lw <= HEDGE_H)
    rv = _SIDE - xr * _SIDE / (TILE_W / 2)
    rw = (_SIDE + rv) * (TILE_H / 2) / _SIDE - yr
    right_in = (rv >= 0) & (rv <= _SIDE) & (rw >= 0) & (rw <= HEDGE_H)
    under = np.zeros((H, W, 3))
    under[left_in] = leaf_dark * 0.30
    under[right_in] = leaf_dark * 0.42
    under[top_in] = leaf_dark * 0.55
    base_mask = (top_in | left_in | right_in).ravel()
    img[base_mask, :3] = under.reshape(-1, 3)[base_mask]
    img[base_mask, 3] = 255
    img[flat[pick], 0] = rgb[pick, 0]
    img[flat[pick], 1] = rgb[pick, 1]
    img[flat[pick], 2] = rgb[pick, 2]
    img[flat[pick], 3] = 255
    return np.clip(img, 0, 255).astype(np.uint8).reshape(H, W, 4)


def _new_rgba(size) -> pygame.Surface:
    """A blank per-pixel-alpha surface: 32-bit, explicitly cleared to fully
    transparent (never relying on how new memory is initialised)."""
    surf = pygame.Surface((max(1, int(size[0])), max(1, int(size[1]))), pygame.SRCALPHA, 32)
    surf.fill((0, 0, 0, 0))
    return surf


def _finish_rgba(surf: pygame.Surface) -> pygame.Surface:
    """Converted to the display's per-pixel-alpha format (the same path the
    sprites take from image.load().convert_alpha()), so blits behave the same
    on every pygame/SDL build. Before a display exists it is left as it is."""
    if pygame.display.get_init() and pygame.display.get_surface() is not None:
        return surf.convert_alpha()
    return surf


def _rgba_from_array(arr) -> pygame.Surface:
    """Surface from an explicit (H, W, 4) uint8 RGBA array: every pixel's alpha
    is written by us, then converted like the sprites."""
    arr = np.ascontiguousarray(arr, dtype=np.uint8)
    H, W, _ = arr.shape
    surf = pygame.image.frombuffer(arr.tobytes(), (W, H), "RGBA")
    return _finish_rgba(surf) if pygame.display.get_surface() is not None else surf.copy()


def _rgba_surface(arr):
    return _rgba_from_array(arr)


def build_hedge_variants():
    """Pre-render the leafy hedge blocks, each in a few fog shades.

    Returns variants[v][shade] surfaces, blitted with the tile's top vertex
    at (HEDGE_OX, HEDGE_OY). Footprint and height match the old flat block.
    """
    variants = []
    levels = np.linspace(HEDGE_FOG_LO, HEDGE_FOG_HI, N_HEDGE_SHADES)
    for i in range(N_HEDGE):
        arr = render_hedge_variant(i)
        shades = []
        for k in levels:
            shaded = arr.copy()
            shaded[..., :3] = np.clip(arr[..., :3].astype(np.float32) * k, 0, 255).astype(np.uint8)
            shades.append(_rgba_surface(shaded).convert_alpha())
        variants.append(shades)
    return variants


def hedge_variant_grid(grid, seed: int):
    """Deterministic variant per hedge cell from a hash of (col, row, seed).

    A cell never repeats the variant of any already-chosen neighbour
    (W, N, NW, NE), so no two touching hedges are the same block.
    """
    size = len(grid)
    out = [[-1] * size for _ in range(size)]
    for row in range(size):
        for col in range(size):
            if grid[row][col] != WALL:
                continue
            h = (col * 73856093) ^ (row * 19349663) ^ (seed * 83492791)
            h = (h ^ (h >> 13)) * 0x5BD1E995 & 0xFFFFFFFF
            h ^= h >> 15
            v = h % N_HEDGE
            taken = set()
            for dc, dr in ((-1, 0), (0, -1), (-1, -1), (1, -1), (-2, 0), (0, -2)):
                c, r = col + dc, row + dr
                if 0 <= c < size and 0 <= r < size:
                    taken.add(out[r][c])
            while v in taken:
                v = (v + 7) % N_HEDGE
            out[row][col] = v
    return out


def _despill(image: pygame.Surface) -> pygame.Surface:
    """Remove the magenta matte fringe left round the cut-out sprites."""
    rgb = pygame.surfarray.pixels3d(image)
    alpha = pygame.surfarray.pixels_alpha(image)
    r = rgb[..., 0].astype(np.int16)
    g = rgb[..., 1].astype(np.int16)
    b = rgb[..., 2].astype(np.int16)
    spill = np.minimum(r, b) - g
    fix = (spill > 0) & (alpha > 0) & ((alpha < 255) | (spill > 30))
    sp = np.where(fix, spill, 0)
    rgb[..., 0] = np.clip(r - sp, 0, 255).astype(np.uint8)
    rgb[..., 2] = np.clip(b - sp, 0, 255).astype(np.uint8)
    del rgb, alpha
    return image


def _base_anchor(image: pygame.Surface):
    """Centre of an isometric base: midway between the lowest points of the
    leftmost and rightmost opaque columns in the bottom third."""
    a = pygame.surfarray.array_alpha(image) > 64
    w, h = a.shape
    y0 = int(h * 0.66)
    xs, ys = np.nonzero(a[:, y0:])
    left, right = xs.min(), xs.max()
    yl = ys[xs == left].max() + y0
    yr = ys[xs == right].max() + y0
    return (left + right + 1) / 2.0, (yl + yr + 1) / 2.0


def _load_scaled(path: str, height: int):
    src = pygame.image.load(path).convert_alpha()
    _despill(src)
    k = height / src.get_height()
    w = max(1, round(src.get_width() * k))
    return src, _finish_rgba(pygame.transform.smoothscale(src, (w, height))), k


def _alpha_scaled(image: pygame.Surface, k: float) -> pygame.Surface:
    """Copy of a per-pixel-alpha surface with its alpha multiplied by k."""
    out = image.copy()
    alpha = pygame.surfarray.pixels_alpha(out)
    alpha[:] = (pygame.surfarray.array_alpha(image).astype(np.float32) * k).astype(np.uint8)
    del alpha
    return out


def _shadowed_text_fades(font: pygame.font.Font, text: str, levels: int):
    """Plain white text with a soft dark drop shadow, composited once into a
    per-pixel-alpha surface; returns pre-faded copies, index 0 invisible and
    the last one solid. Drawn straight onto the window: no scaling, no wobble."""
    glyphs = font.render(text, True, (255, 255, 255))
    w, h = glyphs.get_size()
    pad, off = 4, 2
    W, H = w + pad * 2, h + pad * 2
    a_text = np.zeros((W, H), np.float32)
    a_text[pad:pad + w, pad:pad + h] = pygame.surfarray.array_alpha(glyphs) / 255.0
    # Shadow: the glyph alpha, offset down-right and softened with a 3x3 box blur.
    a_sh = np.zeros((W, H), np.float32)
    a_sh[pad + off:pad + off + w, pad + off:pad + off + h] = pygame.surfarray.array_alpha(glyphs)[:W - pad - off, :H - pad - off] / 255.0
    p = np.pad(a_sh, 1)
    a_sh = sum(p[1 + dx:1 + dx + W, 1 + dy:1 + dy + H] for dx in (-1, 0, 1) for dy in (-1, 0, 1)) / 9.0
    a_sh *= 0.7
    a_out = a_text + a_sh * (1.0 - a_text)
    lum = np.where(a_out > 0, 255.0 * a_text / np.maximum(a_out, 1e-6), 0.0)
    arr = np.zeros((H, W, 4), np.uint8)
    arr[..., :3] = np.clip(lum, 0, 255).astype(np.uint8).T[:, :, None]
    arr[..., 3] = np.clip(a_out * 255.0 + 0.5, 0, 255).astype(np.uint8).T
    out = _rgba_from_array(arr)
    return [_alpha_scaled(out, i / (levels - 1)) for i in range(levels)]


def _faded_silhouettes(image: pygame.Surface, levels: int):
    """White silhouettes of a sprite: index 0 opaque, the last one invisible."""
    white = image.copy()
    rgb = pygame.surfarray.pixels3d(white)
    rgb[:] = 255
    del rgb
    return [_alpha_scaled(white, 1.0 - i / (levels - 1)) for i in range(levels)]


def _make_glow(radius: int, colour, strength: float = 1.0) -> pygame.Surface:
    """Soft radial glow as a per-pixel-alpha sprite, blitted normally (no blend
    flags): colour fading out from a whiter centre, alpha falling to 0."""
    size = radius * 2 + 1
    yy, xx = np.mgrid[0:size, 0:size]
    d = np.sqrt((xx - radius) ** 2 + (yy - radius) ** 2) / max(1, radius)
    fall = np.clip(1.0 - d, 0.0, 1.0)
    core = fall ** 3
    rgb = np.array(colour, dtype=np.float64)[None, None, :] * (1 - core[..., None]) + 255.0 * core[..., None]
    arr = np.zeros((size, size, 4), np.uint8)  # (row, col, RGBA)
    arr[..., :3] = rgb.clip(0, 255).astype(np.uint8)
    arr[..., 3] = (255.0 * strength * fall ** 1.6).clip(0, 255).astype(np.uint8)
    return _rgba_from_array(arr)


class Sounds:
    """Small, fail-safe wrapper round pygame.mixer.

    If the mixer cannot start or a file is missing, play() is a no-op, so the
    game runs silently instead of crashing. `requested` counts every event
    (handy for tests); `played` counts the ones actually sent to the mixer.
    """

    def __init__(self):
        self.ok = False
        self.muted = False
        self.sounds = {}
        self.requested = {}
        self.played = {}
        self.last_step = None
        self._rng = random.Random(4)
        self.hum = None
        self.hum_channels = []
        self.hum_levels = [(0.0, 0.0)] * HUM_CHANNELS  # last (left, right) per hum channel
        self.demat_source = "tardis_demat.wav"       # or the user's tardis_real.* file
        try:
            if not pygame.mixer.get_init():
                pygame.mixer.init(MIXER_FREQ, -16, 2, MIXER_BUFFER)
            self.ok = bool(pygame.mixer.get_init())
        except Exception:
            self.ok = False
        if not self.ok:
            return
        try:
            pygame.mixer.set_num_channels(16)
            pygame.mixer.set_reserved(HUM_CHANNELS)
            self.hum_channels = [pygame.mixer.Channel(i) for i in range(HUM_CHANNELS)]
        except Exception:
            self.hum_channels = []
        try:
            self.hum = pygame.mixer.Sound(os.path.join(SOUND_DIR, HUM_FILE))
        except Exception:
            self.hum = None
        self.title_theme = None
        self.title_channel = None
        self.want_title = False
        try:
            self.title_theme = pygame.mixer.Sound(os.path.join(SOUND_DIR, TITLE_THEME_FILE))
            self.title_theme.set_volume(TITLE_THEME_VOLUME)
            # Channel after the reserved hum ones, so effects and theme never clash.
            self.title_channel = pygame.mixer.Channel(HUM_CHANNELS)
        except Exception:
            self.title_theme = None
            self.title_channel = None
        for name, filename in SOUND_FILES.items():
            try:
                snd = pygame.mixer.Sound(os.path.join(SOUND_DIR, filename))
                snd.set_volume(SOUND_VOLUME.get(name, 0.8))
                self.sounds[name] = snd
            except Exception:
                pass  # missing or unreadable: that one stays silent
        for filename in TARDIS_REAL_FILES:
            path = os.path.join(SOUND_DIR, filename)
            if not os.path.exists(path):
                continue
            try:
                snd = pygame.mixer.Sound(path)
                if snd.get_length() <= 0.0:
                    continue
                snd.set_volume(TARDIS_REAL_VOLUME)
                self.sounds["tardis_demat"] = snd
                self.demat_source = filename
                break
            except Exception:
                continue  # unreadable (e.g. no mp3 support): try the next, else ours

    def length_ms(self, name: str, default: float) -> float:
        snd = self.sounds.get(name)
        try:
            return snd.get_length() * 1000.0 if snd is not None else default
        except Exception:
            return default

    def play(self, name: str) -> bool:
        self.requested[name] = self.requested.get(name, 0) + 1
        if self.muted or not self.ok:
            return False
        snd = self.sounds.get(name)
        if snd is None:
            return False
        try:
            snd.play()
        except Exception:
            return False
        self.played[name] = self.played.get(name, 0) + 1
        return True

    def play_voice(self, name: str, left: float, right: float):
        """Play `name` once at this (left, right) channel volume; returns the
        channel (or None) so the volume can follow a moving Dalek."""
        self.requested[name] = self.requested.get(name, 0) + 1
        if self.muted or not self.ok:
            return None
        snd = self.sounds.get(name)
        if snd is None:
            return None
        try:
            ch = snd.play()
            if ch is None:
                return None
            ch.set_volume(left, right)
        except Exception:
            return None
        self.played[name] = self.played.get(name, 0) + 1
        return ch

    def step(self) -> bool:
        """A soft footstep, never the same sample twice running."""
        choices = [n for n in STEP_SOUNDS if n != self.last_step]
        self.last_step = self._rng.choice(choices)
        return self.play(self.last_step)

    def set_hum(self, i: int, left: float, right: float, keep: bool):
        """Volume of Dalek i's looping hum. Stops the loop when it is silent
        and not wanted soon (far away, dead, won, muted)."""
        if self.muted:
            left = right = 0.0
            keep = False
        if i < len(self.hum_levels):
            self.hum_levels[i] = (left, right)
        if not self.ok or self.hum is None or i >= len(self.hum_channels):
            return
        ch = self.hum_channels[i]
        try:
            if left <= 0.0 and right <= 0.0 and not keep:
                if ch.get_busy():
                    ch.stop()
                return
            if not ch.get_busy():
                ch.set_volume(0.0, 0.0)
                ch.play(self.hum, loops=-1)
            ch.set_volume(left, right)
        except Exception:
            pass

    def stop_hums(self):
        self.hum_levels = [(0.0, 0.0)] * HUM_CHANNELS
        for ch in self.hum_channels:
            try:
                ch.stop()
            except Exception:
                pass

    def stop_all(self):
        self.hum_levels = [(0.0, 0.0)] * HUM_CHANNELS
        if not self.ok:
            return
        try:
            pygame.mixer.stop()
        except Exception:
            pass

    def start_title_theme(self):
        """Loop the title theme; silent if muted or the file is missing."""
        self.want_title = True
        self.stop_hums()
        if self.muted or not self.ok or self.title_theme is None or self.title_channel is None:
            return
        try:
            if not self.title_channel.get_busy():
                self.title_channel.play(self.title_theme, loops=-1)
            self.title_channel.set_volume(TITLE_THEME_VOLUME)
        except Exception:
            pass

    def stop_title_theme(self):
        self.want_title = False
        if self.title_channel is None:
            return
        try:
            self.title_channel.stop()
        except Exception:
            pass

    def toggle_mute(self):
        self.muted = not self.muted
        if self.muted:
            self.stop_all()
        elif self.want_title:
            self.start_title_theme()


def clean_country(text) -> str:
    """A 2-letter ISO country code ("GB"), or "" for anything else."""
    text = str(text or "").strip().upper()
    return text if len(text) == 2 and text.isascii() and text.isalpha() else ""


def open_url(req, timeout):
    """urlopen that copes with Macs whose Python has no CA certificates
    (python.org installs before "Install Certificates" is run): retry with
    certifi if present, else without verification. Only used for public,
    read-only lookups (country code, flag PNGs that are checked on arrival)."""
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.URLError as e:
        if not isinstance(getattr(e, "reason", None), ssl.SSLError):
            raise
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        ctx = ssl._create_unverified_context()
    return urllib.request.urlopen(req, timeout=timeout, context=ctx)


def lookup_country() -> str:
    """This player's country from a free HTTPS lookup (blocking: call it on a
    background thread). "" if every service fails."""
    for url in COUNTRY_URLS:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "DaleksInHedges/1.0"})
            with open_url(req, COUNTRY_TIMEOUT) as resp:
                code = clean_country(resp.read(64).decode("ascii", "replace"))
            if code:
                return code
        except Exception:
            continue
    return ""


class Flags:
    """Small flag images for the Top 10, fetched from flagcdn.com on a
    background thread and kept in FLAG_DIR, so each is downloaded once."""

    def __init__(self, folder: str = FLAG_DIR, download: bool = True):
        self.folder = folder
        self.download = download
        self.images = {}       # code -> Surface (or None: not available)
        self.fetching = set()  # codes being downloaded or already tried
        self.lock = threading.Lock()

    def _path(self, code: str) -> str:
        return os.path.join(self.folder, code.lower() + ".png")

    def _fetch(self, code: str):
        try:
            req = urllib.request.Request(FLAG_URL.format(code=code.lower()),
                                         headers={"User-Agent": "DaleksInHedges/1.0"})
            with open_url(req, FLAG_TIMEOUT) as resp:
                data = resp.read(65536)
            if data[:8] != b"\x89PNG\r\n\x1a\n":
                return
            os.makedirs(self.folder, exist_ok=True)
            tmp = self._path(code) + ".tmp"
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, self._path(code))
        except Exception:
            pass
        finally:
            with self.lock:
                self.images.pop(code, None)  # look on disk again next frame

    def get(self, code: str):
        """The flag for code (about 10 px tall), or None for now."""
        code = clean_country(code)
        if not code:
            return None
        with self.lock:
            if code in self.images:
                return self.images[code]
        img = None
        path = self._path(code)
        if os.path.exists(path):
            try:
                raw = pygame.image.load(path)
                h = 10
                w = max(1, int(round(raw.get_width() * h / raw.get_height())))
                img = pygame.transform.scale(raw, (w, h)).convert()  # nearest: keeps the pixel look
            except Exception:
                img = None
        with self.lock:
            self.images[code] = img
            start = img is None and self.download and code not in self.fetching
            if start:
                self.fetching.add(code)
        if start:
            threading.Thread(target=self._fetch, args=(code,), daemon=True).start()
        return img


def clean_name(text: str) -> str:
    """Letters, digits and single spaces only, at most NAME_MAX characters."""
    out = "".join(ch for ch in str(text) if ch.isascii() and (ch.isalnum() or ch == " "))
    return " ".join(out.split())[:NAME_MAX]


class Leaderboard:
    """dreamlo Top 10 plus a local cache. All network calls run on daemon
    threads with a short timeout, so the game never waits and never crashes
    offline. Scores that could not be sent are kept and retried later."""

    def __init__(self, online: bool = True, path: str = SAVE_FILE):
        self.online = online
        self.path = path
        self.lock = threading.Lock()
        self.save_lock = threading.Lock()
        self.entries = []          # [(name, score, country)], best first
        self.country = None        # this player's country code ("" unknown), looked up once a session
        self.saved_country = ""
        self.pending = []          # [(name, score)] still to send
        self.local_best = 0
        self.last_name = ""
        self.status = "cached"     # "cached" | "loading" | "online" | "offline"
        self.busy = False
        self._load()
        if not self.entries and online:
            self.status = "loading"

    # ----- local file -------------------------------------------------
    def _load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.entries = [(clean_name(e[0]), int(e[1]), clean_country(e[2] if len(e) > 2 else ""))
                            for e in data.get("top", [])][:TOP_N]
            self.saved_country = clean_country(data.get("country", ""))
            self.pending = [(clean_name(n), int(v)) for n, v in data.get("pending", [])]
            self.local_best = int(data.get("local_best", 0))
            self.last_name = clean_name(data.get("last_name", ""))
        except Exception:
            pass

    def _save(self):
        if not self.online:
            return  # headless test runs leave the player's file alone
        with self.lock:
            data = {"top": self.entries, "pending": self.pending,
                    "local_best": self.local_best, "last_name": self.last_name,
                    "country": self.country or self.saved_country}
        with self.save_lock:
            try:
                os.makedirs(os.path.dirname(self.path), exist_ok=True)
                tmp = self.path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(data, f)
                os.replace(tmp, self.path)
            except Exception:
                pass

    # ----- what the game shows ----------------------------------------
    def top(self, n: int = TOP_N):
        """Cached dreamlo Top 10 merged with unsent scores (best per name):
        [(name, score, country)]."""
        with self.lock:
            mine = self.country or self.saved_country
            best = {}
            rows = [(e[0], e[1], e[2] if len(e) > 2 else "") for e in self.entries]
            rows += [(name, score, mine) for name, score in self.pending]
            for name, score, country in rows:
                if name and score > best.get(name, (-1, ""))[0]:
                    best[name] = (score, country)
        return sorted(((k, v[0], v[1]) for k, v in best.items()), key=lambda e: (-e[1], e[0]))[:n]

    def high_score(self) -> int:
        top = self.top(1)
        return max(self.local_best, top[0][1] if top else 0)

    def record_local(self, score: int):
        with self.lock:
            self.local_best = max(self.local_best, int(score))
        self._save()

    # ----- network ----------------------------------------------------
    @staticmethod
    def _get(url: str) -> str:
        req = urllib.request.Request(url, headers={"User-Agent": "DaleksInHedges/1.0"})
        with urllib.request.urlopen(req, timeout=DREAMLO_TIMEOUT) as resp:
            return resp.read().decode("utf-8", "replace")

    @staticmethod
    def _parse(text: str):
        board = (json.loads(text).get("dreamlo") or {}).get("leaderboard") or {}
        entry = board.get("entry") or []
        if isinstance(entry, dict):  # dreamlo sends a lone entry as an object
            entry = [entry]
        out = []
        for e in entry:
            try:
                out.append((clean_name(e.get("name", "")), int(float(e.get("score", 0))),
                            clean_country(e.get("text", ""))))  # older entries have no code
            except (TypeError, ValueError):
                continue
        out = [e for e in out if e[0]]
        out.sort(key=lambda e: (-e[1], e[0]))
        return out[:TOP_N]

    def _send(self, name: str, score: int) -> bool:
        # /add/name/score/seconds/text: the country code rides in the text field.
        url = (DREAMLO_URL + DREAMLO_PRIVATE + "/add/"
               + urllib.parse.quote(name, safe="") + "/" + str(int(score)))
        country = self.country or ""
        if country:
            url += "/0/" + country
        return self._get(url).strip().startswith("OK")

    def _work(self):
        try:
            with self.lock:
                todo = list(self.pending)
                need_country = bool(todo) and self.country is None
            if need_country:
                code = lookup_country()  # once a session, on this background thread
                with self.lock:
                    self.country = code
            sent = []
            for item in todo:
                try:
                    if self._send(*item):
                        sent.append(item)
                except Exception:
                    break  # offline: keep the rest for next time
                time.sleep(1.1)  # dreamlo refuses repeat requests within a second
            with self.lock:
                self.pending = [p for p in self.pending if p not in sent]
            try:
                entries = self._parse(self._get(DREAMLO_URL + DREAMLO_PUBLIC + "/json"))
                with self.lock:
                    self.entries = entries
                    self.status = "online"
            except Exception:
                with self.lock:
                    self.status = "offline"
            self._save()
        finally:
            with self.lock:
                self.busy = False

    def _spawn(self, force: bool = False):
        if not self.online:
            return
        with self.lock:
            if self.busy and not force:
                return  # a refresh is already on its way
            self.busy = True
        threading.Thread(target=self._work, daemon=True).start()

    def refresh(self):
        self._spawn()

    def submit(self, name: str, score: int):
        name = clean_name(name)
        with self.lock:
            self.last_name = name or self.last_name
            self.local_best = max(self.local_best, int(score))
        if name and score > 0:
            with self.lock:
                self.pending.append((name, int(score)))
        self._save()
        if name and score > 0:
            self._spawn(force=True)


class Dalek:
    """Glides cell to cell along corridors; looks only the way it faces."""

    def __init__(self, col: int, row: int, facing, rng: random.Random):
        self.pos = [float(col), float(row)]
        self.target = (col, row)
        self.facing = facing
        self.prev_facing = facing
        self.turn_t0 = -1e9
        self.wait_until = 0.0
        self.state = "roam"  # roam, aim, fire
        self.victim = "me"   # who it is aiming at: "me" (this game's player) or "other"
        self.net_pos = None  # joiner: latest position from the host
        self.fire_at = 0.0
        self.aim_t0 = 0.0
        self.fire_t0 = 0.0
        self.rng = rng
        self.feet = (0, 0)  # view-space feet, set while drawing
        self.glide = 0.0     # hum fade, 0..1: up while gliding, down when it stops
        self.near = 0.0      # smoothed closeness, 0..1
        self.hum_dist = 99.0  # effective distance used for the hum (cells)
        self.hum_lr = (0.0, 0.0)  # (left, right) volume it wants
        self.hum_vol = 0.0

    def cell(self):
        return int(round(self.pos[0])), int(round(self.pos[1]))

    def gliding(self, now: float) -> bool:
        return self.state == "roam" and now >= self.wait_until

    def face(self, direction, now: float) -> bool:
        if direction == self.facing:
            return False
        self.prev_facing = self.facing
        self.facing = direction
        self.turn_t0 = now
        return True

    def sprite_key(self, now: float) -> str:
        if now - self.turn_t0 < DALEK_TURN_MS / 2:
            return FACING_SPRITE[self.prev_facing]
        return FACING_SPRITE[self.facing]


class RemotePlayer:
    """The other player in a network game, as this game last heard it."""

    def __init__(self, character: str, name: str):
        self.character = character
        self.name = name
        self.score = 0
        self.reset((1, 1), DIR_SE)

    def reset(self, cell, facing):
        self.cell = tuple(cell)
        self.src = self.dst = self.cell
        self.moving = False
        self.u = 0.0
        self.facing = facing
        self.disguised = False
        self.disguise_t0 = -1e9
        self.dead = False
        self.death_t0 = 0.0
        self.shooter = None
        self.in_tardis = False
        self.pos = [float(cell[0]), float(cell[1])]
        self.dead_pos = tuple(self.pos)
        self.feet = (0, 0)

    def target_pos(self):
        if self.moving:
            sc, sr = self.src
            dc, dr = self.dst
            u = ease(self.u)
            return sc + (dc - sc) * u, sr + (dr - sr) * u
        return float(self.cell[0]), float(self.cell[1])

    def cells(self):
        cells = {self.cell}
        if self.moving:
            cells.add(self.dst)
        return cells

    def exposed(self) -> bool:
        return not (self.disguised or self.dead or self.in_tardis)

    def ease_towards(self, dt: float):
        if self.dead:
            return
        if self.moving:  # carry the hop on between packets
            self.u = min(1.0, self.u + dt / MOVE_MS)
        tx, ty = self.target_pos()
        if abs(tx - self.pos[0]) + abs(ty - self.pos[1]) > 2.5:
            self.pos = [tx, ty]  # a jump (new level, respawn): no slide across the maze
            return
        k = 1.0 - math.exp(-dt / NET_SMOOTH_MS) if dt > 0 else 0.0
        self.pos[0] += (tx - self.pos[0]) * k
        self.pos[1] += (ty - self.pos[1]) * k


def _clear_holly_leg_gap(image: pygame.Surface) -> pygame.Surface:
    """Clear the narrow white/grey fill between Holly's legs.

    Keeps the white dress (and its blue polka dots) fully opaque, and leaves
    white socks/shoes alone. Only a tight corridor between the two skin-leg
    runs, just under the dress hem, is made transparent. Safe to run more
    than once.
    """
    out = image.copy()
    rgb = pygame.surfarray.pixels3d(out)
    alpha = pygame.surfarray.pixels_alpha(out)
    # surfarray is (x, y, ...); work in that layout then write back.
    r = rgb[..., 0].astype(np.int16)
    g = rgb[..., 1].astype(np.int16)
    b = rgb[..., 2].astype(np.int16)
    a = alpha
    w, h = out.get_size()

    skin = (
        (a > 64) & (r > 155) & (g > 85) & (b < 155)
        & (r > g) & (g > b - 35) & (r - b > 40)
    )
    blue_dot = (
        (a > 64) & (b > r + 12) & (b > g + 8)
        & (b - ((r + g) // 2) > 15)
    )
    light = (
        (a > 8) & (r > 120) & (g > 120) & (b > 120)
        & (np.abs(r - g) < 45) & (np.abs(g - b) < 45) & (np.abs(r - b) < 50)
        & ~skin & ~blue_dot
    )

    def skin_runs(y):
        xs = np.where(skin[:, y])[0]
        if len(xs) < 2:
            return []
        runs, start, prev = [], int(xs[0]), int(xs[0])
        for x in xs[1:]:
            x = int(x)
            if x == prev + 1:
                prev = x
            else:
                runs.append((start, prev))
                start = prev = x
        runs.append((start, prev))
        return runs

    hem_y = int(h * 0.68)
    for y in range(int(h * 0.55), int(h * 0.78)):
        row = light[:, y]
        best = cur = 0
        for x in range(w):
            if row[x]:
                cur += 1
                best = max(best, cur)
            else:
                cur = 0
        if best >= 10:
            hem_y = y

    gaps = []
    for y in range(hem_y + 1, int(h * 0.86)):
        runs = skin_runs(y)
        if len(runs) < 2:
            continue
        le, rs = runs[0][1], runs[-1][0]
        if 1 <= rs - le - 1 <= 8:
            gaps.append((y, le, rs))
    if not gaps:
        del rgb, alpha
        return out

    L = int(np.median([g[1] for g in gaps]))
    R = int(np.median([g[2] for g in gaps]))
    if R - L - 1 < 1:
        del rgb, alpha
        return out

    y0 = max(hem_y, gaps[0][0] - 2)
    y1 = min(int(h * 0.86), gaps[-1][0] + 1)
    cleared = np.zeros((w, h), dtype=bool)
    for y in range(y0, y1 + 1):
        runs = skin_runs(y)
        if len(runs) >= 2:
            le, rs = runs[0][1], runs[-1][0]
            lo, hi = (le, rs) if 1 <= rs - le - 1 <= 8 else (L, R)
        else:
            lo, hi = L, R
        for x in range(lo + 1, hi):
            if light[x, y]:
                cleared[x, y] = True

    for x in range(L + 1, R):
        if not light[x, hem_y]:
            continue
        xl = xr = x
        while xl > 0 and light[xl - 1, hem_y]:
            xl -= 1
        while xr < w - 1 and light[xr + 1, hem_y]:
            xr += 1
        if xr - xl + 1 <= 8:
            cleared[x, hem_y] = True

    alpha[cleared] = 0
    del rgb, alpha
    return out



def _wrap_text(font: pygame.font.Font, text: str, max_width: int) -> list[str]:
    """Greedy word-wrap: break so each line fits max_width pixels."""
    words = text.split()
    if not words:
        return []
    lines, cur = [], words[0]
    for word in words[1:]:
        trial = cur + " " + word
        if font.size(trial)[0] <= max_width:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    lines.append(cur)
    return lines


def _load_char_sprites(files: dict):
    """Scale a character's facing PNGs to SPRITE_H and return (sprites, anchors, white)."""
    sprites, anchors = {}, {}
    for key, path in files.items():
        image = pygame.image.load(path).convert_alpha()
        # Holly: clear the tiny white crotch/between-legs gap (dress stays opaque).
        # Run before and after scale — nearest-neighbour downscale can refill the gap.
        is_holly = "holly" in os.path.basename(path).lower()
        if is_holly:
            image = _clear_holly_leg_gap(image)
        h = image.get_height()
        if h != SPRITE_H:
            w = max(1, round(image.get_width() * SPRITE_H / h))
            # Nearest-neighbour keeps white dress pixels solid (smoothscale muddy them).
            image = _finish_rgba(pygame.transform.scale(image, (w, SPRITE_H)))
        if is_holly:
            image = _clear_holly_leg_gap(image)
        sprites[key] = image
        anchors[key] = _feet_anchor(image)
    white = {key: _faded_silhouettes(image, DEATH_FADE_LEVELS) for key, image in sprites.items()}
    return sprites, anchors, white


def _make_cloak_icon() -> pygame.Surface:
    """Fallback Chameleon Cloak badge if the PNG is missing. Drawn 4x larger
    and smooth-scaled down, so its edges are antialiased like the PNG's."""
    W, H, S = 48, 56, 4
    surf = _new_rgba((W * S, H * S))
    pygame.draw.ellipse(surf, (186, 150, 70, 255), (4 * S, 2 * S, 40 * S, 48 * S))
    pygame.draw.ellipse(surf, (48, 58, 72, 255), (8 * S, 6 * S, 32 * S, 40 * S))
    pygame.draw.ellipse(surf, (70, 196, 110, 255), (16 * S, 16 * S, 16 * S, 16 * S))
    pygame.draw.ellipse(surf, (180, 255, 200, 255), (20 * S, 20 * S, 8 * S, 8 * S))
    pygame.draw.rect(surf, (20, 14, 10, 255), (10 * S, (H - 10) * S, (W - 20) * S, 6 * S))
    pygame.draw.rect(surf, (40, 120, 60, 255), (11 * S, (H - 9) * S, (W - 22) * S, 4 * S))
    return _finish_rgba(pygame.transform.smoothscale(surf, (W, H)))


class Game:
    def __init__(self, seed: int | None, level: int = 1, character: str = "archie", title: bool = True,
                 online: bool = True, net_port: int = net.GAME_PORT, discovery_port: int = net.DISCOVERY_PORT):
        self.board = Leaderboard(online=online)
        self.flags = Flags(download=online)
        self._reset_score()
        self.char_sets = {}
        for cid, info in CHARACTERS.items():
            sprites, anchors, white = _load_char_sprites(info["files"])
            # Title portrait = same size as in-game (SPRITE_H), not enlarged.
            portrait = sprites["se"]
            self.char_sets[cid] = {
                "name": info["name"], "sprites": sprites, "anchors": anchors,
                "white": white, "portrait": portrait,
            }
        self.character = character if character in self.char_sets else "archie"
        self._apply_character(self.character)
        self.dalek_sprites = {}
        self.dalek_anchor = {}
        self.dalek_gun = {}
        self.dalek_eye = {}
        for key in ("se", "ne"):
            mirror = {"se": "sw", "ne": "nw"}[key]
            src, image, k = _load_scaled(DALEK_FILES[key], DALEK_H)
            sw = src.get_width()
            flipped = _finish_rgba(pygame.transform.flip(image, True, False))
            for name, img, flip in ((key, image, False), (mirror, flipped, True)):
                self.dalek_sprites[name] = img
                self.dalek_anchor[name] = _base_anchor(img)
                gx, gy = DALEK_GUN_SRC[key]
                ex, ey = DALEK_EYE_SRC[key]
                if flip:
                    gx, ex = sw - 1 - gx, sw - 1 - ex
                self.dalek_gun[name] = ((gx + 0.5) * k, (gy + 0.5) * k)
                self.dalek_eye[name] = ((ex + 0.5) * k, (ey + 0.5) * k)
        _src, self.tardis, tk = _load_scaled(TARDIS_FILE, TARDIS_H)
        self.tardis_k = tk
        self.tardis_anchor = _base_anchor(self.tardis)
        self.tardis_lamp = ((TARDIS_LAMP_SRC[0] + 0.5) * tk, (TARDIS_LAMP_SRC[1] + 0.5) * tk)
        self.dalek_shadow = _new_rgba((50, 18))
        pygame.draw.ellipse(self.dalek_shadow, (30, 20, 10, 120), self.dalek_shadow.get_rect())
        self.dalek_shadow = _finish_rgba(self.dalek_shadow)
        self.tardis_shadow = _new_rgba((76, 30))
        pygame.draw.ellipse(self.tardis_shadow, (24, 16, 8, 110), self.tardis_shadow.get_rect())
        self.tardis_shadow = _finish_rgba(self.tardis_shadow)
        # Dematerialisation: pre-faded TARDIS and shadow (alpha copied exactly,
        # scaled with numpy) and a lamp glow at a few brightness levels.
        self.demat_fades = [_alpha_scaled(self.tardis, i / (DEMAT_LEVELS - 1)) for i in range(DEMAT_LEVELS)]
        self.demat_shadows = [_alpha_scaled(self.tardis_shadow, i / (DEMAT_LEVELS - 1)) for i in range(DEMAT_LEVELS)]
        if os.path.exists(LAMP_GLOW_FILE):
            lamp = pygame.image.load(LAMP_GLOW_FILE).convert_alpha()
        else:  # (run tools/make_fx.py to write it)
            lamp = _make_glow(11, (255, 236, 190))
        self.demat_lamps = [_alpha_scaled(lamp, i / 11) for i in range(12)]
        self.scorch = _new_rgba((34, 14))
        pygame.draw.ellipse(self.scorch, (20, 12, 6, 170), self.scorch.get_rect())
        pygame.draw.ellipse(self.scorch, (10, 6, 4, 200), self.scorch.get_rect().inflate(-14, -6))
        self.scorch = _finish_rgba(self.scorch)
        self.puffs = []  # puffs[size][alpha level], pre-faded
        for radius in (2, 3, 4, 5, 6):
            puff = _new_rgba((radius * 2 + 2, radius * 2 + 2))
            pygame.draw.circle(puff, (214, 236, 190, 255), (radius + 1, radius + 1), radius)
            pygame.draw.circle(puff, (246, 252, 236, 255), (radius, radius), max(1, radius - 2))
            puff = _finish_rgba(puff)
            self.puffs.append([_alpha_scaled(puff, i / (PUFF_LEVELS - 1)) for i in range(PUFF_LEVELS)])
        self.disguise_cache = {}
        self.shadow = _new_rgba((40, 16))
        pygame.draw.ellipse(self.shadow, (48, 30, 16, 110), self.shadow.get_rect())
        self.shadow = _finish_rgba(self.shadow)
        self.dirt = build_dirt_variants(make_dirt_texture())
        self.hedges = build_hedge_variants()
        self.view_w = max(1, int(round(WIN_W / ZOOM)))
        self.view_h = max(1, int(round(WIN_H / ZOOM)))
        self.view = pygame.Surface((self.view_w, self.view_h)).convert()
        self.frame = None  # opaque window-sized back buffer, made on the first draw
        # Fonts sized for the low-res view; they look chunky after nearest 2x.
        self.font_title = load_font(28, bold=True)   # title screen name
        self.font_title_sub = load_font(14)            # "Choose Archie or Holly"
        self.font_title_help = load_font(11)           # controls help on title
        self.font_title_sel = load_font(11, bold=True) # SELECTED
        self.font_title_copy = load_font(8)              # title-screen copyright
        self.font_big = load_font(18, bold=True)
        self.font_small = load_font(10)
        self.font_shout = load_font(8, bold=True)
        self.font_hud = load_font(8)
        self.font_hint = load_font(8)
        self.font_score = load_font(10, bold=True)     # in-play score / high score
        self.font_entry = load_font(16, bold=True)     # name being typed
        self.cam = (0.0, 0.0)
        self.archie_feet = (0, 0)
        self.sfx = Sounds()
        self.given_seed = seed
        self.level = max(1, int(level))
        if os.path.exists(CLOAK_FILE):
            self.cloak_icon = pygame.image.load(CLOAK_FILE).convert_alpha()
        else:
            self.cloak_icon = _make_cloak_icon()
        # Level-card fade on the low-res view.
        self.fader = _finish_rgba(_new_rgba((self.view_w, self.view_h)))
        self.complete_fades = _shadowed_text_fades(load_font(COMPLETE_SIZE, bold=True), COMPLETE_TEXT, 16)
        self.archie_fades = {}    # (sprite key, level) -> faded copy, for walking into the TARDIS
        self.hum_gain = 1.0
        self.card_t0 = None       # level card running (survives reset)
        self.card_switched = False
        self.hum_assign = [None] * HUM_CHANNELS
        self.hum_ch_gain = [0.0] * HUM_CHANNELS
        self.hum_releasing = [False] * HUM_CHANNELS
        self.taunt_rng = random.Random()
        self.taunt_at = None         # game time of the next ambient callout
        self.taunt_ch = None         # channel of the callout being said
        self.taunt_dalek = None      # ... and the Dalek saying it
        self.taunt_name = None
        self.taunt_until = -1e9
        self.taunt_last = None
        # Network game state (all idle in single player).
        self.net = None              # net.Link while a two-player game is on
        self.net_role = None         # "host" or "join"
        self.net_port = net_port
        self.discovery_port = discovery_port
        self.other = None            # RemotePlayer
        self.lobby = None
        self.beacon = None
        self.scanner = None
        self.net_notice = None       # (text, t0)
        self.net_over = False        # both exterminated: game over for both
        self.net_first = None        # "host"/"join": who reached this level's TARDIS first
        self.net_departing = False
        self.net_card_at = None
        self.net_next_seed = None
        self.net_last_now = None
        self.other_voice_until = -1e9
        self.watch_blend = 0.0
        self.safe_cells = frozenset()
        self.exit_hold = False       # in the TARDIS, waiting for the other player
        self.exit_release_t = -1e9
        self.pay_on_exit = True
        self.mode = "title" if title else "play"
        self.title_pick = CHAR_ORDER.index(self.character) if self.character in CHAR_ORDER else 0
        seed0 = seed if seed is not None else (time.time_ns() & 0x7FFFFFFF)
        if self.mode == "title":
            self._enter_title(seed0)
        else:
            self.reset(seed0)

    def _apply_character(self, character: str):
        """Switch the active sprite set (Archie or Holly)."""
        self.character = character if character in self.char_sets else "archie"
        cs = self.char_sets[self.character]
        self.char_name = cs["name"]
        self.sprites = cs["sprites"]
        self.anchors = cs["anchors"]
        self.white = cs["white"]
        self.archie_fades = {}

    # ----- Score and leaderboard ------------------------------------------
    def _reset_score(self):
        self.survive_ms = 0.0        # time on the move (only counts while stepping)
        self.level_bonus = 0
        self.spot_bonus = 0          # +SCORE_PER_SPOT per "Exterminate!"
        self.bonus_pop_t0 = None
        self.bonus_pop_text = f"+{SCORE_PER_SPOT}"
        self.move_grace_until = -1e9
        self.entry_active = False    # typing a name for the leaderboard
        self.entry_done = False
        self.entry_name = ""
        self.entry_note = ""
        self.final_score = 0

    @property
    def score(self) -> int:
        return (int(self.survive_ms // 1000) * SCORE_PER_SECOND + self.level_bonus
                + self.spot_bonus)

    def _on_the_move(self, now: float) -> bool:
        """Time points only while Archie is actually travelling between cells,
        plus a short grace after each step lands so walking with key-repeat
        gaps counts smoothly. Standing, turning on the spot, cloaked or
        bumping a hedge (no step starts) earn nothing."""
        if self.disguised:
            return False
        return self.moving or now < self.move_grace_until

    def wants_text(self) -> bool:
        """True while a name is being typed (Esc then skips instead of quitting)."""
        return (self.mode == "play" and self.entry_active) or self.mode == "lobby"

    def _begin_entry(self):
        self.final_score = self.score
        self.board.record_local(self.final_score)
        if self.final_score <= 0:
            self.entry_done = True
            return
        self.entry_active = True
        self.entry_name = self.board.last_name

    def _entry_status(self) -> str:
        """Death-panel line about the leaderboard once the name step is over."""
        name = self.entry_note
        if self.final_score <= 0:
            return ""
        if not name:
            return "Score not sent"
        if not self.board.online:
            return f"Saved as {name}"
        if (name, self.final_score) in self.board.pending:
            return f"Sending as {name}" if self.board.busy else "Offline - will send later"
        return f"Score sent as {name}"

    def _entry_key(self, key: int, text: str, now: float | None = None):
        if key in (pygame.K_RETURN, pygame.K_KP_ENTER):
            name = clean_name(self.entry_name)
            if not name:
                return  # type a name first (Esc skips)
            self.entry_active = False
            self.entry_done = True
            self.entry_note = name
            self.board.submit(name, self.final_score)
        elif key == pygame.K_ESCAPE:
            self.entry_active = False
            self.entry_done = True
            self.entry_note = ""
        elif key == pygame.K_BACKSPACE:
            self.entry_name = self.entry_name[:-1]
        elif text and len(text) == 1 and text.isascii() and (text.isalnum() or text == " "):
            if len(self.entry_name) < NAME_MAX and not (text == " " and (not self.entry_name or self.entry_name.endswith(" "))):
                self.entry_name += text

    def _enter_title(self, seed: int | None = None):
        """Decorative maze with TITLE_DALEKS Daleks; no player, no deaths."""
        self._net_close()
        self.other = None
        self.lobby = None
        self.safe_cells = frozenset()
        self.mode = "title"
        self.level = 1
        self.card_t0 = None
        self.card_switched = False
        self.banner = False
        self.won = False
        self.dead = False
        self.disguised = False
        self.disguise_pending = False
        self.exit_t0 = None
        self.exit_demat_t0 = None
        self.num_daleks = TITLE_DALEKS
        self.seed = (seed if seed is not None else time.time_ns()) & 0x7FFFFFFF
        self.grid = generate_maze(MAZE_SIZE, self.seed)
        self.hedge_variant = hedge_variant_grid(self.grid, self.seed)
        self.exit_cell = (MAZE_SIZE - 2, MAZE_SIZE - 2)
        self.demat_cell = (1, 1)
        start_dir = next(d for d in DIR_ORDER if self.is_open(1 + d[0], 1 + d[1]))
        self.start_cell = (1 + start_dir[0], 1 + start_dir[1])
        # Camera follows a quiet open cell near the middle of the maze.
        mid = MAZE_SIZE // 2
        open_cells = [(c, r) for r in range(MAZE_SIZE) for c in range(MAZE_SIZE)
                      if self.grid[r][c] == PATH]
        self.col, self.row = min(open_cells, key=lambda p: abs(p[0] - mid) + abs(p[1] - mid))
        self.src = self.dst = (self.col, self.row)
        self.facing_index = 0
        self.facing = self.turn_from = DIR_ORDER[0]
        self.turning = self.moving = False
        self.wish = self.queued = self.bump_dir = None
        self.last_now = None
        self.start_t0 = None
        self.demat_t0 = None  # no start demat on the title screen
        self.demat_len = DEMAT_MS
        if self.sfx.demat_source != "tardis_demat.wav":
            real = self.sfx.length_ms("tardis_demat", DEMAT_MS)
            self.demat_len = max(DEMAT_MIN_MS, min(DEMAT_MAX_MS, real))
        self.sfx.stop_all()
        self._stop_taunt()
        self.taunt_at = None
        self.hum_assign = [None] * HUM_CHANNELS
        self.hum_ch_gain = [0.0] * HUM_CHANNELS
        self.hum_releasing = [False] * HUM_CHANNELS
        self._hum_from = None
        self._hum_dist = {}
        self.daleks = []
        self._spawn_daleks()
        # Title Daleks never aim/fire: keep them roaming forever.
        for d in self.daleks:
            d.state = "roam"
        self.title_t0 = None         # character choice first, then the Top 10
        self.board.refresh()
        self.sfx.start_title_theme()

    def _start_game(self, now: float):
        """Leave the title screen and begin level 1 with the selected character."""
        self.sfx.stop_title_theme()
        self._apply_character(CHAR_ORDER[self.title_pick])
        self.mode = "play"
        self.level = 1
        self._reset_score()
        self.reset(time.time_ns() & 0x7FFFFFFF, banner=True)
        self.start_t0 = now
        self.last_now = now

    def reset(self, seed: int, banner: bool = True):
        """A new maze for self.level (next level, or a fresh run from the title)."""
        self.sfx.stop_all()
        self.hum_assign = [None] * HUM_CHANNELS
        self.hum_ch_gain = [0.0] * HUM_CHANNELS
        self.hum_releasing = [False] * HUM_CHANNELS
        self.num_daleks = daleks_for_level(self.level)
        self.banner = banner      # show the "Level N" banner as it starts
        self.exit_t0 = None       # Archie heading into the exit TARDIS
        self.exit_from = None
        self.exit_facing = DIR_SE
        self.exit_steps = 0
        self.exit_demat_t0 = None
        self.bump_times = []
        self.last_bump_t = -1e9
        self.rustle_t = -1e9
        self.ow_quiet_until = -1e9
        self.voice_until = -1e9
        self.move_grace_until = -1e9
        self.bonus_pop_t0 = None
        self._stop_taunt()
        self.taunt_at = None         # scheduled on the first update of the level
        self.hops_heard = 0
        self.seed = seed & 0x7FFFFFFF
        self.grid = generate_maze(MAZE_SIZE, self.seed)
        self.hedge_variant = hedge_variant_grid(self.grid, self.seed)
        # The TARDIS that brought him stands on (1, 1); Archie starts one cell
        # forward along the open path, facing on into the maze, away from it.
        self.demat_cell = (1, 1)
        start_dir = next(d for d in DIR_ORDER if self.is_open(1 + d[0], 1 + d[1]))
        self.start_cell = (1 + start_dir[0], 1 + start_dir[1])
        self.col, self.row = self.start_cell
        self.src = self.dst = self.start_cell
        self.facing_index = DIR_ORDER.index(start_dir)
        self.facing = start_dir
        self.turn_from = start_dir
        self.turning = False
        self.turn_t0 = 0
        self.turn_sign = 0
        self.moving = False
        self.move_t0 = 0
        self.won = False
        self.wish = None
        self.queued = None
        self.bump_dir = None
        self.bump_t0 = 0
        self.last_now = None
        self.dead = False
        self.death_t0 = 0.0
        self.dead_pos = (float(self.col), float(self.row))
        self.shooter = None
        self.disguised = False
        self.disguise_t0 = -1e9
        self.disguise_end_t = -1e9
        self.cooldown_until = 0.0
        self.disguise_pending = False
        self.exit_cell = (MAZE_SIZE - 2, MAZE_SIZE - 2)
        self.start_t0 = None  # game time of the first update
        self.demat_t0 = None  # when the dematerialisation (and its sound) starts
        if self.sfx.demat_source != "tardis_demat.wav":
            real = self.sfx.length_ms("tardis_demat", DEMAT_MS)
            self.demat_len = max(DEMAT_MIN_MS, min(DEMAT_MAX_MS, real))
        else:
            self.demat_len = DEMAT_MS
        self.daleks = []
        self._hum_from = None
        self._hum_dist = {}
        self.exit_hold = False
        self.exit_release_t = -1e9
        self.pay_on_exit = self.net is None
        self.safe_cells = self._safe_zone() if self.net is not None else frozenset()
        self._spawn_daleks()
        if self.net is not None:
            self._net_after_reset()

    # ----- Daleks -------------------------------------------------------
    def _path_dist(self, start):
        dist = {start: 0}
        queue = [start]
        i = 0
        while i < len(queue):
            c, r = queue[i]
            i += 1
            for dc, dr in DIR_ORDER:
                n = (c + dc, r + dr)
                if n not in dist and self.is_open(*n):
                    dist[n] = dist[(c, r)] + 1
                    queue.append(n)
        return dist

    def _spawn_daleks(self):
        rng = random.Random((self.seed * 2654435761 + 12345) & 0xFFFFFFFF)
        from_start = self._path_dist(self.start_cell)
        from_exit = self._path_dist(self.exit_cell)
        # The full rule is >= 12 cells from the start and >= 10 from the exit;
        # a small maze that cannot fit them all relaxes those distances.
        cands = []
        for k in (1.0, 0.75, 0.5, 0.25, 0.0):
            cands = sorted(
                cell for cell, d in from_start.items()
                if d >= DALEK_MIN_START_DIST * k
                and from_exit.get(cell, 0) >= DALEK_MIN_EXIT_DIST * k
                and self.grid[cell[1]][cell[0]] == PATH
                and cell not in (self.start_cell, self.demat_cell)
                and cell not in self.safe_cells
            )
            if len(cands) >= self.num_daleks:
                break
        chosen = []
        for _ in range(self.num_daleks):
            # Well apart if the maze allows it; relax the spacing step by step.
            pool = []
            for gap in DALEK_SPACING:
                pool = [c for c in cands if c not in chosen
                        and all(abs(c[0] - o[0]) + abs(c[1] - o[1]) >= gap for o in chosen)]
                if pool:
                    break
            pool = pool or [c for c in cands if c not in chosen]
            if not pool:
                break
            chosen.append(rng.choice(pool))
        self.daleks = []
        for i, (c, r) in enumerate(chosen):
            dirs = [d for d in DIR_ORDER if self.is_open(c + d[0], r + d[1])]
            facing = rng.choice(dirs) if dirs else DIR_SE
            self.daleks.append(Dalek(c, r, facing, random.Random(self.seed * 31 + i * 1009 + 7)))

    def _archie_cells(self):
        cells = {(self.col, self.row)}
        if self.moving:
            cells.add(self.dst)
        return cells

    def _dalek_cells(self, exclude=None):
        cells = set()
        for d in self.daleks:
            if d is exclude:
                continue
            cells.add(d.cell())
            cells.add(d.target)
        return cells

    def archie_exposed(self) -> bool:
        return self.mode == "play" and not (self.disguised or self.dead or self.won)

    def _player_cells(self):
        """[(who, cells, exposed)]: this game's player, plus the other one in
        a network game. A player who is not exposed (cloaked, or a corpse)
        blocks a Dalek's way; an exposed one is a target."""
        players = [("me", self._archie_cells(), self.archie_exposed())]
        o = self.other
        if self.net is not None and o is not None and self.mode == "play" and not o.in_tardis:
            players.append(("other", o.cells(), o.exposed()))
        return players

    def _sees(self, d: Dalek):
        """Who Dalek d can see along the corridor it faces: "me", "other" or None."""
        targets = [(who, cells) for who, cells, exposed in self._player_cells() if exposed]
        if not targets:
            return None
        c, r = d.cell()
        dc, dr = d.facing
        for k in range(1, DALEK_SIGHT + 1):
            cell = (c + dc * k, r + dr * k)
            if not self.is_open(*cell):
                return None
            for who, cells in targets:
                if cell in cells:
                    return who
        return None

    def _choose_next(self, d: Dalek, now: float) -> bool:
        """At a cell centre: pick the next cell. True if it can keep gliding."""
        c, r = d.cell()
        f = d.facing
        back = (-f[0], -f[1])
        others = self._dalek_cells(exclude=d)
        players = self._player_cells()
        opts = []
        for dirn in DIR_ORDER:
            n = (c + dirn[0], r + dirn[1])
            if (not self.is_open(*n) or n == self.exit_cell or n in others
                    or (n == self.demat_cell and self.start_tardis_up(now))
                    or n in self.safe_cells):
                continue
            if any(n in cells and not exposed for _, cells, exposed in players):
                continue  # a hedge (or a corpse) is in the way: turn away
            opts.append(dirn)
        pool = [o for o in opts if o != back] or [o for o in opts if o == back]
        if not pool:
            d.wait_until = now + 250
            return False
        dirn = d.rng.choice(pool)
        n = (c + dirn[0], r + dirn[1])
        turned = d.face(dirn, now)
        victim = next((who for who, cells, _ in players if n in cells), None)
        if victim is not None:
            # Bumped into Archie in the open: that counts as seeing him.
            d.state = "aim"
            d.aim_t0 = now
            d.fire_at = now + DALEK_TELEGRAPH_MS + (DALEK_TURN_MS if turned else 0)
            d.victim = victim
            self._shout(now, victim)
            return False
        d.target = n
        if turned:
            d.wait_until = now + DALEK_TURN_MS
            return False
        return True

    def _update_daleks(self, now: float, dt: float):
        for d in self.daleks:
            if d.state == "fire":
                continue
            if d.state == "aim":
                who = self._sees(d)
                if who is None:
                    d.state = "roam"  # lost him (hedge, or he slipped away)
                elif now >= d.fire_at:
                    d.state = "fire"
                    d.fire_t0 = now
                    self._kill(now, d, who)
                continue
            who = self._sees(d)
            if who is not None:
                d.state = "aim"
                d.aim_t0 = now
                d.fire_at = now + DALEK_TELEGRAPH_MS
                d.victim = who
                self._shout(now, who)
                continue
            if now < d.wait_until:
                continue
            move = dt / DALEK_STEP_MS
            for _ in range(4):
                tc, tr = d.target
                dx = tc - d.pos[0]
                dy = tr - d.pos[1]
                dist = abs(dx) + abs(dy)
                if dist <= move:
                    d.pos = [float(tc), float(tr)]
                    move -= dist
                    if not self._choose_next(d, now) or move <= 0:
                        break
                else:
                    if dx:
                        d.pos[0] += math.copysign(move, dx)
                    else:
                        d.pos[1] += math.copysign(move, dy)
                    break

    def _shout(self, now: float, victim: str = "me"):
        """'Exterminate!' as the telegraph starts; one voice at a time.

        Each shout that is actually voiced is worth SCORE_PER_SPOT. It is only
        called as a Dalek goes from roaming to aiming, and the voice gate means
        two Daleks spotting together (or one re-spotting while it is still
        shouting) is one shout and one bonus."""
        if victim == "other":
            self._net_spotted_other(now)
            return
        if now < self.voice_until:
            return
        self._stop_taunt()  # the real warning always wins over a callout
        self.voice_until = now + self.sfx.length_ms("exterminate", 1600.0)
        self.sfx.play("exterminate")
        if self.mode == "play" and not (self.dead or self.won):
            self.spot_bonus += SCORE_PER_SPOT
            self.bonus_pop_t0 = now
            self.bonus_pop_text = f"+{SCORE_PER_SPOT}"

    def _kill(self, now: float, shooter: Dalek, victim: str = "me"):
        if victim == "other":
            self._net_kill_other(now, shooter)
            return
        if self.dead or self.won:
            return
        self._stop_taunt()
        self.sfx.play("laser")
        self.dead_pos = self.visual_pos(now)
        self.dead = True
        self.death_t0 = now
        self.shooter = shooter
        self.moving = False
        self.turning = False
        self.queued = None
        self.bump_dir = None
        self.disguise_pending = False
        if self.net is not None:
            self._net_local_died(now, shooter)

    # ----- Hedge disguise ----------------------------------------------
    def toggle_disguise(self, now: float):
        if self.dead or self.won:
            return
        if self.disguised:
            self._end_disguise(now)
            return
        if now < self.cooldown_until:
            return
        if self.busy():
            self.disguise_pending = True  # as soon as this hop lands
            self.queued = None
            return
        self._start_disguise(now)

    def _start_disguise(self, now: float):
        self.sfx.play("cloak_on")
        self.disguised = True
        self.disguise_t0 = now
        self.disguise_pending = False
        self.queued = None
        self.wish = None
        self.bump_dir = None

    def _end_disguise(self, now: float):
        self.sfx.play("cloak_off")
        self.disguised = False
        self.disguise_end_t = now
        self.cooldown_until = now + DISGUISE_COOLDOWN_MS

    # ----- Debug scenes for headless screenshots -------------------------
    def _straight_run(self, direction, length: int):
        """First run of length+1 open cells along direction whose inner cells
        are plain corridor (no side openings). Scans from the maze centre."""
        dc, dr = direction
        side = [d for d in DIR_ORDER if d not in (direction, (-dc, -dr))]
        mid = MAZE_SIZE // 2
        cells = sorted(
            ((c, r) for r in range(1, MAZE_SIZE - 1) for c in range(1, MAZE_SIZE - 1)),
            key=lambda p: abs(p[0] - mid) + abs(p[1] - mid),
        )
        for c, r in cells:
            run = [(c + dc * k, r + dr * k) for k in range(length + 1)]
            if not all(self.is_open(*p) and self.grid[p[1]][p[0]] == PATH for p in run):
                continue
            if any(self.is_open(p[0] + s[0], p[1] + s[1]) for p in run[1:-1] for s in side):
                continue
            return run
        return None

    def _place_archie(self, cell, facing):
        self.col, self.row = cell
        self.src = self.dst = cell
        self.facing_index = DIR_ORDER.index(facing)
        self.facing = self.turn_from = facing
        self.moving = self.turning = False

    def _park_other_daleks(self, keep):
        """Move the other Daleks well away so they do not join the scene."""
        far = sorted(
            (abs(c - self.col) + abs(r - self.row), c, r)
            for r in range(MAZE_SIZE) for c in range(MAZE_SIZE)
            if self.grid[r][c] == PATH
        )
        for i, d in enumerate(self.daleks):
            if d is keep:
                continue
            _, c, r = far[-1 - i * 3]
            d.pos = [float(c), float(r)]
            d.target = (c, r)

    def setup_scene(self, name: str, now: float = 0.0) -> float:
        """Arrange a scene and return how long (ms) to simulate before the shot."""
        self.last_now = now
        if name in ("title", "title_top10"):
            self._enter_title(self.seed)
            self.start_t0 = now
            self.title_t0 = now - (TITLE_PANEL_MS + 500 if name == "title_top10" else 0)
            return 800.0
        self.mode = "play"
        if name == "score_hud":
            # A level under way: a couple of minutes survived plus the level bonuses.
            self.level_bonus = SCORE_PER_LEVEL * (self.level - 1)
            self.survive_ms = 127_400.0
            self.spot_bonus = SCORE_PER_SPOT * 2
            self.bonus_pop_t0 = now - 150.0  # the "+20" pop just after a spot
            self._park_other_daleks(None)
            self.start_t0 = now - DEMAT_WAIT_MS - self.demat_len - 1000
            self.demat_t0 = self.start_t0 + DEMAT_WAIT_MS
            self.banner = False
            return 200.0
        if name == "name_entry":
            self.level_bonus = SCORE_PER_LEVEL * (self.level - 1)
            self.survive_ms = 94_000.0
            self.start_t0 = now - DEMAT_WAIT_MS - self.demat_len - 1000
            self.demat_t0 = self.start_t0 + DEMAT_WAIT_MS
            self.banner = False
            if not self.board.last_name:
                self.board.last_name = "Holly" if self.character == "holly" else "Archie"
            self._kill(now, self.daleks[0])
            return DEATH_MSG_MS + 300.0
        if name == "tardis_exit":
            # Archie next to the exit, stepping into the TARDIS. --scene-ms
            # counts from that step: he walks in (0.5 s), demat from 0.9 s, then the card.
            ec, er = self.exit_cell
            for dirn in DIR_ORDER:
                c, r = ec - dirn[0], er - dirn[1]
                if self.is_open(c, r):
                    self._place_archie((c, r), dirn)
                    break
            self._park_other_daleks(None)
            self.start_t0 = now - DEMAT_WAIT_MS - self.demat_len - 1000  # start TARDIS long gone
            self.demat_t0 = self.start_t0 + DEMAT_WAIT_MS
            self.banner = False
            self._start_step(1, now)
            return 300.0
        if name == "tardis_demat":
            # The start of a level: the TARDIS on (1, 1), Archie one cell in front of it.
            self._park_other_daleks(None)
            self.start_t0 = now
            self.demat_t0 = None
            return DEMAT_WAIT_MS + 1500.0  # --scene-ms counts from the start of the game
        if name == "tardis":
            # Three path cells back from the TARDIS, facing along the way to it.
            dist = self._path_dist(self.exit_cell)
            cell = min((c for c, k in dist.items() if k == 3), default=self.exit_cell)
            facing = next(
                (d for d in DIR_ORDER if dist.get((cell[0] + d[0], cell[1] + d[1])) == 2),
                DIR_SE,
            )
            self._place_archie(cell, facing)
            self._park_other_daleks(None)
            return 0.0
        run = self._straight_run(DIR_SE, 4) or self._straight_run(DIR_SW, 4)
        if run is None:
            raise SystemExit("no straight corridor for the scene")
        fwd = (run[1][0] - run[0][0], run[1][1] - run[0][1])
        back = (-fwd[0], -fwd[1])
        d = self.daleks[0]
        if name == "dalek":
            # Archie behind, the Dalek gliding away from him towards the viewer.
            self._place_archie(run[0], fwd)
            d.pos = [float(run[2][0]), float(run[2][1])]
            d.target = run[3]
            d.facing = d.prev_facing = fwd
            self._park_other_daleks(d)
            return 120.0
        if name == "laser":
            self._place_archie(run[4], back)
            d.pos = [float(run[0][0]), float(run[0][1])]
            d.target = run[0]
            d.facing = d.prev_facing = fwd
            self._park_other_daleks(d)
            return DALEK_TELEGRAPH_MS + 260.0
        if name == "telegraph":
            self._place_archie(run[4], back)
            d.pos = [float(run[0][0]), float(run[0][1])]
            d.target = run[0]
            d.facing = d.prev_facing = fwd
            self._park_other_daleks(d)
            return DALEK_TELEGRAPH_MS * 0.8
        if name == "disguise":
            self._place_archie(run[3], back)
            self._start_disguise(now)
            d.pos = [float(run[0][0]), float(run[0][1])]
            d.target = run[1]
            d.facing = d.prev_facing = fwd
            self._park_other_daleks(d)
            return 620.0
        raise SystemExit(f"unknown scene {name!r}")

    def is_open(self, col: int, row: int) -> bool:
        if not (0 <= col < MAZE_SIZE and 0 <= row < MAZE_SIZE):
            return False
        return self.grid[row][col] != WALL

    def busy(self) -> bool:
        return self.moving or self.turning

    def pose(self, now: float) -> str:
        """At rest, the sprite for the facing he is actually pointing.

        A turn keeps the facing he started on, then settles on the new one.
        The extra side-on pictures do not belong in the middle. The maze does not spin.
        """
        if not self.turning:
            return FACING_SPRITE[self.facing]
        t = (now - self.turn_t0) / TURN_MS
        if t < 0.5:
            return FACING_SPRITE[self.turn_from]
        return FACING_SPRITE[self.facing]

    def _start_turn(self, sign: int, now: float):
        index = self.facing_index
        self.turn_from = DIR_ORDER[index]
        if sign > 0:
            index = (index + 1) % 4
        else:
            index = (index - 1) % 4
        self.facing_index = index
        self.facing = DIR_ORDER[index]
        self.turn_t0 = now
        self.turning = True
        self.turn_sign = sign
        self.bump_dir = None
        self.queued = None

    def _start_step(self, sign: int, now: float) -> bool:
        dc, dr = self.facing
        if sign < 0:
            dc, dr = -dc, -dr
        nc, nr = self.col + dc, self.row + dr
        if (not self.is_open(nc, nr) or (nc, nr) in self._dalek_cells()
                or ((nc, nr) == self.demat_cell and self.start_tardis_up(now))):
            if self.bump_dir != (dc, dr):
                self.bump_dir = (dc, dr)
                self.bump_t0 = now
                if not self.is_open(nc, nr):  # a hedge, not a Dalek's cell
                    self._hedge_bump(now)
            return False
        self.bump_dir = None
        self.queued = None
        if (nc, nr) == self.exit_cell:
            self._begin_exit(now)
            return True
        self.src = (self.col, self.row)
        self.dst = (nc, nr)
        self.move_t0 = now
        self.moving = True
        self.hops_heard = 0
        return True

    def _hedge_bump(self, now: float):
        """Soft rustle per bump; a quiet 'ow' only on the 3rd bump in 2 s."""
        if now - self.last_bump_t < BUMP_COUNT_GAP_MS:
            return
        self.last_bump_t = now
        self.bump_times = [t for t in self.bump_times if now - t < OW_WINDOW_MS]
        self.bump_times.append(now)
        if len(self.bump_times) >= OW_BUMPS and now >= self.ow_quiet_until:
            self.bump_times = []
            self.ow_quiet_until = now + OW_QUIET_MS
            self.sfx.play("ow")
        elif now - self.rustle_t >= RUSTLE_GAP_MS:
            self.rustle_t = now
            self.sfx.play("rustle")

    def try_action(self, action, now: float) -> bool:
        if self.won or self.dead or self.disguised or self.disguise_pending:
            return False
        if self.busy():
            # One queued tap, same idea as the old queued step.
            self.queued = action
            return False
        kind, sign = action
        if kind == "turn":
            self._start_turn(sign, now)
            return True
        return self._start_step(sign, now)

    def _held(self, action, keys) -> bool:
        kind, sign = action
        if kind == "turn" and sign < 0:
            return keys[pygame.K_LEFT] or keys[pygame.K_a]
        if kind == "turn" and sign > 0:
            return keys[pygame.K_RIGHT] or keys[pygame.K_d]
        if kind == "step" and sign > 0:
            return keys[pygame.K_UP] or keys[pygame.K_w]
        if kind == "step" and sign < 0:
            return keys[pygame.K_DOWN] or keys[pygame.K_s]
        return False

    def desired_action(self, keys):
        turns = []
        if keys[pygame.K_LEFT] or keys[pygame.K_a]:
            turns.append(("turn", -1))
        if keys[pygame.K_RIGHT] or keys[pygame.K_d]:
            turns.append(("turn", 1))
        steps = []
        if keys[pygame.K_UP] or keys[pygame.K_w]:
            steps.append(("step", 1))
        if keys[pygame.K_DOWN] or keys[pygame.K_s]:
            steps.append(("step", -1))
        # A held turn finishes before a held step. They never share a frame.
        if turns:
            if self.wish in turns:
                return self.wish
            return turns[0]
        if steps:
            if self.wish in steps:
                return self.wish
            return steps[0]
        return None

    def on_key(self, key: int, now: float, text: str = ""):
        if self.mode == "lobby":
            self._lobby_key(key, now)
            return
        if self.wants_text():
            self._entry_key(key, text, now)  # letters (M, H, R ...) are typed, not actions
            return
        if key == MUTE_KEY:
            self.sfx.toggle_mute()
            return
        if self.card_t0 is not None:
            return  # the level card is showing
        if self.mode == "title":
            if key in (pygame.K_LEFT, pygame.K_a, pygame.K_RIGHT, pygame.K_d,
                       pygame.K_1, pygame.K_KP1, pygame.K_2, pygame.K_KP2):
                self.title_t0 = now  # show the characters again while choosing
            if key in (pygame.K_LEFT, pygame.K_a):
                self.title_pick = (self.title_pick - 1) % len(CHAR_ORDER)
            elif key in (pygame.K_RIGHT, pygame.K_d):
                self.title_pick = (self.title_pick + 1) % len(CHAR_ORDER)
            elif key in (pygame.K_1, pygame.K_KP1):
                self.title_pick = 0
            elif key in (pygame.K_2, pygame.K_KP2):
                self.title_pick = min(1, len(CHAR_ORDER) - 1)
            elif key in RESTART_KEYS:
                self._start_game(now)
            elif key == NET_KEY:
                self.open_lobby(now)
            return
        if key in RESTART_KEYS and self.dead and now - self.death_t0 >= LASER_MS and self.entry_done:
            self._enter_title()  # pick a character again
            return
        if key in DISGUISE_KEYS:
            self.toggle_disguise(now)
            return
        action = KEY_ACTIONS.get(key)
        if action is None or self.won or self.dead:
            return
        self.wish = action
        self.try_action(action, now)

    def hold_action(self, keys, now: float):
        if self.mode != "play" or self.busy() or self.won or self.dead or self.disguised or self.card_t0 is not None:
            return
        action = self.desired_action(keys)
        if action is None:
            return
        self.wish = action
        self.try_action(action, now)

    def nudge(self, now: float):
        """Headless test: step forward, or turn right if that cell is shut."""
        if self.mode != "play" or self.busy() or self.won or self.dead or self.disguised:
            return
        dc, dr = self.facing
        if self.is_open(self.col + dc, self.row + dr):
            self.try_action(("step", 1), now)
        else:
            self.try_action(("turn", 1), now)

    def _fire_queued(self, now: float):
        queued = self.queued
        self.queued = None
        if queued is not None and not self.won:
            self.try_action(queued, now)

    def update(self, now: float):
        dt = 0.0 if self.last_now is None else max(0.0, min(100.0, now - self.last_now))
        self.last_now = now
        if self.mode in ("title", "lobby"):
            if self.mode == "lobby":
                self._lobby_update(now)
            if self.start_t0 is None:
                self.start_t0 = now
            if self.title_t0 is None:
                self.title_t0 = now
            # Decorative Daleks only: roam, never aim/fire, and never hum
            # (the title theme plays instead).
            for d in self.daleks:
                if d.state in ("aim", "fire"):
                    d.state = "roam"
            self._update_daleks(now, dt)
            self.sfx.stop_hums()
            return
        if self.net is not None:
            self._net_update(now, dt)
        if self.net_card_at is not None and now >= self.net_card_at and self.card_t0 is None:
            self.net_card_at = None  # exterminated: the card follows the survivor's TARDIS
            self.card_t0 = now
            self.card_switched = False
        target = 1.0 if self.watching(now) else 0.0
        step = dt / NET_WATCH_PAN_MS
        self.watch_blend = min(target, self.watch_blend + step) if target > self.watch_blend else max(target, self.watch_blend - step)
        if self.card_t0 is not None:
            t = now - self.card_t0
            if not self.card_switched and t >= CARD_FADE_MS:
                self._next_level()
            if t < 2 * CARD_FADE_MS + CARD_HOLD_MS:
                self._update_hum(now, dt)
                return  # everything waits until the card has faded
            self.card_t0 = None
            self.last_now = now
        if self.won:
            self._stop_taunt()
            self._update_exit(now)
            if not self.exit_hold:
                self._update_hum(now, dt)
                return  # Daleks freeze while Archie leaves
            # Network game: waiting in the TARDIS while the other player plays on.
        if self.start_t0 is None:
            self.start_t0 = now
        if self.demat_t0 is None and now - self.start_t0 >= DEMAT_WAIT_MS:
            self.demat_t0 = self.start_t0 + DEMAT_WAIT_MS
            self.sfx.play("tardis_demat")
        if self.disguised and now - self.disguise_t0 >= DISGUISE_MS:
            self._end_disguise(now)
        if not self.dead:
            if self._on_the_move(now):
                self.survive_ms += dt  # on the move in play only: not the title, card, exit or death
        elif (not self.entry_active and not self.entry_done and now - self.death_t0 >= DEATH_MSG_MS
              and (self.net is None or self.net_over)):
            self._begin_entry()
        if not self.dead:
            self._update_archie(now)
        if self.disguise_pending and not self.busy() and not self.dead and not self.won:
            self.disguise_pending = False
            if now >= self.cooldown_until:
                self._start_disguise(now)
        if self.net_role == "join" and self.net is not None:
            self._net_ease_daleks(dt)  # the host moves the Daleks
        else:
            self._update_daleks(now, dt)
        self._update_hum(now, dt)
        self._update_taunt(now)

    # ----- Reaching the TARDIS --------------------------------------------
    def _begin_exit(self, now: float):
        """Input locks and the Daleks freeze; Archie walks on into the TARDIS."""
        if self.won or self.dead:
            return
        self.won = True
        self.exit_t0 = now
        self.exit_from = (self.col, self.row)
        self.exit_facing = self.facing
        self.exit_steps = 0
        self.moving = self.turning = False
        self.queued = None
        self.wish = None
        self.bump_dir = None
        self.disguise_pending = False
        if self.net is not None:
            self.exit_hold = True  # it waits for the other player
            self._net_arrived(now)

    def _update_exit(self, now: float):
        if self.exit_t0 is None:
            return
        t = now - self.exit_t0
        # Two soft footsteps as he walks in.
        if self.exit_steps < 2 and t >= EXIT_WALK_MS * (0.25 + 0.4 * self.exit_steps):
            self.exit_steps += 1
            self.sfx.step()
        if self.exit_demat_t0 is None and t >= EXIT_DEMAT_AT and not self.exit_hold:
            self.exit_demat_t0 = max(self.exit_t0 + EXIT_DEMAT_AT, self.exit_release_t)
            self.sfx.play("tardis_demat")
        if (self.exit_demat_t0 is not None and self.card_t0 is None
                and now - self.exit_demat_t0 >= self.demat_len + EXIT_CARD_GAP_MS):
            self.card_t0 = now
            self.card_switched = False

    def _next_level(self):
        """Under the black card: the next level gets a brand-new maze."""
        self.card_switched = True
        self.level += 1
        if self.pay_on_exit:  # (a network game pays the +50 to the first one in, on arrival)
            self.level_bonus += SCORE_PER_LEVEL
        seed = self.net_next_seed if self.net_next_seed is not None else time.time_ns()
        self.net_next_seed = None
        self.reset(seed & 0x7FFFFFFF, banner=False)

    def start_tardis_up(self, now: float) -> bool:
        """The TARDIS that brought Archie still stands on its cell (solid for
        5 s, then dematerialising); nobody walks into it meanwhile."""
        return self.demat_t0 is None or now - self.demat_t0 < self.demat_len

    def exit_walk_u(self, now: float) -> float:
        """0..1 along Archie's walk from his corridor into the exit TARDIS."""
        if self.exit_t0 is None:
            return 0.0
        return max(0.0, min(1.0, (now - self.exit_t0) / EXIT_WALK_MS))

    def exit_fade(self, now: float) -> float:
        """Archie's opacity as he walks into the TARDIS (1 = solid)."""
        u = self.exit_walk_u(now)
        return max(0.0, min(1.0, 1.0 - (u - EXIT_FADE_FROM) / (EXIT_FADE_TO - EXIT_FADE_FROM)))

    # ----- Dalek proximity hum -------------------------------------------
    def _hum_distance(self, d: Dalek, archie_pos) -> float:
        """Distance in cells from Archie to Dalek d for its hum: the straight
        line (hedges do not muffle it), optionally blended with the path
        distance through the maze when HUM_PATH_WEIGHT > 0."""
        straight = math.hypot(d.pos[0] - archie_pos[0], d.pos[1] - archie_pos[1])
        if HUM_PATH_WEIGHT <= 0.0:
            return straight
        here = (self.col, self.row)
        if self._hum_from != here:
            self._hum_from = here
            self._hum_dist = self._path_dist(here)
        tx, ty = d.target
        dx, dy = tx - d.pos[0], ty - d.pos[1]
        frac = min(1.0, abs(dx) + abs(dy))  # how far it still has to go to its target
        to_t = self._hum_dist.get(d.target)
        src = (tx - int(math.copysign(1, dx)) if abs(dx) > 1e-6 else tx,
               ty - int(math.copysign(1, dy)) if abs(dy) > 1e-6 else ty)
        from_s = self._hum_dist.get(src, to_t) if frac > 1e-6 else to_t
        if to_t is None:
            to_t = from_s
        if to_t is None:
            path = straight * 3.0  # not reachable (should not happen in a perfect maze)
        else:
            path = to_t * (1.0 - frac) + from_s * frac
        return HUM_PATH_WEIGHT * path + (1.0 - HUM_PATH_WEIGHT) * straight

    def _update_hum(self, now: float, dt: float):
        active = not (self.dead or self.won or self.sfx.muted or self.card_t0 is not None)
        k = 1.0 - math.exp(-dt / HUM_DIST_SMOOTH_MS) if dt > 0 else 1.0
        archie_pos = self.visual_pos(now)
        ax, ay = tile_origin(*archie_pos)
        for i, d in enumerate(self.daleks):
            dist = self._hum_distance(d, archie_pos)
            d.hum_dist = dist
            u = max(0.0, min(1.0, (HUM_FAR - dist) / (HUM_FAR - HUM_NEAR)))
            d.near += (u ** HUM_CURVE - d.near) * k
            if not active:
                fade_ms = HUM_EXIT_FADE_MS if self.won else HUM_FADE_OUT_MS
                d.glide = 0.0 if self.sfx.muted else max(0.0, d.glide - dt / fade_ms)
            elif d.gliding(now):
                d.glide = min(1.0, d.glide + dt / HUM_FADE_IN_MS)
            else:
                d.glide = max(0.0, d.glide - dt / HUM_FADE_OUT_MS)
            vol = HUM_MAX * d.near * d.glide
            if vol < 0.002:
                vol = 0.0
            # Subtle stereo from where the Dalek is on screen, left or right of Archie.
            dx, _ = tile_origin(d.pos[0], d.pos[1])
            pan = max(-1.0, min(1.0, (dx - ax) / (self.view_w / 2))) * HUM_PAN
            d.hum_lr = (vol * (1.0 - max(0.0, pan)), vol * (1.0 + min(0.0, pan)))
            d.hum_vol = vol
        # HUM_CHANNELS reserved channels carry the loudest audible Daleks. When
        # a louder Dalek (by HUM_SWAP_MARGIN) is left out, the quietest channel
        # fades out over HUM_SWAP_MS and the newcomer fades in on it, so the
        # volume never jumps.
        assign, gains, releasing = self.hum_assign, self.hum_ch_gain, self.hum_releasing
        step = dt / HUM_SWAP_MS if dt > 0 else 1.0
        for ci, d in enumerate(assign):
            if d is not None and (d not in self.daleks or d.hum_vol <= 0.0):
                assign[ci], gains[ci], releasing[ci] = None, 0.0, False  # silent already
        audible = sorted((d for d in self.daleks if d.hum_vol > 0.0), key=lambda d: -d.hum_vol)
        want = audible[:HUM_CHANNELS]
        waiting = [d for d in want if d not in assign]
        for ci, d in enumerate(assign):
            if d is None:
                continue
            if d in want:
                releasing[ci] = False
            elif waiting and waiting[0].hum_vol > d.hum_vol * HUM_SWAP_MARGIN and not any(releasing):
                releasing[ci] = True  # one hand-over at a time
        for ci, d in enumerate(assign):
            if d is None:
                continue
            if releasing[ci]:
                gains[ci] = max(0.0, gains[ci] - step)
                if gains[ci] <= 0.0:
                    assign[ci], releasing[ci] = None, False
            else:
                gains[ci] = min(1.0, gains[ci] + step)
        for d in waiting:
            if d in assign:
                continue
            free = [ci for ci, x in enumerate(assign) if x is None]
            if not free:
                break
            assign[free[0]], gains[free[0]], releasing[free[0]] = d, 0.0, False
        # Several Daleks at once: scale them all down so the sum stays sensible.
        total = sum(d.hum_vol * gains[ci] for ci, d in enumerate(assign) if d is not None)
        target = min(1.0, HUM_SUM_MAX / total) if total > 0.0 else 1.0
        kg = 1.0 - math.exp(-dt / HUM_GAIN_SMOOTH_MS) if dt > 0 else 1.0
        self.hum_gain += (target - self.hum_gain) * kg
        if total * self.hum_gain > HUM_SUM_MAX * 1.15:  # never far over, even mid-smoothing
            self.hum_gain = HUM_SUM_MAX * 1.15 / total
        nearby = active and any(d.hum_dist < HUM_STOP_DIST for d in self.daleks)
        for ci in range(HUM_CHANNELS):
            d = assign[ci]
            left, right = d.hum_lr if d is not None else (0.0, 0.0)
            g = gains[ci] * self.hum_gain
            self.sfx.set_hum(ci, left * g, right * g, nearby)

    # ----- Ambient Dalek callouts ------------------------------------------
    @staticmethod
    def _hum_closeness(dist: float) -> float:
        """0..1 loudness for a Dalek `dist` cells away: the hum's own curve
        (silent at HUM_FAR, full at HUM_NEAR, shaped by HUM_CURVE)."""
        u = max(0.0, min(1.0, (HUM_FAR - dist) / (HUM_FAR - HUM_NEAR)))
        return u ** HUM_CURVE

    def _taunt_levels(self, d: Dalek, now: float):
        """(left, right) channel volume for a callout from Dalek d, using the
        same distance (_hum_distance), closeness curve and stereo pan as its
        hum, scaled to TAUNT_MAX instead of HUM_MAX."""
        archie_pos = self.visual_pos(now)
        vol = TAUNT_MAX * self._hum_closeness(self._hum_distance(d, archie_pos))
        if vol < 0.002:
            return 0.0, 0.0
        ax, _ = tile_origin(*archie_pos)
        dx, _ = tile_origin(d.pos[0], d.pos[1])
        pan = max(-1.0, min(1.0, (dx - ax) / (self.view_w / 2))) * HUM_PAN
        return vol * (1.0 - max(0.0, pan)), vol * (1.0 + min(0.0, pan))

    def _stop_taunt(self):
        ch = getattr(self, "taunt_ch", None)
        if ch is not None:
            try:
                ch.stop()
            except Exception:
                pass
        self.taunt_ch = None
        self.taunt_dalek = None
        self.taunt_name = None
        self.taunt_until = -1e9

    def _schedule_taunt(self, now: float):
        self.taunt_at = now + self.taunt_rng.uniform(TAUNT_MIN_MS, TAUNT_MAX_MS)

    def _update_taunt(self, now: float):
        """Every 30-45 s of play the nearest Dalek says a random callout,
        one at a time; while it speaks its volume follows that Dalek."""
        if self.mode != "play" or self.dead or self.won or self.card_t0 is not None:
            self._stop_taunt()
            return
        if self.taunt_ch is not None:
            if now >= self.taunt_until or self.taunt_dalek not in self.daleks or self.sfx.muted:
                # Finished (or muted: M already stopped every channel). Keep
                # taunt_until so a muted-then-unmuted line is not stacked on.
                self.taunt_ch = None
                self.taunt_dalek = None
            else:
                try:
                    self.taunt_ch.set_volume(*self._taunt_levels(self.taunt_dalek, now))
                except Exception:
                    pass
        if self.taunt_at is None:
            self._schedule_taunt(now)
            return
        if now < self.taunt_at:
            return
        # Due. Never over another voice (a callout or "Exterminate!") or while
        # a Dalek is lining up a shot: try again in a moment.
        if (now < self.taunt_until or now < self.voice_until
                or any(d.state in ("aim", "fire") for d in self.daleks)):
            self.taunt_at = now + TAUNT_RETRY_MS
            return
        self._schedule_taunt(now)
        if not self.daleks:
            return
        archie_pos = self.visual_pos(now)
        nearest = min(self.daleks, key=lambda d: self._hum_distance(d, archie_pos))
        left, right = self._taunt_levels(nearest, now)
        if left <= 0.0 and right <= 0.0:
            return  # every Dalek is out of earshot: this one goes unsaid
        pool = [n for n in TAUNT_SOUNDS if n != self.taunt_last] or list(TAUNT_SOUNDS)
        name = self.taunt_rng.choice(pool)
        self.taunt_last = name
        ch = self.sfx.play_voice(name, left, right)
        if ch is None:
            return  # muted or no audio
        self.taunt_ch = ch
        self.taunt_dalek = nearest
        self.taunt_name = name
        self.taunt_until = now + self.sfx.length_ms(name, 1800.0)

    def _update_archie(self, now: float):
        if self.turning:
            if now - self.turn_t0 >= TURN_MS:
                self.turning = False
                self._fire_queued(now)
            return
        if not self.moving:
            return
        # One soft footstep as each half-tile hop lands (two per tile).
        if self.hops_heard == 0 and now - self.move_t0 >= MOVE_MS / 2:
            self.hops_heard = 1
            self.sfx.step()
        if now - self.move_t0 >= MOVE_MS:
            if self.hops_heard < 2:
                self.hops_heard = 2
                self.sfx.step()
            self.col, self.row = self.dst
            self.moving = False
            self.move_grace_until = now + MOVE_GRACE_MS
            if self.grid[self.row][self.col] == EXIT:  # (normally caught as the step starts)
                self._begin_exit(now)
                return
            self._fire_queued(now)

    def _step_u(self, now: float) -> float:
        """One cell, covered as two half-tile hops."""
        t = (now - self.move_t0) / MOVE_MS
        t = max(0.0, min(1.0, t))
        if t < 0.5:
            return ease(t / 0.5) * 0.5
        return 0.5 + ease((t - 0.5) / 0.5) * 0.5

    def visual_pos(self, now: float):
        if self.dead:
            return self.dead_pos
        if self.exit_t0 is not None:
            u = self.exit_walk_u(now)
            u = u * u * (3.0 - 2.0 * u) * 0.35 + u * 0.65  # steady walk, soft start and stop
            sc, sr = self.exit_from
            ec, er = self.exit_cell
            return sc + (ec - sc) * u, sr + (er - sr) * u
        if self.moving:
            u = self._step_u(now)
            sc, sr = self.src
            dc, dr = self.dst
            return sc + (dc - sc) * u, sr + (dr - sr) * u
        if self.bump_dir is not None:
            t = (now - self.bump_t0) / BUMP_MS
            if t < 1.0:
                amp = 0.18 * math.sin(math.pi * max(0.0, t))
                dc, dr = self.bump_dir
                return self.col + dc * amp, self.row + dr * amp
            self.bump_dir = None
        return float(self.col), float(self.row)

    def _visible_tiles(self, cam_x, cam_y):
        margin_x = TILE_W + 8
        margin_top = HEDGE_H + TILE_H
        margin_bottom = TILE_H * 2
        tiles = []
        for row in range(MAZE_SIZE):
            for col in range(MAZE_SIZE):
                wx, wy = tile_origin(col, row)
                sx = wx - cam_x
                sy = wy - cam_y
                if sx < -margin_x or sx > self.view_w + margin_x:
                    continue
                if sy > self.view_h + margin_bottom or sy < -margin_top:
                    continue
                tiles.append((float(row + col), col, row, int(round(sx)), int(round(sy))))
        tiles.sort()
        return tiles

    def draw(self, window: pygame.Surface, now: float):
        """World, effects and HUD are all drawn on the low-res view, then
        nearest-neighbour scaled into an opaque back buffer and copied to the
        window. Text and overlays look as chunky as the maze; nothing with
        per-pixel alpha is blitted straight onto the window surface."""
        if self.frame is None or self.frame.get_size() != window.get_size():
            self.frame = pygame.Surface(window.get_size()).convert()
        view = self.view
        self._draw_world(view, now)
        if self.mode == "title":
            self._draw_title(view, now)
        elif self.mode == "lobby":
            self._draw_lobby(view, now)
        else:
            self._draw_effects(view, now)
            self._draw_minimap(view)
            self._draw_cloak_hud(view, now)
            self._draw_level_hud(view)
            self._draw_score_hud(view, now)
            if self.dead and now - self.death_t0 >= DEATH_MSG_MS and (self.net is None or self.net_over):
                who = self.char_name
                if self.entry_active:
                    self._draw_name_entry(view, now)
                else:
                    extra = [f"Score {self.final_score}"]
                    status = self._entry_status()
                    if status:
                        extra.append(status)
                    pop = None
                    self._draw_panel(
                        view, "EXTERMINATED",
                        f"{who} reached level {self.level}",
                        (255, 96, 72),
                        foot="Enter to Play Again",
                        extra=extra,
                        pop=pop,
                    )
            self._draw_banners(view, now)
            self._draw_card(view, now)
        if ZOOM_SMOOTH:
            pygame.transform.smoothscale(view, self.frame.get_size(), self.frame)
        else:
            pygame.transform.scale(view, self.frame.get_size(), self.frame)
        window.blit(self.frame, (0, 0))

    def _draw_world(self, screen: pygame.Surface, now: float):
        screen.fill(BG_COLOUR)
        vcol, vrow = self.visual_pos(now)
        feet_x, feet_y = tile_origin(vcol, vrow)
        feet_y += TILE_H // 2
        look_x, look_y = feet_x, feet_y
        if self.watch_blend > 0.0 and self.other is not None:
            # Network game: glide over to the player we are watching.
            ox, oy = tile_origin(*self.other.pos)
            k = ease(self.watch_blend)
            look_x += (ox - feet_x) * k
            look_y += (oy + TILE_H // 2 - feet_y) * k
        # Feet sit near the centre; looking a little above them puts his body
        # just above centre. The maze scrolls; tiles are not yaw-rotated.
        cam_x = look_x - self.view_w / 2
        cam_y = (look_y - 46) - self.view_h / 2
        self.cam = (cam_x, cam_y)
        tiles = self._visible_tiles(cam_x, cam_y)

        feet_sx = int(round(feet_x - cam_x))
        feet_sy = int(round(feet_y - cam_y))
        if self.moving:
            t = max(0.0, min(1.0, (now - self.move_t0) / MOVE_MS))
            local = (t * 2.0) % 1.0 if t < 1.0 else 0.0
            feet_sy -= int(round(math.sin(local * math.pi) * 2))
        elif self.exit_t0 is not None:
            t = self.exit_walk_u(now)
            local = (t * 2.0) % 1.0 if t < 1.0 else 0.0
            feet_sy -= int(round(math.sin(local * math.pi) * 2))
        self.archie_feet = (feet_sx, feet_sy)
        # One pass, back to front. Archie, the Daleks and the TARDIS slot in
        # by depth (row + col) when the tiles in front of them start.
        char_depth = vrow + vcol
        entities = []
        # Walking into the exit TARDIS he keeps his normal depth: the box (same
        # depth, drawn after him) and the hedges in front hide him as he goes in.
        # On the title screen the player is not in the maze.
        if self.mode == "play":
            entities.append((char_depth, 1, lambda: self._draw_archie(screen, feet_sx, feet_sy, now)))
        o = self.other
        if self.mode == "play" and o is not None and not o.in_tardis:
            entities.append((o.pos[0] + o.pos[1], 1, lambda: self._draw_other(screen, now, cam_x, cam_y)))
        for d in self.daleks:
            entities.append((d.pos[0] + d.pos[1], 0, lambda d=d: self._draw_dalek(screen, d, now, cam_x, cam_y)))
        ec, er = self.exit_cell
        entities.append((float(ec + er), 2, lambda: self._draw_tardis(screen, cam_x, cam_y, now)))
        if self.mode == "play" and (self.demat_t0 is None or now - self.demat_t0 < self.demat_len):
            dc, dr = self.demat_cell
            # On its own cell, (1, 1), one behind Archie's start cell.
            entities.append((float(dc + dr), 0.5, lambda: self._draw_demat(screen, now, cam_x, cam_y)))
        entities.sort(key=lambda e: (e[0], e[1]))
        k = 0
        for depth, col, row, tx, ty in tiles:
            while k < len(entities) and depth > entities[k][0] + 0.05:
                entities[k][2]()
                k += 1
            cell = self.grid[row][col]
            if cell == WALL:
                self._draw_hedge(screen, col, row, tx, ty)
            else:
                self._draw_dirt(screen, col, row, tx, ty)
        while k < len(entities):
            entities[k][2]()
            k += 1

    def _fog(self, colour, sy: int):
        # Slight aerial perspective: tiles higher on the screen are a touch darker.
        k = 0.88 + 0.14 * max(0.0, min(1.0, sy / self.view_h))
        return mix(colour, k)

    def _draw_dirt(self, screen, col, row, tx: int, ty: int):
        """Clipped dirt diamond in tile space. Not a rotated sprite."""
        variant = (col * 5 + row * 3 + (col ^ row)) % N_DIRT
        cy = ty + TILE_H // 2
        if cy < self.view_h * 0.33:
            shade = 0
        elif cy < self.view_h * 0.66:
            shade = 1
        else:
            shade = 2
        sprite = self.dirt[variant][shade]
        screen.blit(
            sprite,
            (tx - sprite.get_width() // 2, cy - sprite.get_height() // 2),
        )

    def _draw_hedge(self, screen, col: int, row: int, tx: int, ty: int):
        """Pre-rendered leafy block; same footprint and height as the tile."""
        k = max(0.0, min(1.0, ty / self.view_h))
        shade = int(round(k * (N_HEDGE_SHADES - 1)))
        sprite = self.hedges[self.hedge_variant[row][col]][shade]
        screen.blit(sprite, (tx - HEDGE_OX, ty - HEDGE_OY))

    def _draw_tardis(self, screen, cam_x: float, cam_y: float, now: float):
        """The exit TARDIS on the exit cell, anchored by the centre of its base.
        When Archie has walked into it, it dematerialises."""
        ec, er = self.exit_cell
        fx, fy = tile_origin(ec, er)
        fx -= cam_x
        fy += TILE_H // 2 - cam_y
        img = self.tardis
        if fx < -img.get_width() or fx > self.view_w + img.get_width() or fy < -40 or fy > self.view_h + img.get_height():
            return
        if self.exit_demat_t0 is not None:
            alpha, lamp = self._demat_curve(now - self.exit_demat_t0)
        else:
            alpha, lamp = 1.0, 0.0
        self._blit_tardis(screen, fx, fy, alpha, lamp)

    def _blit_tardis(self, screen, fx: float, fy: float, alpha: float, lamp: float):
        """A TARDIS with its base centre at (fx, fy): pre-faded copies (no
        set_alpha on per-pixel-alpha surfaces) and a roof-lamp glow."""
        level = int(round(alpha * (DEMAT_LEVELS - 1)))
        ax, ay = self.tardis_anchor
        if level > 0:
            sh = self.demat_shadows[level]
            screen.blit(sh, (int(fx - sh.get_width() / 2), int(fy - sh.get_height() / 2 + 2)))
            screen.blit(self.demat_fades[level], (int(round(fx - ax)), int(round(fy - ay))))
        li = int(round(lamp * 11))
        if li > 0:
            g = self.demat_lamps[li]
            lx = fx - ax + self.tardis_lamp[0]
            ly = fy - ay + self.tardis_lamp[1]
            screen.blit(g, (int(lx - g.get_width() / 2), int(ly - g.get_height() / 2)))

    def demat_alpha(self, now: float):
        """(TARDIS opacity, lamp brightness), both 0..1.

        For the first 5 s the TARDIS stands solid with its lamp glowing gently.
        Then the dematerialisation: classic fade pulsing (solid, faint, solid
        again, about once a second with our sound) inside an overall fade that
        reaches nothing DEMAT_END_GAP_MS before the sound ends. A longer
        user-supplied sound stretches the fade and slows the pulses to match.
        The roof lamp flashes while it goes.
        """
        if self.demat_t0 is None or now < self.demat_t0:
            since = 0.0 if self.start_t0 is None else now - self.start_t0
            return 1.0, 0.45 + 0.15 * math.sin(2 * math.pi * since / 2000.0)
        return self._demat_curve(now - self.demat_t0)

    def _demat_curve(self, t: float):
        """(opacity, lamp) t ms into a dematerialisation; used at both ends."""
        if t < 0:
            return 1.0, 0.0
        if t >= self.demat_len:
            return 0.0, 0.0
        fade_end = self.demat_len - DEMAT_END_GAP_MS
        period = DEMAT_PULSE_MS * math.sqrt(max(1.0, self.demat_len / DEMAT_MS))
        u = min(1.0, t / fade_end)
        overall = 1.0 - u * u * (3.0 - 2.0 * u)
        pulse = 0.12 + 0.88 * (0.5 + 0.5 * math.cos(2 * math.pi * t / period)) ** 1.5
        alpha = overall * pulse
        flash = (0.5 + 0.5 * math.cos(2 * math.pi * t / (period / 2))) ** 2
        lamp = flash * min(1.0, 0.25 + 1.2 * overall) * (1.0 if t < fade_end else 0.0)
        return alpha, lamp

    def _draw_demat(self, screen, now: float, cam_x: float, cam_y: float):
        alpha, lamp = self.demat_alpha(now)
        if alpha <= 0.0 and lamp <= 0.0:
            return
        dc, dr = self.demat_cell
        fx, fy = tile_origin(dc, dr)
        fx -= cam_x
        fy += TILE_H // 2 - cam_y
        img_w, img_h = self.tardis.get_size()
        if fx < -img_w or fx > self.view_w + img_w or fy < -40 or fy > self.view_h + img_h:
            return
        self._blit_tardis(screen, fx, fy, alpha, lamp)

    def _draw_other(self, screen, now: float, cam_x: float, cam_y: float):
        """The other player in a network game (their own sprite set)."""
        o = self.other
        cs = self.char_sets[o.character]
        fx, fy = tile_origin(*(o.dead_pos if o.dead else o.pos))
        fx = int(round(fx - cam_x))
        fy = int(round(fy + TILE_H // 2 - cam_y))
        o.feet = (fx, fy)
        if fx < -80 or fx > self.view_w + 80 or fy < -20 or fy > self.view_h + SPRITE_H + 20:
            return
        key = FACING_SPRITE[o.facing]
        sprite = cs["sprites"][key]
        ax, ay = cs["anchors"][key]
        pos = (int(round(fx - ax)), int(round(fy - ay)))
        if o.dead:
            t = now - o.death_t0
            screen.blit(self.scorch, (fx - self.scorch.get_width() // 2, fy - 7))
            ghosts = cs["white"][key]
            if t < LASER_MS:
                screen.blit(ghosts[0] if int(t / 45) % 2 == 0 else sprite, pos)
            elif t < DEATH_FADE_MS:
                u = (t - LASER_MS) / (DEATH_FADE_MS - LASER_MS)
                screen.blit(ghosts[max(0, min(len(ghosts) - 1, int(u * (len(ghosts) - 1) + 0.5)))], pos)
            return
        if o.moving:
            local = (o.u * 2.0) % 1.0 if o.u < 1.0 else 0.0
            fy -= int(round(math.sin(local * math.pi) * 2))
            pos = (pos[0], int(round(fy - ay)))
        if o.disguised:
            shade = int(round(max(0.0, min(1.0, (fy - TILE_H // 2) / self.view_h)) * (N_HEDGE_SHADES - 1)))
            self._blit_disguise(screen, fx, fy, DISGUISE_SCALE, shade)
            return
        screen.blit(self.shadow, (fx - self.shadow.get_width() // 2, fy - 6))
        screen.blit(sprite, pos)

    def _draw_dalek(self, screen, d: Dalek, now: float, cam_x: float, cam_y: float):
        fx, fy = tile_origin(d.pos[0], d.pos[1])
        fx = int(round(fx - cam_x))
        fy = int(round(fy + TILE_H // 2 - cam_y))
        d.feet = (fx, fy)
        if fx < -80 or fx > self.view_w + 80 or fy < -20 or fy > self.view_h + DALEK_H + 20:
            return
        key = d.sprite_key(now)
        img = self.dalek_sprites[key]
        ax, ay = self.dalek_anchor[key]
        sh = self.dalek_shadow
        screen.blit(sh, (fx - sh.get_width() // 2, fy - sh.get_height() // 2 + 1))
        screen.blit(img, (int(round(fx - ax)), int(round(fy - ay))))

    def _dalek_point(self, d: Dalek, now: float, table):
        """A point on a Dalek sprite (gun or eye) in view coordinates."""
        key = d.sprite_key(now)
        ax, ay = self.dalek_anchor[key]
        px, py = table[key]
        return d.feet[0] - ax + px, d.feet[1] - ay + py

    def _disguise_sprite(self, scale: float, shade: int):
        variant = ((self.col * 7 + self.row * 13 + self.seed) * 2654435761 >> 7) % N_HEDGE
        base = self.hedges[variant][shade]
        key = (variant, shade, round(scale, 2))
        cached = self.disguise_cache.get(key)
        if cached is None:
            w = max(1, int(round(base.get_width() * scale)))
            h = max(1, int(round(base.get_height() * scale)))
            cached = _finish_rgba(pygame.transform.smoothscale(base, (w, h)))
            if len(self.disguise_cache) > 64:
                self.disguise_cache.clear()
            self.disguise_cache[key] = cached
        return cached

    def _draw_poof(self, screen, fx: int, fy: int, t: float):
        """A little cloud of leaf-dust; t runs 0..1."""
        if not 0.0 <= t <= 1.0:
            return
        alpha = int(235 * (1.0 - t) ** 1.3)
        reach = 6 + 22 * ease(t)
        for i in range(12):
            ang = i * (math.tau / 12) + (i % 3) * 0.35
            r = reach * (0.75 + 0.25 * ((i * 7) % 5) / 4)
            px = fx + math.cos(ang) * r
            py = fy - 18 + math.sin(ang) * r * 0.6
            sizes = self.puffs[max(0, min(4, 4 - int(t * 4) + (i % 2) - 1))]
            puff = sizes[max(0, min(PUFF_LEVELS - 1, round(alpha / 255 * (PUFF_LEVELS - 1))))]
            screen.blit(puff, (int(px - puff.get_width() / 2), int(py - puff.get_height() / 2)))

    def _draw_archie(self, screen, feet_sx: int, feet_sy: int, now: float):
        key = self.pose(now)
        sprite = self.sprites[key]
        ax, ay = self.anchors[key]
        pos = (int(round(feet_sx - ax)), int(round(feet_sy - ay)))
        if self.dead:
            t = now - self.death_t0
            screen.blit(self.scorch, (feet_sx - self.scorch.get_width() // 2, feet_sy - 7))
            ghosts = self.white[key]
            if t < LASER_MS:
                screen.blit(ghosts[0] if int(t / 45) % 2 == 0 else sprite, pos)
            elif t < DEATH_FADE_MS:
                u = (t - LASER_MS) / (DEATH_FADE_MS - LASER_MS)
                level = max(0, min(len(ghosts) - 1, int(u * (len(ghosts) - 1) + 0.5)))
                screen.blit(ghosts[level], pos)
            return
        shade = int(round(max(0.0, min(1.0, (feet_sy - TILE_H // 2) / self.view_h)) * (N_HEDGE_SHADES - 1)))
        since_on = now - self.disguise_t0
        since_off = now - self.disguise_end_t
        if self.disguised:
            if since_on < POOF_MS:
                u = since_on / POOF_MS
                # ease-out with a little overshoot
                pop = 1.0 + 0.12 * math.sin(math.tau * (u - 0.5)) if u > 0.5 else 0.35 + 0.65 * ease(u * 2)
                scale = DISGUISE_SCALE * pop
            else:
                scale = DISGUISE_SCALE
            self._blit_disguise(screen, feet_sx, feet_sy, scale, shade)
            if since_on > POOF_MS * 0.7:
                self._draw_peek_eyes(screen, feet_sx, feet_sy, now)
            self._draw_poof(screen, feet_sx, feet_sy, since_on / POOF_MS)
            return
        if self.exit_t0 is not None:
            level = int(round(self.exit_fade(now) * 16))
            if level <= 0:
                return
            if level < 16:
                faded = self.archie_fades.get((key, level))
                if faded is None:
                    faded = (_alpha_scaled(self.shadow, level / 16), _alpha_scaled(sprite, level / 16))
                    self.archie_fades[(key, level)] = faded
                screen.blit(faded[0], (feet_sx - self.shadow.get_width() // 2, feet_sy - 6))
                screen.blit(faded[1], pos)
                return
        screen.blit(self.shadow, (feet_sx - self.shadow.get_width() // 2, feet_sy - 6))
        screen.blit(sprite, pos)
        if since_off < POOF_MS:
            u = since_off / POOF_MS
            self._blit_disguise(screen, feet_sx, feet_sy, DISGUISE_SCALE * (1.0 - ease(u)), shade)
            self._draw_poof(screen, feet_sx, feet_sy, u)

    def _draw_peek_eyes(self, screen, fx: int, fy: int, now: float):
        """Two little eyes peeping over the hedge so the player can find him."""
        y = fy - 32
        if (now - self.disguise_t0) % 2600 > 2470:  # blink
            for ex in (fx - 7, fx + 2):
                pygame.draw.line(screen, (18, 36, 16), (ex, y + 2), (ex + 4, y + 2))
            return
        look = 2 if self.facing in (DIR_SE, DIR_NE) else 0
        for ex in (fx - 7, fx + 2):
            pygame.draw.rect(screen, (18, 36, 16), (ex - 1, y - 1, 7, 6))
            pygame.draw.rect(screen, (248, 248, 238), (ex, y, 5, 4))
            pygame.draw.rect(screen, (24, 20, 16), (ex + 1 + look // 2, y + 1, 2, 2))

    def _blit_disguise(self, screen, fx: int, fy: int, scale: float, shade: int):
        if scale < 0.05:
            return
        img = self._disguise_sprite(scale, shade)
        k = img.get_width() / HEDGE_SW
        # Ground-diamond centre of the hedge sprite sits on Archie's feet.
        screen.blit(img, (int(round(fx - HEDGE_OX * k)), int(round(fy - (HEDGE_OY + TILE_H // 2) * k))))

    def _draw_effects(self, screen, now: float):
        """Telegraph glow, shout and laser, drawn on the low-res view."""
        for d in self.daleks:
            if d.state == "aim":
                u = max(0.0, min(1.0, (now - d.aim_t0) / max(1.0, d.fire_at - d.aim_t0)))
                ex, ey = self._dalek_point(d, now, self.dalek_eye)
                flick = 0.75 + 0.25 * math.sin(now * 0.06)
                self._solid_glow(screen, ex, ey, (2.0 + 4.0 * u) * flick, EYE_GLOW)
            if d.state == "aim" or (d.state == "fire" and now - d.fire_t0 < LASER_MS + 400):
                self._draw_shout(screen, d, now)
        if self.dead and self.shooter is not None:
            t = now - self.death_t0
            if t < LASER_MS:
                gx, gy = self._dalek_point(self.shooter, now, self.dalek_gun)
                fx, fy = self.archie_feet
                self._draw_laser(screen, (gx, gy), (fx, fy - 34), now, t)
        o = self.other
        if o is not None and o.dead and o.shooter is not None and o.shooter in self.daleks:
            t = now - o.death_t0
            if 0 <= t < LASER_MS:
                gx, gy = self._dalek_point(o.shooter, now, self.dalek_gun)
                self._draw_laser(screen, (gx, gy), (o.feet[0], o.feet[1] - 34), now, t)

    def _draw_shout(self, screen, d: Dalek, now: float):
        fx, fy = d.feet
        x, y = fx, fy - DALEK_H - 6
        jig = int(math.sin(now * 0.05) * 1.5)
        text = "EXTERMINATE!"
        fg = self.font_shout.render(text, True, (255, 236, 120))
        bg = self.font_shout.render(text, True, (60, 10, 6))
        bx = int(x - fg.get_width() / 2) + jig
        by = int(y - fg.get_height())
        for ox, oy in ((-1, 0), (1, 0), (0, -1), (0, 1), (1, 1)):
            screen.blit(bg, (bx + ox, by + oy))
        screen.blit(fg, (bx, by))

    def _solid_glow(self, screen, x: float, y: float, radius: float, colours):
        """A glow impression from solid concentric circles (no alpha)."""
        c = (int(round(x)), int(round(y)))
        for k, colour in zip((1.0, 0.72, 0.45), colours):
            r = int(round(radius * k))
            if r >= 1:
                pygame.draw.circle(screen, colour, c, r)

    def _draw_laser(self, screen, start, end, now: float, t: float):
        """Solid opaque lines straight onto the frame, wide dark blue to a thin
        white core, flickering in width and colour; solid circles at the gun
        tip and the hit point. No intermediate surface, no alpha."""
        rng = random.Random(int(now // 33))
        fade = 1.0 if t < LASER_MS * 0.6 else max(0.0, 1.0 - (t - LASER_MS * 0.6) / (LASER_MS * 0.4))
        if fade <= 0.05:
            return
        a = (int(round(start[0])), int(round(start[1])))
        b = (int(round(end[0])), int(round(end[1])))
        j = rng.uniform(0.75, 1.25)
        hot = rng.random() < 0.5  # flicker between two palettes
        for width, colour in zip(LASER_WIDTHS, LASER_HOT if hot else LASER_COLOURS):
            w = int(round(width * j * fade))
            if w >= 1:
                pygame.draw.line(screen, colour, a, b, w)
        self._solid_glow(screen, a[0], a[1], 4.0 * j * fade, LASER_TIP_GLOW)
        self._solid_glow(screen, b[0], b[1], 5.0 * rng.uniform(0.8, 1.15) * fade, LASER_HIT_GLOW)

    def _draw_cloak_hud(self, screen, now: float):
        """Chameleon Cloak device with charge meter (bottom-right of the view)."""
        if self.disguised:
            left = max(0.0, DISGUISE_MS - (now - self.disguise_t0))
            frac = left / DISGUISE_MS
            bar = (96, 196, 84)
            status = "active"
        elif now < self.cooldown_until:
            left = self.cooldown_until - now
            frac = 1.0 - left / DISGUISE_COOLDOWN_MS
            bar = (150, 128, 84)
            status = "recharge"
        else:
            frac = 1.0
            bar = (96, 196, 84)
            status = "ready"
        icon = self.cloak_icon
        label = self.font_hud.render("Chameleon Cloak", True, (240, 234, 214))
        pad = 6
        bw = max(icon.get_width() + 10, label.get_width() + 8, 70)
        bh = icon.get_height() + label.get_height() + 18
        box = _new_rgba((bw + pad * 2, bh + pad * 2))
        box.fill((36, 24, 16, 200))
        pygame.draw.rect(box, (186, 160, 96, 220), box.get_rect(), 1)
        ix = pad + (bw - icon.get_width()) // 2
        box.blit(icon, (ix, pad))
        # Charge meter under the badge
        mx, my, mw, mh = pad + 4, pad + icon.get_height() + 2, bw - 8, 5
        pygame.draw.rect(box, (20, 14, 10, 230), (mx, my, mw, mh))
        pygame.draw.rect(box, bar, (mx, my, int(mw * max(0.0, min(1.0, frac))), mh))
        if status == "ready" and int(now / 400) % 2 == 0:
            pygame.draw.rect(box, (220, 255, 180, 180), (mx, my, mw, mh), 1)
        box.blit(label, (pad + (bw - label.get_width()) // 2, my + mh + 3))
        box = _finish_rgba(box)
        screen.blit(box, (self.view_w - box.get_width() - 8, self.view_h - box.get_height() - 8))

    def _draw_minimap(self, screen):
        # scale 3 on the low-res view → twice the old on-screen size after 2x zoom.
        scale = 3
        pad = 4
        size = MAZE_SIZE * scale
        box = _new_rgba((size + pad * 2, size + pad * 2))
        box.fill((36, 24, 16, 200))
        pygame.draw.rect(box, (186, 160, 96, 200), box.get_rect(), 1)
        for row in range(MAZE_SIZE):
            for col in range(MAZE_SIZE):
                colour = (28, 72, 36) if self.grid[row][col] == WALL else (142, 98, 56)
                box.fill(colour, (pad + col * scale, pad + row * scale, scale, scale))
        ec, er = self.exit_cell
        pygame.draw.circle(box, (70, 130, 255), (pad + ec * scale + 1, pad + er * scale + 1), 3)
        for d in self.daleks:
            c, r = d.cell()
            pygame.draw.circle(box, (235, 40, 36), (pad + c * scale + 1, pad + r * scale + 1), 2)
        o = self.other
        if o is not None and not o.in_tardis:
            colour = (120, 70, 60) if o.dead else ((255, 150, 210) if o.character == "holly" else (140, 220, 255))
            ox, oy = int(round(o.pos[0])), int(round(o.pos[1]))
            pygame.draw.rect(box, colour, (pad + ox * scale, pad + oy * scale, scale, scale))
        px = pad + int(self.col * scale)
        py = pad + int(self.row * scale)
        pygame.draw.rect(box, (255, 248, 230), (px, py, scale, scale))
        box = _finish_rgba(box)
        screen.blit(box, (self.view_w - box.get_width() - 8, 8))

    def _draw_level_hud(self, screen):
        text = self.font_hud.render(f"Level {self.level}    Daleks: {len(self.daleks)}", True, (240, 234, 214))
        pad_x, pad_y = 8, 5
        box = _new_rgba((text.get_width() + pad_x * 2, text.get_height() + pad_y * 2))
        box.fill((36, 24, 16, 180))
        pygame.draw.rect(box, (186, 160, 96, 200), box.get_rect(), 1)
        box = _finish_rgba(box)
        screen.blit(box, (8, 8))
        screen.blit(text, (8 + pad_x, 8 + pad_y))

    def _draw_score_hud(self, screen, now: float | None = None):
        """Score and high score, under the level box (top-left)."""
        best = self.board.high_score()
        score = self.score
        beating = score > best and best > 0
        hi = max(best, score)
        cream, gold = (240, 234, 214), (255, 220, 120)
        rows = [("Score", str(score), gold if beating else cream),
                ("High", str(hi), gold if beating else (220, 200, 160))]
        labels = [self.font_score.render(a, True, c) for a, _, c in rows]
        values = [self.font_score.render(b, True, c) for _, b, c in rows]
        pad_x, pad_y, gap, line = 8, 4, 14, self.font_score.get_height()
        inner_w = max(l.get_width() for l in labels) + gap + max(v.get_width() for v in values)
        w = max(92, inner_w + pad_x * 2)
        h = pad_y * 2 + line * len(rows)
        box = _new_rgba((w, h))
        box.fill((36, 24, 16, 180))
        pygame.draw.rect(box, (186, 160, 96, 200), box.get_rect(), 1)
        for i, (l, v) in enumerate(zip(labels, values)):
            y = pad_y + i * line
            box.blit(l, (pad_x, y))
            box.blit(v, (w - pad_x - v.get_width(), y))
        y0 = 8 + self.font_hud.get_height() + 10 + 4  # just under the level box
        screen.blit(_finish_rgba(box), (8, y0))
        if self.other is not None:
            o = self.other
            state = " (in the TARDIS)" if o.in_tardis else (" (exterminated)" if o.dead else "")
            line = self.font_hud.render(f"{o.name}: {o.score}{state}", True, (230, 214, 180))
            back = _new_rgba((line.get_width() + 10, line.get_height() + 4))
            back.fill((36, 24, 16, 160))
            screen.blit(_finish_rgba(back), (8, y0 + h + 3))
            screen.blit(line, (13, y0 + h + 5))
        if now is not None and self.bonus_pop_t0 is not None:
            t = (now - self.bonus_pop_t0) / BONUS_POP_MS
            if 0.0 <= t < 1.0:
                # "+20" beside the Score row: drifts up a few pixels and fades.
                fade = 1.0 if t < 0.6 else 1.0 - (t - 0.6) / 0.4
                text = self.bonus_pop_text
                surf = self.font_score.render(text, True, (255, 220, 120))
                shadow = self.font_score.render(text, True, (20, 12, 6))
                x = 8 + w + 5
                y = y0 + pad_y - int(round(6 * t))
                screen.blit(_alpha_scaled(shadow, fade * 0.8), (x + 1, y + 1))
                screen.blit(_alpha_scaled(surf, fade), (x, y))

    def _draw_name_entry(self, screen, now: float):
        """After a death with points: type a name for the leaderboard."""
        best = self.board.high_score()
        cream = (232, 214, 170)
        title = self.font_big.render("EXTERMINATED", True, (255, 96, 72))
        sub = self.font_small.render(f"{self.char_name} reached level {self.level}", True, cream)
        sc = self.font_score.render(f"Score {self.final_score}", True, (255, 236, 170))
        lines = [title, sub, sc]
        if self.final_score >= best and self.final_score > 0:
            lines.append(self.font_score.render("New high score!", True, (255, 220, 120)))
        prompt = self.font_small.render(f"Enter your name (up to {NAME_MAX} letters)", True, cream)
        foot = self.font_small.render("Enter to submit    Esc to skip", True, (200, 180, 140))
        note = self.font_hud.render("Your country's flag is shown next to your score", True, (170, 152, 118))
        sample = self.font_entry.render("W" * NAME_MAX, True, (0, 0, 0))
        field_w, field_h = sample.get_width() + 16, sample.get_height() + 8
        gap = 7
        blocks = lines + [prompt, None, note, foot]
        width = max([t.get_width() for t in blocks if t is not None] + [field_w]) + 40
        height = sum(t.get_height() if t is not None else field_h for t in blocks) + gap * (len(blocks) - 1) + 26
        panel = _new_rgba((width, height))
        panel.fill((36, 24, 16, 255))
        pygame.draw.rect(panel, (212, 170, 90), panel.get_rect(), 2)
        y = 13
        for t in blocks:
            if t is None:
                fx = (width - field_w) // 2
                pygame.draw.rect(panel, (16, 10, 6, 255), (fx, y, field_w, field_h))
                pygame.draw.rect(panel, (255, 220, 120, 255), (fx, y, field_w, field_h), 1)
                name = self.font_entry.render(self.entry_name, True, (255, 248, 230))
                nx = fx + 8
                panel.blit(name, (nx, y + 4))
                if int(now / 450) % 2 == 0 and len(self.entry_name) < NAME_MAX:
                    cx = nx + name.get_width() + 1
                    pygame.draw.rect(panel, (255, 220, 120, 255), (cx, y + 5, 2, field_h - 10))
                y += field_h + gap
                continue
            panel.blit(t, ((width - t.get_width()) // 2, y))
            y += t.get_height() + gap
        panel = _finish_rgba(panel)
        screen.blit(panel, ((self.view_w - width) // 2, (self.view_h - height) // 2))

    def _banner(self, screen, text: str, colour, fade: float, y: int):
        if fade <= 0.02:
            return
        surf = self.font_big.render(text, True, colour)
        shadow = self.font_big.render(text, True, (20, 12, 6))
        x = (self.view_w - surf.get_width()) // 2
        screen.blit(_alpha_scaled(shadow, fade * 0.8), (x + 1, y + 1))
        screen.blit(_alpha_scaled(surf, fade), (x, y))

    def _small_banner(self, screen, text: str, y: int, colour=(255, 236, 170)):
        surf = self.font_small.render(text, True, colour)
        back = _new_rgba((surf.get_width() + 16, surf.get_height() + 8))
        back.fill((20, 12, 6, 190))
        pygame.draw.rect(back, (186, 160, 96, 220), back.get_rect(), 1)
        x = (self.view_w - back.get_width()) // 2
        screen.blit(_finish_rgba(back), (x, y))
        screen.blit(surf, (x + 8, y + 4))

    def _draw_net_banners(self, screen, now: float):
        y = self.view_h - 30
        if self.watching(now):
            name = self.other.name
            self._small_banner(screen, f"Watching {name}", y)
            sub = (f"In the TARDIS - it leaves when {name} gets here" if self.won
                   else f"If {name} reaches the TARDIS you're both through")
            t = self.font_hud.render(sub, True, (230, 214, 180))
            screen.blit(t, ((self.view_w - t.get_width()) // 2, y - 12))
        elif self.dead and self.net is not None and not self.net_over and self.other is not None:
            self._small_banner(screen, "EXTERMINATED", y, (255, 96, 72))
        if self.net_notice is not None:
            text, t0 = self.net_notice
            if now - t0 < NET_NOTICE_MS:
                self._small_banner(screen, text, 64, (255, 140, 110))
            else:
                self.net_notice = None

    def _draw_banners(self, screen, now: float):
        self._draw_net_banners(screen, now)
        # "Level N" as a level starts (the card already said it after a level change).
        if self.banner and self.start_t0 is not None and not self.won and not self.dead:
            t = now - self.start_t0
            if t < LEVEL_BANNER_MS:
                fade = min(1.0, t / 300.0, (LEVEL_BANNER_MS - t) / 600.0)
                self._banner(screen, f"Level {self.level}", (255, 236, 170), fade, 40)
        # "Level complete!" while the TARDIS dematerialises with Archie inside.
        if self.exit_demat_t0 is not None and self.card_t0 is None:
            t = now - self.exit_demat_t0
            fade = max(0.0, min(1.0, t / COMPLETE_FADE_IN_MS,
                                (self.demat_len + EXIT_CARD_GAP_MS - t) / COMPLETE_FADE_OUT_MS))
            level = int(round(fade * (len(self.complete_fades) - 1)))
            if level > 0:
                img = self.complete_fades[level]
                screen.blit(img, ((self.view_w - img.get_width()) // 2, int(self.view_h * COMPLETE_Y)))

    def _draw_card(self, screen, now: float):
        """Fade to black, "Level N", fade into the new maze."""
        if self.card_t0 is None:
            return
        t = now - self.card_t0
        if t < CARD_FADE_MS:
            a = t / CARD_FADE_MS
        elif t < CARD_FADE_MS + CARD_HOLD_MS:
            a = 1.0
        else:
            a = max(0.0, 1.0 - (t - CARD_FADE_MS - CARD_HOLD_MS) / CARD_FADE_MS)
        self.fader.fill((0, 0, 0, int(255 * a)))
        screen.blit(self.fader, (0, 0))
        if self.card_switched:
            level = self.level
            text_fade = min(1.0, (t - CARD_FADE_MS) / 250.0) if t < CARD_FADE_MS + CARD_HOLD_MS else a
        else:
            level = self.level + 1
            text_fade = a
        self._banner(screen, f"Level {level}", (255, 236, 170), text_fade, self.view_h // 2 - 24)
        sub = self.font_small.render(f"{daleks_for_level(level)} Daleks", True, (232, 214, 170))
        if text_fade > 0.02:
            screen.blit(_alpha_scaled(sub, text_fade), ((self.view_w - sub.get_width()) // 2, self.view_h // 2 + 4))

    def _draw_panel(self, screen, title_text: str, sub_text: str, title_colour, foot: str | None = None,
                    extra: list[str] | None = None, pop: tuple[str, float] | None = None,
                    strip_dots: bool = True):
        """Death / message panel. No full stops on any line. `pop` is a gold
        (text, t 0..1) that drifts up and fades beside the first extra line."""
        dot = "" if strip_dots else "."
        title_text = title_text.replace(".", dot)
        sub_text = sub_text.replace(".", dot)
        if foot:
            foot = foot.replace(".", dot)
        title = self.font_big.render(title_text, True, title_colour)
        sub = self.font_small.render(sub_text, True, (232, 214, 170))
        foot_s = self.font_small.render(foot, True, (232, 214, 170)) if foot else None
        extra_s = [self.font_score.render(e.replace(".", dot), True, (255, 236, 170) if i == 0 else (200, 180, 140))
                   for i, e in enumerate(extra or [])]
        gap = 8
        lines = [title, sub] + extra_s + ([foot_s] if foot_s else [])
        width = max(t.get_width() for t in lines) + 40
        height = sum(t.get_height() for t in lines) + gap * (len(lines) - 1) + 28
        panel = _new_rgba((width, height))
        panel.fill((36, 24, 16, 255))
        pygame.draw.rect(panel, (212, 170, 90), panel.get_rect(), 2)
        y = 14
        pop_at = None
        for t in lines:
            panel.blit(t, ((width - t.get_width()) // 2, y))
            if extra_s and t is extra_s[0]:
                pop_at = ((width + t.get_width()) // 2 + 6, y)
            y += t.get_height() + gap
        if pop and pop_at is not None:
            text, pt = pop
            fade = 1.0 if pt < 0.6 else 1.0 - (pt - 0.6) / 0.4
            surf = self.font_score.render(text, True, (255, 220, 120))
            shadow = self.font_score.render(text, True, (20, 12, 6))
            px, py = pop_at[0], pop_at[1] - int(round(6 * pt))
            panel.blit(_alpha_scaled(shadow, fade * 0.8), (px + 1, py + 1))
            panel.blit(_alpha_scaled(surf, fade), (px, py))
        panel = _finish_rgba(panel)
        screen.blit(panel, ((self.view_w - width) // 2, (self.view_h - height) // 2))


    # ----- Two players on a local network -----------------------------------
    # The host runs the maze, the Daleks and the TARDIS rules; the joiner moves
    # its own player at once and mirrors everything else. Transport: net.py.
    def open_lobby(self, now: float | None = None, stage: str = "menu", addr=None):
        """Network lobby over the title maze: Host or Join."""
        self._net_close()
        self.mode = "lobby"
        self.lobby = {"stage": "menu", "pick": 0, "char": self.title_pick, "hosts": [], "sel": 0,
                      "msg": "", "t0": time.monotonic()}
        if stage == "host":
            self._lobby_host()
        elif stage == "scan":
            self._lobby_scan()
        elif stage == "join" and addr is not None:
            self._lobby_join(addr)

    def _lobby_host(self):
        lb = self.lobby
        try:
            self.net = net.Link("host", port=self.net_port)
        except OSError as exc:
            lb["stage"], lb["msg"] = "menu", f"Can't use UDP port {self.net_port}: {exc.strerror or exc}"
            return
        self.net_role = "host"
        char = CHAR_ORDER[lb["char"]]
        self.beacon = net.Beacon({"name": net.host_label(), "char": char, "level": 1,
                                  "port": self.net.port, "ip": net.local_ip()}, port=self.discovery_port)
        lb["stage"], lb["msg"] = "hosting", ""

    def _lobby_scan(self):
        lb = self.lobby
        if self.scanner is not None:
            self.scanner.close()
        self.scanner = net.Scanner(port=self.discovery_port)
        lb["stage"], lb["hosts"], lb["sel"] = "scanning", [], 0
        lb["msg"] = self.scanner.error or ""

    def _lobby_join(self, addr):
        lb = self.lobby
        self._net_close()
        try:
            self.net = net.Link("join", port=0, peer=tuple(addr))
        except OSError as exc:
            lb["stage"], lb["msg"] = "menu", f"Network error: {exc.strerror or exc}"
            return
        self.net_role = "join"
        self.net.send_event("hello", v=net.PROTOCOL, name=net.host_label())
        lb["stage"], lb["addr"], lb["t0"], lb["msg"] = "joining", tuple(addr), time.monotonic(), ""

    def _lobby_key(self, key: int, now: float):
        lb = self.lobby
        stage = lb["stage"]
        if key == pygame.K_ESCAPE:
            if stage == "menu":
                self._enter_title(self.seed)
            else:
                self._net_close()
                lb["stage"], lb["msg"] = "menu", ""
            return
        if stage == "menu":
            if key in (pygame.K_UP, pygame.K_w, pygame.K_DOWN, pygame.K_s):
                lb["pick"] = 1 - lb["pick"]
            elif key in (pygame.K_LEFT, pygame.K_a, pygame.K_RIGHT, pygame.K_d):
                lb["char"] = 1 - lb["char"]
            elif key in (pygame.K_1, pygame.K_KP1):
                lb["char"] = 0
            elif key in (pygame.K_2, pygame.K_KP2):
                lb["char"] = 1
            elif key in RESTART_KEYS:
                self._lobby_host() if lb["pick"] == 0 else self._lobby_scan()
        elif stage == "scanning":
            hosts = lb["hosts"]
            if key in (pygame.K_UP, pygame.K_w) and hosts:
                lb["sel"] = (lb["sel"] - 1) % len(hosts)
            elif key in (pygame.K_DOWN, pygame.K_s) and hosts:
                lb["sel"] = (lb["sel"] + 1) % len(hosts)
            elif key == pygame.K_r:
                self._lobby_scan()
            elif key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE) and hosts:
                self._lobby_join(hosts[lb["sel"]][0])

    def _lobby_update(self, now: float):
        lb = self.lobby
        if lb is None:
            return
        stage = lb["stage"]
        if stage == "hosting":
            self.beacon.tick(char=CHAR_ORDER[lb["char"]])
            self.net.poll()
            for kind, data in self.net.take_events():
                if kind == "hello" and data.get("v") == net.PROTOCOL:
                    mine = CHAR_ORDER[lb["char"]]
                    theirs = CHAR_ORDER[1 - lb["char"]]
                    seed = time.time_ns() & 0x7FFFFFFF
                    self.net.send_event("start", seed=seed, level=1, host=mine, join=theirs)
                    self._net_start_play(seed, 1, mine, theirs, now)
                    return
        elif stage == "scanning":
            sc = self.scanner
            if sc is not None:
                sc.poll()
                lb["hosts"] = sc.hosts()
                if lb["sel"] >= len(lb["hosts"]):
                    lb["sel"] = 0
                if sc.done():
                    sc.close()
                    self.scanner = None
        elif stage == "joining":
            self.net.poll()
            for kind, data in self.net.take_events():
                if kind == "start":
                    self._net_start_play(int(data["seed"]), int(data.get("level", 1)),
                                         data["join"], data["host"], now)
                    return
            if time.monotonic() - lb["t0"] > NET_JOIN_WAIT_S:
                self._net_close()
                lb["stage"], lb["msg"] = "menu", "No answer from that game"

    def _draw_lobby(self, screen, now: float):
        veil = _new_rgba((self.view_w, self.view_h))
        veil.fill((10, 6, 4, 150))
        screen.blit(_finish_rgba(veil), (0, 0))
        title = self.font_title.render("Daleks in Hedges", True, (255, 236, 170))
        screen.blit(self.font_title.render("Daleks in Hedges", True, (20, 12, 6)), ((self.view_w - title.get_width()) // 2 + 1, 8))
        screen.blit(title, ((self.view_w - title.get_width()) // 2, 7))
        lb = self.lobby
        stage = lb["stage"]
        char = self.char_sets[CHAR_ORDER[lb["char"]]]["name"]
        other = self.char_sets[CHAR_ORDER[1 - lb["char"]]]["name"]
        mark = lambda on, text: f"> {text} <" if on else text
        if stage == "menu":
            extra = [mark(lb["pick"] == 0, f"Host a game as {char}"), mark(lb["pick"] == 1, "Join a game on this network")]
            if lb["msg"]:
                extra.append(lb["msg"])
            if lb["pick"] == 1:
                extra[0], extra[1] = extra[1], extra[0]  # the chosen line is the bright one
            self._draw_panel(screen, "Two Players", "Up/Down: host or join    Left/Right: Archie or Holly",
                             (255, 220, 120), foot="Enter to choose    Esc back", extra=extra, strip_dots=False)
        elif stage == "hosting":
            addr = f"{net.local_ip()}  UDP {self.net.port}"
            self._draw_panel(screen, "Hosting", f"Waiting for a player to join{'.' * (int(now / 400) % 4)}",
                             (255, 220, 120), foot="Esc to cancel",
                             extra=[f"You are {char}, they will be {other}", addr], strip_dots=False)
        elif stage == "scanning":
            hosts = lb["hosts"]
            lines = []
            for i, ((ip, port), info) in enumerate(hosts):
                who = CHARACTERS.get(info.get("char"), {}).get("name", "?")
                lines.append(mark(i == lb["sel"], f"{info.get('name', ip)} as {who}  {ip}"))
            if lines:
                lines.insert(0, lines.pop(lb["sel"]))
            searching = self.scanner is not None
            sub = "Looking for games" + "." * (int(now / 400) % 4) if searching else f"Found {len(hosts)} game{'s' if len(hosts) != 1 else ''}"
            if not lines:
                lines = [lb["msg"] or ("" if searching else "No games found - is the host waiting?")]
            self._draw_panel(screen, "Join a Game", sub, (255, 220, 120),
                             foot="Enter join    R search again    Esc back", extra=[l for l in lines if l] or None, strip_dots=False)
        elif stage == "joining":
            ip, port = lb["addr"]
            self._draw_panel(screen, "Joining", f"Contacting {ip} on UDP {port}", (255, 220, 120), foot="Esc to cancel", strip_dots=False)

    def _net_close(self):
        for attr in ("net", "beacon", "scanner"):
            obj = getattr(self, attr, None)
            if obj is not None:
                obj.close()
            setattr(self, attr, None)
        self.net_role = None

    def _net_start_play(self, seed: int, level: int, mine: str, theirs: str, now: float):
        if self.beacon is not None:
            self.beacon.close()
            self.beacon = None
        if self.scanner is not None:
            self.scanner.close()
            self.scanner = None
        self.lobby = None
        self.sfx.stop_title_theme()
        self._apply_character(mine)
        self.title_pick = CHAR_ORDER.index(mine)
        self.other = RemotePlayer(theirs, self.char_sets[theirs]["name"])
        self.mode = "play"
        self.level = level
        self.net_over = False
        self.net_notice = None
        self.watch_blend = 0.0
        self._reset_score()
        self.reset(seed, banner=True)
        self.start_t0 = now
        self.last_now = now
        self.net_last_now = now

    def _safe_zone(self):
        dist = self._path_dist(self.demat_cell)
        return frozenset(c for c, d in dist.items() if d <= NET_SAFE_RADIUS)

    def _net_after_reset(self):
        """Both players start by the TARDIS that brought them: the host on the
        usual cell, the joiner one cell further in."""
        self.net_departing = False
        self.net_card_at = None
        self.net_first = None
        sc = self.start_cell
        nxt = [(sc[0] + d[0], sc[1] + d[1], d) for d in DIR_ORDER
               if self.is_open(sc[0] + d[0], sc[1] + d[1]) and (sc[0] + d[0], sc[1] + d[1]) != self.demat_cell]
        second, second_dir = ((nxt[0][0], nxt[0][1]), nxt[0][2]) if nxt else (sc, self.facing)
        if self.net_role == "join":
            self._place_archie(second, second_dir)
            self.dead_pos = (float(second[0]), float(second[1]))
            if self.other is not None:
                self.other.reset(sc, (sc[0] - self.demat_cell[0], sc[1] - self.demat_cell[1]))
        elif self.other is not None:
            self.other.reset(second, second_dir)

    def watching(self, now: float) -> bool:
        """Out of it (exterminated, or waiting in the TARDIS) with the other
        player still going: the camera follows them."""
        o = self.other
        if self.mode != "play" or self.net is None or o is None or o.dead or o.in_tardis:
            return False
        if self.dead:
            return now - self.death_t0 >= DEATH_MSG_MS
        return self.won and self.exit_hold and self.exit_walk_u(now) >= 1.0

    # Messages ---------------------------------------------------------------
    def _net_state(self, now: float) -> dict:
        st = {"lv": self.level, "c": [self.col, self.row], "s": list(self.src), "d": list(self.dst),
              "m": int(self.moving), "u": round(self._step_u(now), 3) if self.moving else 0,
              "f": self.facing_index, "k": int(self.disguised), "x": int(self.dead), "w": int(self.won),
              "p": self.score, "ch": self.character}
        if self.net_role == "host":
            st["fst"] = self.net_first
            st["dk"] = [[round(d.pos[0], 3), round(d.pos[1], 3), d.target[0], d.target[1],
                         DIR_ORDER.index(d.facing), DALEK_STATES.index(d.state), int(d.gliding(now))]
                        for d in self.daleks]
        return st

    def _net_update(self, now: float, dt: float):
        link = self.net
        link.poll()
        for kind, data in link.take_events():
            self._net_event(kind, data, now)
            if self.net is None:
                return
        if link.state_fresh:
            link.state_fresh = False
            self._net_apply_state(link.state or {}, now)
        if self.other is not None:
            self.other.ease_towards(dt)
        if link.state_due():
            link.send_state(self._net_state(now))
        if link.lost() and not self.net_over:
            self._net_drop(now)
            return
        if self.net_role == "host":
            self._net_check_depart(now)

    def _net_apply_state(self, st: dict, now: float):
        o = self.other
        if o is None:
            return
        o.score = int(st.get("p", o.score))
        if st.get("lv") != self.level or self.card_t0 is not None:
            return  # from the other side of a level change
        cell = tuple(st.get("c", o.cell))
        if self.net_role == "host" and cell != o.cell and not self._net_legal(o.cell, cell):
            return  # not a move a player could make (through a hedge): keep the last good one
        o.cell = cell
        o.src, o.dst = tuple(st.get("s", cell)), tuple(st.get("d", cell))
        moving = bool(st.get("m"))
        if moving and (not o.moving or o.dst != tuple(st.get("d", cell))):
            o.u = float(st.get("u", 0.0))
        elif moving:
            o.u = max(o.u, float(st.get("u", 0.0)))
        o.moving = moving
        o.facing = DIR_ORDER[int(st.get("f", 0)) % 4]
        if bool(st.get("k")) and not o.disguised:
            o.disguise_t0 = now
        o.disguised = bool(st.get("k"))
        if self.net_role == "join":
            self.net_first = st.get("fst")
            if "dk" in st:
                self._net_mirror_daleks(st["dk"], now)

    def _net_legal(self, a, b) -> bool:
        """b is open and reachable from a in a few steps (a packet or two lost)."""
        if not self.is_open(*b):
            return False
        frontier, seen = [tuple(a)], {tuple(a)}
        for _ in range(3):
            nxt = []
            for c, r in frontier:
                for dc, dr in DIR_ORDER:
                    n = (c + dc, r + dr)
                    if n == b:
                        return True
                    if n not in seen and self.is_open(*n):
                        seen.add(n)
                        nxt.append(n)
            frontier = nxt
        return False

    def _net_mirror_daleks(self, rows, now: float):
        if len(rows) != len(self.daleks):
            self.daleks = [Dalek(int(round(r[0])), int(round(r[1])), DIR_ORDER[r[4] % 4], random.Random(i))
                           for i, r in enumerate(rows)]
        for d, (x, y, tx, ty, f, st, glide) in zip(self.daleks, rows):
            d.net_pos = (x, y)
            if abs(x - d.pos[0]) + abs(y - d.pos[1]) > 1.5:
                d.pos = [x, y]
            d.target = (tx, ty)
            d.face(DIR_ORDER[f % 4], now)
            state = DALEK_STATES[st % 3]
            if state != d.state:
                if state == "aim":
                    d.aim_t0 = now
                    d.fire_at = now + DALEK_TELEGRAPH_MS
                elif state == "fire":
                    d.fire_t0 = now
                d.state = state
            d.wait_until = 0.0 if glide else now + 1e6

    def _net_ease_daleks(self, dt: float):
        k = 1.0 - math.exp(-dt / NET_SMOOTH_MS) if dt > 0 else 0.0
        for d in self.daleks:
            if d.net_pos is not None:
                d.pos[0] += (d.net_pos[0] - d.pos[0]) * k
                d.pos[1] += (d.net_pos[1] - d.pos[1]) * k

    def _net_event(self, kind: str, data: dict, now: float):
        o = self.other
        if kind == "spot":         # (joiner) a Dalek shouted at me
            self._stop_taunt()
            if now >= self.voice_until:
                self.voice_until = now + self.sfx.length_ms("exterminate", 1600.0)
                self.sfx.play("exterminate")
            if not (self.dead or self.won):
                self.spot_bonus += SCORE_PER_SPOT
                self.bonus_pop_t0 = now
                self.bonus_pop_text = f"+{SCORE_PER_SPOT}"
        elif kind == "kill":       # (joiner) the host's Dalek got me
            i = int(data.get("d", -1))
            shooter = self.daleks[i] if 0 <= i < len(self.daleks) else None
            if shooter is not None:
                shooter.state, shooter.fire_t0 = "fire", now
            self._kill(now, shooter)
        elif kind == "died" and o is not None:      # the other player was exterminated
            i = int(data.get("d", -1))
            self._net_other_down(now, self.daleks[i] if 0 <= i < len(self.daleks) else None)
        elif kind == "tardis" and o is not None:    # (host) the joiner stepped into the TARDIS
            o.dead = False
            o.in_tardis = True
            self._net_arrival("join", now)
        elif kind == "bonus":      # (joiner) first into the TARDIS
            self._net_bonus(now)
        elif kind == "first":      # (joiner) the host got there first
            self.net_first = data.get("who")
            if o is not None and data.get("who") == "host":
                o.in_tardis = True
        elif kind == "level":
            self.net_next_seed = int(data["seed"])
            self._net_depart(now)
        elif kind == "gameover":
            self.net_over = True

    # Rules (host) -------------------------------------------------------------
    def _net_spotted_other(self, now: float):
        """A host Dalek shouted at the joiner: their +20, one voice at a time."""
        if now < self.other_voice_until:
            return
        self.other_voice_until = now + self.sfx.length_ms("exterminate", 1600.0)
        self.net.send_event("spot")
        if now >= self.voice_until:
            self._stop_taunt()
            self.voice_until = self.other_voice_until
            self.sfx.play("exterminate")

    def _net_kill_other(self, now: float, shooter: Dalek):
        o = self.other
        if o is None or o.dead or o.in_tardis or self.net is None:
            return
        self.sfx.play("laser")
        self.net.send_event("kill", d=self.daleks.index(shooter) if shooter in self.daleks else -1)
        self._net_other_down(now, shooter)

    def _net_other_down(self, now: float, shooter):
        o = self.other
        o.dead = True
        o.death_t0 = now
        o.shooter = shooter
        o.moving = False
        o.disguised = False
        o.dead_pos = tuple(o.pos)

    def _net_local_died(self, now: float, shooter):
        if self.net_role == "host":
            self.net.send_event("died", d=self.daleks.index(shooter) if shooter in self.daleks else -1)

    def _net_arrived(self, now: float):
        if self.net_role == "host":
            self._net_arrival("host", now)
        else:
            self.net.send_event("tardis")

    def _net_arrival(self, who: str, now: float):
        if self.net_first is not None:
            return  # second one in: no +50
        self.net_first = who
        if who == "host":
            self._net_bonus(now)
            if self.net is not None:
                self.net.send_event("first", who="host")
        elif self.net is not None:
            self.net.send_event("bonus")

    def _net_bonus(self, now: float):
        self.level_bonus += SCORE_PER_LEVEL
        self.bonus_pop_t0 = now
        self.bonus_pop_text = f"+{SCORE_PER_LEVEL}"

    def _net_check_depart(self, now: float):
        o = self.other
        if o is None or self.net_departing or self.net_over or self.card_t0 is not None:
            return
        if self.dead and o.dead:
            self.net_over = True
            self.net.send_event("gameover")
            return
        me_out = self.won or self.dead
        other_out = o.in_tardis or o.dead
        if (self.won or o.in_tardis) and me_out and other_out:
            seed = time.time_ns() & 0x7FFFFFFF
            self.net.send_event("level", seed=seed, level=self.level + 1)
            self.net_next_seed = seed
            self._net_depart(now)

    def _net_depart(self, now: float):
        """Both are through (or one is through and the other was exterminated):
        the TARDIS goes, and everyone moves on to the next level."""
        if self.net_departing:
            return
        self.net_departing = True
        if self.won:
            self.exit_hold = False
            self.exit_release_t = now
        else:  # exterminated: fade out as the survivor's TARDIS goes
            self.net_card_at = now + EXIT_DEMAT_AT + self.demat_len + EXIT_CARD_GAP_MS

    def _net_drop(self, now: float):
        """No packets for a while: carry on alone."""
        first = self.net_first
        self._net_close()
        self.other = None
        self.net_notice = ("Connection lost - playing on alone", now)
        self.pay_on_exit = first is None
        self.safe_cells = frozenset()
        for d in self.daleks:  # the joiner takes over its mirrored Daleks
            if d.net_pos is not None:
                d.pos = [d.net_pos[0], d.net_pos[1]]
                d.net_pos = None
            if d.wait_until > now + 1000:
                d.wait_until = now
            if d.state != "roam":
                d.state = "roam"
        if self.exit_hold:
            self.exit_hold = False
            self.exit_release_t = now
        if self.dead:
            self.net_card_at = None  # nobody left to carry on: game over
            self.net_next_seed = None

    def _title_char_select_bounds(self):
        """Top, bottom and vertical centre of the character-choice block.

        Matches `_draw_title_portraits`: frames from pad_top above the sprites
        down through pad_bot, plus the SELECTED label 14px above the frame.
        """
        pad_top, pad_bot = 14, 34
        img_h = max(self.char_sets[cid]["portrait"].get_height() for cid in CHAR_ORDER)
        y0 = self.view_h // 2 - 50
        by = y0 - pad_top
        bh = pad_top + img_h + pad_bot
        top = by - 14  # SELECTED sits above the frame
        bot = by + bh
        return top, bot, (top + bot) / 2.0, y0

    def _title_footer_help_y(self) -> int:
        """Y of the first controls-help line on the title screen."""
        copy_margin = 8
        copy_gap = 10
        n_help = 3
        copy_lines = _wrap_text(self.font_title_copy, TITLE_COPYRIGHT, self.view_w - copy_margin * 2)
        copy_y0 = self.view_h - 6 - copy_gap * len(copy_lines)
        return copy_y0 - 6 - 14 * n_help

    def _draw_title(self, screen, now: float):
        """Title overlay on the live decorative maze: name, portraits, help."""
        # Soft dark veil so the text is readable over the scrolling maze.
        veil = _new_rgba((self.view_w, self.view_h))
        veil.fill((10, 6, 4, 110))
        screen.blit(_finish_rgba(veil), (0, 0))
        title = self.font_title.render("Daleks in Hedges", True, (255, 236, 170))
        shadow = self.font_title.render("Daleks in Hedges", True, (20, 12, 6))
        tx = (self.view_w - title.get_width()) // 2
        screen.blit(shadow, (tx + 1, 8))
        screen.blit(title, (tx, 7))
        t0 = self.title_t0 if self.title_t0 is not None else now
        show_scores = int((now - t0) // TITLE_PANEL_MS) % 2 == 1
        if show_scores:
            # Heading + panel share the character-choice block's vertical centre.
            self._draw_title_scores(screen, title_bottom=7 + title.get_height())
        else:
            sub = self.font_title_sub.render("Choose Archie or Holly", True, (220, 200, 160))
            sub_y = 7 + title.get_height() + 4
            screen.blit(sub, ((self.view_w - sub.get_width()) // 2, sub_y))
            self._draw_title_portraits(screen)
        self._draw_title_footer(screen)

    def _draw_title_scores(self, screen, title_bottom: int):
        """Top 10 heading + panel, centred on the character-choice block."""
        entries = self.board.top(TOP_N)
        status = self.board.status
        cream, gold, dim = (240, 234, 214), (255, 220, 120), (200, 180, 140)
        heading = self.font_title_sub.render("Top 10 Scores", True, (220, 200, 160))
        head_gap = 6  # space between heading and panel
        font = self.font_title_help
        pad_x, pad_y = 16, 8
        rank_w = font.size("10.")[0]
        name_w = font.size("W" * NAME_MAX)[0]
        score_w = font.size("000000")[0]
        col_gap = 18
        flag_w, flag_gap = 20, 8  # flags are 20x10 (flagcdn w20)
        width = pad_x * 2 + rank_w + flag_gap + flag_w + flag_gap + name_w + col_gap + score_w
        _, _, centre_y, _ = self._title_char_select_bounds()
        min_top = title_bottom + 4
        max_bot = self._title_footer_help_y() - 8
        avail = max(40, max_bot - min_top)

        def unit_height(row_h: int) -> tuple[int, int]:
            offline_extra = row_h if status == "offline" else 0
            panel_h = pad_y * 2 + row_h * TOP_N + offline_extra
            return heading.get_height() + head_gap + panel_h, panel_h

        row_h = 14
        unit_h, panel_h = unit_height(row_h)
        while unit_h > avail and row_h > 10:
            row_h -= 1
            unit_h, panel_h = unit_height(row_h)

        top = int(round(centre_y - unit_h / 2.0))
        top = max(min_top, min(top, max_bot - unit_h))
        screen.blit(heading, ((self.view_w - heading.get_width()) // 2, top))
        panel_top = top + heading.get_height() + head_gap

        panel = _new_rgba((width, panel_h))
        panel.fill((36, 24, 16, 215))
        pygame.draw.rect(panel, (255, 220, 120, 255), panel.get_rect(), 2)
        x_rank = pad_x
        x_flag = x_rank + rank_w + flag_gap
        x_name = x_flag + flag_w + flag_gap
        x_score = x_name + name_w + col_gap + score_w  # right edge
        if not entries:
            msg = "Loading scores" if status == "loading" else "No scores yet - be the first!"
            t = font.render(msg, True, cream)
            panel.blit(t, ((width - t.get_width()) // 2, (panel_h - t.get_height()) // 2))
        for i in range(TOP_N):
            y = pad_y + i * row_h
            colour = gold if i == 0 else (cream if i < 3 else dim)
            if i < len(entries):
                name, value, country = entries[i]
                flag = self.flags.get(country) if country else None
                if flag is not None:
                    fy = y + (font.get_height() - flag.get_height()) // 2
                    pygame.draw.rect(panel, (20, 14, 10, 255), (x_flag - 1, fy - 1, flag.get_width() + 2, flag.get_height() + 2))
                    panel.blit(flag, (x_flag + (flag_w - flag.get_width()) // 2, fy))
                elif country:  # not downloaded (yet): the code, muted
                    c = self.font_title_copy.render(country, True, (150, 134, 104))
                    panel.blit(c, (x_flag + (flag_w - c.get_width()) // 2, y + (font.get_height() - c.get_height()) // 2))
                r = font.render(f"{i + 1}.", True, colour)
                panel.blit(r, (x_rank + rank_w - r.get_width(), y))
                panel.blit(font.render(name, True, colour), (x_name, y))
                v = font.render(str(value), True, colour)
                panel.blit(v, (x_score - v.get_width(), y))
        if status == "offline":
            t = self.font_title_copy.render("Offline - showing saved scores", True, dim)
            panel.blit(t, ((width - t.get_width()) // 2, pad_y + row_h * TOP_N + 2))
        screen.blit(_finish_rgba(panel), ((self.view_w - width) // 2, panel_top))

    def _draw_title_portraits(self, screen):
        # Portraits — boxes sized for the larger title fonts (name under each).
        pad_x, pad_top, pad_bot = 20, 14, 34
        gap = 44
        portraits = []
        for i, cid in enumerate(CHAR_ORDER):
            portraits.append((cid, self.char_sets[cid]))
        # Centre by the highlight frames (wider than the sprites).
        total_w = sum(cs["portrait"].get_width() + pad_x * 2 for _, cs in portraits) + gap
        x0 = (self.view_w - total_w) // 2 + pad_x
        _, _, _, y0 = self._title_char_select_bounds()
        for i, (cid, cs) in enumerate(portraits):
            img = cs["portrait"]
            selected = i == self.title_pick
            name = self.font_title_sub.render(cs["name"], True, (255, 236, 170) if selected else (200, 180, 140))
            content_w = max(img.get_width(), name.get_width())
            bw = content_w + pad_x * 2
            bh = pad_top + img.get_height() + pad_bot
            bx = x0 + (img.get_width() - content_w) // 2 - pad_x
            by = y0 - pad_top
            frame = _new_rgba((bw, bh))
            frame.fill((36, 24, 16, 210 if selected else 150))
            border = (255, 220, 120, 255) if selected else (140, 110, 70, 180)
            pygame.draw.rect(frame, border, frame.get_rect(), 2 if selected else 1)
            frame = _finish_rgba(frame)
            screen.blit(frame, (bx, by))
            screen.blit(img, (x0, y0))
            screen.blit(name, (x0 + (img.get_width() - name.get_width()) // 2, y0 + img.get_height() + 8))
            if selected:
                mark = self.font_title_sel.render("SELECTED", True, (255, 220, 120))
                screen.blit(mark, (bx + (bw - mark.get_width()) // 2, by - 14))
            x0 += img.get_width() + pad_x * 2 + gap

    def _draw_title_footer(self, screen):
        mute = "M unmute" if self.sfx.muted else "M mute"
        lines = [
            "Left/Right or A/D (or 1/2): choose character",
            "Enter / Space: start    N: two players on a network",
            "In game: Left/Right turn, Up/Down step, H Chameleon Cloak, " + mute,
        ]
        # Copyright at the very bottom (wrapped); controls sit just above it.
        copy_margin = 8
        copy_gap = 10  # line height for size-8 font on the low-res view
        copy_lines = _wrap_text(self.font_title_copy, TITLE_COPYRIGHT, self.view_w - copy_margin * 2)
        copy_block_h = copy_gap * len(copy_lines)
        copy_y0 = self.view_h - 6 - copy_block_h
        help_y = self._title_footer_help_y()
        y = help_y
        for line in lines:
            t = self.font_title_help.render(line, True, (230, 214, 180))
            screen.blit(t, ((self.view_w - t.get_width()) // 2, y))
            y += 14
        for i, line in enumerate(copy_lines):
            t = self.font_title_copy.render(line, True, (180, 165, 130))
            screen.blit(t, ((self.view_w - t.get_width()) // 2, copy_y0 + i * copy_gap))

def parse_args(argv):
    parser = argparse.ArgumentParser(description="Daleks in Hedges")
    parser.add_argument("--seed", type=int, default=None, help="maze seed (default: time)")
    parser.add_argument("--screenshot", type=str, default=None, help="save a frame to this path and quit")
    parser.add_argument("--frames", type=int, default=None, help="after the first frame, simulate N movement frames")
    parser.add_argument("--level", type=int, default=1, help="start on this level (3 + 2 per level Daleks)")
    parser.add_argument("--character", choices=list(CHAR_ORDER), default="archie",
                        help="playable character (skipped on the title screen)")
    parser.add_argument("--no-title", action="store_true", help="skip the title screen and start playing")
    parser.add_argument("--scene", choices=("dalek", "laser", "telegraph", "disguise", "tardis", "tardis_demat", "tardis_exit",
                                 "title", "title_top10", "score_hud", "name_entry"), default=None,
                        help="debug: arrange a scene, simulate it briefly, then screenshot")
    parser.add_argument("--scene-ms", type=float, default=None, help="debug: override the scene's simulated time")
    parser.add_argument("--demo-scores", action="store_true",
                        help="debug: fill the Top 10 with sample names (shown only, never sent)")
    parser.add_argument("--offline", action="store_true", help="never contact the online leaderboard")
    parser.add_argument("--host", action="store_true",
                        help="network: host a two-player game straight away (as --character)")
    parser.add_argument("--join", type=str, default=None, metavar="HOST[:PORT]",
                        help="network: join the game hosted at this address, skipping discovery")
    parser.add_argument("--net-port", type=int, default=net.GAME_PORT,
                        help=f"network: UDP game port to host on, or to join (default {net.GAME_PORT})")
    parser.add_argument("--discovery-port", type=int, default=net.DISCOVERY_PORT, help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def _run_net_headless(game: Game, screen, args) -> int:
    """Test run of a network game without a screen: real time (the other copy
    is another process), the player wanders like --frames, and a JSON summary
    is printed at the end."""
    frames = args.frames or 600
    t0 = time.monotonic()
    started = None
    for i in range(frames):
        now = (time.monotonic() - t0) * 1000.0
        if game.mode == "play":
            started = started if started is not None else i
            game.nudge(now)
        game.update(now)
        pygame.event.pump()
        if game.mode == "lobby" and game.lobby and game.lobby["stage"] == "menu":
            break  # could not connect
        time.sleep(max(0.0, (i + 1) / 60.0 - (time.monotonic() - t0)))
    now = (time.monotonic() - t0) * 1000.0
    game.draw(screen, now)
    if args.screenshot:
        pygame.image.save(screen, args.screenshot)
    o = game.other
    summary = {
        "role": game.net_role, "mode": game.mode, "seed": game.seed, "level": game.level,
        "me": [game.col, game.row], "score": game.score, "dead": game.dead,
        "other": None if o is None else {"cell": list(o.cell), "score": o.score, "dead": o.dead},
        "daleks": [[round(d.pos[0], 2), round(d.pos[1], 2)] for d in game.daleks],
        "stats": game.net.stats if game.net else None,
        "lobby_msg": game.lobby["msg"] if game.lobby else None,
    }
    print("NETSUMMARY " + json.dumps(summary), flush=True)
    game._net_close()
    pygame.quit()
    return 0


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        pygame.mixer.pre_init(MIXER_FREQ, -16, 2, MIXER_BUFFER)
    except Exception:
        pass
    pygame.init()
    pygame.display.set_caption("Daleks in Hedges")
    screen = pygame.display.set_mode((WIN_W, WIN_H))
    skip_title = args.no_title or args.scene is not None or args.frames is not None or args.screenshot is not None
    if args.scene in ("title", "title_top10"):
        skip_title = False
    headless = args.screenshot is not None or args.frames is not None or args.scene is not None
    # Headless test runs never touch the network or the player's score file.
    netplay = args.host or args.join is not None
    if netplay:
        skip_title = False
    game = Game(args.seed, args.level, character=args.character, title=not skip_title,
                online=not (headless or args.offline), net_port=args.net_port,
                discovery_port=args.discovery_port)
    if netplay:
        game.title_pick = CHAR_ORDER.index(args.character)
        if args.host:
            game.open_lobby(stage="host")
        else:
            game.open_lobby(stage="join", addr=net.parse_hostport(args.join, args.net_port))
        if headless:
            return _run_net_headless(game, screen, args)
    if args.demo_scores:
        game.board.entries = [("Holly", 1460, "GB"), ("Archie", 1215, "GB"), ("Dad", 980, "IE"),
                              ("Nathan", 744, "US"), ("Rose", 610, "FR"), ("Clara", 502, "DE"),
                              ("Amy", 388, "ES"), ("Rory", 251, "IT"), ("K9", 140, "AU"), ("Davros", 12, "")]
        game.board.status = "online"
    if args.scene in ("title", "title_top10"):
        game.mode = "title"
        if args.character in CHAR_ORDER:
            game.title_pick = CHAR_ORDER.index(args.character)

    if not headless:
        clock = pygame.time.Clock()
        running = True
        while running:
            clock.tick(60)
            now = pygame.time.get_ticks()
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                elif event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE and not game.wants_text():
                        running = False
                    else:
                        game.on_key(event.key, now, getattr(event, "unicode", ""))
            if not running:
                break
            game.hold_action(pygame.key.get_pressed(), now)
            game.update(now)
            game.draw(screen, now)
            pygame.display.flip()
        pygame.quit()
        return 0

    now = 0.0
    if args.scene:
        sim = game.setup_scene(args.scene, now)
        if args.scene_ms is not None:
            sim = args.scene_ms
        end = now + sim
        while now < end:
            now = min(end, now + 1000.0 / 60.0)
            game.update(now)
    game.draw(screen, now)
    pygame.display.flip()
    if args.scene and not args.frames:
        if args.screenshot:
            pygame.image.save(screen, args.screenshot)
        pygame.quit()
        return 0
    if args.screenshot:
        folder = os.path.dirname(os.path.abspath(args.screenshot))
        if folder:
            os.makedirs(folder, exist_ok=True)
        pygame.image.save(screen, args.screenshot)
    frames = args.frames or 0
    for _ in range(frames):
        now += 1000.0 / 60.0
        game.nudge(now)
        game.update(now)
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                pygame.quit()
                return 0
        game.draw(screen, now)
        pygame.display.flip()
    pygame.quit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
