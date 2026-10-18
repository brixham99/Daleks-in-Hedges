#!/usr/bin/env python3
"""Record a gameplay video of Daleks in Hedges, headless.

The real game runs under the dummy SDL video/audio drivers in simulated time
(60 updates a second, every other frame kept for a 30 fps video). A scripted
autopilot plays it through the game's own controls: it holds and taps the
same keys a player would (arrows to turn and step, H to cloak, I+M for the
secret, letters and Enter for the name), fed in exactly as main() does
(on_key for each new key press, then secret_keys / hold_action / update /
draw). The game itself is not changed.

Sound: every sound the game asks for is logged against simulated time
(one-shots, voices with their per-channel volume changes, the Dalek hum
levels each frame, the title theme and the cloak bed) and mixed afterwards
from the game's own decoded samples, then muxed as AAC.

Leaderboard: nothing touches the network. The board is read from a JSON
copy of the public dreamlo board (--board), the country lookup returns GB,
flags come from --flags, and sending a score is stubbed (it is only noted
locally and shown on the recorded board; the real board is never written).

Usage:
    python3 tools/record_video.py --board /tmp/dihrec/board.json \
        --flags /tmp/dihrec/flags --out /workspace/dih_gameplay.mp4
"""
import argparse
import collections
import json
import math
import os
import shutil
import subprocess
import sys
import time as _time
import wave

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)

import numpy as np  # noqa: E402
import pygame  # noqa: E402

import game as G  # noqa: E402

DT = 1000.0 / 60.0
FPS = 30
SIM = {"now": 0.0}


# ----- deterministic seeds ---------------------------------------------------
class TimeProxy:
    """Stands in for the time module inside game.py so each new maze uses a
    seed from our list instead of the clock. Everything else is passed on."""

    def __init__(self, seeds):
        self.seeds = list(seeds)
        self.n = 0

    def time_ns(self):
        self.n += 1
        if self.seeds:
            return self.seeds.pop(0)
        return 900001 + self.n * 7777

    def __getattr__(self, name):
        return getattr(_time, name)


# ----- leaderboard without network --------------------------------------------
POSTED = []


def install_board(board_path, rec_dir):
    with open(board_path, "r", encoding="utf-8") as f:
        board_text = f.read()

    def fake_get(url):
        if url.endswith("/json") and G.DREAMLO_PUBLIC in url:
            data = json.loads(board_text)
            lb = data.setdefault("dreamlo", {}).setdefault("leaderboard", {}) or {}
            entry = lb.get("entry") or []
            if isinstance(entry, dict):
                entry = [entry]
            for name, score, country in POSTED:  # what the board would show after the (stubbed) send
                entry.append({"name": name, "score": str(score), "text": country})
            lb["entry"] = entry
            data["dreamlo"]["leaderboard"] = lb
            return json.dumps(data)
        raise RuntimeError("recorder: network disabled")

    def fake_send(self, name, score):
        POSTED.append((name, int(score), self.country or ""))
        print(f"[stub] score send suppressed: {name} {score} (nothing posted)", flush=True)
        return True

    G.Leaderboard._get = staticmethod(fake_get)
    G.Leaderboard._send = fake_send
    G.lookup_country = lambda: "GB"
    save = os.path.join(rec_dir, "scores.json")
    if os.path.exists(save):
        os.remove(save)
    G.Leaderboard.__init__.__defaults__ = (True, save)


# ----- sound logging ------------------------------------------------------------
class FakeChannel:
    def __init__(self, ev):
        self.ev = ev

    def set_volume(self, left, right=None):
        if right is None:
            right = left
        self.ev["vols"].append((SIM["now"], float(left), float(right)))

    def stop(self):
        if self.ev.get("stop") is None:
            self.ev["stop"] = SIM["now"]

    def get_busy(self):
        return SIM["now"] < self.ev["t"] + self.ev["len"] and self.ev.get("stop") is None

    def fadeout(self, ms):
        self.stop()


class SoundLog:
    def __init__(self, sfx):
        self.sfx = sfx
        self.oneshots = []
        self.stop_all_t = []
        self.title = []      # [start, stop]
        self.cloak = []      # [start, stop]
        self.hum_frames = []  # (t, [(l, r)] * HUM_CHANNELS)
        sfx.play = self.play
        sfx.play_voice = self.play_voice
        orig_stop_all = sfx.stop_all
        orig_stop_hums = sfx.stop_hums

        def stop_all():
            self.stop_all_t.append(SIM["now"])
            self._end(self.title)
            self._end(self.cloak)
            orig_stop_all()
        sfx.stop_all = stop_all
        sfx.stop_hums = orig_stop_hums

        def start_title():
            sfx.want_title = True
            orig_stop_hums()
            if not self.title or self.title[-1][1] is not None:
                self.title.append([SIM["now"], None])
        sfx.start_title_theme = start_title

        def stop_title():
            sfx.want_title = False
            self._end(self.title)
        sfx.stop_title_theme = stop_title

        def start_cloak():
            self._end(self.cloak)
            self.cloak.append([SIM["now"], None])
        sfx.start_cloak_loop = start_cloak

        def stop_cloak():
            self._end(self.cloak)
        sfx.stop_cloak_loop = stop_cloak
        sfx.cloak_loop_on = lambda: bool(self.cloak) and self.cloak[-1][1] is None

    @staticmethod
    def _end(lst):
        if lst and lst[-1][1] is None:
            lst[-1][1] = SIM["now"]

    def _event(self, name, left, right):
        snd = self.sfx.sounds.get(name)
        self.sfx.requested[name] = self.sfx.requested.get(name, 0) + 1
        if snd is None:
            return None
        ev = {"t": SIM["now"], "name": name, "len": snd.get_length() * 1000.0,
              "vols": [(SIM["now"], float(left), float(right))], "stop": None}
        self.oneshots.append(ev)
        self.sfx.played[name] = self.sfx.played.get(name, 0) + 1
        return ev

    def play(self, name):
        return self._event(name, 1.0, 1.0) is not None

    def play_voice(self, name, left, right):
        ev = self._event(name, left, right)
        return FakeChannel(ev) if ev is not None else None

    def record_hum(self):
        self.hum_frames.append((SIM["now"], list(self.sfx.hum_levels)))

    def count(self, name):
        return sum(1 for e in self.oneshots if e["name"] == name)


def snd_array(snd):
    a = pygame.sndarray.array(snd).astype(np.float32) / 32768.0
    if a.ndim == 1:
        a = np.stack([a, a], axis=1)
    return a


def mix_audio(log, total_ms, rate, path):
    n = int(total_ms / 1000.0 * rate) + rate
    out = np.zeros((n, 2), np.float32)
    sfx = log.sfx
    cache = {}

    def s2i(t):
        return int(round(t / 1000.0 * rate))

    def cut_after(t):
        later = [s for s in log.stop_all_t if s > t + 0.01]
        return min(later) if later else None

    for ev in log.oneshots:
        snd = sfx.sounds[ev["name"]]
        if ev["name"] not in cache:
            cache[ev["name"]] = snd_array(snd)
        a = cache[ev["name"]] * snd.get_volume()
        start = s2i(ev["t"])
        end_t = ev["stop"]
        c = cut_after(ev["t"])
        if c is not None and (end_t is None or c < end_t):
            end_t = c
        length = len(a) if end_t is None else max(0, min(len(a), s2i(end_t) - start))
        if length <= 0 or start >= n:
            continue
        length = min(length, n - start)
        seg = a[:length].copy()
        gl = np.empty(length, np.float32)
        gr = np.empty(length, np.float32)
        vols = ev["vols"]
        for i, (vt, l, r) in enumerate(vols):
            a0 = max(0, s2i(vt) - start)
            a1 = length if i + 1 >= len(vols) else max(0, s2i(vols[i + 1][0]) - start)
            gl[a0:a1] = l
            gr[a0:a1] = r
        seg[:, 0] *= gl
        seg[:, 1] *= gr
        fade = min(length, int(0.004 * rate))
        if end_t is not None and fade > 0:
            seg[-fade:] *= np.linspace(1, 0, fade)[:, None]
        out[start:start + length] += seg

    def loop_into(arr, start, stop, gain, fade_in_ms=0.0, fade_out_ms=0.0):
        s0 = s2i(start)
        s1 = n if stop is None else min(n, s2i(stop) + s2i(fade_out_ms))
        if s1 <= s0:
            return
        L = s1 - s0
        reps = L // len(arr) + 1
        seg = np.tile(arr, (reps, 1))[:L] * gain
        env = np.ones(L, np.float32)
        fi = s2i(fade_in_ms)
        if fi:
            env[:min(fi, L)] = np.linspace(0, 1, fi)[:min(fi, L)]
        if stop is not None:
            fo = max(1, s2i(fade_out_ms) or int(0.01 * rate))
            k = min(fo, L)
            env[L - k:] *= np.linspace(1, 0, k)
        out[s0:s1] += seg * env[:, None]

    if sfx.title_theme is not None:
        arr = snd_array(sfx.title_theme)
        gain = sfx.title_theme.get_volume() * G.TITLE_THEME_VOLUME
        for a, b in log.title:
            loop_into(arr, a, b, gain, 0, 30)
    if sfx.cloak_loop is not None:
        arr = snd_array(sfx.cloak_loop)
        gain = sfx.cloak_loop.get_volume()
        for a, b in log.cloak:
            loop_into(arr, a, b, gain, 300, 120)
    if sfx.hum is not None and log.hum_frames:
        hum = snd_array(sfx.hum) * sfx.hum.get_volume()
        times = np.array([t for t, _ in log.hum_frames])
        levels = np.array([lv for _, lv in log.hum_frames], np.float32)  # (F, ch, 2)
        s_times = np.arange(n) / rate * 1000.0
        for ch in range(levels.shape[1]):
            if levels[:, ch].max() <= 0:
                continue
            gl = np.interp(s_times, times, levels[:, ch, 0]).astype(np.float32)
            gr = np.interp(s_times, times, levels[:, ch, 1]).astype(np.float32)
            off = (ch * 9973) % len(hum)
            idx = (np.arange(n) + off) % len(hum)
            out[:, 0] += hum[idx, 0] * gl
            out[:, 1] += hum[idx, 1] * gr
    out = np.tanh(out * 1.1) / math.tanh(1.1)
    out = out[:s2i(total_ms)]
    pcm = (np.clip(out, -1, 1) * 32000).astype(np.int16)
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())


# ----- the world: one game, stepped like main() ----------------------------------
class Keys:
    def __init__(self, held):
        self.held = held

    def __getitem__(self, k):
        return k in self.held


class World:
    def __init__(self, game, screen, out_video, still_dir):
        self.g = game
        self.screen = screen
        self.now = 0.0
        self.tick_n = 0
        self.held = set()
        self.marks = []
        self.still_dir = still_dir
        w, h = screen.get_size()
        self.ff = subprocess.Popen(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
             "-s", f"{w}x{h}", "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "medium",
             "-crf", "20", "-maxrate", "3500k", "-bufsize", "7000k", "-pix_fmt", "yuv420p",
             "-r", str(FPS), out_video], stdin=subprocess.PIPE)
        self.frames = 0

    @property
    def vt(self):
        return self.frames / FPS

    def mark(self, label):
        self.marks.append((self.vt, label))
        print(f"{self.vt:7.2f}s  {label}", flush=True)

    def still(self, name):
        path = os.path.join(self.still_dir, name)
        pygame.image.save(self.screen, path)
        self.mark(f"still {name}")

    def step(self, held=None, taps=()):
        """One 60 Hz frame. `held` replaces the held-key set; keys that go down
        now (newly held, or tapped) reach on_key first, as KEYDOWN events do."""
        g = self.g
        self.now += DT
        SIM["now"] = self.now
        if held is not None:
            new = [k for k in held if k not in self.held]
            self.held = set(held)
        else:
            new = []
        for k in new:
            g.on_key(k, self.now, "")
        for k, text in taps:
            g.on_key(k, self.now, text)
        keys = Keys(self.held)
        g.secret_keys(keys, self.now)
        g.hold_action(keys, self.now)
        g.update(self.now)
        self.log.record_hum()
        g.draw(self.screen, self.now)
        if self.tick_n % 2 == 0:
            self.ff.stdin.write(pygame.image.tostring(self.screen, "RGB"))
            self.frames += 1
        self.tick_n += 1

    def wait(self, ms, held=None):
        end = self.now + ms
        first = True
        while self.now < end:
            self.step(held if first else None)
            first = False

    def tap(self, key, text="", gap_ms=250):
        self.step(taps=[(key, text)])
        self.wait(gap_ms)

    def close(self):
        self.ff.stdin.close()
        self.ff.wait()


# ----- autopilot -------------------------------------------------------------------
TURN_KEY = {1: pygame.K_RIGHT, -1: pygame.K_LEFT}


class Pilot:
    """Heads for the TARDIS along the shortest path, keeping out of Dalek
    sight lines; if a Dalek spots him anyway, he cloaks (H)."""

    def __init__(self, world):
        self.w = world
        self.g = world.g
        self.aggressive = False   # walk into sight lines (to get spotted on purpose)
        self.cloak_ok = True      # react to a spot with the cloak
        self.wait_since = None
        self.spots = 0

    # geometry
    def los_cells(self, d, extra=1):
        g = self.g
        c, r = d.cell()
        dc, dr = d.facing
        cells = set()
        for k in range(1, G.DALEK_SIGHT + 1 + extra):
            cell = (c + dc * k, r + dr * k)
            if not g.is_open(*cell):
                break
            cells.add(cell)
        tc, tr = d.target
        for k in range(1, G.DALEK_SIGHT + 1):
            cell = (tc + dc * k, tr + dr * k)
            if not g.is_open(*cell):
                break
            cells.add(cell)
        return cells

    def safe(self, cell):
        g = self.g
        for d in g.daleks:
            dc_, dr_ = d.cell()
            if abs(dc_ - cell[0]) + abs(dr_ - cell[1]) <= 2:
                return False
            if cell in self.los_cells(d):
                return False
        return True

    def path(self, start):
        g = self.g
        goal = g.exit_cell
        blocked = {g.demat_cell}  # Daleks move on: safe() keeps him clear of them
        prev = {start: None}
        q = collections.deque([start])
        while q:
            cur = q.popleft()
            if cur == goal:
                break
            for d in G.DIR_ORDER:
                n = (cur[0] + d[0], cur[1] + d[1])
                if n in prev or not g.is_open(*n) or (n in blocked and n != goal):
                    continue
                prev[n] = cur
                q.append(n)
        if goal not in prev:
            return None
        out = [goal]
        while out[-1] != start:
            out.append(prev[out[-1]])
        return out[::-1]

    def spotted(self):
        return any(d.state == "aim" and d.victim == "me" for d in self.g.daleks)

    def control(self):
        """Keys to hold this frame, plus taps."""
        g, now = self.g, self.w.now
        if g.disguised:
            threat = any((g.col, g.row) in self.los_cells(d, 0) or
                         abs(d.cell()[0] - g.col) + abs(d.cell()[1] - g.row) <= 2 for d in g.daleks)
            if not threat and now - g.disguise_t0 > 2000:  # long enough to see the disguise
                return set(), [(pygame.K_h, "")]
            return set(), []
        if self.spotted():
            if self.cloak_ok and now >= g.cooldown_until and not g.disguise_pending:
                self.spots += 1
                return set(), [(pygame.K_h, "")]
            return set(), []
        if g.disguise_pending:
            return set(), []
        pos = g.dst if g.moving else (g.col, g.row)
        p = self.path(pos)
        if not p or len(p) < 2:
            return set(), []
        nxt = p[1]
        d = (nxt[0] - pos[0], nxt[1] - pos[1])
        if g.facing == d:
            ok = self.aggressive or nxt == g.exit_cell or self.safe(nxt)
            if not ok and self.wait_since is not None and now - self.wait_since > 7000 \
                    and now >= g.cooldown_until:
                ok = True  # been waiting a long time: risk it (the cloak is ready)
            if ok:
                self.wait_since = None
                return {pygame.K_UP}, []
            if self.wait_since is None:
                self.wait_since = now
            return set(), []
        fi = G.DIR_ORDER.index(g.facing)
        ti = G.DIR_ORDER.index(d)
        sign = 1 if (ti - fi) % 4 in (1, 2) else -1
        return {TURN_KEY[sign]}, []

    def run_level(self, max_ms=120000, on_frame=None):
        """Until he walks into the TARDIS (True) or dies (False)."""
        g, w = self.g, self.w
        start = w.now
        while w.now - start < max_ms:
            if g.won:
                return True
            if g.dead:
                return False
            if g.card_t0 is not None:
                w.step(set())
                continue
            held, taps = self.control()
            w.step(held, taps)
            if on_frame:
                on_frame()
        return None


# ----- the script --------------------------------------------------------------------
def wait_level_ready(w, g, extra_ms=0):
    """Through the exit, the TARDIS, the black level card, until play resumes."""
    while g.won or g.card_t0 is not None:
        w.step(set())
    w.wait(extra_ms, set())


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--board", required=True, help="JSON copy of the public dreamlo board")
    ap.add_argument("--flags", required=True, help="folder with flag PNGs (gb.png ...)")
    ap.add_argument("--out", default="/workspace/dih_gameplay.mp4")
    ap.add_argument("--stills", default="/workspace", help="folder for dih_vid_still_*.png")
    ap.add_argument("--work", default="/tmp/dihrec")
    ap.add_argument("--seeds", default="986,226,593,1236", help="maze seeds: levels 1..4")
    ap.add_argument("--title-seed", type=int, default=7)
    ap.add_argument("--levels", type=int, default=3, help="levels to clear before the end")
    args = ap.parse_args(argv)

    os.makedirs(args.work, exist_ok=True)
    G.time = TimeProxy([int(s) for s in args.seeds.split(",")])
    install_board(args.board, args.work)

    pygame.mixer.pre_init(G.MIXER_FREQ, -16, 2, G.MIXER_BUFFER)
    pygame.init()
    screen = pygame.display.set_mode((G.WIN_W, G.WIN_H))
    g = G.Game(args.title_seed, 1, character="archie", title=True, online=True)
    g.flags = G.Flags(folder=args.flags, download=False)
    log = SoundLog(g.sfx)
    # The title theme started inside the constructor (before logging): log it now.
    log.title.append([0.0, None])
    for _ in range(50):  # the board "refresh" thread reads the local copy
        if g.board.status == "online":
            break
        _time.sleep(0.05)
    print("board:", g.board.status, g.board.top(10), flush=True)

    vid = os.path.join(args.work, "video.mp4")
    w = World(g, screen, vid, args.stills)
    w.log = log
    pilot = Pilot(w)

    # --- title: Top 10 first (as the panel rotation shows it), then the characters
    w.step(set())
    g.title_t0 = w.now - G.TITLE_PANEL_MS  # start the rotation on the Top 10 panel
    w.mark("title screen: Top 10 panel")
    w.wait(3000)
    w.still("dih_vid_still_1_top10.png")
    w.wait(G.TITLE_PANEL_MS - 3000 + 300)
    w.mark("panel rotates to character choice (Archie)")
    w.wait(1500)
    w.tap(pygame.K_RIGHT, gap_ms=1400)
    w.mark("Holly highlighted")
    w.still("dih_vid_still_2_choose.png")
    w.tap(pygame.K_LEFT, gap_ms=1300)
    w.mark("back to Archie, Enter")
    w.tap(pygame.K_RETURN, gap_ms=0)

    # --- level 1
    w.mark("level 1 starts: Archie steps out of the TARDIS")
    w.wait(1200, set())
    # bump a hedge until he says "ow"
    for _ in range(4):
        dc, dr = g.facing
        if not g.is_open(g.col + dc, g.row + dr):
            break
        w.step({pygame.K_RIGHT})
        while g.busy():
            w.step(set())
    ow_before = log.count(G._ow_sound(g.character))
    w.mark("bumping a hedge")
    t0 = w.now
    while log.count(G._ow_sound(g.character)) == ow_before and w.now - t0 < 4000:
        w.step({pygame.K_UP})
    w.mark("'ow!'" if log.count(G._ow_sound(g.character)) > ow_before else "(no ow)")
    w.wait(600, set())

    results = []
    spot_still = {"done": False}
    events = {"spot": 0, "cloak": 0}

    def watch():
        if g.disguised and not watch.cl:
            watch.cl = True
            events["cloak"] += 1
            w.mark("Chameleon Cloak on (H)")
        if not g.disguised and watch.cl:
            watch.cl = False
            w.mark("cloak off")
        n = log.count("exterminate")
        if n > watch.ex:
            watch.ex = n
            events["spot"] += 1
            w.mark(f"'Exterminate!' spotted: +20 (score {g.score})")
            watch.still_at = w.now
        if watch.still_at and w.now >= watch.still_at and not spot_still["done"]:
            spot_still["done"] = True
            watch.still_at = None
            w.still("dih_vid_still_3_spotted.png")
    watch.cl = False
    watch.ex = 0
    watch.still_at = None

    dead = False
    for lvl in range(1, args.levels + 1):
        pilot.aggressive = (events["spot"] == 0)
        ok = pilot.run_level(on_frame=watch)
        results.append(ok)
        if not ok:
            w.mark(f"died on level {g.level}")
            dead = True
            break
        w.mark(f"level {g.level} reached the TARDIS (score {g.score})")
        while g.exit_demat_t0 is None:
            w.step(set())
        w.mark("TARDIS dematerialises, 'Level complete!'")
        if lvl == 1:
            w.wait(1500, set())
            w.still("dih_vid_still_4_tardis.png")
        while g.card_t0 is None:
            w.step(set())
        w.mark(f"level card: Level {g.level + 1}")
        wait_level_ready(w, g)
        w.mark(f"level {g.level} begins in a new maze ({g.num_daleks} Daleks)")
        w.wait(1500, set())

    if not dead:
        # --- the secret, then the end
        w.wait(800, set())
        w.mark("holding I+M (secret)")
        w.wait(2300, {pygame.K_i, pygame.K_m})
        w.wait(100, set())
        w.mark(f"secret: maze swarms with {len(g.daleks)} Daleks, 'Find the human!'")
        w.wait(2500, set())
        pilot.aggressive = True
        pilot.cloak_ok = False
        ok = pilot.run_level(max_ms=60000, on_frame=watch)
        if ok:
            w.mark("(reached the TARDIS instead of dying)")
            dead = False
        else:
            dead = True
    if dead:
        w.mark(f"EXTERMINATED (laser) on level {g.level}, score {g.score}")
        while not g.entry_active and not g.entry_done:
            w.step(set())
        w.mark("EXTERMINATED panel: name entry")
        w.wait(1400, set())
        for ch in "ARCHIE":
            w.tap(getattr(pygame, "K_" + ch.lower()), ch, gap_ms=260)
        w.wait(500)
        w.still("dih_vid_still_5_name.png")
        w.tap(pygame.K_RETURN, gap_ms=0)
        w.mark("Enter: score 'sent' as ARCHIE (stubbed, nothing posted)")
        w.wait(3200, set())
        w.mark("Enter to Play Again")
        w.tap(pygame.K_RETURN, gap_ms=0)
        w.mark("back on the title screen")
        g.title_t0 = w.now - G.TITLE_PANEL_MS  # straight to the Top 10 with the new score
        w.wait(800)
        g.board.refresh()
        w.wait(5200, set())
        w.still_dir = args.work  # (extra still, kept out of the five)
        w.still("top10_after.png")
        w.wait(300, set())

    total_ms = w.frames * 1000.0 / FPS
    w.close()
    wav = os.path.join(args.work, "audio.wav")
    mix_audio(log, total_ms, G.MIXER_FREQ, wav)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", vid, "-i", wav, "-c:v", "copy",
                    "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-shortest",
                    "-movflags", "+faststart", args.out], check=True)
    summary = {
        "duration_s": round(total_ms / 1000, 2), "levels": results, "marks": w.marks,
        "posted_stub": POSTED, "sounds": {k: log.count(k) for k in sorted({e["name"] for e in log.oneshots})},
        "cloak_loops": len(log.cloak), "score_final": g.final_score,
    }
    with open(os.path.join(args.work, "summary.json"), "w") as f:
        json.dump(summary, f, indent=1)
    print(json.dumps({k: v for k, v in summary.items() if k != "marks"}, indent=1))
    pygame.quit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
