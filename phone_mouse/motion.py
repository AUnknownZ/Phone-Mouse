"""Turn raw phone sensor samples into cursor deltas.

Sensor conventions follow the W3C DeviceMotionEvent spec, which is what
iOS Safari reports:

* Device frame: +x to the right of the screen, +y towards the top edge,
  +z out of the screen towards the user.
* ``rotationRate`` is in deg/s: ``alpha`` about z, ``beta`` about x,
  ``gamma`` about y (right-hand rule).
* ``acceleration`` is linear acceleration in m/s^2 with gravity removed.
* ``accelerationIncludingGravity`` at rest points *up* (flat on a table,
  screen up, it reads roughly (0, 0, +9.81)).

Screen coordinates are +x right, +y down.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

Vec3 = tuple[float, float, float]

GRAVITY = 9.80665


def _norm(v: Vec3) -> float:
    return math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])


def _dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _deadzone(value: float, threshold: float) -> float:
    """Soft deadzone: zero inside the band, continuous outside it."""
    if abs(value) <= threshold:
        return 0.0
    return value - math.copysign(threshold, value)


@dataclass
class SubpixelAccumulator:
    """Carries fractional pixels between updates so slow motion still moves."""

    rx: float = 0.0
    ry: float = 0.0

    def take(self, dx: float, dy: float) -> tuple[int, int]:
        self.rx += dx
        self.ry += dy
        ix = int(self.rx)
        iy = int(self.ry)
        self.rx -= ix
        self.ry -= iy
        return ix, iy

    def reset(self) -> None:
        self.rx = self.ry = 0.0


def pointer_acceleration(speed: float, threshold: float, factor: float) -> float:
    """Gain multiplier that grows with speed, like an OS pointer curve.

    Below ``threshold`` the multiplier is 1 (precise, for fine CAD work);
    above it, it rises linearly so big sweeps don't need big wrist motion.
    """
    if speed <= threshold or factor <= 0:
        return 1.0
    return 1.0 + factor * (speed - threshold) / threshold


@dataclass
class GyroPointer:
    """Air mouse: aim the phone like a laser pointer.

    Horizontal motion uses the yaw rate about the *world* vertical axis
    (rotation rate projected onto gravity), so it works whether the phone is
    held flat like a remote or upright like a camera. Vertical motion uses
    pitch about the device x-axis (tilting the top edge up/down).
    """

    pixels_per_degree: float = 18.0
    deadzone_dps: float = 1.5
    accel_threshold_dps: float = 60.0
    accel_factor: float = 0.6
    smoothing: float = 0.35
    _sx: float = 0.0
    _sy: float = 0.0

    def update(
        self,
        rotation_rate: Vec3,
        dt: float,
        gravity: Vec3 | None = None,
    ) -> tuple[float, float]:
        alpha, beta, gamma = rotation_rate
        omega: Vec3 = (beta, gamma, alpha)

        up: Vec3 = (0.0, 0.0, 1.0)
        if gravity is not None:
            g = _norm(gravity)
            if g > 0.5 * GRAVITY:
                up = (gravity[0] / g, gravity[1] / g, gravity[2] / g)

        yaw_rate = _deadzone(_dot(omega, up), self.deadzone_dps)
        pitch_rate = _deadzone(beta, self.deadzone_dps)

        speed = math.hypot(yaw_rate, pitch_rate)
        gain = self.pixels_per_degree * pointer_acceleration(
            speed, self.accel_threshold_dps, self.accel_factor
        )

        # Positive yaw about "up" turns the top edge left; positive pitch
        # lifts the top edge. Both should move the cursor left/up.
        raw_dx = -yaw_rate * gain * dt
        raw_dy = -pitch_rate * gain * dt

        a = self.smoothing
        self._sx = a * self._sx + (1 - a) * raw_dx
        self._sy = a * self._sy + (1 - a) * raw_dy
        return self._sx, self._sy

    def reset(self) -> None:
        self._sx = self._sy = 0.0


@dataclass
class SlidePointer:
    """Desk mouse: slide the phone flat on a surface like a real mouse.

    Double-integrating a phone accelerometer drifts badly, so this uses
    zero-velocity updates: when acceleration and rotation stay near zero for
    a few samples the phone is assumed to be at rest and velocity is reset.
    A slowly-adapting bias estimate removes sensor offset while at rest.
    This is the experimental mode; the gyro mode is far more precise.
    """

    pixels_per_meter: float = 9000.0
    accel_deadzone: float = 0.12
    still_accel: float = 0.18
    still_gyro_dps: float = 6.0
    still_samples: int = 6
    velocity_damping: float = 0.92
    bias_rate: float = 0.05
    max_speed: float = 1.5
    _vx: float = 0.0
    _vy: float = 0.0
    _bias: list[float] = field(default_factory=lambda: [0.0, 0.0])
    _still: int = 0

    @property
    def velocity(self) -> tuple[float, float]:
        return self._vx, self._vy

    def update(
        self,
        acceleration: Vec3,
        dt: float,
        rotation_rate: Vec3 | None = None,
    ) -> tuple[float, float]:
        ax = acceleration[0] - self._bias[0]
        ay = acceleration[1] - self._bias[1]
        gyro = _norm(rotation_rate) if rotation_rate is not None else 0.0

        if math.hypot(ax, ay) < self.still_accel and gyro < self.still_gyro_dps:
            self._still += 1
        else:
            self._still = 0

        if self._still >= self.still_samples:
            self._vx = self._vy = 0.0
            self._bias[0] += self.bias_rate * ax
            self._bias[1] += self.bias_rate * ay
            return 0.0, 0.0

        self._vx = (self._vx + _deadzone(ax, self.accel_deadzone) * dt) * self.velocity_damping
        self._vy = (self._vy + _deadzone(ay, self.accel_deadzone) * dt) * self.velocity_damping

        speed = math.hypot(self._vx, self._vy)
        if speed > self.max_speed:
            scale = self.max_speed / speed
            self._vx *= scale
            self._vy *= scale

        # Device +y (towards the top edge) is screen "up", i.e. negative y.
        return (
            self._vx * dt * self.pixels_per_meter,
            -self._vy * dt * self.pixels_per_meter,
        )

    def reset(self) -> None:
        self._vx = self._vy = 0.0
        self._still = 0
