"""Count clean flyovers across a WHOLE recording — the accumulation measure.

The per-window leak score (audit_video.py) never sums flyovers across time, and that is
exactly what trilateration does: three or more clean flyovers at different geometries, spread
over a whole stream, constrain a position. This tool tiles the entire file (no sampling), runs
the detector on each tile, de-duplicates flyovers that straddle a tile boundary, and reports
how many CLEAN ones accumulate over the stream.

"Clean" = long and deep enough to fit well: duration >= --min-dur s and pitch drop >= --min-drop Hz.
Those are the flyovers that carry usable geometry; short/shallow detections do not.

Output is COUNTS ONLY. No per-flyover geometry, no timestamps, no location — by design, so the
result can inform a risk decision without itself being a locating artifact.

Usage: accumulate.py VIDEO [--tile-s 90] [--overlap-s 10] [--min-dur 40] [--min-drop 15]
                      [--sr 16000] [--report PATH]
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

from contrail.detect import find_tracks, group_flyovers  # noqa: E402
from contrail.report import _plausible  # noqa: E402


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


def accumulate(path: str, tile_s: float, overlap_s: float, min_dur: float, min_drop: float,
               sr: int) -> dict:
    total = duration_s(path)
    step = max(tile_s - overlap_s, 1.0)
    starts = np.arange(0.0, max(total - overlap_s, step), step)

    # Each clean flyover recorded as (global_tc, f0) only, for de-duplication across tiles.
    seen: list[tuple[float, float]] = []
    clean = 0
    clean_plausible = 0
    any_fly = 0
    tiles_ok = 0
    tiles_err = 0
    for start in starts:
        with tempfile.TemporaryDirectory() as td:
            wav = str(Path(td) / "t.wav")
            try:
                extract(path, float(start), tile_s, sr, wav)
                fs, y = wavfile.read(wav)
                y = y.astype(np.float32) / 32768.0
                if y.size < fs:  # < 1 s decoded (stream gap) — skip
                    raise ValueError("short extract")
                groups = group_flyovers(find_tracks(y, fs))
            except Exception:  # noqa: BLE001
                tiles_err += 1
                continue
        tiles_ok += 1
        for g in groups:
            any_fly += 1
            if g.duration < min_dur or g.drop_hz < min_drop:
                continue
            gtc = float(start) + g.fit.tc
            # same aircraft seen in an overlapping tile if close in global time and pitch
            if any(abs(gtc - t) < tile_s and abs(g.fit.f0 - f) < 2.0 for t, f in seen):
                continue
            seen.append((gtc, g.fit.f0))
            clean += 1
            # same realism filter the report uses; geometry is checked, never stored
            if _plausible({"v_mps": g.fit.v, "miss_m": g.fit.d, "harmonics": g.harmonics,
                           "rms_frac": g.fit.rms / g.fit.f0}):
                clean_plausible += 1

    return {
        "file": Path(path).name,
        "duration_s": round(total, 1),
        "coverage": "full",
        "tiles": len(starts),
        "tiles_processed": tiles_ok,
        "tiles_errored": tiles_err,
        "flyover_detections": any_fly,
        "clean_flyovers": clean,
        "clean_plausible_flyovers": clean_plausible,
        "min_dur_s": min_dur,
        "min_drop_hz": min_drop,
        # three clean flyovers at distinct geometries is the detector's own trilateration bar
        "crosses_trilateration_bar": clean >= 3,
        "crosses_trilateration_bar_plausible": clean_plausible >= 3,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video")
    ap.add_argument("--tile-s", type=float, default=90.0)
    ap.add_argument("--overlap-s", type=float, default=10.0)
    ap.add_argument("--min-dur", type=float, default=40.0, help="clean-flyover minimum duration (s)")
    ap.add_argument("--min-drop", type=float, default=15.0, help="clean-flyover minimum pitch drop (Hz)")
    ap.add_argument("--sr", type=int, default=16_000)
    ap.add_argument("--report", default=None)
    a = ap.parse_args()

    rep = accumulate(a.video, a.tile_s, a.overlap_s, a.min_dur, a.min_drop, a.sr)
    print(f"{rep['file']}: {rep['duration_s']/3600:.1f} h, full coverage "
          f"({rep['tiles_processed']}/{rep['tiles']} tiles)")
    print(f"  flyover detections: {rep['flyover_detections']}")
    print(f"  CLEAN flyovers (>={a.min_dur:.0f}s, >={a.min_drop:.0f}Hz): {rep['clean_flyovers']}")
    print(f"  CLEAN + plausible (speed/height/confirmation): {rep['clean_plausible_flyovers']}")
    print(f"  crosses 3-flyover trilateration bar: {rep['crosses_trilateration_bar']}")
    if a.report:
        Path(a.report).write_text(json.dumps(rep, indent=2))
        print(f"  wrote {a.report}")


if __name__ == "__main__":
    main()
