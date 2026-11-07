"""contrail audit <wav> [<wav> ...]  — count clean flyovers across whole recordings
   contrail scrub <in> <out> [--no-gate] [--no-notch] [--decoy [--decoys N]]
   contrail report <folder-of-json> --name <who> [--by <you>] -o audit.html

`audit` covers every second of each file (in overlapping tiles), lists the
clean flyovers with a timestamp to listen at, and totals them across all the
files given: that count is what tracks exposure. The per-window score is
printed for what it is and is not a verdict on the archive.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from scipy.io import wavfile

from .accumulate import CleanConfig, as_float, accumulate_array, combine
from .detect import leak_score
from .report import main as report_main
from .scrub import ScrubConfig, scrub


def _load(path: str) -> tuple[np.ndarray, int]:
    sr, y = wavfile.read(path)
    y = as_float(y)
    if y.ndim == 2:
        y = y.mean(axis=1)
    return y, int(sr)


def _save(path: str, y: np.ndarray, sr: int) -> None:
    wavfile.write(path, sr, (np.clip(y, -1, 1) * 32767).astype(np.int16))


def _hms(s: float) -> str:
    s = int(s)
    return f"{s // 3600}:{(s % 3600) // 60:02d}:{s % 60:02d}"


def cmd_audit(args) -> int:
    cfg = CleanConfig(min_dur_s=args.min_dur, min_drop_hz=args.min_drop,
                      tile_s=args.tile_s, overlap_s=args.overlap_s)
    summaries = []
    for path in args.inputs:
        sr, y = wavfile.read(path, mmap=True)     # tiles are converted one at a time
        duration = y.shape[0] / sr
        name = Path(path).name
        print(f"{name}: {duration / 60:.1f} min at {sr} Hz, full coverage in {cfg.tile_s:.0f} s tiles")

        def progress(i, n):
            print(f"  tile {i + 1}/{n}", end="\r", file=sys.stderr, flush=True)

        exp, windows = accumulate_array(y, int(sr), cfg, progress=progress)
        print(" " * 24, end="\r", file=sys.stderr)
        for s in sorted(exp.sightings, key=lambda s: s.tc_s):
            tag = "plausible" if s.plausible else "unconfirmed"
            print(f"  {_hms(s.tc_s)}  {s.group}  [{tag}]")
        s = exp.summary(name, duration)
        summaries.append(s)
        print(f"  flyover detections: {s['flyover_detections']}   "
              f"CLEAN (>= {cfg.min_dur_s:.0f} s, >= {cfg.min_drop_hz:.0f} Hz drop): {s['clean_flyovers']}   "
              f"plausible: {s['clean_plausible_flyovers']}")
        print(f"  peak {cfg.tile_s:.0f}-s window score: {s['peak_window_score']:.2f}  "
              f"(one window's score; it does not add up and is not a verdict on the file)")
        print()

    arch = combine(summaries)
    bar = "YES" if arch["crosses_trilateration_bar"] else "no"
    bar_p = "YES" if arch["crosses_trilateration_bar_plausible"] else "no"
    print(f"ARCHIVE: {arch['files']} file(s), {arch['hours']:.2f} h, "
          f"{arch['clean_flyovers']} clean flyover(s) ({arch['clean_plausible_flyovers']} plausible), "
          f"about {arch['clean_per_hour']:.1f} per hour")
    print(f"  crosses the 3-flyover trilateration bar: {bar} (plausible only: {bar_p})")
    if args.json:
        Path(args.json).write_text(json.dumps({"files": summaries, "archive": arch}, indent=2))
        print(f"  wrote {args.json}")
    return 0


def cmd_scrub(args) -> int:
    y, sr = _load(args.input)
    cfg = ScrubConfig(gate=not args.no_gate, notch=not args.no_notch, decoy=args.decoy,
                      decoy_count=args.decoys, seed=args.seed)
    out, rep = scrub(y, sr, cfg)
    _save(args.output, out, sr)
    print(f"before: {len(rep.tracks_before)} line(s), leak {leak_score(rep.tracks_before):.2f}")
    decoy_note = f" + {len(rep.decoys)} decoys" if rep.decoys else ""
    print(f"after:  {len(rep.tracks_after)} line(s) detected{decoy_note}, "
          f"speech in {rep.vad_speech_fraction:.0%} of frames")
    print(f"wrote {args.output}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="contrail", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("audit", help="count clean flyovers across whole recordings")
    a.add_argument("inputs", nargs="+", metavar="wav")
    a.add_argument("--tile-s", type=float, default=120.0)
    a.add_argument("--overlap-s", type=float, default=45.0,
                   help="large on purpose: a flyover cut in two fits as two aircraft")
    a.add_argument("--min-dur", type=float, default=40.0, help="clean-flyover minimum duration (s)")
    a.add_argument("--min-drop", type=float, default=15.0, help="clean-flyover minimum pitch drop (Hz)")
    a.add_argument("--json", default=None, help="write per-file and archive counts here")
    a.set_defaults(fn=cmd_audit)
    s = sub.add_parser("scrub"); s.add_argument("input"); s.add_argument("output")
    s.add_argument("--no-gate", action="store_true"); s.add_argument("--no-notch", action="store_true")
    s.add_argument("--decoy", action="store_true",
                   help="optional defense-in-depth: mix in decoy flyovers (off by default; "
                        "does not defeat a flight-data attacker)")
    s.add_argument("--decoys", type=int, default=8)
    s.add_argument("--seed", type=int, default=None); s.set_defaults(fn=cmd_scrub)
    r = sub.add_parser("report", add_help=False); r.add_argument("rest", nargs=argparse.REMAINDER)
    r.set_defaults(fn=lambda a: report_main(a.rest))
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
