#!/usr/bin/env python3
"""Make per-character "ow" sounds from assets/sounds/ow.wav by resampling
(pitch and length change together, like a tape speed change):

    python3 tools/make_ow_voices.py   ->  ow_archie.wav (-2 st), ow_holly.wav (+2 st)

Loudness: RMS matched to the original, then peak-limited to the original's
peak so nothing clips. Upward shifts are low-passed first (no aliasing).
"""
import os
import wave

import numpy as np

SOUNDS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets", "sounds")
SHIFTS = {"archie": -2, "holly": 2}


def main():
    with wave.open(os.path.join(SOUNDS, "ow.wav")) as w:
        p = w.getparams()
        x = np.frombuffer(w.readframes(p.nframes), "<i2").astype(float).reshape(-1, p.nchannels)
    rms0, pk0 = np.sqrt((x ** 2).mean()), np.abs(x).max()
    idx = np.arange(len(x))
    for name, st in SHIFTS.items():
        r = 2 ** (st / 12)
        src = x
        if r > 1:
            k = np.sinc(np.arange(-32, 33) / r) / r * np.hanning(65)
            src = np.stack([np.convolve(x[:, c], k / k.sum(), "same") for c in range(p.nchannels)], 1)
        t = np.arange(int(len(x) / r)) * r
        y = np.stack([np.interp(t, idx, src[:, c]) for c in range(p.nchannels)], 1)
        y *= rms0 / np.sqrt((y ** 2).mean())
        lim = min(pk0, 32000)
        if np.abs(y).max() > lim:
            y *= lim / np.abs(y).max()
        with wave.open(os.path.join(SOUNDS, f"ow_{name}.wav"), "wb") as o:
            o.setparams(p)
            o.writeframes(np.round(y).astype("<i2").tobytes())
        print(f"ow_{name}.wav  {st:+d} st  rms {np.sqrt((y ** 2).mean()):.0f}/{rms0:.0f}  peak {np.abs(y).max():.0f}")


if __name__ == "__main__":
    main()
