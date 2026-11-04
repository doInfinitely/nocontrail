"""Plant known flyovers, run the tracker, score recovery, plot spectrogram."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from contrail.detect import DetectConfig, find_tracks, leak_score, spectrogram  # noqa: E402
from contrail.synth import SynthConfig, render, write_wav  # noqa: E402

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("demo")
OUT.mkdir(parents=True, exist_ok=True)


def match(tracks, flyovers, t_grid, tol_hz=3.0, tol_frac=0.6):
    """A flyover is 'recovered' if some track sits within tol_hz of its
    fundamental (or a harmonic) for > tol_frac of the track's samples."""
    hits = []
    for fo in flyovers:
        f_true = fo.observed_frequency(t_grid)
        best = 0.0
        for tr in tracks:
            ft = np.interp(tr.times, t_grid, f_true)
            for h in (1, 2, 3):
                frac = np.mean(np.abs(tr.freqs - h * ft) < tol_hz * h)
                best = max(best, frac)
        hits.append(best >= tol_frac)
    return hits


results = []
for seed in range(5):
    cfg = SynthConfig(seed=seed)
    y, flyovers = render(cfg)
    if seed == 0:
        write_wav(str(OUT / "synthetic_leak.wav"), y, cfg.sr)
    tracks = find_tracks(y, cfg.sr)
    t_grid = np.arange(len(y)) / cfg.sr
    hits = match(tracks, flyovers, t_grid)
    results.append((seed, len(flyovers), sum(hits), len(tracks), leak_score(tracks)))
    print(f"seed {seed}: planted={len(flyovers)} recovered={sum(hits)} tracks_found={len(tracks)} leak={leak_score(tracks):.2f}")
    if seed == 0:
        for fo in flyovers:
            print(f"   planted  f0={fo.f0_hz:6.1f} Hz  v={fo.speed_mps:5.1f} m/s  alt={fo.altitude_m:6.0f} m  t_c={fo.t_closest:5.1f}s")
        for tr in tracks:
            print("   found   ", tr)

# spectrogram figure for seed 0
import matplotlib  # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

cfg = SynthConfig(seed=0)
y, flyovers = render(cfg)
dc = DetectConfig()
f, t, S = spectrogram(y, cfg.sr, dc)
tracks = find_tracks(y, cfg.sr, dc)

fig, ax = plt.subplots(figsize=(11, 4.5), dpi=130)
ax.pcolormesh(t, f, S, shading="auto", cmap="magma", vmin=np.percentile(S, 50), vmax=np.percentile(S, 99.5))
for tr in tracks:
    ax.plot(tr.times, tr.freqs, color="#39ff14", lw=1.2, alpha=0.9)
t_grid = np.arange(len(y)) / cfg.sr
for fo in flyovers:
    ax.plot(t_grid[::400], fo.observed_frequency(t_grid)[::400], "w--", lw=0.6, alpha=0.5)
ax.set_ylim(dc.f_lo, 500)
ax.set_xlabel("time (s)"); ax.set_ylabel("Hz")
ax.set_title("synthetic clip: speech + 3 planted flyovers — white dashed = truth, green = detected")
fig.tight_layout()
fig.savefig(OUT / "spectrogram.png")
print(f"\nwrote {OUT/'synthetic_leak.wav'} and {OUT/'spectrogram.png'}")
