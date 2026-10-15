#!/usr/bin/env python3
"""Two-player LAN self-test: two Game objects in one process, talking over
real UDP on localhost. Headless; never posts to dreamlo.

    SDL_VIDEODRIVER=dummy python3 tools/net_selftest.py [shot_dir]

Scores go to a throwaway file in the temp folder, never ~/.daleks_in_hedges.

Screenshots (lobby, both players, TARDIS wait, watching) go to shot_dir if given.
"""
import os
import sys
import tempfile
import time

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import pygame  # noqa: E402

import game as G  # noqa: E402
import net  # noqa: E402

# Never touch the player's own score file.
G.Leaderboard.__init__.__defaults__ = (False, os.path.join(tempfile.mkdtemp(prefix="dih-test-"), "scores.json"))

SHOTS = sys.argv[1] if len(sys.argv) > 1 else None
DISC = 47490          # test discovery port (the real one is net.DISCOVERY_PORT)
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""), flush=True)


class Clock:
    def __init__(self):
        self.t0 = time.monotonic()

    def now(self):
        return (time.monotonic() - self.t0) * 1000.0


pygame.init()
screen = pygame.display.set_mode((G.WIN_W, G.WIN_H))
clock = Clock()


def pump(games, ms, until=None, each=None):
    end = clock.now() + ms
    while clock.now() < end:
        now = clock.now()
        for g in games:
            g.update(now)
        if each:
            each(now)
        if until and until(now):
            return True
        time.sleep(1 / 120)
    return False


def shot(g, name):
    if SHOTS:
        g.draw(screen, clock.now())
        pygame.image.save(screen, os.path.join(SHOTS, name))


def new_pair(port, host_char="archie", seed=None):
    """Host + joiner connected through discovery; returns (host, join)."""
    h = G.Game(seed, title=True, online=False, net_port=port, discovery_port=DISC)
    j = G.Game(None, title=True, online=False, net_port=port, discovery_port=DISC)
    h.title_pick = G.CHAR_ORDER.index(host_char)
    h.open_lobby()
    h.on_key(pygame.K_RETURN, clock.now())          # Host
    j.open_lobby()
    j.on_key(pygame.K_DOWN, clock.now())            # -> Join
    j.on_key(pygame.K_RETURN, clock.now())
    pump([h, j], 3500, until=lambda n: bool(j.lobby["hosts"]))
    return h, j


def connect(h, j):
    j.on_key(pygame.K_RETURN, clock.now())
    pump([h, j], 2000, until=lambda n: h.mode == "play" and j.mode == "play")


def teleport(j, h, cell, facing):
    """Put the joiner somewhere for a test. A real move is one cell at a time;
    the host refuses jumps, so tell the host too."""
    j._place_archie(cell, facing)
    h.other.cell = h.other.src = h.other.dst = tuple(cell)
    h.other.moving = False


def corridor(g, length):
    for d in G.DIR_ORDER:
        run = g._straight_run(d, length)
        if run:
            return d, run
    raise RuntimeError("no straight corridor")


# --------------------------------------------------------------------------
# 1. Discovery, joining, shared maze, Dalek sync, safe zone, hedge check.
h, j = new_pair(47481, "archie")
hosts = j.lobby["hosts"]
check("discovery finds the host", len(hosts) == 1 and hosts[0][1]["char"] == "archie",
      f"{[(a, i.get('name')) for a, i in hosts]}")
shot(h, "lan_lobby_host.png")
shot(j, "lan_lobby_join.png")
connect(h, j)
check("both in play", h.mode == j.mode == "play")
check("maze seed matches", h.seed == j.seed and h.grid == j.grid, f"seed {h.seed}")
check("characters split", h.character == "archie" and j.character == "holly" and j.other.character == "archie")
check("joiner starts one cell further in", (j.col, j.row) != (h.col, h.row) and h.other.cell == (j.col, j.row))

worst = [0.0]
inside = [0]


def sync_probe(now):
    for d in h.daleks:
        if d.cell() in h.safe_cells:
            inside[0] += 1
    if j.net and j.net.state_fresh is False and len(j.daleks) == len(h.daleks):
        for dh, dj in zip(h.daleks, j.daleks):
            if dj.net_pos is not None:
                worst[0] = max(worst[0], abs(dh.pos[0] - dj.net_pos[0]) + abs(dh.pos[1] - dj.net_pos[1]))


pump([h, j], 4000, each=sync_probe)
# One state packet every 50 ms; a Dalek glides one cell per 260 ms.
check("Dalek positions match within a tick", worst[0] <= 2 * (50 / G.DALEK_STEP_MS) + 0.05, f"max drift {worst[0]:.3f} cells")
check("no Dalek enters the start area", inside[0] == 0 and len(h.safe_cells) > 6, f"{len(h.safe_cells)} safe cells")

# Joiner walks; host sees it move. Then a forged move through a hedge is refused.
before = h.other.cell
j.nudge(clock.now())
pump([h, j], 1500, each=lambda n: j.nudge(n))  # (time to turn out of a dead end first)
check("host follows the joiner's moves", h.other.cell == (j.col, j.row) and h.other.cell != before,
      f"{before} -> {h.other.cell}")
wall = next((c, r) for r in range(1, G.MAZE_SIZE) for c in range(1, G.MAZE_SIZE)
            if not h.is_open(c, r) and abs(c - h.other.cell[0]) + abs(r - h.other.cell[1]) == 1)
good = h.other.cell
h._net_apply_state(dict(j._net_state(clock.now()), c=list(wall), s=list(wall), d=list(wall)), clock.now())
check("host refuses a move through a hedge", h.other.cell == good, f"tried {wall}")
far = max(h.safe_cells | {(1, 1)}, key=lambda c: 0)
shot(h, "lan_both_players.png")

# 2. A Dalek spots and kills the joiner (with 25% packet loss both ways).
h.net.drop_rate = j.net.drop_rate = 0.25
d_dir, run = corridor(h, 5)
target = run[-1]
back = (-d_dir[0], -d_dir[1])
teleport(j, h, target, back)               # joiner stands at the far end, facing the Dalek
pump([h, j], 300)
dk = h.daleks[0]
h._park_other_daleks(dk)
for other in h.daleks[1:]:
    other.state = "roam"
dk.pos = [float(run[0][0]), float(run[0][1])]
dk.target = run[0]
dk.facing = dk.prev_facing = d_dir
dk.state, dk.wait_until = "roam", clock.now() + 1e5   # stands still, looking down the corridor
spot0 = j.spot_bonus
pump([h, j], 4000, until=lambda n: j.dead and h.other.dead)
check("a Dalek spots and kills the joiner", j.dead and h.other.dead,
      f"joiner +{j.spot_bonus - spot0} for the shout")
check("the joiner gets +20 for the shout", j.spot_bonus - spot0 == G.SCORE_PER_SPOT)
h.net.drop_rate = j.net.drop_rate = 0.0
pump([h, j], G.DEATH_MSG_MS + G.NET_WATCH_PAN_MS + 200)
check("the dead joiner watches the host", j.watching(clock.now()) and j.watch_blend >= 1.0)
check("no game over while one player lives", not j.entry_active and not j.net_over)
shot(j, "lan_watching.png")

# 3. The survivor reaches the TARDIS: both go on, the joiner respawns.
for d in h.daleks:
    d.pos = [float(G.MAZE_SIZE // 2), float(G.MAZE_SIZE // 2)]
ex = h.exit_cell
nb = next(((ex[0] - dc, ex[1] - dr), (dc, dr)) for dc, dr in G.DIR_ORDER if h.is_open(ex[0] - dc, ex[1] - dr))
h.daleks = []
h._place_archie(nb[0], nb[1])
lvl, hb, jb = h.level, h.level_bonus, j.level_bonus
h.try_action(("step", 1), clock.now())
pump([h, j], 12000, until=lambda n: h.level == j.level == lvl + 1 and h.card_t0 is None and j.card_t0 is None)
check("survivor through: both on the next level", h.level == j.level == lvl + 1 and h.seed == j.seed,
      f"level {h.level}/{j.level}, seed {h.seed}/{j.seed}")
check("the fallen player respawns in the start area", not j.dead and (j.col, j.row) in h.safe_cells)
check("+50 to the survivor only", h.level_bonus - hb == G.SCORE_PER_LEVEL and j.level_bonus == jb)

# 4. First in gets +50 and waits; the TARDIS leaves when the second arrives.
h.daleks = []
pump([h, j], 300)
ex = h.exit_cell
nb = next(((ex[0] - dc, ex[1] - dr), (dc, dr)) for dc, dr in G.DIR_ORDER if h.is_open(ex[0] - dc, ex[1] - dr))
teleport(j, h, nb[0], nb[1])
pump([h, j], 200)
lvl, hb, jb = h.level, h.level_bonus, j.level_bonus
j.try_action(("step", 1), clock.now())
pump([h, j], 2500)
check("joiner first in: +50 to the joiner", j.level_bonus - jb == G.SCORE_PER_LEVEL and h.net_first == "join")
check("the TARDIS waits for the second player", j.won and j.exit_hold and j.exit_demat_t0 is None and h.level == lvl)
check("the waiting player is hidden and watches", h.other.in_tardis and j.watching(clock.now()))
shot(j, "lan_tardis_waiting.png")
h._place_archie(nb[0], nb[1])
h.try_action(("step", 1), clock.now())
pump([h, j], 12000, until=lambda n: h.level == j.level == lvl + 1 and h.card_t0 is None and j.card_t0 is None)
check("second in: no +50, both to the next level", h.level_bonus == hb and h.level == j.level == lvl + 1
      and h.seed == j.seed, f"level {h.level}")
h._net_close(); j._net_close()

# 5. Both exterminated: game over, each gets their own name entry.
h, j = new_pair(47482, "holly")
connect(h, j)
check("host as Holly: joiner is Archie", h.character == "holly" and j.character == "archie")
pump([h, j], 500)
h.survive_ms = j.survive_ms = 7000.0      # something to put on the board
now = clock.now()
h._kill(now, h.daleks[0])
h._net_kill_other(now, h.daleks[1])
pump([h, j], G.DEATH_MSG_MS + 600)
check("both dead: game over on both", h.net_over and j.net_over and h.entry_active and j.entry_active)
for g, name in ((h, "HOSTY"), (j, "JOINY")):
    g.entry_name = ""
    for ch in name:
        g.on_key(getattr(pygame, f"K_{ch.lower()}"), clock.now(), ch)
    g.on_key(pygame.K_RETURN, clock.now())
check("each player saves their own name", h.entry_note == "HOSTY" and j.entry_note == "JOINY"
      and not h.board.online and not j.board.online)
h.on_key(pygame.K_RETURN, clock.now() + G.LASER_MS)
j.on_key(pygame.K_RETURN, clock.now() + G.LASER_MS)
check("then back to the title", h.mode == j.mode == "title" and h.net is None and j.net is None)

# 6. A dropped connection: both carry on alone.
h, j = new_pair(47483)
connect(h, j)
pump([h, j], 800)
h.net.blocked = j.net.blocked = True
pump([h, j], net.TIMEOUT_S * 1000 + 500, until=lambda n: h.net is None and j.net is None)
check("connection loss noticed on both", h.net is None and j.net is None and h.net_notice and j.net_notice)
before = [tuple(d.pos) for d in j.daleks]
pump([h, j], 1500, each=lambda n: j.nudge(n))
check("the joiner plays on alone (its Daleks move)", [tuple(d.pos) for d in j.daleks] != before and j.mode == "play")
shot(j, "lan_connection_lost.png")

# 7. --join style: straight to a host address, no discovery.
h = G.Game(None, title=True, online=False, net_port=47484, discovery_port=DISC)
h.open_lobby(stage="host")
j = G.Game(None, title=True, online=False)
j.open_lobby(stage="join", addr=net.parse_hostport("127.0.0.1:47484"))
pump([h, j], 2000, until=lambda n: h.mode == j.mode == "play")
check("join by address", h.mode == j.mode == "play" and h.seed == j.seed)
h._net_close(); j._net_close()

# 7b. After a kill the shooter patrols again, ignores the corpse and can
# still get the survivor (it used to freeze in its "fire" state for good).
for victim, port in (("joiner", 47487), ("host", 47488)):
    h, j = new_pair(port)
    connect(h, j)
    pump([h, j], 500)
    h.survive_ms = j.survive_ms = 5000.0
    d = h.daleks[0]
    now = clock.now()
    d.state, d.fire_t0 = "fire", now
    if victim == "joiner":
        h._net_kill_other(now, d)
    else:
        h._kill(now, d)
    start = tuple(d.pos)
    moved = pump([h, j], 2000 + G.DALEK_RECOVER_MS,
                 until=lambda n: abs(d.pos[0] - start[0]) + abs(d.pos[1] - start[1]) >= 1.0)
    jd = j.daleks[0]
    check(f"{victim} shot: the Dalek patrols again on both sides", moved and d.state == "roam" and jd.state == "roam",
          f"host {d.state}, joiner {jd.state}, moved {abs(d.pos[0] - start[0]) + abs(d.pos[1] - start[1]):.1f} cells")
    if victim == "joiner":
        shot(h, "lan_after_kill.png")
    # The same Dalek can still see and shoot the survivor.
    survivor = h if victim == "joiner" else j
    d_dir, run = corridor(h, 5)
    if victim == "joiner":
        h._place_archie(run[-1], (-d_dir[0], -d_dir[1]))
    else:
        teleport(j, h, run[-1], (-d_dir[0], -d_dir[1]))
    for o in h.daleks[1:]:
        o.pos = [float(G.MAZE_SIZE // 2), float(G.MAZE_SIZE // 2)]
        o.target = (G.MAZE_SIZE // 2, G.MAZE_SIZE // 2)
        o.state, o.wait_until = "roam", clock.now() + 1e5
    h.daleks = [d] + [o for o in h.daleks[1:] if o.cell() not in run]
    d.pos, d.target = [float(run[0][0]), float(run[0][1])], run[0]
    d.facing = d.prev_facing = d_dir
    d.state, d.wait_until = "roam", clock.now() + 1e5
    pump([h, j], 3000, until=lambda n: survivor.dead)
    pump([h, j], 600)                         # the game-over event goes round
    check(f"{victim} shot: that Dalek can still get the survivor", survivor.dead and h.net_over and j.net_over,
          f"dead {survivor.dead}, over {h.net_over}/{j.net_over}, dalek {d.state} at {d.cell()} facing {d.facing}, run {run[0]}->{run[-1]}, survivor at {(survivor.col, survivor.row)} disguised {survivor.disguised} won {survivor.won}")
    h._net_close(); j._net_close()

# 7c. Sound: the other player's steps, bumps and cloak; and watching keeps
# the game audible, heard from where the survivor is.
def listen(g):
    log = []
    real_voice = g.sfx.play_voice
    real_play = g.sfx.play

    def voice(name, left, right):
        log.append((name, round(max(left, right), 3)))
        return real_voice(name, left, right)

    def play(name):
        log.append((name, None))
        return real_play(name)
    g.sfx.play_voice, g.sfx.play = voice, play
    return log


h, j = new_pair(47489)
connect(h, j)
hlog, jlog = listen(h), listen(j)
pump([h, j], 1500, each=lambda n: j.nudge(n))
steps = [v for n, v in hlog if n.startswith("step") and v is not None]
check("host hears the joiner's footsteps", len(steps) >= 4 and all(0 < v <= G.REMOTE_SOUND_GAIN for v in steps),
      f"{len(steps)} steps, loudest {max(steps, default=0)}")
pump([h, j], 1500, each=lambda n: h.nudge(n))
steps = [v for n, v in jlog if n.startswith("step") and v is not None]
check("joiner hears the host's footsteps", len(steps) >= 4, f"{len(steps)} steps")
del hlog[:]
pump([h, j], 400)
j.toggle_disguise(clock.now())
for _ in range(G.OW_BUMPS):
    j.last_bump_t = -1e9
    j._hedge_bump(clock.now())
    pump([h, j], 120)
pump([h, j], 400)
names = [n for n, v in hlog if v is not None]
check("host hears the joiner's cloak and hedge bumps", "cloak_on" in names and ("rustle" in names or "ow" in names),
      f"{sorted(set(names))}")
# Watching: the joiner is exterminated; it still hears Daleks and the host's steps.
j.toggle_disguise(clock.now())
pump([h, j], 300)
near = sorted((dist, c) for c, dist in h._path_dist((h.col, h.row)).items() if 7 <= dist <= 10 and c not in h.safe_cells)
dk = h.daleks[0]
dk.pos, dk.target, dk.state, dk.wait_until = [float(near[0][1][0]), float(near[0][1][1])], near[0][1], "roam", 0.0
h.disguised = True                       # hidden, so the test Dalek can't end the game
h.disguise_t0 = clock.now() + 1e6
h._net_kill_other(clock.now(), h.daleks[1])
pump([h, j], G.DEATH_MSG_MS + G.NET_WATCH_PAN_MS + 200)
del jlog[:]
hums = []
pump([h, j], 2000, each=lambda n: hums.append(max(max(l) for l in j.sfx.hum_levels)))
lp = j.listener_pos(clock.now())
check("watching: sound is heard from the survivor", j.watching(clock.now()) and j.sound_live(clock.now())
      and abs(lp[0] - h.col) + abs(lp[1] - h.row) < 1.5, f"listener {tuple(round(x, 1) for x in lp)}, host {(h.col, h.row)}")
check("watching: the Daleks still hum", max(hums) > 0.0, f"loudest hum {max(hums):.3f}")
h.disguised = False
pump([h, j], 1500, each=lambda n: h.nudge(n))
steps = [v for n, v in jlog if n.startswith("step") and v is not None]
check("watching: the survivor's steps are heard", len(steps) >= 4, f"{len(steps)} steps, loudest {max(steps, default=0)}")
h._net_close(); j._net_close()

# 8. Transport: reliable events under heavy loss, stale states dropped.
a = net.Link("host", port=47485)
b = net.Link("join", peer=("127.0.0.1", 47485))
a.drop_rate = b.drop_rate = 0.5
b.send_event("hello")
for i in range(20):
    b.send_event("ping", n=i)
got = []
t_end = time.monotonic() + 5
while time.monotonic() < t_end and len(got) < 21:
    a.poll(); b.poll()
    got += a.take_events()
    time.sleep(0.005)
nums = [d["n"] for k, d in got if k == "ping"]
check("reliable events: all 20 arrive once through 50% loss", sorted(nums) == list(range(20)), f"resent {b.stats['resent']}, duplicates dropped {a.stats['dup']}")
a.drop_rate = b.drop_rate = 0.0
b.out_seq = 10; b.send_state({"x": 10})
b.out_seq = 4; b.send_state({"x": 5})        # arrives after a newer one
time.sleep(0.05); a.poll()
check("out-of-order state packets are dropped", a.state == {"x": 10} and a.stats["stale"] == 1)
a.close(); b.close()

failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} passed")
pygame.quit()
sys.exit(1 if failed else 0)
