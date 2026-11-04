import numpy as np

from contrail.detect import DetectConfig, find_tracks, fit_doppler
from contrail.doppler import Flyover
from contrail.synth import SynthConfig, render


def test_doppler_model_sanity():
    fo = Flyover(f0_hz=200, speed_mps=100, altitude_m=1000, offset_m=0, t_closest=30)
    t = np.linspace(0, 60, 601)
    f = fo.observed_frequency(t)
    assert np.all(np.diff(f) <= 1e-9)          # monotone falling
    assert f[0] > 200 > f[-1]                   # approach above f0, recede below


def test_fit_recovers_planted_parameters():
    fo = Flyover(f0_hz=180, speed_mps=95, altitude_m=1500, offset_m=600, t_closest=40)
    t = np.arange(0, 90, 0.25)
    f = fo.observed_frequency(t) + np.random.default_rng(0).normal(0, 0.2, len(t))
    fit = fit_doppler(t, f)
    assert fit.rms < 0.4
    assert abs(fit.f0 - 180) < 3
    assert abs(fit.v - 95) < 10
    assert abs(fit.d - np.hypot(1500, 600)) < 300


def test_no_false_positives_on_clean_speech():
    cfg = SynthConfig(seed=7, aircraft_level=0.0, flyovers=[])
    cfg.flyovers = []
    y, _ = render(cfg)
    # render() substitutes default flyovers when the list is empty, so zero their level instead
    assert cfg.aircraft_level == 0.0
    tracks = find_tracks(y, cfg.sr, DetectConfig())
    assert tracks == []


def test_recovers_most_planted_flyovers():
    hits = 0
    total = 0
    for seed in range(3):
        cfg = SynthConfig(seed=seed)
        y, flyovers = render(cfg)
        tracks = find_tracks(y, cfg.sr)
        t = np.arange(len(y)) / cfg.sr
        for fo in flyovers:
            total += 1
            ft = fo.observed_frequency(t)
            for tr in tracks:
                fi = np.interp(tr.times, t, ft)
                if any(np.mean(np.abs(tr.freqs - h * fi) < 3 * h) > 0.6 for h in (1, 2, 3)):
                    hits += 1
                    break
    assert hits / total >= 0.8
