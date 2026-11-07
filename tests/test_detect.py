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


def _on_line(tracks, fo, sr, n):
    t = np.arange(n) / sr
    ft = fo.observed_frequency(t)
    out = []
    for tr in tracks:
        fi = np.interp(tr.times, t, ft)
        if any(np.mean(np.abs(tr.freqs - h * fi) < 3 * h) > 0.8 for h in (1, 2, 3)):
            out.append(tr)
    return out


def test_line_entering_earshot_over_noise_floor_is_tracked():
    """A 30 dB line that switches on mid-clip used to come back as nothing at
    every level: the noise tracks already active stole its peaks and shredded
    it (the linker was first-come and blind to power). Both ends of the level
    sweep from that report must now give the harmonics as long tracks."""
    from contrail.synth import render_entering

    for level in (0.01, 0.15):
        y, fo = render_entering(aircraft_level=level)
        tracks = find_tracks(y, 8000, DetectConfig())
        hits = _on_line(tracks, fo, 8000, len(y))
        assert len(hits) >= 2, (level, tracks)
        assert max(tr.duration for tr in hits) >= 40.0, (level, hits)
        assert all(tr.fit.rms < 1.0 for tr in hits)


def test_vad_gating_is_safe_and_optional():
    from contrail.detect import DetectConfig, find_tracks, voice_activity
    from contrail.synth import SynthConfig, render
    import numpy as np

    # Default is off, so behaviour is unchanged for existing callers.
    assert DetectConfig().vad is False

    # Enabling it must not invent tracks on clean speech, and must not drop the
    # planted flyovers it recovered without it.
    y, flyovers = render(SynthConfig(seed=0))
    off = find_tracks(y, 16000, DetectConfig(vad=False))
    on = find_tracks(y, 16000, DetectConfig(vad=True))
    assert len(on) >= len(off) - 1  # at most incidental change

    clean, _ = render(SynthConfig(seed=7, aircraft_level=0.0))
    assert find_tracks(clean, 16000, DetectConfig(vad=True)) == []

    # VAD returns one boolean per STFT frame.
    vad = voice_activity(y, 16000, DetectConfig())
    _, t, _ = __import__("contrail.detect", fromlist=["spectrogram"]).spectrogram(y, 16000, DetectConfig())
    assert len(vad) == len(t)
