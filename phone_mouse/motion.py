"""Turn raw phone sensor samples into cursor motion.

Device frame (W3C DeviceMotionEvent, also used by iOS): +x to the right of
the screen, +y towards the top edge, +z out of the screen towards the user.
``acceleration`` is linear acceleration in m/s^2 with gravity removed and
``accelerationIncludingGravity`` at rest points *up* (flat, screen up, it
reads about (0, 0, +9.81)). Rotation rates are deg/s, right-hand rule.

Browsers disagree on how ``rotationRate.alpha/beta/gamma`` map to device
axes. The spec says alpha=z, beta=x, gamma=y, which Chrome on Android
follows, but WebKit (every browser on iOS) reports alpha=x, beta=y,
gamma=z. :class:`AxisDetector` works out which one a phone is using from the
data itself.

Screen coordinates are +x right, +y down.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

Vec3 = tuple[float, float, float]

GRAVITY = 9.80665
DEG = math.pi / 180.0
AXIS_CONVENTIONS = ("w3c", "ios")


def _norm(v: Vec3) -> float:
    return math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])


def _dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a: Vec3, b: Vec3) -> Vec3:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _sub(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _scale(a: Vec3, k: float) -> Vec3:
    return (a[0] * k, a[1] * k, a[2] * k)


def _unit(v: Vec3) -> Vec3:
    n = _norm(v)
    return (v[0] / n, v[1] / n, v[2] / n) if n > 0 else (0.0, 0.0, 1.0)


def _deadzone(value: float, threshold: float) -> float:
    """Soft deadzone: zero inside the band, continuous outside it."""
    if abs(value) <= threshold:
        return 0.0
    return value - math.copysign(threshold, value)


def device_rotation(rot: Vec3, convention: str) -> Vec3:
    """Map browser (alpha, beta, gamma) rates to device-frame (x, y, z)."""
    alpha, beta, gamma = rot
    if convention == "ios":
        return (alpha, beta, gamma)
    return (beta, gamma, alpha)


def up_vector(gravity: Vec3 | None, linear: Vec3 | None = None) -> Vec3 | None:
    """Unit "up" vector in the device frame, or None if unusable.

    Subtracting linear acceleration from ``accelerationIncludingGravity``
    leaves the phone's own fused gravity estimate, which is far steadier
    than the raw accelerometer while the hand is moving.
    """
    if gravity is None:
        return None
    g = _sub(gravity, linear) if linear is not None else gravity
    n = _norm(g)
    if n < 0.5 * GRAVITY:
        return None
    return _scale(g, 1.0 / n)


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
class AxisDetector:
    """Decide which rotation-rate convention the browser uses.

    A world-fixed vector seen from a rotating phone changes as
    ``d(up)/dt = -omega x up``. Whichever convention's omega better predicts
    how the measured gravity direction actually turned is the right one.
    """

    min_rate_dps: float = 25.0
    min_samples: int = 15
    confidence: float = 3.0
    decay: float = 0.995
    max_turn_rate: float = 10.0
    error: dict[str, float] = field(default_factory=lambda: {c: 0.0 for c in AXIS_CONVENTIONS})
    samples: int = 0
    decided: str | None = None
    _prev_up: Vec3 | None = None

    def update(self, rot: Vec3, up: Vec3 | None, dt: float) -> None:
        prev, self._prev_up = self._prev_up, up
        if up is None or prev is None or dt <= 0:
            return
        if _norm(rot) < self.min_rate_dps:
            return
        measured = _scale(_sub(up, prev), 1.0 / dt)
        if _norm(measured) > self.max_turn_rate:
            return  # a dropped sample or glitch, not real rotation
        mid = _unit(((up[0] + prev[0]) / 2, (up[1] + prev[1]) / 2, (up[2] + prev[2]) / 2))
        for conv in AXIS_CONVENTIONS:
            omega = _scale(device_rotation(rot, conv), DEG)
            predicted = _scale(_cross(omega, mid), -1.0)
            diff = _sub(measured, predicted)
            self.error[conv] = self.decay * self.error[conv] + _dot(diff, diff)
        self.samples += 1
        if self.samples >= self.min_samples:
            w3c, ios = self.error["w3c"], self.error["ios"]
            if ios * self.confidence < w3c:
                self.decided = "ios"
            elif w3c * self.confidence < ios:
                self.decided = "w3c"

    @property
    def best(self) -> str | None:
        """The convention in use, once there is clear evidence for one."""
        return self.decided


@dataclass
class PointingFrame:
    """Which way the phone points, and how fast that direction is turning.

    Held like a remote (closer to flat), the top edge (+y) points at the
    screen; held upright like a camera, the back (-z) does. Rotation is
    decomposed relative to that pointing direction so that rolling the phone
    around it does not move the cursor, just like a laser pointer.
    """

    upright_enter_deg: float = 55.0
    upright_exit_deg: float = 45.0
    upright: bool = False

    @property
    def axis(self) -> Vec3:
        return (0.0, 0.0, -1.0) if self.upright else (0.0, 1.0, 0.0)

    def observe(self, up: Vec3) -> bool:
        """Update the pointing axis from gravity. Returns True if it changed."""
        top_elevation = math.degrees(math.asin(max(-1.0, min(1.0, abs(up[1])))))
        was = self.upright
        if not self.upright and top_elevation > self.upright_enter_deg:
            self.upright = True
        elif self.upright and top_elevation < self.upright_exit_deg:
            self.upright = False
        return was != self.upright

    def elevation(self, up: Vec3) -> float:
        """Angle of the pointing direction above the horizon, in degrees."""
        return math.degrees(math.asin(max(-1.0, min(1.0, _dot(up, self.axis)))))

    def rates(self, omega: Vec3, up: Vec3) -> tuple[float, float]:
        """(left-turn rate, upward rate) of the pointing direction in deg/s.

        Both are angular speeds across the field of view, so they map
        directly to cursor speed.
        """
        p = self.axis
        s = _dot(up, p)
        c = math.sqrt(max(1.0 - s * s, 0.25))
        turn_left = (_dot(omega, up) - _dot(omega, p) * s) / c
        tilt_up = _dot(omega, _cross(p, up)) / c
        return turn_left, tilt_up


@dataclass
class GyroPointer:
    """Air mouse: cursor speed follows how fast you sweep the phone."""

    pixels_per_degree: float = 18.0
    deadzone_dps: float = 1.5
    accel_threshold_dps: float = 60.0
    accel_factor: float = 0.6
    smoothing: float = 0.35
    frame: PointingFrame = field(default_factory=PointingFrame)
    _sx: float = 0.0
    _sy: float = 0.0

    def update(
        self,
        omega: Vec3,
        dt: float,
        up: Vec3 | None = None,
        pointer_accel: bool = True,
    ) -> tuple[float, float]:
        """``omega`` is the device-frame rotation rate (x, y, z) in deg/s."""
        up = up or (0.0, 0.0, 1.0)
        self.frame.observe(up)
        turn_left, tilt_up = self.frame.rates(omega, up)
        turn_left = _deadzone(turn_left, self.deadzone_dps)
        tilt_up = _deadzone(tilt_up, self.deadzone_dps)

        gain = self.pixels_per_degree
        if pointer_accel:
            gain *= pointer_acceleration(
                math.hypot(turn_left, tilt_up), self.accel_threshold_dps, self.accel_factor
            )

        a = self.smoothing
        self._sx = a * self._sx + (1 - a) * (-turn_left * gain * dt)
        self._sy = a * self._sy + (1 - a) * (-tilt_up * gain * dt)
        return self._sx, self._sy

    def reset(self) -> None:
        self._sx = self._sy = 0.0


@dataclass
class LaserPointer:
    """Laser pointer: where the phone points is where the cursor goes.

    Tracks absolute aim angles. Elevation comes straight from gravity (no
    drift); heading is integrated from the gyro and drifts slowly, which
    re-aiming corrects. The cursor position is ``anchor + tan(angle)``
    relative to the aim recorded when the anchor was set.
    """

    pixels_per_degree: float = 40.0
    max_angle_deg: float = 70.0
    invert_x: bool = False
    invert_y: bool = False
    frame: PointingFrame = field(default_factory=PointingFrame)
    heading: float = 0.0
    elevation: float = 0.0
    anchor: tuple[float, float] | None = None
    has_data: bool = False
    _ref: tuple[float, float] = (0.0, 0.0)
    _shown: tuple[float, float] = (0.0, 0.0)

    def update(self, omega: Vec3, dt: float, up: Vec3 | None) -> None:
        up = up or (0.0, 0.0, 1.0)
        if self.frame.observe(up):
            self.anchor = None
        turn_left, _ = self.frame.rates(omega, up)
        elevation = self.frame.elevation(up)
        cos_el = max(math.cos(math.radians(elevation)), 0.5)
        self.heading += turn_left / cos_el * dt
        self.elevation = elevation
        self.has_data = True

    def aim(self, cursor: tuple[float, float]) -> None:
        """Declare that the phone is currently pointing at ``cursor``."""
        self.anchor = cursor
        self._shown = cursor
        self._ref = (self.heading, self.elevation)

    def hold(self) -> None:
        """Keep the cursor where it was last shown while the aim moves."""
        if self.anchor is not None:
            self.aim(self._shown)

    def target(self) -> tuple[float, float]:
        assert self.anchor is not None
        k = self.pixels_per_degree / DEG
        lim = self.max_angle_deg
        d_heading = max(-lim, min(lim, self.heading - self._ref[0]))
        d_elev = max(-lim, min(lim, self.elevation - self._ref[1]))
        sx = 1 if self.invert_x else -1
        sy = 1 if self.invert_y else -1
        self._shown = (
            self.anchor[0] + sx * k * math.tan(d_heading * DEG),
            self.anchor[1] + sy * k * math.tan(d_elev * DEG),
        )
        return self._shown


@dataclass
class SlidePointer:
    """Desk mouse: slide the phone flat on a surface.

    This is the stroke tracker from the version whose directions matched the
    phone and which sat still at rest. Two desk-only changes sit on top of it:

    * A firm slide ends once the acceleration goes quiet, so the cursor does
      not keep the last speed, and a weak leftover acceleration cannot speed
      it up. A very gentle slide may coast briefly, because its acceleration
      dips into the noise and that dip is not a stop.
    * While the phone is lifted (tipped, or a clear upward acceleration on Z)
      the output is zero. Setting it flat again resumes tracking. There is
      no pressure sensor on the back of an iPhone that a web page can read.

    Position is the integral of horizontal acceleration, one stroke at a time.
    At rest the velocity is zero and the sensor bias is re-learned.
    """

    pixels_per_meter: float = 15000.0
    start_accel: float = 0.3
    still_accel: float = 0.12
    still_gyro_dps: float = 10.0
    still_time: float = 0.12
    end_fraction: float = 0.15
    reverse_fraction: float = 0.5
    min_reverse_speed: float = 0.05
    reverse_window: float = 0.5
    max_stroke_time: float = 2.0
    bias_rate: float = 0.03
    max_speed: float = 2.0
    # Lift / put-down, from accelerationIncludingGravity (m/s^2), not from
    # how still the other axes are.
    lift_gxy: float = 5.0
    lift_gz: float = 7.5
    down_gz: float = 8.6
    down_gxy: float = 3.2
    lift_az: float = 2.5
    lift_time: float = 0.08
    resume_time: float = 0.12
    moving: bool = False
    lifted: bool = False
    _v: list[float] = field(default_factory=lambda: [0.0, 0.0])
    _bias: list[float] = field(default_factory=lambda: [0.0, 0.0])
    _pending: list[float] = field(default_factory=lambda: [0.0, 0.0])
    _peak: float = 0.0
    _peak_a: float = 0.0
    _dir: tuple[float, float] = (0.0, 0.0)
    _last_peak: float = 0.0
    _since_stroke: float = math.inf
    _still_t: float = 0.0
    _stroke_t: float = 0.0
    _lift_t: float = 0.0
    _down_t: float = 0.0
    _weak_t: float = 0.0

    @property
    def velocity(self) -> tuple[float, float]:
        return self._v[0], self._v[1]

    def _end_stroke(self) -> None:
        self.moving = False
        self._last_peak = self._peak
        self._since_stroke = 0.0
        self._v = [0.0, 0.0]
        self._pending = [0.0, 0.0]
        self._peak = 0.0
        self._peak_a = 0.0
        self._weak_t = 0.0

    def _sense_lift(self, grav: Vec3 | None, az: float, dt: float) -> None:
        if grav is None:
            return
        gxy = math.hypot(grav[0], grav[1])
        gz = abs(grav[2])
        tipped = gxy >= self.lift_gxy and gz <= self.lift_gz
        rising = az >= self.lift_az
        if not self.lifted:
            if tipped or rising:
                self._lift_t += dt
                if self._lift_t >= self.lift_time:
                    self.lifted = True
                    self._down_t = 0.0
                    self._end_stroke()
            else:
                self._lift_t = 0.0
            return
        # Flat again. A hand on the phone keeps the other axes noisy, so
        # those are not part of this check.
        if gz >= self.down_gz and gxy <= self.down_gxy and az < self.lift_az:
            self._down_t += dt
            if self._down_t >= self.resume_time:
                self.lifted = False
                self._lift_t = 0.0
                self._down_t = 0.0
        else:
            self._down_t = 0.0

    def update(
        self,
        acceleration: Vec3,
        dt: float,
        omega: Vec3 | None = None,
        grav: Vec3 | None = None,
    ) -> tuple[float, float]:
        ax = acceleration[0] - self._bias[0]
        ay = acceleration[1] - self._bias[1]
        az = acceleration[2]
        a_mag = math.hypot(ax, ay)
        gyro = _norm(omega) if omega is not None else 0.0
        self._sense_lift(grav, az, dt)
        if self.lifted:
            return 0.0, 0.0

        self._since_stroke += dt
        if a_mag < self.still_accel and gyro < self.still_gyro_dps:
            self._still_t += dt
        else:
            self._still_t = 0.0

        # Quiet means the phone has stopped. A brisk slide ends here so the
        # cursor does not keep the last speed. A gentle slide (peak
        # acceleration under 0.8 m/s²) is allowed a short coast, because its
        # acceleration dips into the noise and that dip is not a stop.
        speed_now = math.hypot(*self._v)
        gentle = self._peak_a < 0.8
        coasting = (
            self.moving
            and gentle
            and self._peak > 0
            and speed_now > 0.5 * self._peak
            and self._still_t < 3 * self.still_time
        )
        if self._still_t >= self.still_time and not coasting:
            if self.moving:
                self._end_stroke()
            elif self._since_stroke > self.reverse_window:
                self._bias[0] += self.bias_rate * ax
                self._bias[1] += self.bias_rate * ay
            self._v = [0.0, 0.0]
            self._pending = [0.0, 0.0]
            return 0.0, 0.0

        # After a real push, leftover acceleration that still points the same
        # way is sensor bias. It must not speed the cursor up, and if it
        # persists the stroke ends. Braking still counts, so the cursor
        # follows the phone through the slowdown. Gentle slides never reach
        # this (their peak acceleration stays under 0.8) and keep integrating.
        ix, iy = ax, ay
        if self.moving and self._peak_a > 0.8 and self._peak > 0:
            along_a = ax * self._dir[0] + ay * self._dir[1]
            speed = math.hypot(*self._v)
            if along_a < 0 or speed <= 0.7 * self._peak:
                self._weak_t = 0.0
            elif a_mag < self.still_accel:
                # The push is over and acceleration is gone. Stop with the
                # phone instead of coasting at the last speed. A single quiet
                # sample at the acceleration zero-crossing is shorter than
                # this, so the braking half of a real slide still counts.
                self._weak_t += dt
                if self._weak_t >= 0.05:
                    self._end_stroke()
                    return 0.0, 0.0
            elif a_mag < self.start_accel:
                self._weak_t += dt
                ix, iy = 0.0, 0.0
                if self._weak_t >= 0.06:
                    self._end_stroke()
                    return 0.0, 0.0
            else:
                self._weak_t = 0.0

        self._v[0] += ix * dt
        self._v[1] += iy * dt
        vx, vy = self._v
        speed = math.hypot(vx, vy)

        if not self.moving:
            self._pending[0] += vx * dt
            self._pending[1] += vy * dt
            recent = self._since_stroke < self.reverse_window
            backwards = recent and vx * self._dir[0] + vy * self._dir[1] < 0
            if backwards:
                needed = max(self.reverse_fraction * self._last_peak, self.min_reverse_speed)
                if speed < needed:
                    return 0.0, 0.0
                self._pending = [0.0, 0.0]
            elif a_mag < self.start_accel or speed == 0:
                return 0.0, 0.0
            self.moving = True
            self._peak = speed
            self._peak_a = a_mag
            self._dir = (vx / speed, vy / speed)
            self._stroke_t = 0.0
            px, py = self._pending
            self._pending = [0.0, 0.0]
            return px * self.pixels_per_meter, -py * self.pixels_per_meter

        self._stroke_t += dt
        self._peak_a = max(self._peak_a, a_mag)
        if speed > self._peak and speed > 0:
            self._peak = speed
            self._dir = (vx / speed, vy / speed)
        along = vx * self._dir[0] + vy * self._dir[1] if speed else 0.0
        if (
            along <= 0
            or speed < self.end_fraction * self._peak
            or self._stroke_t > self.max_stroke_time
        ):
            self._end_stroke()
            return 0.0, 0.0

        if speed > self.max_speed:
            k = self.max_speed / speed
            self._v = [vx * k, vy * k]
            vx, vy = self._v

        # Device +y (towards the top edge) is screen "up", i.e. negative y.
        return vx * dt * self.pixels_per_meter, -vy * dt * self.pixels_per_meter

    def reset(self) -> None:
        """Forget motion in progress (e.g. while the phone is lifted)."""
        if self.moving:
            self._end_stroke()
        self._v = [0.0, 0.0]
        self._pending = [0.0, 0.0]
        self._still_t = 0.0
        self._weak_t = 0.0
