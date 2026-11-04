"""Remove or drown aircraft-Doppler signatures in audio you own.

Three tiers, composable. All operate in the same STFT domain the detector
uses (1 s Hann windows, 0.25 s hop) so what the scrubber touches is what the
detector sees.

  gate   — in frames with no voice activity, attenuate everything below
           `gate_f_hi`. Cheap, inaudible, removes the clean stretches a fitter
           needs most.
  notch  — for each detected ridge (and its harmonics), zero a narrow band
           that follows the ridge frame by frame. Surgical.
  decoy  — mix in synthetic flyovers with random, physically plausible
           parameters at the same level as the real lines. A fitter now sees
           N fakes per real line, with nothing to tell them apart.

Order: notch → gate → decoy. Decoys go in last so the gate doesn't eat them.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.signal import butter, get_window, sosfilt

from .detect import DetectConfig, Track, find_tracks
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
    notch_half_width_hz: float = 2.5
    notch_harmonics: int = 3
    notch_atten_db: float = 60.0
    # decoy
    decoy: bool = True
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


def notch_tracks(Z: np.ndarray, f: np.ndarray, t: np.ndarray, tracks: list[Track], cfg: ScrubConfig) -> np.ndarray:
    Z = Z.copy()
    g = 10 ** (-cfg.notch_atten_db / 20)
    for tr in tracks:
        # ridge at every STFT frame inside the track's span (interpolated)
        inside = (t >= tr.times[0]) & (t <= tr.times[-1])
        ridge = np.interp(t[inside], tr.times, tr.freqs)
        cols = np.where(inside)[0]
        for h in range(1, cfg.notch_harmonics + 1):
            # the fundamental the tracker found may itself be a harmonic; also
            # cover sub-harmonics so f0/2 isn't left behind
            for mult in (h, 1.0 / h):
                fc = ridge * mult
                hw = cfg.notch_half_width_hz * max(mult, 1.0)
                for j, fcj in zip(cols, fc):
                    band = np.abs(f - fcj) <= hw
                    Z[band, j] *= g
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
    tracks_after: list[Track]
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

    f, t, Z, nper, hop = _stft(y, sr, dc)
    if cfg.notch and tracks:
        Z = notch_tracks(Z, f, t, tracks, cfg)
    vad = voice_activity(y, sr, cfg, dc.hop_s)
    if cfg.gate:
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
    after = find_tracks(out, sr, dc)
    return out, ScrubReport(tracks, after, decoys, level, float(vad.mean()))
