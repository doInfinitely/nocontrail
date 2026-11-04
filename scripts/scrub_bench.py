"""Before/after: do the planted lines survive each scrub tier?

A planted flyover 'survives' if any detected track lies on it (or a harmonic)
for >60% of the track. Decoys are expected to be detected — that's the point —
so we report real-survivors and decoy-detections separately."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from contrail.detect import find_tracks  # noqa: E402
from contrail.scrub import ScrubConfig, scrub  # noqa: E402
from contrail.synth import SynthConfig, render, write_wav  # noqa: E402

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("demo")
OUT.mkdir(parents=True, exist_ok=True)


def on_line(tracks, flyovers, t):
    hits = []
    for fo in flyovers:
        ft = fo.observed_frequency(t)
        best = 0.0
        for tr in tracks:
            fi = np.interp(tr.times, t, ft)
            best = max(best, max(np.mean(np.abs(tr.freqs - h * fi) < 3 * h) for h in (1, 2, 3)))
        hits.append(best > 0.6)
    return hits


tiers = {
    "gate only":   dict(gate=True,  notch=False, decoy=False),
    "notch only":  dict(gate=False, notch=True,  decoy=False),
    "decoy only":  dict(gate=False, notch=False, decoy=True),
    "all three":   dict(gate=True,  notch=True,  decoy=True),
}

for name, kw in tiers.items():
    real_before = real_after = decoy_hits = n_planted = n_decoys = 0
    for seed in range(3):
        cfg = SynthConfig(seed=seed)
        y, flyovers = render(cfg)
        t = np.arange(len(y)) / cfg.sr
        out, rep = scrub(y, cfg.sr, ScrubConfig(seed=seed, **kw))
        n_planted += len(flyovers)
        real_before += sum(on_line(rep.tracks_before, flyovers, t))
        real_after += sum(on_line(rep.tracks_after, flyovers, t))
        if rep.decoys:
            n_decoys += len(rep.decoys)
            decoy_hits += sum(on_line(rep.tracks_after, rep.decoys, t))
        if seed == 0 and name == "all three":
            write_wav(str(OUT / "synthetic_scrubbed.wav"), out, cfg.sr)
    line = f"{name:12s} real lines still detectable: {real_after}/{n_planted} (was {real_before}/{n_planted})"
    if n_decoys:
        line += f"   decoys detected: {decoy_hits}/{n_decoys}"
    print(line)
