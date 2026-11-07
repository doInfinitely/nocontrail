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

    The bar is the attacker's own acceptance bar, taken from the blind
    localisation pipeline that fixed a real recording site to 0.7 km: a tone
    enters its fit if it is at least 12 s long, falls by at least 3 % of its
    frequency, and fits the Doppler model to under 1.2 Hz rms. Our detector's
    output gates (12 s, 3 Hz, 1.0 Hz rms) sit at that bar already, so "clean"
    is close to "detected". The earlier 40 s / 15 Hz bar, inherited from
    leak_score's saturation scale, discarded most of what the attack consumes:
    on a real 40-minute recording it kept 2 of 33 detections while the window
    score read 1.00. A physically impossible fit never counts.

    Tiles are 300 s with 90 s overlap at 8 kHz. 120 s tiles truncated 44-59 s
    passes into views that fell under the bar, and 8 kHz is plenty for a
    50-800 Hz band: 40 min of 48 kHz audio took 968 s against 1453 s at
    120 s / 16 kHz, and found 7 passes against 2."""
    min_dur_s: float = 12.0
    min_drop_frac: float = 0.03        # f_start / f_end >= 1.03 on the line's own frequency
    max_fit_rms_hz: float = 1.2
    # Median whitened level of the counted line. Not part of the attacker's bar,
    # which it meets on paper: a chain of just-over-prominence noise peaks can
    # pass duration, drop and fit (measured: a 16 s "line" at 146->120 Hz, fit
    # rms 1.0 Hz, where the audio is floor at 1.8 dB median). A line a fitter
    # can trace stands out of the floor; real passes on a 40-minute recording
    # ran 7-17 dB, their faint high harmonics 4.5-7 dB.
    min_power_db: float = 7.0
    tile_s: float = 300.0
    overlap_s: float = 90.0
    analysis_sr: int = 8000


def physically_possible(v_mps: float, miss_m: float) -> bool:
    """Could an aircraft have produced this fit? Speed floor matches the
    detector's v_range (a light aircraft on approach flies 35-45 m/s); an
    airliner speed within a few hundred metres is a fitter matching something
    else."""
    return 40 <= v_mps <= 300 and not (v_mps > 180 and miss_m < 600)


def confirmed_params(n_harmonics: int, rms_frac: float) -> bool:
    """Higher-confidence tier: two or more harmonics, or a very tight fit.
    Shown as a subset, never used as a gate: the attacker needs neither (its
    fit bar is 1.2 Hz, ~0.8 % at 150 Hz, with no harmonic requirement), and
    real single-line passes sit at 0.3-1.5 %."""
    return n_harmonics >= 2 or rms_frac < 0.002


def plausible_params(v_mps: float, miss_m: float, n_harmonics: int, rms_frac: float) -> bool:
    """Physically possible AND confirmed (the report's appendix tag)."""
    return physically_possible(v_mps, miss_m) and confirmed_params(n_harmonics, rms_frac)


def is_possible(g: FlyoverGroup) -> bool:
    return physically_possible(g.fit.v, g.fit.d)


def is_confirmed(g: FlyoverGroup) -> bool:
    return confirmed_params(len(g.harmonics), g.fit.rms / g.fit.f0)


def is_plausible(g: FlyoverGroup) -> bool:
    return is_possible(g) and is_confirmed(g)


def is_clean(g: FlyoverGroup, cfg: CleanConfig | None = None) -> bool:
    """Usable by the attacker: the acceptance bar above, on the group's most
    complete line, plus a physically possible fit."""
    cfg = cfg or CleanConfig()
    ratio = max(tr.f_start / tr.f_end for tr in g.tracks)
    power = max(float(np.median(tr.power)) for tr in g.tracks)
    return (g.duration >= cfg.min_dur_s
            and ratio >= 1.0 + cfg.min_drop_frac
            and g.fit.rms <= cfg.max_fit_rms_hz
            and power >= cfg.min_power_db
            and is_possible(g))


def to_analysis_rate(y: np.ndarray, sr: int, target: int = ANALYSIS_SR) -> tuple[np.ndarray, int]:
    """Mono float at <= target. Never upsamples (8 kHz is fine as it is)."""
    y = np.asarray(y, dtype=np.float64)
    if y.ndim == 2:
        y = y.mean(axis=1)
    if sr <= target:
        return y, sr
    r = Fraction(target, sr)
    return resample_poly(y, r.numerator, r.denominator), target


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


_RATIOS = tuple(float(h) for h in range(1, 7)) + tuple(1.0 / h for h in range(2, 7))


def same_aircraft(a: Lines, b: Lines, tol_hz: float = 3.0, min_common_s: float = 3.0) -> bool:
    """Do two sets of measured lines belong to one aircraft? Yes if any pair
    agrees over at least `min_common_s` of common time at a harmonic ratio up
    to 6 (a propeller's ladder reaches that far: 85/160/.../590 Hz on a real
    recording, and a lone 5th harmonic must not count as a second plane).
    Measured frequencies are compared, not fitted parameters: two partial views
    of one pass fit different (f0, v, d), but over the audio both tiles share
    their lines coincide to well under a hertz."""
    for ta, fa in a:
        for tb, fb in b:
            lo, hi = max(ta[0], tb[0]), min(ta[-1], tb[-1])
            if hi - lo < min_common_s:
                continue
            tt = np.linspace(lo, hi, 25)
            fa_i, fb_i = np.interp(tt, ta, fa), np.interp(tt, tb, fb)
            for r in _RATIOS:
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
    def confirmed(self) -> bool:
        return is_confirmed(self.group)

    plausible = confirmed   # older name


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
    def clean_confirmed(self) -> int:
        return sum(s.confirmed for s in self.sightings)

    clean_plausible = clean_confirmed   # older name

    @property
    def crosses_bar(self) -> bool:
        """Three clean flyovers is the detector's own trilateration bar."""
        return self.clean >= 3

    @property
    def crosses_bar_confirmed(self) -> bool:
        return self.clean_confirmed >= 3

    def summary(self, file: str = "", duration_s: float = 0.0) -> dict:
        return {
            "file": file,
            "duration_s": round(duration_s, 1),
            "coverage": "full",
            "windows": self.windows,
            "flyover_detections": self.detections,
            "clean_flyovers": self.clean,
            "clean_confirmed_flyovers": self.clean_confirmed,
            "clean_plausible_flyovers": self.clean_confirmed,   # older name
            "min_dur_s": self.cfg.min_dur_s,
            "min_drop_frac": self.cfg.min_drop_frac,
            "max_fit_rms_hz": self.cfg.max_fit_rms_hz,
            "min_power_db": self.cfg.min_power_db,
            # per-window; does not add up across windows and is not an archive verdict
            "peak_window_score": round(self.peak_window_score, 3),
            "crosses_trilateration_bar": self.crosses_bar,
            "crosses_trilateration_bar_confirmed": self.crosses_bar_confirmed,
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
        seg, sr_a = to_analysis_rate(as_float(y[a:b]), sr, cfg.analysis_sr)
        tracks = find_tracks(seg, sr_a, dc)
        windows.append(exp.add_window(group_flyovers(tracks), tracks, start_s))
    return exp, windows


def combine(summaries: list[dict]) -> dict:
    """Archive totals over per-file summaries."""
    clean = sum(s["clean_flyovers"] for s in summaries)
    conf = sum(s.get("clean_confirmed_flyovers", s.get("clean_plausible_flyovers", 0)) for s in summaries)
    hours = sum(s["duration_s"] for s in summaries) / 3600
    return {
        "files": len(summaries),
        "hours": round(hours, 2),
        "flyover_detections": sum(s["flyover_detections"] for s in summaries),
        "clean_flyovers": clean,
        "clean_confirmed_flyovers": conf,
        "clean_per_hour": round(clean / hours, 2) if hours else 0.0,
        "peak_window_score": max((s["peak_window_score"] for s in summaries), default=0.0),
        "crosses_trilateration_bar": clean >= 3,
        "crosses_trilateration_bar_confirmed": conf >= 3,
    }
