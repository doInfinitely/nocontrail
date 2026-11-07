"""Audit a recording for aircraft-Doppler location leaks.

Samples fixed-length windows across a long file (ffmpeg extracts each to mono
16 kHz), runs the detector and flyover grouping on each, and writes a report:
per-window flyover count, parameters, and leak score.

This is the defensive audit the project is for: run it on audio you own, or on
a recording whose owner has asked you to check it, and give the result back to
them. It reports whether a clip leaks and the flyover geometry relative to the
mic (speed, miss distance, closest approach) — not a position on Earth.

Usage:
  audit_video.py VIDEO [--windows N] [--window-s S] [--report PATH] [--seed K]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from scipy.io import wavfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from contrail.detect import find_tracks, group_flyovers, leak_score  # noqa: E402


def duration_s(path: str) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


def extract(path: str, start: float, length: float, sr: int, dst: str) -> None:
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y", "-ss", f"{start:.3f}", "-t", f"{length:.3f}",
         "-i", path, "-vn", "-ac", "1", "-ar", str(sr), dst],
        check=True,
    )


def audit(path: str, n_windows: int, window_s: float, sr: int, seed: int) -> dict:
    total = duration_s(path)
    usable = max(total - window_s, 1.0)
    # evenly spaced windows, jittered, skipping the first/last 2% (intros/outros)
    rng = np.random.default_rng(seed)
    lo, hi = 0.02 * usable, 0.98 * usable
    centers = np.linspace(lo, hi, n_windows)
    jitter = rng.uniform(-0.5, 0.5, n_windows) * (usable / max(n_windows, 1))
    starts = np.clip(centers + jitter, 0, usable)

    windows = []
    with tempfile.TemporaryDirectory() as td:
        for i, start in enumerate(sorted(float(s) for s in starts)):
            wav = str(Path(td) / f"w{i}.wav")
            try:
                extract(path, start, window_s, sr, wav)
                fs, y = wavfile.read(wav)
                y = y.astype(np.float32) / 32768.0
                # a stream gap at this offset can yield a near-empty extract;
                # too few samples to form even one STFT frame -> skip it
                if y.size < fs:  # < 1 s of audio
                    raise ValueError(f"empty/short extract ({y.size} samples)")
                tracks = find_tracks(y, fs)
                groups = group_flyovers(tracks)
            except Exception as e:  # noqa: BLE001
                windows.append({"start_s": round(start, 1), "error": str(e)[:200]})
                continue
            windows.append({
                "start_s": round(start, 1),
                "lines": len(tracks),
                "flyovers": len(groups),
                "leak": round(leak_score(tracks), 3),
                "groups": [
                    {"harmonics": g.harmonics, "v_mps": round(g.fit.v, 1),
                     "miss_m": round(g.fit.d, 0), "tc_s": round(g.fit.tc, 1),
                     "rms_frac": round(g.fit.rms / g.fit.f0, 4),
                     # so the report can tell a clean (usable) flyover from a shallow one
                     "dur_s": round(g.duration, 1), "drop_hz": round(g.drop_hz, 1)}
                    for g in groups
                ],
            })

    scored = [w for w in windows if "leak" in w]
    return {
        "file": Path(path).name,
        "duration_s": round(total, 1),
        "windows_sampled": len(windows),
        "window_s": window_s,
        "windows_with_flyover": sum(w.get("flyovers", 0) > 0 for w in scored),
        "max_leak": max((w["leak"] for w in scored), default=0.0),
        "mean_leak": round(float(np.mean([w["leak"] for w in scored])), 3) if scored else 0.0,
        "windows": windows,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video")
    ap.add_argument("--windows", type=int, default=8)
    ap.add_argument("--window-s", type=float, default=90.0)
    ap.add_argument("--sr", type=int, default=16_000)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--report", default=None, help="write JSON report here")
    args = ap.parse_args()

    rep = audit(args.video, args.windows, args.window_s, args.sr, args.seed)
    print(f"{rep['file']}")
    print(f"  {rep['duration_s']/3600:.1f} h, {rep['windows_sampled']} windows of {rep['window_s']:.0f}s")
    print(f"  windows with a flyover: {rep['windows_with_flyover']}/{rep['windows_sampled']}")
    print(f"  leak score: max {rep['max_leak']:.2f}, mean {rep['mean_leak']:.2f}")
    for w in rep["windows"]:
        if w.get("flyovers"):
            gs = "; ".join(f"h{g['harmonics']} v={g['v_mps']:.0f} miss={g['miss_m']:.0f}m" for g in w["groups"])
            print(f"    {w['start_s']/3600:5.2f}h  leak {w['leak']:.2f}  {w['flyovers']} flyover(s): {gs}")
    if args.report:
        Path(args.report).write_text(json.dumps(rep, indent=2))
        print(f"  wrote {args.report}")


if __name__ == "__main__":
    main()
