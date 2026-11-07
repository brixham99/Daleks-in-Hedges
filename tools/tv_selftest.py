#!/usr/bin/env python3
"""Headless checks for the television fullscreen and the gamepad.

    SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy python3 tools/tv_selftest.py

Covers fitting at 1920x1080 and 3840x2160 (and letterboxing on a 4:3 display),
synthetic controller events, a hot-plugged virtual pad, pause, the name wheel,
and saves title and name-entry shots that show the pad hints.
"""
import ctypes
import glob
import os
import sys
import tempfile
import time

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
os.environ.pop("ZENSTATION", None)
os.environ.pop("ZENSTATION_DISPLAY", None)

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import pygame  # noqa: E402

import game as G  # noqa: E402

SHOTS = os.environ.get("DIH_ARTIFACTS", "/opt/cursor/artifacts")
os.makedirs(SHOTS, exist_ok=True)

# Never touch a real score file.
G.Leaderboard.__init__.__defaults__ = (False, os.path.join(tempfile.mkdtemp(prefix="dih-tv-"), "scores.json"))

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""), flush=True)


def post(pads, kind, **fields):
    ev = pygame.event.Event(kind, fields)
    pads.handle_event(ev)


def axis_event(pads, axis, value, instance=7):
    post(pads, pygame.CONTROLLERAXISMOTION, instance_id=instance, axis=axis, value=value)


def button_event(pads, button, down=True, instance=7):
    kind = pygame.CONTROLLERBUTTONDOWN if down else pygame.CONTROLLERBUTTONUP
    post(pads, kind, instance_id=instance, button=button)


# ----- aspect ---------------------------------------------------------------
pygame.init()

r = G.fit_rect((1280, 720), (1920, 1080))
check("1080p fills the frame", (r.x, r.y, r.w, r.h) == (0, 0, 1920, 1080), str(r))
r = G.fit_rect((1280, 720), (3840, 2160))
check("4K fills the frame", (r.x, r.y, r.w, r.h) == (0, 0, 3840, 2160), str(r))
r = G.fit_rect((1280, 720), (1024, 768))
check("4:3 letterboxes", r.w == 1024 and r.h == 576 and r.y == (768 - 576) // 2 and r.x == 0, str(r))
r = G.fit_rect((1280, 720), (2560, 1080))
check("ultrawide pillarboxes", r.h == 1080 and r.w == 1920 and r.x == (2560 - 1920) // 2, str(r))
r = G.fit_rect((1280, 720), (1280, 720))
check("internal size is 1:1", (r.x, r.y, r.w, r.h) == (0, 0, 1280, 720))


def present_colour(size, colour):
    window = pygame.display.set_mode(size, pygame.NOFRAME)
    view = pygame.Surface((G.WIN_W, G.WIN_H))
    view.fill(colour)
    frame = G.present_frame(window, view, None, False)
    return window, frame


window, _ = present_colour((1920, 1080), (10, 200, 40))
check("1080p present size", window.get_size() == (1920, 1080))
check("1080p content reaches the corner", window.get_at((0, 0))[:3] == (10, 200, 40))
check("1080p content reaches the far corner", window.get_at((1919, 1079))[:3] == (10, 200, 40))

window, frame = present_colour((3840, 2160), (10, 200, 40))
check("4K present size", window.get_size() == (3840, 2160))
check("4K content reaches the corner", window.get_at((0, 0))[:3] == (10, 200, 40)
      and window.get_at((3839, 2159))[:3] == (10, 200, 40))
view = pygame.Surface((G.WIN_W, G.WIN_H))
view.fill((10, 200, 40))
t0 = time.perf_counter()
n = 15
buf = frame
for _ in range(n):
    buf = G.present_frame(window, view, buf, False)
ms = (time.perf_counter() - t0) / n * 1000.0
# Nearest scale of 720p onto 4K. Smoothscale would be the expensive one; this
# should stay a small slice of a 60 Hz frame even before a Pi 5's margin.
check("4K present stays cheap", ms < 25.0, f"{ms:.2f} ms")
print(f"      4K present average {ms:.2f} ms", flush=True)

window, _ = present_colour((1024, 768), (10, 200, 40))
check("letterbox bar is black", window.get_at((0, 0))[:3] == (0, 0, 0)
      and window.get_at((512, 10))[:3] == (0, 0, 0))
check("letterbox centre is the view", window.get_at((512, 384))[:3] == (10, 200, 40))

# Desktop fullscreen follows the dummy driver's reported mode (1024x768),
# with no hardcoded television size.
os.environ.pop("ZENSTATION_DISPLAY", None)
screen = G.open_fullscreen()
desk = pygame.display.get_desktop_sizes()[0]
check("fullscreen is GPU-scaled 1280x720 (fills the desktop)", screen.get_size() == (G.WIN_W, G.WIN_H), str(screen.get_size()))
check("fullscreen flag is set", bool(pygame.display.is_fullscreen()))
check("pointer hidden on the television", pygame.mouse.get_visible() is False)

os.environ["ZENSTATION_DISPLAY"] = "1920x1080"
screen = G.open_fullscreen()
check("simulated 1080p window", screen.get_size() == (1920, 1080), str(screen.get_size()))
os.environ["ZENSTATION_DISPLAY"] = "3840x2160"
screen = G.open_fullscreen()
check("simulated 4K window", screen.get_size() == (3840, 2160), str(screen.get_size()))
os.environ.pop("ZENSTATION_DISPLAY", None)

mode = G.ScreenMode(False)
check("windowed surface is 1280x720", mode.screen.get_size() == (G.WIN_W, G.WIN_H))
check("pointer returns in a window", pygame.mouse.get_visible() is True)
mode.apply(True)
check("toggle to television is scaled full screen", mode.screen.get_size() == (G.WIN_W, G.WIN_H) and mode.fullscreen)
check("pointer hidden again", pygame.mouse.get_visible() is False)
mode.apply(False)
check("toggle back to the window", mode.screen.get_size() == (G.WIN_W, G.WIN_H) and not mode.fullscreen)

# ----- pad ------------------------------------------------------------------
pads = G.Gamepads()
check("no pad until an event", not pads.connected())

axis_event(pads, pygame.CONTROLLER_AXIS_LEFTX, int(0.2 * 32767))
pads.poll(0, 0)
check("deadzone ignores a small stick", not any(pads.dirs.values()), str(pads.dirs))

axis_event(pads, pygame.CONTROLLER_AXIS_LEFTX, int(0.5 * 32767))
pads.poll(10, 10)
check("stick right past the deadzone", pads.dirs["right"] and pads.dir_pressed["right"] and not pads.dirs["left"])
axis_event(pads, pygame.CONTROLLER_AXIS_LEFTX, int(0.25 * 32767))
pads.poll(20, 20)
check("hysteresis holds the direction", pads.dirs["right"])
axis_event(pads, pygame.CONTROLLER_AXIS_LEFTX, int(0.05 * 32767))
pads.poll(30, 30)
check("stick released under the return threshold", not pads.dirs["right"])

axis_event(pads, pygame.CONTROLLER_AXIS_LEFTY, -20000)
pads.poll(40, 40)
check("stick up is negative Y", pads.dirs["up"] and not pads.dirs["down"])
axis_event(pads, pygame.CONTROLLER_AXIS_LEFTY, 0)
button_event(pads, pygame.CONTROLLER_BUTTON_DPAD_LEFT, True)
pads.poll(50, 50)
check("D-pad left", pads.dirs["left"] and pads.dir_pressed["left"])
button_event(pads, pygame.CONTROLLER_BUTTON_DPAD_LEFT, False)

button_event(pads, pygame.CONTROLLER_BUTTON_BACK, True)
pads.poll(100, 100)
check("a tap of Back does not quit", not pads.quit_requested)
pads.poll(1099, 100)
check("Back held just under a second does not quit", not pads.quit_requested)
pads.poll(1100, 100)
check("Back held a second quits", pads.quit_requested)
# Held up repeats for the name wheel, after a short delay, and not before.
held = G.Gamepads()
axis_event(held, pygame.CONTROLLER_AXIS_LEFTY, -20000)
held.poll(0, 0)
check("name wheel does not repeat at once", held.dir_pressed["up"] and not held.repeat_due("up", 0))
check("name wheel waits out the delay", not held.repeat_due("up", G.PAD_REPEAT_DELAY - 1))
check("name wheel repeats after the delay", held.repeat_due("up", G.PAD_REPEAT_DELAY))
check("name wheel repeats on its interval", not held.repeat_due("up", G.PAD_REPEAT_DELAY + 40)
      and held.repeat_due("up", G.PAD_REPEAT_DELAY + G.PAD_REPEAT_EVERY))

# A or the right trigger walks forward, but only when walking is allowed.
fw = G.Gamepads()
button_event(fw, pygame.CONTROLLER_BUTTON_A, True)
fw.poll(0, 0)
check("A held counts as forward", fw.forward and fw.forward_pressed)
check("A is forward only while walking", fw.as_dirs(True)[2] and not fw.as_dirs(False)[2])
button_event(fw, pygame.CONTROLLER_BUTTON_A, False)
fw.poll(16, 16)
check("A released stops forward", not fw.forward and not fw.as_dirs(True)[2])
axis_event(fw, pygame.CONTROLLER_AXIS_TRIGGERRIGHT, 1)
fw.poll(32, 32)
check("resting right trigger is not forward", not fw.forward)
axis_event(fw, pygame.CONTROLLER_AXIS_TRIGGERRIGHT, 32767)
fw.poll(48, 48)
check("right trigger pulled is forward", fw.forward and fw.as_dirs(True)[2])
axis_event(fw, pygame.CONTROLLER_AXIS_TRIGGERRIGHT, 0)
fw.poll(64, 64)
check("right trigger let go stops forward", not fw.forward)

# A fresh pad for the quit return value, then the game.
pads = G.Gamepads()
button_event(pads, pygame.CONTROLLER_BUTTON_BACK, True)
pads.poll(0, 0)
pads.poll(1000, 0)

game = G.Game(1, title=True, online=False)
check("keyboard hint text", G.KEYBOARD_TITLE_HINTS == (
    "Left/Right or A/D (or 1/2): choose character",
    "Enter / Space: start    N: Two players on a network",
    "In game: Left/Right turn, Up/Down step, H Chameleon Cloak",
))
for line in G.PAD_TITLE_HINTS:
    width = game.font_title_help.size(line)[0]
    check(f"title hint fits ({line[:28]})", width < G.WIN_W - 16, str(width))
check("resting raw 1 is not a tilt", abs(G._axis_unit(1)) < 0.01 and abs(G._axis_unit(-1)) < 0.01)
check("raw full tilt is -1", G._axis_unit(-32768) == -1.0)
check("play hint fits", game.font_hud.size(G.PAD_PLAY_HINT)[0] < G.WIN_W - 200)
check("name hint fits", game.font_small.size(G.PAD_NAME_FOOT)[0] < G.WIN_W - 80)

check("Back hold asks the loop to quit", G.apply_pad(game, pads, 1000) == "quit")

# The keyboard choice is unchanged when the pad reports nothing. (Posted key
# events do not flip pygame.key.get_pressed under the dummy driver, so this
# uses the same indexing the real key array does.)
class Keys:
    def __init__(self, down):
        self.down = set(down)

    def __getitem__(self, key):
        return key in self.down

idle = (False, False, False, False)
left = Keys([pygame.K_LEFT])
check("idle pad does not change keyboard turning",
      game.desired_action(left) == game.desired_action(left, None) == game.desired_action(left, idle)
      == ("turn", -1))
check("pad left matches the left key", game.desired_action(Keys([]), (True, False, False, False)) == ("turn", -1))
check("a held turn still beats a held step",
      game.desired_action(Keys([pygame.K_UP]), (True, False, False, False)) == ("turn", -1))

pick = game.title_pick
game.on_pad_dir("right", 0)
check("pad right changes character", game.title_pick != pick)
game.on_pad_dir("up", 0)
check("pad up does not leave the title", game.mode == "title")
game.on_pad_button(pygame.CONTROLLER_BUTTON_Y, 0)
check("Y opens the lobby", game.mode == "lobby" and game.lobby["stage"] == "menu")
game.on_pad_dir("down", 0)
check("lobby down selects join", game.lobby["pick"] == 1)
char = game.lobby["char"]
game.on_pad_dir("left", 0)
check("lobby left swaps character", game.lobby["char"] != char)
game.on_pad_button(pygame.CONTROLLER_BUTTON_B, 0)
check("B leaves the lobby", game.mode == "title")
game.on_pad_button(pygame.CONTROLLER_BUTTON_A, 0)
check("A starts the game", game.mode == "play")

# Tank controls from a held stick, the same path as a held key.
game.reset(1)
game.last_now = 0
origin = (game.col, game.row, game.facing_index)
now = 0.0
keys = pygame.key.get_pressed()
for _ in range(36):
    now += 1000.0 / 60.0
    game.hold_action(keys, now, (False, False, True, False))
    game.update(now)
check("stick up walks", (game.col, game.row) != origin[:2], str((game.col, game.row, origin)))

game.reset(1)
now = 0.0
face = game.facing_index
for _ in range(24):
    now += 1000.0 / 60.0
    game.hold_action(keys, now, (True, False, False, False))
    game.update(now)
check("stick left turns", game.facing_index != face)

while game.busy() and now < 5000:
    now += 1000.0 / 60.0
    game.update(now)
game.on_pad_button(pygame.CONTROLLER_BUTTON_X, now)
check("X cloaks", game.disguised)
game.on_pad_button(pygame.CONTROLLER_BUTTON_Y, now + 50)
check("Y clears the cloak", not game.disguised)

# Pause freezes single-player time, so a long wall-clock gap is not a jump.
game.pause_offset = 0
game.paused = False
raw = 2000.0
game.toggle_pause(raw)
check("Start pauses", game.paused)
frozen = (game.col, game.row, game.facing_index)
game.hold_action(keys, game.clock_now(raw + 5000), (False, False, True, False))
check("paused pad does not walk", (game.col, game.row, game.facing_index) == frozen)
check("paused clock stays put", game.clock_now(raw + 8000) == raw)
game.toggle_pause(raw + 8000)
check("Start continues", not game.paused)
check("game time does not skip the pause", abs(game.clock_now(raw + 8000) - raw) < 0.1)
check("time then runs again", abs(game.clock_now(raw + 8016) - (raw + 16)) < 0.1)

# Name wheel.
game.paused = False
game.using_pad = True
sim = game.setup_scene("name_entry", 0.0)
now = 0.0
while now < sim:
    now = min(sim, now + 1000.0 / 60.0)
    game.update(now)
check("name entry is up", game.entry_active, game.entry_name)
before = game.entry_name
game.on_pad_dir("up", now)
check("up adds a letter", game.entry_name != before and len(game.entry_name) == len(before) + 1, game.entry_name)
game.on_pad_dir("left", now)
at = game.entry_cursor
was = game.entry_name[at]
game.on_pad_dir("down", now)
check("down cycles that letter", game.entry_cursor == at and game.entry_name[at] != was, game.entry_name)
game.on_pad_button(pygame.CONTROLLER_BUTTON_A, now)
check("A submits the name", game.entry_done and not game.entry_active and game.entry_note)

game.entry_active = True
game.entry_done = False
game.entry_name = ""
game.entry_cursor = 0
game.dead = True
game.on_pad_button(pygame.CONTROLLER_BUTTON_B, now)
check("B skips an empty name", game.entry_done and game.entry_note == "" and not game.entry_active)

# ----- hot-plug a virtual SDL controller -----------------------------------
pads = G.Gamepads()


def sdl():
    base = os.path.dirname(pygame.__file__)
    libs = glob.glob(os.path.join(base, "..", "pygame_ce.libs", "libSDL2-2*"))
    if not libs:
        libs = glob.glob("/usr/lib/*/libSDL2-2.0.so*") + glob.glob("/usr/lib/libSDL2-2.0.so*")
    lib = ctypes.CDLL(libs[0])

    class GUID(ctypes.Structure):
        _fields_ = [("data", ctypes.c_uint8 * 16)]

    lib.SDL_JoystickAttachVirtual.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
    lib.SDL_JoystickAttachVirtual.restype = ctypes.c_int
    lib.SDL_JoystickDetachVirtual.argtypes = [ctypes.c_int]
    lib.SDL_JoystickDetachVirtual.restype = ctypes.c_int
    lib.SDL_JoystickGetDeviceGUID.argtypes = [ctypes.c_int]
    lib.SDL_JoystickGetDeviceGUID.restype = GUID
    lib.SDL_JoystickGetGUIDString.argtypes = [GUID, ctypes.c_char_p, ctypes.c_int]
    lib.SDL_GameControllerAddMapping.argtypes = [ctypes.c_char_p]
    lib.SDL_GameControllerAddMapping.restype = ctypes.c_int
    lib.SDL_JoystickOpen.argtypes = [ctypes.c_int]
    lib.SDL_JoystickOpen.restype = ctypes.c_void_p
    lib.SDL_JoystickSetVirtualButton.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint8]
    lib.SDL_JoystickSetVirtualButton.restype = ctypes.c_int
    lib.SDL_JoystickSetVirtualAxis.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int16]
    lib.SDL_JoystickSetVirtualAxis.restype = ctypes.c_int
    return lib


try:
    lib = sdl()
    index = lib.SDL_JoystickAttachVirtual(1, 6, 16, 0)
    guid = lib.SDL_JoystickGetDeviceGUID(index)
    buf = ctypes.create_string_buffer(64)
    lib.SDL_JoystickGetGUIDString(guid, buf, 64)
    mapping = (buf.value.decode() + ",Virtual Pad,a:b0,b:b1,x:b2,y:b3,back:b4,start:b6,"
               "dpup:b11,dpdown:b12,dpleft:b13,dpright:b14,leftx:a0,lefty:a1,rightx:a2,righty:a3,platform:Linux")
    mapped = lib.SDL_GameControllerAddMapping(mapping.encode())
    pygame.event.clear()
    pygame.event.pump()
    for ev in pygame.event.get():
        pads.handle_event(ev)
    pads.scan()
    check("virtual pad hot-plugs", pads.connected() and mapped >= 0, f"index {index}")
    js = lib.SDL_JoystickOpen(index)
    lib.SDL_JoystickSetVirtualButton(js, 0, 1)  # A
    lib.SDL_JoystickSetVirtualAxis(js, 1, -22000)  # left Y, up
    pygame.event.pump()
    for ev in pygame.event.get():
        pads.handle_event(ev)
    pads.poll(0, 0)
    check("virtual A and stick up", pygame.CONTROLLER_BUTTON_A in pads.held and pads.dirs["up"],
          str(sorted(pads.held)))
    detached = lib.SDL_JoystickDetachVirtual(index)
    pygame.event.pump()
    for ev in pygame.event.get():
        pads.handle_event(ev)
    pads.poll(10, 10)
    check("virtual pad unplugs", detached == 0 and not pads.controllers, str(detached))
except Exception as exc:  # a missing symbol should fail the check, not the file
    check("virtual pad hot-plugs", False, repr(exc))

# ----- shots ----------------------------------------------------------------
pygame.display.set_mode((G.WIN_W, G.WIN_H))
game.using_pad = True
game._enter_title(1)
game.title_t0 = 0
game.paused = False
game.draw(pygame.display.get_surface(), 200)
title_path = os.path.join(SHOTS, "title-pad-hints.png")
pygame.image.save(pygame.display.get_surface(), title_path)
check("title shot saved", os.path.exists(title_path), title_path)

# Same title with the keyboard, so a later compare can prove the hints differ.
game.using_pad = False
game.draw(pygame.display.get_surface(), 200)
key_path = os.path.join(SHOTS, "title-keyboard-hints.png")
pygame.image.save(pygame.display.get_surface(), key_path)
a = pygame.surfarray.array3d(pygame.image.load(title_path))
b = pygame.surfarray.array3d(pygame.image.load(key_path))
check("pad hints differ from the keyboard title", a.shape == b.shape and not (a == b).all())

game.entry_active = False
game.entry_done = False
game.board.last_name = "Holly"
sim = game.setup_scene("name_entry", 0.0)
now = 0.0
while now < sim:
    now = min(sim, now + 1000.0 / 60.0)
    game.update(now)
game.using_pad = True
game.paused = False
game.on_pad_dir("left", now)  # sit on a letter so the wheel's mark is visible
game.draw(pygame.display.get_surface(), now)
name_path = os.path.join(SHOTS, "name-entry-pad-hints.png")
pygame.image.save(pygame.display.get_surface(), name_path)
check("name entry shot saved", os.path.exists(name_path) and game.entry_active)

# Scaled television frames of that title, for the eye as well as the asserts.
game.using_pad = True
game._enter_title(1)
game.title_t0 = 0
big = pygame.display.set_mode((1920, 1080), pygame.NOFRAME)
game.draw(big, 200)
shot = os.path.join(SHOTS, "title-1080p.png")
pygame.image.save(big, shot)
check("1080p title fills", big.get_size() == (1920, 1080))
# A 4:3 window must show black bars above a non-black picture.
box = pygame.display.set_mode((1024, 768), pygame.NOFRAME)
game.draw(box, 200)
bars = box.get_at((8, 8))[:3] == (0, 0, 0) and box.get_at((512, 8))[:3] == (0, 0, 0)
centre = box.get_at((512, 384))[:3]
check("title letterboxes on 4:3", bars and centre != (0, 0, 0), str(centre))
pygame.image.save(box, os.path.join(SHOTS, "title-letterbox.png"))

pygame.quit()
failed = [name for name, ok in results if not ok]
print(f"\n{len(results) - len(failed)} passed, {len(failed)} failed", flush=True)
sys.exit(1 if failed else 0)
