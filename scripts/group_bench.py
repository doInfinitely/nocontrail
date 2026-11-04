"""Score flyover grouping against planted truth.

Usage: group_bench.py [first_seed] [last_seed]

A track belongs to a planted flyover if it sits on one of its harmonics. Pair
precision: of track pairs grouped together, how many share a plane. Pair recall:
of track pairs sharing a plane, how many were grouped.
"""
from __future__ import annotations

import sys
from itertools import combinations
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from contrail.detect import find_tracks, group_flyovers, leak_score  # noqa: E402
from contrail.synth import SynthConfig, render  # noqa: E402


def owner(tr, flyovers, t_grid, tol_hz=3.0, tol_frac=0.6):
    """Index of the planted flyover this track sits on, or None."""
    best, best_frac = None, tol_frac
    for i, fo in enumerate(flyovers):
        ft = np.interp(tr.times, t_grid, fo.observed_frequency(t_grid))
        for h in (1, 2, 3):
            frac = float(np.mean(np.abs(tr.freqs - h * ft) < tol_hz * h))
            if frac >= best_frac:
                best, best_frac = i, frac
    return best


def score(tracks, groups, flyovers, t_grid):
    owners = {id(tr): owner(tr, flyovers, t_grid) for tr in tracks}
    label = {id(tr): g for g, grp in enumerate(groups) for tr in grp.tracks}
    tp = fp = fn = 0
    for a, b in combinations(tracks, 2):
        oa, ob = owners[id(a)], owners[id(b)]
        if oa is None or ob is None:
            continue
        same_group = label[id(a)] == label[id(b)]
        if same_group and oa == ob:
            tp += 1
        elif same_group:
            fp += 1
        elif oa == ob:
            fn += 1
    return tp, fp, fn, sum(o is None for o in owners.values())


def main() -> None:
    first = int(sys.argv[1]) if len(sys.argv) > 1 else 8
    last = int(sys.argv[2]) if len(sys.argv) > 2 else 15
    totals = np.zeros(4, dtype=int)
    for seed in range(first, last + 1):
        cfg = SynthConfig(seed=seed)
        y, flyovers = render(cfg)
        t_grid = np.arange(len(y)) / cfg.sr
        tracks = find_tracks(y, cfg.sr)
        groups = group_flyovers(tracks)
        row = score(tracks, groups, flyovers, t_grid)
        totals += row
        print(f"seed {seed}: planted={len(flyovers)} lines={len(tracks)} groups={len(groups)} "
              f"same-plane merged={row[0]} wrong merges={row[1]} missed merges={row[2]} "
              f"off-truth lines={row[3]} leak={leak_score(tracks):.2f}")
    tp, fp, fn, off = totals
    print(f"\npair precision {tp}/{tp + fp}, pair recall {tp}/{tp + fn}, off-truth lines {off}")


if __name__ == "__main__":
    main()
