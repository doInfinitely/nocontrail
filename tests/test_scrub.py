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
    # voice band largely untouched
    assert np.corrcoef(y, out)[0, 1] > 0.9


def test_decoys_are_mixed_in_at_real_level():
    cfg = SynthConfig(seed=1)
    y, _ = render(cfg)
    out, rep = scrub(y, cfg.sr, ScrubConfig(gate=False, notch=False, decoy=True, decoy_count=4, seed=1))
    assert len(rep.decoys) == 4
    assert 0.0 < rep.decoy_level < 0.2
    assert not np.allclose(y, out)
