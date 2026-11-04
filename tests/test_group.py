import numpy as np

from contrail.detect import Track, find_tracks, fit_doppler, group_flyovers, leak_score
from contrail.doppler import Flyover
from contrail.synth import SynthConfig, render

PLANE = Flyover(f0_hz=150, speed_mps=100, altitude_m=1500, offset_m=500, t_closest=45)


def line(fo, harmonic, start=0.0, stop=90.0, seed=0):
    t = np.arange(start, stop, 0.25)
    noise = np.random.default_rng(seed).normal(0, 0.1, len(t))
    f = harmonic * fo.observed_frequency(t) + noise
    return Track(t, f, np.zeros_like(t), fit_doppler(t, f))


def test_harmonics_of_one_plane_form_one_group():
    groups = group_flyovers([line(PLANE, h, seed=h) for h in (1, 2, 3)])
    assert len(groups) == 1
    assert sorted(groups[0].harmonics) == [1, 2, 3]
    assert abs(groups[0].fit.v - 100) < 10


def test_missing_fundamental_gives_relative_harmonic_numbers():
    groups = group_flyovers([line(PLANE, h, seed=h) for h in (2, 3)])
    assert len(groups) == 1
    assert sorted(groups[0].harmonics) == [2, 3]


def test_short_fragment_joins_its_plane():
    groups = group_flyovers([line(PLANE, 1), line(PLANE, 2, start=35, stop=55, seed=1)])
    assert len(groups) == 1


def test_integer_ratio_alone_does_not_merge_different_planes():
    other = Flyover(f0_hz=300, speed_mps=80, altitude_m=800, offset_m=300, t_closest=20)
    assert len(group_flyovers([line(PLANE, 1), line(other, 1, seed=1)])) == 2


def test_harmonics_do_not_inflate_leak_score():
    one_plane = [line(PLANE, h, seed=h) for h in (1, 2, 3)]
    other = Flyover(f0_hz=170, speed_mps=80, altitude_m=800, offset_m=300, t_closest=20)
    third = Flyover(f0_hz=115, speed_mps=120, altitude_m=2200, offset_m=900, t_closest=70)
    three_planes = [line(PLANE, 1), line(other, 1, seed=1), line(third, 1, seed=2)]
    assert leak_score(one_plane) <= 1 / 3 + 1e-9
    assert leak_score(three_planes) > 0.9


def test_no_tracks_scores_zero():
    assert group_flyovers([]) == []
    assert leak_score([]) == 0.0


def test_rendered_single_flyover_counts_as_one_plane():
    cfg = SynthConfig(seed=22, flyovers=[PLANE])
    y, _ = render(cfg)
    tracks = find_tracks(y, cfg.sr)
    assert len(tracks) >= 2
    assert len(group_flyovers(tracks)) == 1
