"""Generate synthetic test clips: speech-like foreground + planted Doppler tracks.

No real audio is shipped. The "speech" is a crude formant-ish buzz with
pauses so VAD-style gating has something to work with.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.io import wavfile
from scipy.signal import butter, sosfilt

from .doppler import Flyover


@dataclass
class SynthConfig:
    sr: int = 16_000
    duration_s: float = 90.0
    speech_level: float = 0.3
    noise_level: float = 0.01
    aircraft_level: float = 0.02      # quiet: ~-24 dB below speech
    harmonics: int = 3                # extra blade-pass harmonics per flyover
    seed: int = 0
    flyovers: list[Flyover] = field(default_factory=list)


def default_flyovers(duration_s: float, rng: np.random.Generator, n: int = 3) -> list[Flyover]:
    out = []
    for i in range(n):
        out.append(
            Flyover(
                f0_hz=float(rng.uniform(90, 220)),
                speed_mps=float(rng.uniform(70, 130)),      # approach / departure speeds
                altitude_m=float(rng.uniform(600, 2500)),
                offset_m=float(rng.uniform(200, 1500)),
                t_closest=float(duration_s * (i + 0.5) / n + rng.uniform(-5, 5)),
            )
        )
    return out


def _fake_speech(t: np.ndarray, sr: int, rng: np.random.Generator) -> np.ndarray:
    """Pitch-wobbling pulse train through a couple of resonances, with pauses."""
    n = len(t)
    # phrase on/off envelope: ~2 s phrases, ~0.7 s gaps
    env = np.zeros(n)
    pos = 0.0
    while pos < t[-1]:
        on = rng.uniform(1.2, 3.0)
        off = rng.uniform(0.4, 1.0)
        env[(t >= pos) & (t < pos + on)] = 1.0
        pos += on + off
    # smooth the envelope edges
    k = int(0.03 * sr)
    env = np.convolve(env, np.ones(k) / k, mode="same")

    f_pitch = 120 + 25 * np.sin(2 * np.pi * 0.7 * t) + 10 * rng.standard_normal(n).cumsum() / np.sqrt(np.arange(1, n + 1))
    f_pitch = np.clip(f_pitch, 80, 200)
    phase = 2 * np.pi * np.cumsum(f_pitch) / sr
    pulses = np.sin(phase) + 0.5 * np.sin(2 * phase) + 0.3 * np.sin(3 * phase) + 0.2 * np.sin(4 * phase)
    # two cheap "formants"
    sos1 = butter(2, [500, 900], btype="band", fs=sr, output="sos")
    sos2 = butter(2, [1400, 2200], btype="band", fs=sr, output="sos")
    sp = sosfilt(sos1, pulses) + 0.6 * sosfilt(sos2, pulses) + 0.15 * pulses
    sp = sp / (np.abs(sp).max() + 1e-9)
    return sp * env


def render(cfg: SynthConfig) -> tuple[np.ndarray, list[Flyover]]:
    rng = np.random.default_rng(cfg.seed)
    n = int(cfg.sr * cfg.duration_s)
    t = np.arange(n) / cfg.sr

    flyovers = cfg.flyovers or default_flyovers(cfg.duration_s, rng)

    y = cfg.speech_level * _fake_speech(t, cfg.sr, rng)
    y += cfg.noise_level * rng.standard_normal(n)

    for fo in flyovers:
        f_obs = fo.observed_frequency(t)
        amp = fo.relative_amplitude(t)
        for h in range(1, cfg.harmonics + 1):
            phase = 2 * np.pi * np.cumsum(h * f_obs) / cfg.sr
            y += cfg.aircraft_level * amp * (1.0 / h) * np.sin(phase)

    y = np.clip(y, -1, 1).astype(np.float32)
    return y, flyovers


def render_entering(sr: int = 8000, duration_s: float = 100.0, onset_s: float = 25.0,
                    offset_s: float = 85.0, aircraft_level: float = 0.05,
                    noise_level: float = 0.01, harmonics: int = 3, seed: int = 3,
                    flyover: Flyover | None = None) -> tuple[np.ndarray, Flyover]:
    """A quiet room (pink-ish floor, no speech) and one propeller aircraft that
    comes into earshot mid-clip: its lines switch on at `onset_s`, when the
    detector is already tracking the floor's noise peaks, and off at
    `offset_s`. Default aircraft: Cessna-like blade-pass line at 76 Hz, 48 m/s,
    450 m up, 350 m lateral miss. This is the case that defeated the detector
    (see DetectConfig.link_power_db) and the stream-audio case that matters:
    the streamer is quiet and a plane goes over."""
    rng = np.random.default_rng(seed)
    n = int(sr * duration_s)
    t = np.arange(n) / sr
    noise = rng.standard_normal(n)
    sos = butter(1, 200.0, btype="low", fs=sr, output="sos")
    noise = sosfilt(sos, noise) + 0.15 * noise          # ~ -3 dB/oct room floor
    noise = noise_level * noise / noise.std()
    fo = flyover or Flyover(f0_hz=76.0, speed_mps=48.0, altitude_m=450.0, offset_m=350.0,
                            t_closest=0.5 * (onset_s + offset_s))
    f_obs = fo.observed_frequency(t)
    amp = fo.relative_amplitude(t)
    gate = ((t >= onset_s) & (t <= offset_s)).astype(float)
    k = int(0.5 * sr)
    gate = np.convolve(gate, np.ones(k) / k, mode="same")
    y = noise.copy()
    for h in range(1, harmonics + 1):
        phase = 2 * np.pi * np.cumsum(h * f_obs) / sr
        y += aircraft_level * amp * gate * (1.0 / h) * np.sin(phase)
    return np.clip(y, -1, 1).astype(np.float32), fo


def write_wav(path: str, y: np.ndarray, sr: int) -> None:
    wavfile.write(path, sr, (y * 32767).astype(np.int16))
