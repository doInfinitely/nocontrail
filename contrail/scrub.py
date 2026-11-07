"""Remove aircraft-Doppler signatures in audio you own.

The two defenses that actually hold, plus one optional layer, all in the same
STFT domain the detector uses (1 s Hann windows, 0.25 s hop) so what the
scrubber touches is what the detector sees.

  notch  — for each detected aircraft, replace narrow bands that follow its
           fundamental and every harmonic up to the band top, frame by frame,
           with floor-level noise. Surgical. This is the primary defense: it
           removes the real lines without leaving a hole that traces the same
           curve.
  gate   — in frames with no voice activity, attenuate everything below
           `gate_f_hi`. Cheap and inaudible, but it scales line and floor
           together, so a ridge's SNR is unchanged: measured on a synthetic
           flyover, the whitened level along the ridge was identical before
           and after a 40 dB gate, and an SNR-based fitter recovered the tone
           from the gated audio exactly as from the raw. It only helps where
           the attacker's floor is set elsewhere (codec noise, playback). Do
           not count on it; the notch is the defense.
  decoy  — OPTIONAL, off by default, defense-in-depth only. Mix in synthetic
           flyovers at the real-line level so a fitter sees extra candidates.
           This only confuses an attacker who is NOT cross-referencing flight
           data: the decoys are physically plausible but correspond to no real
           aircraft, so an attacker with ADS-B/flight history discards them for
           free. It does not defeat the flight-data matching attack, and at an
           inaudible level it is unreliable (most decoys don't resolve as clean
           tracks). Notch + gate are what you rely on; decoys are a bonus
           against a casual attacker, nothing more.

Order: notch → gate → decoy. Decoys go in last so the gate doesn't eat them.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.signal import butter, get_window, sosfilt

from .detect import DetectConfig, Track, find_tracks, group_flyovers
from .doppler import Flyover


@dataclass
class ScrubConfig:
    detect: DetectConfig = field(default_factory=DetectConfig)
    # gate
    gate: bool = True
    gate_f_hi: float = 500.0
    gate_atten_db: float = 40.0
    vad_band: tuple[float, float] = (300.0, 3400.0)
    vad_margin_db: float = 12.0        # above the band's noise floor counts as speech
    # notch
    notch: bool = True
    notch_half_width_hz: float = 2.5   # base; widens with harmonic number and line level (see notch_tracks)
    notch_harmonics: int = 16          # cap; the sweep stops at the detection band's top anyway
    notch_passes: int = 2              # detect -> erase -> re-detect -> erase; see scrub()
    notch_atten_db: float = 60.0       # unused: the band is filled to floor level, not attenuated
    # decoy — optional defense-in-depth, off by default (see module docstring)
    decoy: bool = False
    decoy_count: int = 8
    decoy_level: float | None = None   # None → match the measured real-line level
    decoy_harmonics: int = 3
    seed: int | None = None


# --------------------------------------------------------------------------- VAD

def voice_activity(y: np.ndarray, sr: int, cfg: ScrubConfig, hop_s: float) -> np.ndarray:
    """Per-frame boolean: is someone talking? Energy in the voice band vs its
    own floor. Deliberately simple; swap in silero/webrtcvad for real use."""
    sos = butter(4, cfg.vad_band, btype="band", fs=sr, output="sos")
    v = sosfilt(sos, y)
    hop = int(hop_s * sr)
    n_frames = int(np.ceil(len(y) / hop))
    e = np.zeros(n_frames)
    for i in range(n_frames):
        seg = v[i * hop:(i + 1) * hop]
        e[i] = 10 * np.log10(np.mean(seg ** 2) + 1e-12)
    floor = np.percentile(e, 10)
    return e > floor + cfg.vad_margin_db


# --------------------------------------------------------------------------- core

# Invertible STFT as weighted overlap-add. scipy.signal.stft/istft build a
# sliding_window_view that overflows a 32-bit (WASM) build, so the engine could
# not run the scrubber under Pyodide. This analysis+synthesis-Hann pair is a
# clean inverse (perfect reconstruction where frames overlap) and its time axis
# matches the detector's (boundary=None), so notches land on the tracked ridges.
# The signal is zero-padded by one window each side so the edges reconstruct.

def _stft(y, sr, dc: DetectConfig):
    nper, hop = int(dc.win_s * sr), int(dc.hop_s * sr)
    win = get_window("hann", nper, fftbins=True)
    pad = nper
    yp = np.concatenate([np.zeros(pad), np.asarray(y, dtype=float), np.zeros(pad)])
    nseg = 1 + int(np.ceil(max(len(yp) - nper, 0) / hop))
    total = (nseg - 1) * hop + nper
    if total > len(yp):
        yp = np.concatenate([yp, np.zeros(total - len(yp))])
    idx = np.arange(nper)[None, :] + hop * np.arange(nseg)[:, None]
    frames = yp[idx] * win
    Z = np.fft.rfft(frames, axis=1).T / win.sum()  # (nfreq, nseg); scaling="spectrum"
    f = np.fft.rfftfreq(nper, 1.0 / sr)
    t = (np.arange(nseg) * hop + nper / 2 - pad) / sr   # original timeline
    return f, t, Z, nper, hop


def _istft(Z, sr, nper, hop, n):
    win = get_window("hann", nper, fftbins=True)
    pad = nper
    nseg = Z.shape[1]
    frames = np.fft.irfft(Z * win.sum(), n=nper, axis=0).T * win  # undo forward scaling, synthesis window
    total = (nseg - 1) * hop + nper
    out = np.zeros(total)
    wsum = np.zeros(total)
    w2 = win * win
    for i in range(nseg):
        a = i * hop
        out[a:a + nper] += frames[i]
        wsum[a:a + nper] += w2
    out = out / np.where(wsum > 1e-8, wsum, 1.0)
    return out[pad:pad + n]


def notch_tracks(Z: np.ndarray, f: np.ndarray, t: np.ndarray, tracks: list[Track], cfg: ScrubConfig,
                 rng: np.random.Generator | None = None, fill: np.ndarray | None = None) -> np.ndarray:
    """Erase each tracked ridge (and its harmonics) by replacing the band with
    floor-level noise, not silence.

    A zeroed band leaves a hole. Its edges become the most prominent "peaks" in
    the frame (a 60 dB valley beside ordinary floor bins) and the hole follows
    the Doppler curve exactly, so a hole is as trackable as the line was:
    measured on a synthetic flyover, a hard ±4 Hz notch left a ridge parallel
    to the original, 4 Hz off, that find_tracks picked up as a clean flyover.
    The fill takes the median magnitude of the bins just outside the band, so
    the band becomes indistinguishable from the floor around it. Its phases
    come from `fill`, the STFT of a real time-domain noise signal on the same
    frame grid: phases drawn independently per frame are inconsistent across
    the 75 %-overlapping frames, overlap-add partly cancels them, and the band
    comes back 5-10 dB below its ring as a shallow trough that still traces the
    Doppler curve (measured; the detector tracked its edge). Frame-consistent
    noise survives resynthesis at the level it was given. Without `fill` it
    falls back to random phase.

    The width grows with the line's level because a loud line's window skirts
    extend past a fixed notch: Hann sidelobes start at -31 dB, so a 36 dB line
    still stands 5 dB over the floor at ±3-4 Hz. One extra bin per 6 dB above
    25 dB keeps the skirts inside the fill."""
    rng = rng if rng is not None else np.random.default_rng(0)
    Z = Z.copy()
    df = float(f[1] - f[0])
    dc = cfg.detect

    def erase(j: int, fc: float, hw: float) -> None:
        dist = np.abs(f - fc)
        band = dist <= hw
        if not band.any():
            return
        ring = (dist > hw) & (dist <= hw + 10 * df)
        mag = float(np.median(np.abs(Z[ring, j]))) if ring.any() else 0.0
        if fill is not None:
            ref = fill[band, j]
            Z[band, j] = ref * (mag / max(float(np.median(np.abs(ref))), 1e-30))
        else:
            Z[band, j] = mag * np.exp(1j * rng.uniform(0.0, 2 * np.pi, int(band.sum())))

    # Work per aircraft, not per line: the lines of one plane are harmonics of
    # one fundamental, so erasing each line's own multiples would cover the
    # same bands three times over (and still skip the harmonics nobody
    # tracked). Pool the group's members into one fundamental curve and sweep
    # its harmonics once each, up to the top of the detection band.
    for g in group_flyovers(tracks):
        tt = np.concatenate([tr.times for tr in g.tracks])
        ff = np.concatenate([tr.freqs / h for tr, h in zip(g.tracks, g.harmonics)])
        pp = np.concatenate([tr.power for tr in g.tracks])
        hs = np.concatenate([np.full(len(tr.times), h) for tr, h in zip(g.tracks, g.harmonics)])
        order = np.argsort(tt)
        tt, ff, pp, hs = tt[order], ff[order], pp[order], hs[order]
        t_u, inv = np.unique(tt, return_inverse=True)
        f0 = np.bincount(inv, weights=ff) / np.bincount(inv)
        inside = (t >= t_u[0]) & (t <= t_u[-1])
        cols = np.where(inside)[0]
        f0_c = np.interp(t[inside], t_u, f0)
        # level per tracked harmonic (for the width); untracked harmonics are faint
        level_of: dict[int, np.ndarray] = {}
        for h in set(int(x) for x in hs):
            m = hs == h
            level_of[h] = np.interp(t[inside], tt[m], pp[m], left=pp[m][0], right=pp[m][-1])
        n_h = int(min(cfg.notch_harmonics, max(1, dc.f_hi // max(float(np.median(f0)), 1.0))))
        n_h = max(n_h, max(g.harmonics))
        for h in range(1, n_h + 1):
            level = level_of.get(h, np.zeros(len(cols)))
            # base width grows slowly with h (an f0 error scales with h) and with level
            hw = cfg.notch_half_width_hz + 0.5 * (h - 1) + df * np.maximum(0.0, (level - 25.0) / 6.0)
            for j, fcj, hwj in zip(cols, h * f0_c, hw):
                erase(j, fcj, hwj)
        # the group's "fundamental" may itself be the 2nd harmonic of one too
        # faint to track; cover f0/2 as well
        for j, fcj in zip(cols, 0.5 * f0_c):
            if fcj >= dc.f_lo:
                erase(j, fcj, cfg.notch_half_width_hz)
    return Z


def gate_quiet_frames(Z: np.ndarray, f: np.ndarray, vad: np.ndarray, cfg: ScrubConfig) -> np.ndarray:
    Z = Z.copy()
    g = 10 ** (-cfg.gate_atten_db / 20)
    low = f <= cfg.gate_f_hi
    n = min(Z.shape[1], len(vad))
    quiet = ~vad[:n]
    Z[np.ix_(low, np.where(quiet)[0])] *= g
    return Z


def real_line_level(y: np.ndarray, sr: int, tracks: list[Track], dc: DetectConfig) -> float:
    """RMS amplitude of the detected lines, estimated from the STFT magnitude
    along each ridge. Used to size decoys so they are indistinguishable."""
    if not tracks:
        return 0.0
    f, t, Z, nper, _ = _stft(y, sr, dc)
    vals = []
    for tr in tracks:
        inside = (t >= tr.times[0]) & (t <= tr.times[-1])
        ridge = np.interp(t[inside], tr.times, tr.freqs)
        for j, fc in zip(np.where(inside)[0], ridge):
            k = np.argmin(np.abs(f - fc))
            vals.append(np.abs(Z[k, j]))
    # Hann-window STFT magnitude of a unit sine ≈ 0.5 (scipy scales by window sum)
    return float(np.median(vals) * 2.0)


def make_decoys(duration_s: float, rng: np.random.Generator, n: int) -> list[Flyover]:
    out = []
    for _ in range(n):
        out.append(
            Flyover(
                f0_hz=float(rng.uniform(80, 260)),
                speed_mps=float(rng.uniform(65, 240)),
                altitude_m=float(rng.uniform(500, 4000)),
                offset_m=float(rng.uniform(100, 3000)),
                t_closest=float(rng.uniform(-10, duration_s + 10)),
            )
        )
    return out


def render_decoys(n: int, sr: int, flyovers: list[Flyover], level: float, harmonics: int) -> np.ndarray:
    t = np.arange(n) / sr
    y = np.zeros(n)
    for fo in flyovers:
        f_obs = fo.observed_frequency(t)
        amp = fo.relative_amplitude(t)
        for h in range(1, harmonics + 1):
            phase = 2 * np.pi * np.cumsum(h * f_obs) / sr
            y += level * amp * (1.0 / h) * np.sin(phase)
    return y


@dataclass
class ScrubReport:
    tracks_before: list[Track]
    tracks_after: list[Track]      # still detectable after the notch passes (pre-gate; the gate can't change it)
    decoys: list[Flyover]
    decoy_level: float
    vad_speech_fraction: float


def scrub(y: np.ndarray, sr: int, cfg: ScrubConfig | None = None) -> tuple[np.ndarray, ScrubReport]:
    cfg = cfg or ScrubConfig()
    dc = cfg.detect
    rng = np.random.default_rng(cfg.seed)
    n = len(y)

    tracks = find_tracks(y, sr, dc)
    level = cfg.decoy_level if cfg.decoy_level is not None else real_line_level(y, sr, tracks, dc)

    # Notch in passes. Lines a louder one was masking, and fragments the first
    # detection could not assemble (half a flyover under speech), show up as
    # clean tracks once the first set is gone; each pass erases what the
    # re-detection finds. The last re-detection is the report's "after": the
    # gate below scales lines and floor together, so it cannot change it.
    out = np.asarray(y, dtype=float)
    remaining = tracks
    for _ in range(cfg.notch_passes if cfg.notch else 0):
        if not remaining:
            break
        f, t, Z, nper, hop = _stft(out, sr, dc)
        fill = _stft(rng.standard_normal(n), sr, dc)[2]   # frame-consistent noise phases
        Z = notch_tracks(Z, f, t, remaining, cfg, rng, fill)
        out = _istft(Z, sr, nper, hop, n)
        remaining = find_tracks(out, sr, dc)
    after = remaining

    vad = voice_activity(y, sr, cfg, dc.hop_s)
    if cfg.gate:
        f, t, Z, nper, hop = _stft(out, sr, dc)
        # Map each STFT column to its VAD frame by time (the STFT is zero-padded,
        # so column i is not VAD frame i).
        col = np.clip((t / dc.hop_s).astype(int), 0, len(vad) - 1)
        Z = gate_quiet_frames(Z, f, vad[col], cfg)
        out = _istft(Z, sr, nper, hop, n)

    decoys: list[Flyover] = []
    if cfg.decoy and level > 0:
        decoys = make_decoys(n / sr, rng, cfg.decoy_count)
        out = out + render_decoys(n, sr, decoys, level, cfg.decoy_harmonics)

    out = np.clip(out, -1, 1).astype(np.float32)
    return out, ScrubReport(tracks, after, decoys, level, float(vad.mean()))
