import numpy as np

from contrail.scrub import ScrubConfig, scrub
from contrail.synth import SynthConfig, render


def _on_line(tracks, flyovers, t):
    hits = 0
    for fo in flyovers:
        ft = fo.observed_frequency(t)
        for tr in tracks:
            fi = np.interp(tr.times, t, ft)
            if max(np.mean(np.abs(tr.freqs - h * fi) < 3 * h) for h in (1, 2, 3)) > 0.6:
                hits += 1
                break
    return hits


def test_notch_removes_detected_lines():
    cfg = SynthConfig(seed=0)
    y, flyovers = render(cfg)
    t = np.arange(len(y)) / cfg.sr
    out, rep = scrub(y, cfg.sr, ScrubConfig(gate=False, notch=True, decoy=False))
    before = _on_line(rep.tracks_before, flyovers, t)
    after = _on_line(rep.tracks_after, flyovers, t)
    assert before >= 2
    assert after <= before - 2
    # voice band largely untouched. Judged above 300 Hz: the synthetic speech's
    # pulse harmonics sit in the aircraft band itself, and erasing lines under a
    # voice necessarily costs some of that overlap (full-band ~0.90 here).
    from scipy.signal import butter, sosfiltfilt
    hp = butter(4, 300, btype="high", fs=cfg.sr, output="sos")
    assert np.corrcoef(sosfiltfilt(hp, y.astype(float)), sosfiltfilt(hp, out.astype(float)))[0, 1] > 0.9
    assert np.corrcoef(y, out)[0, 1] > 0.85


def test_notch_erases_entering_line_without_leaving_a_hole():
    """A plane coming into earshot over a quiet room: the lines must be gone
    from the scrubbed audio in SNR terms (not merely quieter), and the notch
    must not leave a hole or trough that traces the same curve. The gate is
    off here because it scales everything and would mask what the notch did."""
    from contrail.detect import DetectConfig, spectrogram, _whiten
    from contrail.synth import render_entering

    y, fo = render_entering(sr=8000, duration_s=80.0, onset_s=20.0, offset_s=70.0, aircraft_level=0.15)
    out, rep = scrub(y, 8000, ScrubConfig(gate=False, notch=True, decoy=False, seed=0))
    assert len(rep.tracks_before) >= 2

    t_all = np.arange(len(y)) / 8000
    ft = fo.observed_frequency(t_all)
    # nothing the detector finds afterwards may lie on the line or its harmonics
    for tr in rep.tracks_after:
        fi = np.interp(tr.times, t_all, ft)
        assert all(np.mean(np.abs(tr.freqs - h * fi) < 3 * h) < 0.5 for h in (1, 2, 3)), tr

    cfg = DetectConfig()
    f, t, S = spectrogram(out.astype(float), 8000, cfg)
    W = _whiten(S)
    sel = (t >= 20) & (t <= 70)
    fr = np.interp(t[sel], t_all, ft)
    cols = np.flatnonzero(sel)
    for h in (1, 2, 3):
        along = np.array([W[np.abs(f - h * fc) <= 2.0 + 0.3 * h, j].max() for j, fc in zip(cols, fr)])
        assert np.percentile(along, 90) < 10.0, (h, np.percentile(along, 90))   # floor, not a line
    # no trough: the filled band sits at the level of its surroundings
    band = np.array([np.median(S[np.abs(f - fc) <= 3, j]) for j, fc in zip(cols, fr)])
    ring = np.array([np.median(S[(np.abs(f - fc) > 5) & (np.abs(f - fc) <= 15), j]) for j, fc in zip(cols, fr)])
    assert abs(np.median(band - ring)) < 3.0, np.median(band - ring)


def test_decoys_are_mixed_in_at_real_level():
    cfg = SynthConfig(seed=1)
    y, _ = render(cfg)
    out, rep = scrub(y, cfg.sr, ScrubConfig(gate=False, notch=False, decoy=True, decoy_count=4, seed=1))
    assert len(rep.decoys) == 4
    assert 0.0 < rep.decoy_level < 0.2
    assert not np.allclose(y, out)
