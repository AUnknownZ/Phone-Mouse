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

    A phone has no optical sensor, so position comes from integrating the
    accelerometer twice. Velocity is integrated with no damping, so cursor
    travel follows the distance the phone moved, including the glide after
    the push. Braking can cancel that velocity but cannot reverse it, so the
    cursor does not bounce back. While the phone rests, velocity is zero and
    the sensor bias is re-estimated.

    Lifting the phone off the desk (tilting it, or a clear upward
    acceleration) freezes the cursor until it is set back down flat and still.
    """

    pixels_per_meter: float = 15000.0
    start_accel: float = 0.2
    still_accel: float = 0.12
    still_gyro_dps: float = 10.0
    still_time: float = 0.1
    end_speed: float = 0.02
    bias_rate: float = 0.03
    max_speed: float = 2.0
    # Lifted off the desk: tilted, or a clear upward acceleration.
    lift_tilt_deg: float = 20.0
    resume_tilt_deg: float = 10.0
    lift_az: float = 3.5
    resume_time: float = 0.08
    moving: bool = False
    lifted: bool = False
    _v: list[float] = field(default_factory=lambda: [0.0, 0.0])
    _bias: list[float] = field(default_factory=lambda: [0.0, 0.0])
    _dir: tuple[float, float] = (0.0, 0.0)
    _peak: float = 0.0
    _still_t: float = 0.0
    _down_t: float = 0.0
    _brake_t: float = 0.0

    @property
    def velocity(self) -> tuple[float, float]:
        return self._v[0], self._v[1]

    def _stop(self, brake: bool = False) -> None:
        self.moving = False
        self._v = [0.0, 0.0]
        self._peak = 0.0
        if brake:
            # The deceleration that just stopped the phone is still arriving.
            # Ignore it until it dies down; the next push starts after that.
            self._brake_t = 1.0
        else:
            self._dir = (0.0, 0.0)
            self._brake_t = 0.0

    @staticmethod
    def _tilt_deg(up: Vec3 | None) -> float:
        """Angle between the screen and the desk. 0 means lying flat, screen up."""
        if up is None:
            return 0.0
        return math.degrees(math.acos(max(-1.0, min(1.0, up[2]))))

    def _sense_lift(self, az: float, gyro: float, up: Vec3 | None, a_mag: float, dt: float) -> None:
        tilt = self._tilt_deg(up)
        if not self.lifted:
            if tilt >= self.lift_tilt_deg or az >= self.lift_az:
                self.lifted = True
                self._down_t = 0.0
                self._stop()
            return
        # Back on the desk only once it is flat and quiet. Moving it through
        # the air, even held flat, keeps the cursor frozen.
        settled = (
            tilt <= self.resume_tilt_deg
            and abs(az) < 1.0
            and a_mag < self.still_accel
            and gyro < self.still_gyro_dps
        )
        if settled:
            self._down_t += dt
            if self._down_t >= self.resume_time:
                self.lifted = False
                self._down_t = 0.0
        else:
            self._down_t = 0.0

    def update(
        self,
        acceleration: Vec3,
        dt: float,
        omega: Vec3 | None = None,
        up: Vec3 | None = None,
    ) -> tuple[float, float]:
        ax = acceleration[0] - self._bias[0]
        ay = acceleration[1] - self._bias[1]
        az = acceleration[2]
        a_mag = math.hypot(ax, ay)
        gyro = _norm(omega) if omega is not None else 0.0
        self._sense_lift(az, gyro, up, a_mag, dt)
        if self.lifted:
            return 0.0, 0.0

        quiet = a_mag < self.still_accel and gyro < self.still_gyro_dps
        self._still_t = self._still_t + dt if quiet else 0.0
        speed = math.hypot(*self._v)
        # Quiet and no longer sliding: at rest. Quiet but still fast: gliding,
        # so keep the velocity instead of dropping the cursor mid-move.
        if self._still_t >= self.still_time and speed < self.end_speed:
            if not self.moving:
                self._bias[0] += self.bias_rate * ax
                self._bias[1] += self.bias_rate * ay
            self._stop()
            return 0.0, 0.0

        if self._brake_t > 0.0:
            # Hold until the braking acceleration itself dies down, so the
            # tail of a stop can't become a move in the opposite direction.
            opposing = a_mag >= self.still_accel and ax * self._dir[0] + ay * self._dir[1] < 0
            if opposing:
                return 0.0, 0.0
            self._brake_t = 0.0
            self._dir = (0.0, 0.0)

        if not self.moving:
            if a_mag < self.start_accel:
                return 0.0, 0.0
            self.moving = True

        self._v[0] += ax * dt
        self._v[1] += ay * dt
        vx, vy = self._v
        speed = math.hypot(vx, vy)
        if speed == 0:
            self._stop()
            return 0.0, 0.0
        # Braking may shrink the velocity but cannot flip it.
        if self._peak > 0.05 and vx * self._dir[0] + vy * self._dir[1] < 0:
            self._stop(brake=True)
            return 0.0, 0.0
        self._dir = (vx / speed, vy / speed)
        self._peak = max(self._peak, speed)
        # Quiet and well below the speed this push reached: the phone has
        # stopped (or only a little residual velocity is left). A real glide
        # keeps its speed, so it is not cut off here.
        if quiet and self._peak > 0.05 and speed < 0.3 * self._peak:
            self._stop(brake=True)
            return 0.0, 0.0
        # Only a slowing phone has stopped. The first samples of a push are
        # slow too, and dropping them is what made the cursor feel late.
        if speed < self.end_speed and ax * vx + ay * vy <= 0:
            self._stop(brake=True)
            return 0.0, 0.0
        if speed > self.max_speed:
            k = self.max_speed / speed
            self._v = [vx * k, vy * k]
            vx, vy = self._v

        # Device +y (towards the top edge) is screen "up", i.e. negative y.
        return vx * dt * self.pixels_per_meter, -vy * dt * self.pixels_per_meter

    def reset(self) -> None:
        """Forget motion in progress."""
        self._stop()
        self._still_t = 0.0
