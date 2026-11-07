import numpy as np

from contrail.accumulate import (CleanConfig, Exposure, accumulate_array, combine, confirmed_params, is_clean,
                                 physically_possible, plausible_params, same_aircraft, tile_bounds,
                                 to_analysis_rate)
from contrail.detect import FlyoverGroup, Track, fit_doppler
from contrail.doppler import Flyover
from contrail.synth import render_entering


def _line(fo: Flyover, h: float, t0: float, t1: float):
    t = np.arange(t0, t1, 0.25)
    return t, h * fo.observed_frequency(t)


def _group(fo: Flyover, t0: float, t1: float, seed: int = 0, power_db: float = 15.0) -> FlyoverGroup:
    """One single-line flyover group as the detector would hand it over."""
    t, f = _line(fo, 1.0, t0, t1)
    f = f + np.random.default_rng(seed).normal(0, 0.05, len(t))
    tr = Track(t, f, np.full_like(t, power_db), fit_doppler(t, f))
    return FlyoverGroup([tr], [1], tr.fit)


def test_same_aircraft_matches_partial_views_and_harmonics():
    fo = Flyover(f0_hz=90, speed_mps=60, altitude_m=500, offset_m=300, t_closest=60)
    other = Flyover(f0_hz=140, speed_mps=90, altitude_m=900, offset_m=600, t_closest=62)
    a = [_line(fo, 1, 30, 70)]              # first tile's view
    b = [_line(fo, 1, 55, 95)]              # second tile's view, same pass
    assert same_aircraft(a, b)
    assert same_aircraft(a, [_line(fo, 2, 40, 80)])     # the 2nd harmonic, split off by the grouper
    assert not same_aircraft(a, [_line(other, 1, 40, 80)])
    assert not same_aircraft(a, [_line(fo, 1, 72, 95)])  # no common time


def test_possible_and_confirmed_tiers():
    assert physically_possible(45, 800)                  # light aircraft on approach
    assert not physically_possible(220, 300)             # airliner speed at rooftop height
    assert not physically_possible(30, 1200)
    assert confirmed_params(2, 0.01)                     # harmonics confirm
    assert confirmed_params(1, 0.001)                    # or a very tight fit
    assert not confirmed_params(1, 0.01)                 # single loose line: possible, not confirmed
    assert plausible_params(80, 1200, 2, 0.01) and not plausible_params(220, 300, 3, 0.001)


def test_clean_bar_is_the_attackers():
    """What counts is what the attack pipeline accepts: >= 12 s, >= 3 % drop,
    fit under 1.2 Hz, and a fit an aircraft could have produced. A 20 s single
    line around closest approach is in; 10 s is not; a far pass with almost no
    drop is not; a fit at airliner speed next to the mic is not."""
    near = Flyover(f0_hz=150, speed_mps=80, altitude_m=500, offset_m=300, t_closest=30)
    assert is_clean(_group(near, 20, 40))
    assert not is_clean(_group(near, 25, 35))                        # 10 s
    far = Flyover(f0_hz=150, speed_mps=80, altitude_m=1500, offset_m=6000, t_closest=200)
    assert not is_clean(_group(far, 20, 40))                         # < 3 % drop this far from CPA
    silly = Flyover(f0_hz=150, speed_mps=250, altitude_m=200, offset_m=100, t_closest=30)
    assert not is_clean(_group(silly, 20, 40))                       # physically impossible fit
    assert not is_clean(_group(near, 20, 40, power_db=3.0))          # a chain of noise peaks at floor level


def test_lone_high_harmonic_is_the_same_aircraft():
    fo = Flyover(f0_hz=85, speed_mps=60, altitude_m=500, offset_m=300, t_closest=60)
    assert same_aircraft([_line(fo, 1, 30, 90)], [_line(fo, 5, 50, 70)])     # 5th harmonic, 425 Hz
    assert same_aircraft([_line(fo, 2, 30, 90)], [_line(fo, 6, 50, 70)])     # h2 vs h6: ratio 3


def test_tiles_cover_everything_and_overlap():
    cfg = CleanConfig(tile_s=120, overlap_s=45)
    b = tile_bounds(600 * 16000, 16000, cfg)
    assert b[0][1] == 0 and b[-1][2] == 600 * 16000
    for (s0, a0, b0), (s1, a1, b1) in zip(b, b[1:]):
        assert a1 < b0                                   # neighbours overlap
        assert abs((a1 - a0) / 16000 - 75) < 1e-6
    assert tile_bounds(100 * 16000, 16000, cfg) == [(0.0, 0, 100 * 16000)]
    d = CleanConfig()
    assert (d.min_dur_s, d.min_drop_frac, d.max_fit_rms_hz, d.min_power_db, d.tile_s, d.overlap_s, d.analysis_sr) == \
        (12.0, 0.03, 1.2, 7.0, 300.0, 90.0, 8000)


def test_resample_to_analysis_rate():
    y48 = np.zeros((48000 * 3, 2), dtype=np.int16)
    y, sr = to_analysis_rate(y48, 48000)
    assert sr == 16000 and y.ndim == 1 and abs(len(y) - 16000 * 3) <= 2
    y8, sr8 = to_analysis_rate(np.zeros(8000), 8000)
    assert sr8 == 8000 and len(y8) == 8000


def test_one_pass_over_a_tile_edge_is_one_flyover_and_two_planes_are_two():
    """A pass straddling a tile boundary used to count as several aircraft
    (partial views fit different parameters). Count by measured lines."""
    second = Flyover(f0_hz=112.0, speed_mps=70.0, altitude_m=600.0, offset_m=400.0, t_closest=190.0)
    y, fo = render_entering(sr=16000, duration_s=240.0, onset_s=60.0, offset_s=125.0,
                            aircraft_level=0.1, extra=[(second, 165.0, 215.0)])
    cfg = CleanConfig(tile_s=120, overlap_s=45, analysis_sr=16000)   # tiles at 0, 75, 150: pass 1 straddles 75..120
    exp, windows = accumulate_array(y, 16000, cfg)
    assert len(windows) == 3
    assert exp.detections >= 3, windows                     # the first pass is seen from two tiles
    assert exp.clean == 2, [(s.tc_s, s.group) for s in exp.sightings]
    tcs = sorted(s.tc_s for s in exp.sightings)
    assert abs(tcs[0] - fo.t_closest) < 8 and abs(tcs[1] - second.t_closest) < 8
    s = exp.summary("x.wav", 240.0)
    assert s["clean_flyovers"] == 2 and not s["crosses_trilateration_bar"]
    arch = combine([s, s])
    assert arch["clean_flyovers"] == 4 and arch["crosses_trilateration_bar"]
