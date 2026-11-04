"""Physical Doppler model for a straight-line aircraft flyover.

Everything here is pure geometry + acoustics; no location data, no network.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

C_SOUND = 343.0  # m/s at ~20 C


@dataclass(frozen=True)
class Flyover:
    """Aircraft on a straight, level, constant-speed track.

    Geometry is relative to the listener at the origin. The aircraft moves
    along +x at height `altitude_m`, passing the listener's x-coordinate at
    `t_closest` with lateral (y) offset `offset_m`.
    """

    f0_hz: float          # emitted tonal frequency (fan/blade-pass line)
    speed_mps: float      # ground speed
    altitude_m: float
    offset_m: float       # lateral miss distance at closest approach
    t_closest: float      # seconds (in recording time) of closest approach

    def emission_time(self, t_obs: np.ndarray) -> np.ndarray:
        """Solve for the emission time whose sound arrives at t_obs.

        r(te) = ||(v*(te - tc), offset, alt)||, and t_obs = te + r(te)/c.
        Quadratic in te; take the root with te < t_obs.
        """
        v, c = self.speed_mps, C_SOUND
        d2 = self.offset_m ** 2 + self.altitude_m ** 2
        # (t_obs - te)^2 c^2 = v^2 (te - tc)^2 + d2
        # Let u = te. Expand: c^2 (t_obs^2 - 2 t_obs u + u^2) = v^2 (u^2 - 2 tc u + tc^2) + d2
        a = c ** 2 - v ** 2
        b = -2 * c ** 2 * t_obs + 2 * v ** 2 * self.t_closest
        cc = c ** 2 * t_obs ** 2 - v ** 2 * self.t_closest ** 2 - d2
        disc = np.sqrt(np.maximum(b * b - 4 * a * cc, 0.0))
        te = (-b - disc) / (2 * a)
        return te

    def observed_frequency(self, t_obs: np.ndarray) -> np.ndarray:
        """Received frequency at each observation time (Hz)."""
        te = self.emission_time(t_obs)
        x = self.speed_mps * (te - self.t_closest)
        r = np.sqrt(x ** 2 + self.offset_m ** 2 + self.altitude_m ** 2)
        # radial velocity component toward listener (positive = approaching)
        v_rad = -self.speed_mps * x / r
        return self.f0_hz * C_SOUND / (C_SOUND - v_rad)

    def relative_amplitude(self, t_obs: np.ndarray) -> np.ndarray:
        """1/r spreading, normalised to 1 at closest approach."""
        te = self.emission_time(t_obs)
        x = self.speed_mps * (te - self.t_closest)
        r = np.sqrt(x ** 2 + self.offset_m ** 2 + self.altitude_m ** 2)
        r_min = np.sqrt(self.offset_m ** 2 + self.altitude_m ** 2)
        return r_min / r
