# Daleks in Hedges

An isometric hedge-maze stealth game. Play as Archie or Holly, dodge the Daleks and reach the TARDIS in the far corner to escape to the next level: a new maze with more Daleks.

## Install

```bash
git clone https://github.com/brixham99/Daleks-in-Hedges.git
cd Daleks-in-Hedges
python3 -m pip install -r requirements.txt
python3 game.py
```

If pygame won't build ("Failed building wheel", common on very new Pythons such as 3.14), install the drop-in replacement pygame-ce instead:

```bash
python3 -m pip install pygame-ce numpy
```

Cloned under the old name (Archie-Maze)? GitHub redirects it, so `git pull` still works. To tidy up, rename the folder and run `git remote set-url origin https://github.com/brixham99/Daleks-in-Hedges.git`.

## How to play

On the title screen, choose Archie or Holly and start. Every 7 seconds the title screen swaps between the character choice and the online Top 10.

Daleks roam the maze. A Dalek sees you only along the corridor it is facing, up to 8 cells away with no hedge in between. When it spots you it shouts "Exterminate!", its eye stalk glows, and a moment later it fires. Listen out: each Dalek hums as it glides, louder the closer it is, even through hedges.

Use the **Chameleon Cloak** to turn into a hedge for up to 4 seconds. Daleks can't see you while cloaked. It recharges in proportion to how long you hid: 3 seconds after a full 4-second hide, 1.5 seconds after 2 seconds, and never less than 1 second; the badge in the bottom-right corner shows its charge.

Step onto the TARDIS to go to the next level. Level N has 3 + 2 × (N − 1) Daleks, up to 15. If you are exterminated, you return to the title screen.

## Two players on a local network

Two copies of the game on the same network can play one maze together, Archie and Holly at once. It's a race to the TARDIS: the first one in gets the +50 and waits inside, and the TARDIS leaves when the other player gets there too.

- **Host:** on the title screen press **N**, choose **Host a game** and pick Archie or Holly with Left / Right. The other player gets the other character.
- **Join:** press **N** on the other computer and choose **Join a game**. Games on the network appear within about 3 seconds; pick one and press Enter.

Both players start in a Dalek-free area round the TARDIS. Daleks hunt either player. If one is exterminated, the camera follows the other; if they reach the TARDIS, both go on to the next level. If both are exterminated, it's game over and each player can post their own score. If the connection drops for 3 seconds, each game carries on alone.

The first time you host, your Mac may ask whether to allow incoming network connections for Python. Click **Allow**.

Ports: UDP **47474** to find games, and UDP **47475** for the game itself.

Some Wi-Fi networks (guest, school or office networks) block broadcasts, so the game won't appear in the list. Then join by address instead: the host's lobby screen shows its IP address, and the other player runs `python3 game.py --join 192.168.1.20` (with that address).

## Controls

The game runs at 1280×720 in a window that you can resize; press **F** for full screen, or start full screen with `python3 game.py --fullscreen`.


| Key | Action |
| --- | --- |
| Left / Right or A / D | Turn |
| Up / W | Step forward |
| Down / S | Step back |
| H or Shift | Chameleon Cloak (press again to stop early) |
| F | Full screen on / off (not while typing a name) |
| Esc | Quit |
| Title screen: Left / Right, A / D or 1 / 2 | Choose a character |
| Title screen: Enter or Space | Start |
| Title screen: N | Two players on a local network |
| After extermination: Enter, Space or R | Back to the title screen |

## Scoring

- **1 point per second while moving.** Standing still, turning, cloaking or bumping into hedges earns nothing.
- **+20** each time a Dalek shouts "Exterminate!" at you (even if it then gets you).
- **+50** for each new level.

When you are exterminated, type a name (up to 8 letters, digits or spaces) and press Enter to post it, or Esc to skip. The online Top 10 keeps one best score per name: <http://dreamlo.com/lb/6ac7b3018f40bc15a8400bbc/json>

When you post a score, the game looks up your country (from ipinfo.io, or ipapi.co if that fails) and the Top 10 shows its flag, downloaded once from flagcdn.com into `~/.daleks_in_hedges/flags40/`. Older scores have no flag.

Offline, the game shows the last cached Top 10 and sends unsent scores later. The cache, your last name and your best score are in `~/.daleks_in_hedges/scores.json`.

Note: the free [dreamlo](http://dreamlo.com) tier keeps the private code in the client, so anyone reading `game.py` could post or clear scores. Fine for a family game.

---

## For developers

### Command-line flags

| Flag | Effect |
| --- | --- |
| `--seed N` | Fixed maze seed |
| `--level N` | Start on level N |
| `--character archie\|holly` | Character to use when skipping the title |
| `--no-title` | Skip the title screen |
| `--offline` | Never contact dreamlo |
| `--screenshot FILE` | Save a frame and quit |
| `--frames N` | Simulate N movement frames before the screenshot |
| `--scene NAME` | Set up a test scene, then screenshot |
| `--scene-ms N` | How far into the scene (ms) to take the shot |
| `--demo-scores` | Fill the Top 10 with sample names (never sent) |
| `--host` | Host a network game straight away (as `--character`) |
| `--join HOST[:PORT]` | Join the network game at that address, skipping discovery |
| `--net-port N` | UDP game port to host on or join (default 47475) |

### Screenshots without a screen

```bash
SDL_VIDEODRIVER=dummy python3 game.py --seed 1 --scene laser --screenshot laser.png
SDL_VIDEODRIVER=dummy python3 game.py --seed 1 --scene tardis_demat --scene-ms 6500 --screenshot demat.png
```

Scenes: `dalek`, `laser`, `telegraph`, `disguise` (the cloak), `tardis`, `tardis_demat`, `tardis_exit`, `title`, `title_top10`, `score_hud`, `name_entry`. Headless runs never contact dreamlo or write the score file.

### Testing network play

The networking lives in `net.py` (standard library only). `tools/net_selftest.py` runs a host and a joiner in one process over localhost and checks discovery, sync, extermination, the shared TARDIS, game over and a dropped connection. It uses a throwaway score file and never contacts dreamlo:

```bash
SDL_VIDEODRIVER=dummy python3 tools/net_selftest.py
```

Two separate copies on one machine also work: `python3 game.py --host --net-port 47480` in one terminal and `python3 game.py --join 127.0.0.1:47480` in another. Add `--frames N` with `SDL_VIDEODRIVER=dummy` for a headless run that prints a summary.

### Sounds and graphics

Sounds are WAV files in `assets/sounds/` (22050 Hz, 16-bit mono). The voices use espeak-ng with a ring modulator; everything else is synthesised, apart from the supplied TARDIS recording. The title theme is an original track, not the BBC theme. To regenerate them (needs numpy; voices also need `espeak-ng`):

```bash
python3 tools/make_sounds.py                              # all sounds
python3 tools/make_sounds.py --only dalek_destroy         # just some
python3 tools/make_sounds.py --spectrograms /tmp          # also write spectrogram PNGs
```

`make_sounds.py` keeps the existing `tardis_demat.wav` unless you pass `--overwrite-tardis`. To use your own TARDIS sound, save it as `assets/sounds/tardis_real.wav` (or `.ogg`/`.mp3`); it is git-ignored, and the dematerialisation stretches to match its length (3–15 s).

The cloak badge and TARDIS lamp glow in `assets/fx/` are made by `python3 tools/make_fx.py`.

Without working audio, or with a sound file missing, the game still runs silently.

## Copyright

©2026 Nathan, Archie & Holly Anderson. Doctor Who, the TARDIS, the Daleks and related sound effects are trademarks and copyright of the BBC/BBC Studios. Dalek is also associated with the estate of Terry Nation. All rights in those works remain with their respective owners.
