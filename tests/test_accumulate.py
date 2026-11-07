import numpy as np

from contrail.accumulate import (CleanConfig, Exposure, accumulate_array, combine, plausible_params,
                                 same_aircraft, tile_bounds, to_analysis_rate)
from contrail.doppler import Flyover
from contrail.synth import render_entering


def _line(fo: Flyover, h: float, t0: float, t1: float):
    t = np.arange(t0, t1, 0.25)
    return t, h * fo.observed_frequency(t)


def test_same_aircraft_matches_partial_views_and_harmonics():
    fo = Flyover(f0_hz=90, speed_mps=60, altitude_m=500, offset_m=300, t_closest=60)
    other = Flyover(f0_hz=140, speed_mps=90, altitude_m=900, offset_m=600, t_closest=62)
    a = [_line(fo, 1, 30, 70)]              # first tile's view
    b = [_line(fo, 1, 55, 95)]              # second tile's view, same pass
    assert same_aircraft(a, b)
    assert same_aircraft(a, [_line(fo, 2, 40, 80)])     # the 2nd harmonic, split off by the grouper
    assert not same_aircraft(a, [_line(other, 1, 40, 80)])
    assert not same_aircraft(a, [_line(fo, 1, 72, 95)])  # no common time


def test_plausibility_rule():
    assert plausible_params(80, 1200, 2, 0.01)
    assert plausible_params(80, 1200, 1, 0.001)          # single line, tight fit
    assert not plausible_params(80, 1200, 1, 0.01)       # single line, loose fit
    assert not plausible_params(220, 300, 3, 0.001)      # airliner speed at rooftop height
    assert not plausible_params(30, 1200, 3, 0.001)


def test_tiles_cover_everything_and_overlap():
    cfg = CleanConfig(tile_s=120, overlap_s=45)
    b = tile_bounds(600 * 16000, 16000, cfg)
    assert b[0][1] == 0 and b[-1][2] == 600 * 16000
    for (s0, a0, b0), (s1, a1, b1) in zip(b, b[1:]):
        assert a1 < b0                                   # neighbours overlap
        assert abs((a1 - a0) / 16000 - 75) < 1e-6
    assert tile_bounds(100 * 16000, 16000, cfg) == [(0.0, 0, 100 * 16000)]


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
    cfg = CleanConfig(tile_s=120, overlap_s=45)            # tiles at 0, 75, 150: pass 1 straddles 75..120
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
