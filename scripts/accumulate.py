"""Count clean flyovers across a WHOLE recording (video or audio) — the accumulation measure.

Thin wrapper over contrail.accumulate for files ffmpeg can read: each tile is extracted with
ffmpeg at the analysis rate and folded into one Exposure, which recognises the same aircraft
seen from two overlapping tiles by its measured lines. For WAV input `contrail audit` does the
same without ffmpeg.

Output is COUNTS ONLY. No per-flyover geometry, no timestamps, no location — by design, so the
result can inform a risk decision without itself being a locating artifact.

Usage: accumulate.py VIDEO [--tile-s 300] [--overlap-s 90] [--min-dur 12] [--min-drop-pct 3]
                      [--report PATH]
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

from contrail.accumulate import CleanConfig, Exposure, as_float  # noqa: E402
from contrail.detect import find_tracks, group_flyovers  # noqa: E402


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


def accumulate(path: str, cfg: CleanConfig) -> dict:
    total = duration_s(path)
    step = max(cfg.tile_s - cfg.overlap_s, 1.0)
    starts = np.arange(0.0, max(total - cfg.overlap_s, step), step)
    exp = Exposure(cfg)
    tiles_err = 0
    for start in starts:
        with tempfile.TemporaryDirectory() as td:
            wav = str(Path(td) / "t.wav")
            try:
                extract(path, float(start), cfg.tile_s, cfg.analysis_sr, wav)
                fs, y = wavfile.read(wav)
                y = as_float(y)
                if y.size < fs:  # < 1 s decoded (stream gap) — skip
                    raise ValueError("short extract")
                tracks = find_tracks(y, fs)
            except Exception:  # noqa: BLE001
                tiles_err += 1
                continue
        exp.add_window(group_flyovers(tracks), tracks, float(start))
    rep = exp.summary(Path(path).name, total)
    rep.update({"tiles": len(starts), "tiles_processed": exp.windows, "tiles_errored": tiles_err})
    return rep


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video")
    ap.add_argument("--tile-s", type=float, default=300.0)
    ap.add_argument("--overlap-s", type=float, default=90.0)
    ap.add_argument("--min-dur", type=float, default=12.0, help="clean-flyover minimum duration (s)")
    ap.add_argument("--min-drop-pct", type=float, default=3.0, help="clean-flyover minimum drop, %% of frequency")
    ap.add_argument("--report", default=None)
    a = ap.parse_args()

    cfg = CleanConfig(min_dur_s=a.min_dur, min_drop_frac=a.min_drop_pct / 100.0,
                      tile_s=a.tile_s, overlap_s=a.overlap_s)
    rep = accumulate(a.video, cfg)
    print(f"{rep['file']}: {rep['duration_s']/3600:.1f} h, full coverage "
          f"({rep['tiles_processed']}/{rep['tiles']} tiles)")
    print(f"  flyover detections: {rep['flyover_detections']}")
    print(f"  CLEAN flyovers (>={a.min_dur:.0f} s, >={a.min_drop_pct:.0f}% drop, possible fit): {rep['clean_flyovers']}")
    print(f"  confirmed by harmonics or a tight fit: {rep['clean_confirmed_flyovers']}")
    print(f"  peak {a.tile_s:.0f}-s window score: {rep['peak_window_score']:.2f} (per window; does not add up)")
    print(f"  crosses 3-flyover trilateration bar: {rep['crosses_trilateration_bar']}")
    if a.report:
        Path(a.report).write_text(json.dumps(rep, indent=2))
        print(f"  wrote {a.report}")


if __name__ == "__main__":
    main()
