"""contrail audit <wav>    — list detected flyover lines and a leak score
   contrail scrub <in> <out> [--no-gate] [--no-notch] [--no-decoy] [--decoys N]
"""
from __future__ import annotations

import argparse
import sys

import numpy as np
from scipy.io import wavfile

from .detect import find_tracks, leak_score
from .scrub import ScrubConfig, scrub


def _load(path: str) -> tuple[np.ndarray, int]:
    sr, y = wavfile.read(path)
    if y.dtype.kind == "i":
        y = y.astype(np.float32) / np.iinfo(y.dtype).max
    elif y.dtype.kind == "u":
        y = (y.astype(np.float32) - 128) / 128
    y = y.astype(np.float32)
    if y.ndim == 2:
        y = y.mean(axis=1)
    return y, int(sr)


def _save(path: str, y: np.ndarray, sr: int) -> None:
    wavfile.write(path, sr, (np.clip(y, -1, 1) * 32767).astype(np.int16))


def cmd_audit(args) -> int:
    y, sr = _load(args.input)
    tracks = find_tracks(y, sr)
    for tr in tracks:
        print(tr)
    print(f"\n{len(tracks)} flyover line(s). leak score: {leak_score(tracks):.2f}")
    return 0


def cmd_scrub(args) -> int:
    y, sr = _load(args.input)
    cfg = ScrubConfig(gate=not args.no_gate, notch=not args.no_notch, decoy=not args.no_decoy,
                      decoy_count=args.decoys, seed=args.seed)
    out, rep = scrub(y, sr, cfg)
    _save(args.output, out, sr)
    print(f"before: {len(rep.tracks_before)} line(s), leak {leak_score(rep.tracks_before):.2f}")
    print(f"after:  {len(rep.tracks_after)} line(s) detected (real + {len(rep.decoys)} decoys), "
          f"speech in {rep.vad_speech_fraction:.0%} of frames")
    print(f"wrote {args.output}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="contrail", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("audit"); a.add_argument("input"); a.set_defaults(fn=cmd_audit)
    s = sub.add_parser("scrub"); s.add_argument("input"); s.add_argument("output")
    s.add_argument("--no-gate", action="store_true"); s.add_argument("--no-notch", action="store_true")
    s.add_argument("--no-decoy", action="store_true"); s.add_argument("--decoys", type=int, default=8)
    s.add_argument("--seed", type=int, default=None); s.set_defaults(fn=cmd_scrub)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
