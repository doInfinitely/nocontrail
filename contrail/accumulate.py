"""Count clean flyovers across a whole recording, and across an archive.

The per-window leak score (detect.leak_score) describes one stretch of audio
and saturates there: it never adds up across time. Trilateration does. Three
or more clean flyovers at different geometries, anywhere in an archive,
constrain a position, so the number that tracks exposure is the COUNT of
clean flyovers accumulated over everything a person has published, not the
score of the worst 90 seconds. Measured on a real 21-hour recording: 52 clean
flyovers in total, yet no single hour scored above 0.41 ("moderate").

This module is the one implementation of that count. It tiles a recording,
runs the detector per tile, recognises the same aircraft seen from two tiles
(or split in two by the grouper) by its measured lines, and keeps counts:
detections, clean flyovers (long and deep enough to carry usable geometry),
and clean flyovers that also pass the physical plausibility rule the report
uses. Everything is relative to the microphone (speed, miss distance, time);
nothing here is a position on Earth.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction

import numpy as np
from scipy.signal import resample_poly

from .detect import DetectConfig, FlyoverGroup, Track, find_tracks, group_flyovers, leak_score

ANALYSIS_SR = 16_000   # the detector's band ends at 800 Hz; anything above this is wasted work


@dataclass
class CleanConfig:
    """What counts as a flyover an attacker can use, and how a recording is tiled.

    Tiles overlap by 45 s so that any flyover up to ~70 s long has a view of at
    least 45 s in some tile; shorter overlaps cut long flyovers into two partial
    views whose fits disagree (measured: one 75 s pass came out as three
    aircraft under 90 s tiles with 10 s overlap). Costs 1.6x the audio length
    in detector time."""
    min_dur_s: float = 40.0        # the scale leak_score saturates at: a full approach->recede
    min_drop_hz: float = 15.0      # total pitch drop; shallow lines carry little geometry
    tile_s: float = 120.0
    overlap_s: float = 45.0


def plausible_params(v_mps: float, miss_m: float, n_harmonics: int, rms_frac: float) -> bool:
    """A real flyover: plausible speed and either harmonic confirmation or a
    tight fit. The one rule the report, the CLI and the app share. The speed
    floor matches the detector's v_range: a light aircraft on approach flies
    35-45 m/s, and those passes are real (a 45 m/s Cessna pass with three
    harmonics was being marked implausible at the old 50 m/s floor)."""
    speed_ok = 40 <= v_mps <= 300
    not_absurd = not (v_mps > 180 and miss_m < 600)   # airliner speed at rooftop height
    confirmed = n_harmonics >= 2 or rms_frac < 0.002
    return speed_ok and not_absurd and confirmed


def is_plausible(g: FlyoverGroup) -> bool:
    return plausible_params(g.fit.v, g.fit.d, len(g.harmonics), g.fit.rms / g.fit.f0)


def is_clean(g: FlyoverGroup, cfg: CleanConfig | None = None) -> bool:
    cfg = cfg or CleanConfig()
    return g.duration >= cfg.min_dur_s and g.drop_hz >= cfg.min_drop_hz


def to_analysis_rate(y: np.ndarray, sr: int) -> tuple[np.ndarray, int]:
    """Mono float at <= ANALYSIS_SR. Never upsamples (8 kHz is fine as it is)."""
    y = np.asarray(y, dtype=np.float64)
    if y.ndim == 2:
        y = y.mean(axis=1)
    if sr <= ANALYSIS_SR:
        return y, sr
    r = Fraction(ANALYSIS_SR, sr)
    return resample_poly(y, r.numerator, r.denominator), ANALYSIS_SR


def as_float(y: np.ndarray) -> np.ndarray:
    """PCM of any width, or float, to float in [-1, 1]."""
    y = np.asarray(y)
    if y.dtype.kind == "i":
        return y.astype(np.float32) / np.iinfo(y.dtype).max
    if y.dtype.kind == "u":
        return (y.astype(np.float32) - 128) / 128
    return y.astype(np.float32)


def tile_bounds(n_samples: int, sr: int, cfg: CleanConfig | None = None) -> list[tuple[float, int, int]]:
    """(start_s, first_sample, last_sample) for every tile; full coverage."""
    cfg = cfg or CleanConfig()
    total = n_samples / sr
    if total <= cfg.tile_s * 1.2:
        return [(0.0, 0, n_samples)]
    step = max(cfg.tile_s - cfg.overlap_s, 1.0)
    starts = np.arange(0.0, max(total - cfg.overlap_s, step), step)
    out = []
    for s in starts:
        a = int(round(s * sr))
        b = min(n_samples, a + int(round(cfg.tile_s * sr)))
        if b - a >= sr:   # at least one second
            out.append((float(s), a, b))
    return out


Lines = list[tuple[np.ndarray, np.ndarray]]   # per tracked line: (global time s, Hz)


def same_aircraft(a: Lines, b: Lines, tol_hz: float = 3.0, min_common_s: float = 3.0) -> bool:
    """Do two sets of measured lines belong to one aircraft? Yes if any pair
    agrees over at least `min_common_s` of common time at a small harmonic
    ratio. Measured frequencies are compared, not fitted parameters: two
    partial views of one pass fit different (f0, v, d), but over the audio
    both tiles share their lines coincide to well under a hertz."""
    for ta, fa in a:
        for tb, fb in b:
            lo, hi = max(ta[0], tb[0]), min(ta[-1], tb[-1])
            if hi - lo < min_common_s:
                continue
            tt = np.linspace(lo, hi, 25)
            fa_i, fb_i = np.interp(tt, ta, fa), np.interp(tt, tb, fb)
            for r in (1.0, 2.0, 3.0, 0.5, 1.0 / 3.0):
                if float(np.median(np.abs(fa_i - r * fb_i))) < tol_hz * max(r, 1.0):
                    return True
    return False


@dataclass
class Sighting:
    """One counted flyover: its most complete view, and every line it was
    seen with (so a later partial view still matches it)."""
    group: FlyoverGroup
    start_s: float
    lines: Lines

    @property
    def tc_s(self) -> float:
        return self.start_s + self.group.fit.tc

    @property
    def plausible(self) -> bool:
        return is_plausible(self.group)


@dataclass
class Exposure:
    """Running count over the tiles of ONE recording. For an archive, make one
    per file and `combine` the summaries (a flyover cannot be in two files)."""
    cfg: CleanConfig = field(default_factory=CleanConfig)
    windows: int = 0
    detections: int = 0
    peak_window_score: float = 0.0
    sightings: list[Sighting] = field(default_factory=list)

    def add_window(self, groups: list[FlyoverGroup], tracks: list[Track], start_s: float) -> dict:
        """Fold one tile in. Returns the tile's own summary."""
        self.windows += 1
        score = leak_score(tracks)
        self.peak_window_score = max(self.peak_window_score, score)
        new = 0
        for g in groups:
            self.detections += 1
            if not is_clean(g, self.cfg):
                continue
            lines = [(tr.times + start_s, tr.freqs) for tr in g.tracks]
            dup = next((s for s in self.sightings if same_aircraft(lines, s.lines)), None)
            if dup is None:
                self.sightings.append(Sighting(g, start_s, lines))
                new += 1
            else:
                dup.lines.extend(lines)
                # keep the most complete view: more lines, then longer
                if (len(g.tracks), g.duration) > (len(dup.group.tracks), dup.group.duration):
                    dup.group, dup.start_s = g, start_s
        return {"start_s": start_s, "score": score, "flyovers": len(groups), "new_clean": new}

    @property
    def clean(self) -> int:
        return len(self.sightings)

    @property
    def clean_plausible(self) -> int:
        return sum(s.plausible for s in self.sightings)

    @property
    def crosses_bar(self) -> bool:
        """Three clean flyovers is the detector's own trilateration bar."""
        return self.clean >= 3

    @property
    def crosses_bar_plausible(self) -> bool:
        return self.clean_plausible >= 3

    def summary(self, file: str = "", duration_s: float = 0.0) -> dict:
        return {
            "file": file,
            "duration_s": round(duration_s, 1),
            "coverage": "full",
            "windows": self.windows,
            "flyover_detections": self.detections,
            "clean_flyovers": self.clean,
            "clean_plausible_flyovers": self.clean_plausible,
            "min_dur_s": self.cfg.min_dur_s,
            "min_drop_hz": self.cfg.min_drop_hz,
            # per-window; does not add up across windows and is not an archive verdict
            "peak_window_score": round(self.peak_window_score, 3),
            "crosses_trilateration_bar": self.crosses_bar,
            "crosses_trilateration_bar_plausible": self.crosses_bar_plausible,
        }


def accumulate_array(y: np.ndarray, sr: int, cfg: CleanConfig | None = None,
                     dc: DetectConfig | None = None, progress=None) -> tuple[Exposure, list[dict]]:
    """Run the detector over every tile of an in-memory recording.

    `y` may be int or float, mono or stereo, at any rate; each tile is brought
    to the analysis rate on its own, so a long 48 kHz file never has to exist
    at full rate as a float array. `progress(i, n)` is called before each tile.
    """
    cfg = cfg or CleanConfig()
    exp = Exposure(cfg)
    windows = []
    bounds = tile_bounds(y.shape[0], sr, cfg)
    for i, (start_s, a, b) in enumerate(bounds):
        if progress:
            progress(i, len(bounds))
        seg, sr_a = to_analysis_rate(as_float(y[a:b]), sr)
        tracks = find_tracks(seg, sr_a, dc)
        windows.append(exp.add_window(group_flyovers(tracks), tracks, start_s))
    return exp, windows


def combine(summaries: list[dict]) -> dict:
    """Archive totals over per-file summaries."""
    clean = sum(s["clean_flyovers"] for s in summaries)
    plaus = sum(s["clean_plausible_flyovers"] for s in summaries)
    hours = sum(s["duration_s"] for s in summaries) / 3600
    return {
        "files": len(summaries),
        "hours": round(hours, 2),
        "flyover_detections": sum(s["flyover_detections"] for s in summaries),
        "clean_flyovers": clean,
        "clean_plausible_flyovers": plaus,
        "clean_per_hour": round(clean / hours, 2) if hours else 0.0,
        "peak_window_score": max((s["peak_window_score"] for s in summaries), default=0.0),
        "crosses_trilateration_bar": clean >= 3,
        "crosses_trilateration_bar_plausible": plaus >= 3,
    }
