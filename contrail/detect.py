"""Find slowly drifting narrowband lines (aircraft tonal signatures) in audio.

Pipeline:
  1. Low band STFT (long window → fine frequency resolution, coarse time).
  2. Per-frame spectral whitening so loud speech bands don't dominate.
  3. Peak picking per frame.
  4. Greedy ridge tracking: link peaks across frames when the frequency
     moves less than `max_hz_per_s`.
  5. Keep tracks long enough and smooth enough to be a flyover, and whose
     frequency is monotonic non-increasing (approach → recede) after smoothing.
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from math import gcd, lcm

import numpy as np
from scipy.signal import find_peaks, get_window

from .doppler import C_SOUND


@dataclass
class DopplerFit:
    f0: float        # emitted Hz
    v: float         # m/s
    d: float         # slant miss distance, m
    tc: float        # time of closest approach, s
    rms: float       # residual Hz


@dataclass
class Track:
    times: np.ndarray     # seconds
    freqs: np.ndarray     # Hz
    power: np.ndarray     # whitened dB
    fit: DopplerFit | None = None

    @property
    def duration(self) -> float:
        return float(self.times[-1] - self.times[0])

    @property
    def f_start(self) -> float:
        return float(self.freqs[0])

    @property
    def f_end(self) -> float:
        return float(self.freqs[-1])

    @property
    def drop_hz(self) -> float:
        return self.f_start - self.f_end

    def __repr__(self) -> str:
        s = (
            f"Track({self.times[0]:6.1f}s→{self.times[-1]:6.1f}s, "
            f"{self.f_start:6.1f}→{self.f_end:6.1f} Hz, n={len(self.times)}"
        )
        if self.fit:
            s += f", fit: f0={self.fit.f0:.1f} v={self.fit.v:.0f}m/s d={self.fit.d:.0f}m tc={self.fit.tc:.1f}s rms={self.fit.rms:.2f}"
        return s + ")"


def doppler_model(t: np.ndarray, f0: float, v: float, d: float, tc: float) -> np.ndarray:
    """Observed frequency for a straight level flyover, including propagation
    delay (retarded time). `d` is the slant miss distance at closest approach,
    `tc` the time of closest approach in *emission* time."""
    c = C_SOUND
    # emission time te: t = te + r(te)/c, r(te)^2 = v^2 (te-tc)^2 + d^2
    a = c * c - v * v
    b = -2 * c * c * t + 2 * v * v * tc
    cc = c * c * t * t - v * v * tc * tc - d * d
    te = (-b - np.sqrt(np.maximum(b * b - 4 * a * cc, 0.0))) / (2 * a)
    x = v * (te - tc)
    r = np.sqrt(x * x + d * d)
    v_rad = -v * x / r
    return f0 * c / (c - v_rad)


def _doppler_jac(t: np.ndarray, f0: float, v: float, d: float, tc: float, eps=(1e-3, 1e-3, 1e-2, 1e-3)) -> np.ndarray:
    """Central-difference Jacobian; 8 model evals, all vectorised, so cheap enough."""
    p = np.array([f0, v, d, tc], dtype=float)
    cols = []
    for i, e in enumerate(eps):
        dp = np.zeros(4); dp[i] = e
        cols.append((doppler_model(t, *(p + dp)) - doppler_model(t, *(p - dp))) / (2 * e))
    return np.column_stack(cols)


def fit_doppler(times: np.ndarray, freqs: np.ndarray) -> DopplerFit:
    from scipy.optimize import least_squares

    # collapse duplicate timestamps (overlapping fragments)
    times, inv = np.unique(times, return_inverse=True)
    freqs = np.bincount(inv, weights=freqs) / np.bincount(inv)

    t0 = times[0]
    tt = times - t0
    f_mid = float(np.median(freqs))
    slope = np.gradient(freqs, tt) if len(tt) > 2 else np.zeros_like(tt)
    tc0 = float(tt[np.argmin(slope)]) - 1500.0 / C_SOUND
    lo = np.array([20.0, 20.0, 50.0, tt[0] - 120.0])
    hi = np.array([2000.0, 400.0, 30_000.0, tt[-1] + 120.0])

    # at closest approach |df/dt| = f0 v^2 / (c d); guess d, derive v from the steepest slope
    d0 = 1500.0
    max_slope = float(max(-slope.min(), 0.05))
    v0 = float(np.clip(np.sqrt(max_slope * C_SOUND * d0 / f_mid), 30.0, 300.0))
    x0 = np.array([f_mid, v0, d0, tc0])
    res = least_squares(
        lambda p: doppler_model(tt, *p) - freqs,
        x0,
        jac=lambda p: _doppler_jac(tt, *p),
        bounds=(lo, hi),
        x_scale=[100.0, 50.0, 1000.0, 20.0],
        max_nfev=60,
    )
    rms = float(np.sqrt(np.mean(res.fun ** 2)))
    f0, v, d, tc = res.x
    return DopplerFit(float(f0), float(v), float(d), float(tc + t0), rms)


@dataclass
class DetectConfig:
    f_lo: float = 50.0
    f_hi: float = 800.0
    win_s: float = 1.0            # STFT window; 1 s → 1 Hz bins
    hop_s: float = 0.25
    peak_prominence_db: float = 6.0
    max_hz_per_s: float = 12.0    # max drift between frames to link (steep S-curve middle)
    link_tol_hz: float = 2.5      # tolerance around the slope-predicted frequency
    max_gap_frames: int = 4       # frames a track may go missing (speech harmonic crossings)
    min_fragment_s: float = 3.0   # shortest piece worth trying to merge
    max_merge_gap_s: float = 8.0  # fragments further apart than this are not joined
    min_duration_s: float = 12.0
    min_drop_hz: float = 3.0      # total downward shift over the track
    min_occupancy: float = 0.7    # fraction of frames with an actual peak
    max_fit_rms_hz: float = 1.0   # residual to the physical Doppler model
    v_range: tuple[float, float] = (40.0, 320.0)   # plausible ground speed, m/s
    d_range: tuple[float, float] = (100.0, 12_000.0)  # plausible slant miss distance, m
    # VAD gating: in talking frames, drop picked peaks in the speech pitch band.
    # VALIDATED ON REAL SPEECH AND FOUND NOT TO HELP — kept off, as scaffolding
    # only. Across ~48 min of real speech (3 recordings, 12 windows) + planted
    # flyovers, no vad_margin_db beats vad=False: off gives the fewest spurious
    # tracks at equal recovery (29/36, 18 spurious); a low margin fires often and
    # deletes the aircraft lines that share the speech band, a high margin barely
    # fires and pushes spurious tracks up, not down. When speech is near-
    # continuous you cannot blanket-suppress the band the line sits in. A real
    # fix needs a learned VAD (silero/webrtcvad) and a targeted rejection (only
    # peaks coincident with a tracked voice harmonic), not this band gate.
    # voice_activity() below is kept as the primitive to build that on. Do not
    # enable vad as-is.
    vad: bool = False
    vad_band: tuple[float, float] = (300.0, 3400.0)   # band whose energy signals speech
    vad_margin_db: float = 12.0                        # dB above the band's floor = speech
    vad_pitch_hi: float = 450.0                        # suppress speech-frame peaks below this Hz


def _whiten(S_db: np.ndarray, med_bins: int = 25) -> np.ndarray:
    """Subtract a running median along frequency, per frame."""
    from scipy.ndimage import median_filter
    bg = median_filter(S_db, size=(med_bins, 1), mode="nearest")
    return S_db - bg


def spectrogram(y: np.ndarray, sr: int, cfg: DetectConfig):
    """Short-time Fourier magnitude in dB over the low band.

    Frames are built explicitly rather than via scipy.signal.stft: that routine
    takes a sliding_window_view whose logical size overflows a 32-bit build
    (Pyodide/WASM), and the engine ships into the desktop app that way. This
    matches stft(window="hann", scaling="spectrum", boundary=None, padded=False)
    to floating-point rounding; verified against it downstream."""
    nper = int(cfg.win_s * sr)
    hop = int(cfg.hop_s * sr)
    win = get_window("hann", nper, fftbins=True)
    n = len(y)
    if n < nper:
        return np.array([]), np.array([]), np.zeros((0, 0))
    nseg = 1 + (n - nper) // hop
    idx = np.arange(nper)[None, :] + hop * np.arange(nseg)[:, None]
    frames = y[idx] * win
    Z = np.fft.rfft(frames, axis=1) / win.sum()
    f = np.fft.rfftfreq(nper, 1.0 / sr)
    t = (np.arange(nseg) * hop + nper / 2) / sr
    band = (f >= cfg.f_lo) & (f <= cfg.f_hi)
    S_db = 20 * np.log10(np.abs(Z.T[band]) + 1e-10)
    return f[band], t, S_db


def _smooth_residual(times: np.ndarray, freqs: np.ndarray) -> float:
    """RMS distance to a cubic fit — real Doppler curves are very smooth."""
    if len(times) < 6:
        return 0.0
    coef = np.polyfit(times - times[0], freqs, 3)
    fit = np.polyval(coef, times - times[0])
    return float(np.sqrt(np.mean((freqs - fit) ** 2)))


def voice_activity(y: np.ndarray, sr: int, cfg: DetectConfig) -> np.ndarray:
    """Per-STFT-frame boolean: is someone talking? Energy in the voice band vs
    its own floor, measured on the same frames the spectrogram uses. Simple on
    purpose; swap in silero/webrtcvad for real use."""
    from scipy.signal import butter, sosfilt
    nper, hop = int(cfg.win_s * sr), int(cfg.hop_s * sr)
    if len(y) < nper:
        return np.zeros(0, dtype=bool)
    sos = butter(4, cfg.vad_band, btype="band", fs=sr, output="sos")
    v = sosfilt(sos, y)
    nseg = 1 + (len(y) - nper) // hop
    idx = np.arange(nper)[None, :] + hop * np.arange(nseg)[:, None]
    e = 10 * np.log10(np.mean(v[idx] ** 2, axis=1) + 1e-12)
    floor = float(np.percentile(e, 20))
    return e > floor + cfg.vad_margin_db


def find_tracks(y: np.ndarray, sr: int, cfg: DetectConfig | None = None) -> list[Track]:
    cfg = cfg or DetectConfig()
    f, t, S_db = spectrogram(y, sr, cfg)
    W = _whiten(S_db)
    vad = voice_activity(y, sr, cfg) if cfg.vad else None

    # peaks per frame, with parabolic sub-bin interpolation
    df = float(f[1] - f[0])
    frame_peaks: list[list[tuple[float, float]]] = []
    for j in range(W.shape[1]):
        col = W[:, j]
        idx, props = find_peaks(col, prominence=cfg.peak_prominence_db)
        speech = vad is not None and j < len(vad) and bool(vad[j])
        pk = []
        for i in idx:
            if speech and f[i] < cfg.vad_pitch_hi:
                continue  # voice harmonics dominate this band while talking
            if 0 < i < len(col) - 1:
                a, b, c = col[i - 1], col[i], col[i + 1]
                denom = a - 2 * b + c
                delta = 0.5 * (a - c) / denom if denom != 0 else 0.0
                delta = float(np.clip(delta, -0.5, 0.5))
            else:
                delta = 0.0
            pk.append((float(f[i] + delta * df), float(col[i])))
        frame_peaks.append(pk)

    # greedy tracking
    active: list[dict] = []
    done: list[dict] = []
    max_step = cfg.max_hz_per_s * cfg.hop_s

    def predict(tr: dict, t_now: float) -> float:
        """Extrapolate the last ~2 s of the track linearly (Doppler drift is smooth)."""
        tt, ff = np.array(tr["t"][-8:]), np.array(tr["f"][-8:])
        if len(tt) < 3:
            return ff[-1]
        slope = float(np.clip(_slope(tt, ff), -cfg.max_hz_per_s, 0.0))  # lines never rise
        return ff[-1] + slope * (t_now - tt[-1])

    for j, peaks in enumerate(frame_peaks):
        used = set()
        for tr in active:
            best, best_d = None, None
            f_pred = predict(tr, t[j])
            tol = cfg.link_tol_hz * (1 + 0.5 * tr["gap"])
            for k, (fp, pw) in enumerate(peaks):
                if k in used:
                    continue
                d = abs(fp - f_pred)
                if d <= tol and fp <= tr["f"][-1] + cfg.link_tol_hz and (best_d is None or d < best_d):
                    best, best_d = k, d
            if best is None:
                tr["gap"] += 1
            else:
                used.add(best)
                fp, pw = peaks[best]
                tr["t"].append(t[j]); tr["f"].append(fp); tr["p"].append(pw); tr["gap"] = 0
        still = []
        for tr in active:
            (done if tr["gap"] > cfg.max_gap_frames else still).append(tr)
        active = still
        for k, (fp, pw) in enumerate(peaks):
            if k not in used:
                active.append({"t": [t[j]], "f": [fp], "p": [pw], "gap": 0})
    done.extend(active)

    # --- fragments: anything plausibly monotone and at least a few seconds long
    frags: list[Track] = []
    for tr in done:
        tt, ff, pp = (np.array(tr[k]) for k in ("t", "f", "p"))
        if tt[-1] - tt[0] < cfg.min_fragment_s:
            continue
        if np.max(np.diff(ff)) > cfg.link_tol_hz:   # Doppler lines never rise
            continue
        frags.append(Track(tt, ff, pp))
    frags.sort(key=lambda x: x.times[0])

    # --- merge pass: join fragments whose union still fits one flyover
    merged = _merge_fragments(frags, cfg)

    tracks: list[Track] = []
    queue = list(merged)
    while queue:
        tr = queue.pop()
        tt, ff, pp = tr.times, tr.freqs, tr.power
        if tt[-1] - tt[0] < cfg.min_duration_s:
            continue
        if ff[0] - ff[-1] < cfg.min_drop_hz:
            continue
        expected_frames = (tt[-1] - tt[0]) / cfg.hop_s + 1
        if len(tt) / expected_frames < cfg.min_occupancy:
            continue
        fit = tr.fit or fit_doppler(tt, ff)
        if fit.rms > cfg.max_fit_rms_hz:
            # Possibly two crossing lines glued together: split at the best
            # point and re-queue the halves.
            if tt[-1] - tt[0] >= 2 * cfg.min_duration_s:
                queue.extend(_split(tr, cfg))
            continue
        if not (cfg.v_range[0] <= fit.v <= cfg.v_range[1]):
            continue
        if not (cfg.d_range[0] <= fit.d <= cfg.d_range[1]):
            continue
        tracks.append(Track(tt, ff, pp, fit))

    tracks.sort(key=lambda x: x.times[0])
    return _dedupe(tracks, cfg)


def _split(tr: Track, cfg: DetectConfig, n_candidates: int = 9) -> list[Track]:
    """Try splitting at a handful of interior points; return the pair whose
    combined fit residual is lowest, provided it beats the unsplit fit."""
    n = len(tr.times)
    min_pts = int(cfg.min_duration_s / cfg.hop_s * cfg.min_occupancy)
    if n < 2 * min_pts + 2:
        return []
    cands = np.linspace(min_pts, n - min_pts, n_candidates).astype(int)
    best = None
    for k in cands:
        a = fit_doppler(tr.times[:k], tr.freqs[:k])
        b = fit_doppler(tr.times[k:], tr.freqs[k:])
        score = max(a.rms, b.rms)
        if best is None or score < best[0]:
            best = (score, k, a, b)
    score, k, a, b = best
    if score >= (tr.fit.rms if tr.fit else cfg.max_fit_rms_hz * 2):
        return []
    return [
        Track(tr.times[:k], tr.freqs[:k], tr.power[:k], a),
        Track(tr.times[k:], tr.freqs[k:], tr.power[k:], b),
    ]


def _slope(tt: np.ndarray, ff: np.ndarray) -> float:
    if len(tt) < 3:
        return 0.0
    tm, fm = tt.mean(), ff.mean()
    var = np.sum((tt - tm) ** 2)
    return float(np.sum((tt - tm) * (ff - fm)) / var) if var > 0 else 0.0


def _tail_extrapolate(tr: Track, t_target: float, n: int = 12) -> float:
    tt, ff = tr.times[-n:], tr.freqs[-n:]
    return float(ff[-1] + _slope(tt, ff) * (t_target - tt[-1]))


def _head_extrapolate(tr: Track, t_target: float, n: int = 12) -> float:
    tt, ff = tr.times[:n], tr.freqs[:n]
    return float(ff[0] + _slope(tt, ff) * (t_target - tt[0]))


def _merge_fragments(frags: list[Track], cfg: DetectConfig) -> list[Track]:
    """Forward sweep over time-sorted fragments. For each fragment, look at the
    later fragments that start within the merge gap and whose head agrees with
    our tail's linear extrapolation (and vice versa); run the physical fit only
    on those, join the best, and continue from the joined track."""
    frags = sorted(frags, key=lambda x: x.times[0])
    used = [False] * len(frags)
    out: list[Track] = []
    for i in range(len(frags)):
        if used[i]:
            continue
        used[i] = True
        cur = frags[i]
        while True:
            best = None
            for j in range(i + 1, len(frags)):
                if used[j]:
                    continue
                b = frags[j]
                gap = b.times[0] - cur.times[-1]
                if gap > cfg.max_merge_gap_s:
                    break              # sorted by start → nothing further will qualify
                if gap < -1.0:
                    continue
                t_mid = 0.5 * (cur.times[-1] + b.times[0])
                tol = cfg.link_tol_hz + 0.25 * abs(gap)
                if b.freqs[0] > cur.freqs[-1] + cfg.link_tol_hz:
                    continue
                if abs(_tail_extrapolate(cur, t_mid) - _head_extrapolate(b, t_mid)) > tol:
                    continue
                tt = np.concatenate([cur.times, b.times])
                ff = np.concatenate([cur.freqs, b.freqs])
                pp = np.concatenate([cur.power, b.power])
                order = np.argsort(tt)
                fit = fit_doppler(tt[order], ff[order])
                if fit.rms <= cfg.max_fit_rms_hz and (best is None or fit.rms < best[0]):
                    best = (fit.rms, j, Track(tt[order], ff[order], pp[order], fit))
            if best is None:
                break
            used[best[1]] = True
            cur = best[2]
        out.append(cur)
    return out


def _dedupe(tracks: list[Track], cfg: DetectConfig) -> list[Track]:
    """Drop a track that lies within link tolerance of a longer one over its whole span."""
    keep: list[Track] = []
    for tr in sorted(tracks, key=lambda x: -len(x.times)):
        dup = False
        for k in keep:
            fk = np.interp(tr.times, k.times, k.freqs, left=np.nan, right=np.nan)
            ok = ~np.isnan(fk)
            if ok.mean() > 0.8 and np.mean(np.abs(tr.freqs[ok] - fk[ok]) < cfg.link_tol_hz) > 0.8:
                dup = True
                break
        if not dup:
            keep.append(tr)
    return sorted(keep, key=lambda x: x.times[0])


@dataclass
class GroupConfig:
    """When lines count as harmonics of one aircraft. Both must hold."""
    max_ratio_error: float = 0.01     # measured frequency ratio vs a small-integer ratio
    max_harmonic: int = 6             # largest integer in that ratio
    max_joint_rms_frac: float = 0.005 # joint fit residual, as a fraction of f0


@dataclass
class FlyoverGroup:
    """Tracks attributed to one aircraft. `harmonics` holds each track's harmonic
    number relative to the group; the true fundamental may not have been detected."""
    tracks: list[Track]
    harmonics: list[int]
    fit: DopplerFit

    @property
    def duration(self) -> float:
        return max(tr.duration for tr in self.tracks)

    @property
    def drop_hz(self) -> float:
        return max(tr.drop_hz for tr in self.tracks)

    def __repr__(self) -> str:
        f = self.fit
        return (
            f"FlyoverGroup({len(self.tracks)} line(s), harmonics={self.harmonics}, "
            f"v={f.v:.0f}m/s d={f.d:.0f}m tc={f.tc:.1f}s rms={f.rms / f.f0:.2%})"
        )


def _joint_fit(members: list[Track], ratios: list[Fraction]) -> DopplerFit:
    """One flyover model through every member, each scaled onto a common fundamental."""
    tt = np.concatenate([tr.times for tr in members])
    ff = np.concatenate([tr.freqs / float(r) for tr, r in zip(members, ratios)])
    order = np.argsort(tt)
    return fit_doppler(tt[order], ff[order])


def _harmonic_numbers(ratios: list[Fraction]) -> list[int]:
    """Smallest integers in the same proportion as `ratios`."""
    denom = lcm(*(r.denominator for r in ratios))
    ints = [int(r * denom) for r in ratios]
    div = gcd(*ints)
    return [i // div for i in ints]


def group_flyovers(tracks: list[Track], cfg: GroupConfig | None = None) -> list[FlyoverGroup]:
    """Attribute lines to aircraft.

    Harmonics of one source share speed, miss distance and closest approach, and
    their frequencies stand in an integer ratio. So the test is physical: measure
    the candidate's ratio to the group's curve, snap it to a small-integer ratio,
    then require one flyover model to fit both. The separate fit of a short fragment
    is too poorly constrained to compare parameters directly. The longest lines
    seed groups.
    """
    cfg = cfg or GroupConfig()
    candidates = sorted(
        {Fraction(p, q) for p in range(1, cfg.max_harmonic + 1) for q in range(1, cfg.max_harmonic + 1)}
    )
    groups: list[tuple[list[Track], list[Fraction], DopplerFit]] = []
    fitted = [tr for tr in tracks if tr.fit is not None]
    for tr in sorted(fitted, key=lambda x: -x.duration):
        best = None
        for g, (members, ratios, fit) in enumerate(groups):
            observed = float(np.median(tr.freqs / doppler_model(tr.times, fit.f0, fit.v, fit.d, fit.tc)))
            r = min(candidates, key=lambda c: abs(observed / float(c) - 1.0))
            if abs(observed / float(r) - 1.0) > cfg.max_ratio_error:
                continue
            joint = _joint_fit(members + [tr], ratios + [r])
            err = joint.rms / joint.f0
            if err <= cfg.max_joint_rms_frac and (best is None or err < best[0]):
                best = (err, g, r, joint)
        if best is None:
            groups.append(([tr], [Fraction(1)], tr.fit))
        else:
            _, g, r, joint = best
            members, ratios, _ = groups[g]
            groups[g] = (members + [tr], ratios + [r], joint)
    out = [FlyoverGroup(m, _harmonic_numbers(r), f) for m, r, f in groups]
    return sorted(out, key=lambda g: g.fit.tc)


def leak_score(tracks: list[Track]) -> float:
    """0-1. Three or more clean, long flyovers is enough to trilaterate. Counts
    aircraft rather than lines, since a plane's harmonics add no new geometry."""
    groups = group_flyovers(tracks)
    if not groups:
        return 0.0
    s = sum(min(1.0, g.duration / 40.0) * min(1.0, g.drop_hz / 15.0) for g in groups)
    return float(min(1.0, s / 3.0))
