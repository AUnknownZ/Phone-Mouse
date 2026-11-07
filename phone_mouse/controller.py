"""Translate phone messages into mouse actions.

Wire protocol (JSON objects sent by the phone over the WebSocket):

``{"type": "settings", "mode": "air"|"desk"|"touch",
   "axes": "auto"|"w3c"|"ios", "platform": "ios"|"other",
   "modes": {"air": {"sensitivity": 1.0, "invertX": false, "invertY": false,
                     "style": "mouse"|"laser", "pointerAccel": true},
             "desk": {...}, "touch": {...}}}``
``{"type": "motion", "dt": 0.016, "rot": [alpha, beta, gamma],
   "acc": [x, y, z] | null, "grav": [x, y, z] | null, "active": true}``
   (``rot`` is the browser's raw rotationRate; see motion.py for axes)
``{"type": "reaim"}``                                  (laser: "I'm pointing at the cursor")
``{"type": "move", "dx": 3.5, "dy": -1.0}``            (touchpad, CSS px)
``{"type": "button", "button": "left", "action": "down"|"up"|"click"}``
``{"type": "scroll", "dx": 0, "dy": -12.0}``           (finger travel, CSS px)
``{"type": "ping", "t": 123}``                          (answered with pong)

The server may reply with ``{"type": "status", "axes": "ios"|"w3c",
"axesSource": "manual"|"detected"|"assumed"}``.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .motion import (
    AXIS_CONVENTIONS,
    AxisDetector,
    GyroPointer,
    LaserPointer,
    SlidePointer,
    SubpixelAccumulator,
    device_rotation,
    up_vector,
)
from .mouse import BUTTONS, MouseBackend

MODES = ("air", "desk", "touch")
AIR_STYLES = ("mouse", "laser")

MAX_DT = 0.1
MAX_STEP_PX = 400
TOUCH_GAIN = 2.0
SCROLL_PX_PER_STEP = 18.0
CLICK_FREEZE_S = 0.15


def _num(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    value = float(value)
    return value if math.isfinite(value) else default


def _vec3(value: Any) -> tuple[float, float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return None
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value):
        return None
    vec = tuple(float(v) for v in value)
    if not all(math.isfinite(v) for v in vec):
        return None
    return vec  # type: ignore[return-value]


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


@dataclass
class ModeSettings:
    sensitivity: float = 1.0
    invert_x: bool = False
    invert_y: bool = False
    style: str = "mouse"
    pointer_accel: bool = True

    def apply(self, msg: dict) -> None:
        if "sensitivity" in msg:
            self.sensitivity = _clamp(_num(msg["sensitivity"], self.sensitivity), 0.1, 5.0)
        if isinstance(msg.get("invertX"), bool):
            self.invert_x = msg["invertX"]
        if isinstance(msg.get("invertY"), bool):
            self.invert_y = msg["invertY"]
        if msg.get("style") in AIR_STYLES:
            self.style = msg["style"]
        if isinstance(msg.get("pointerAccel"), bool):
            self.pointer_accel = msg["pointerAccel"]


@dataclass
class Settings:
    mode: str = "air"
    axes: str = "auto"
    platform: str = "other"
    modes: dict[str, ModeSettings] = field(
        default_factory=lambda: {m: ModeSettings() for m in MODES}
    )

    @property
    def current(self) -> ModeSettings:
        return self.modes[self.mode]


class Controller:
    def __init__(self, mouse: MouseBackend, clock: Callable[[], float] = time.monotonic) -> None:
        self.mouse = mouse
        self.clock = clock
        self.settings = Settings()
        self.gyro = GyroPointer()
        self.laser = LaserPointer()
        self.slide = SlidePointer()
        self.axis_detector = AxisDetector()
        self._move_acc = SubpixelAccumulator()
        self._scroll_acc = SubpixelAccumulator()
        self._held: set[str] = set()
        self._freeze_until = 0.0
        self._last_target: tuple[int, int] | None = None
        self._reported_axes: tuple[str, str] | None = None
        self._reported_lifted: bool | None = None

    def handle(self, msg: Any) -> dict | None:
        """Apply one message. Returns a reply to send back, if any."""
        if not isinstance(msg, dict):
            return None
        kind = msg.get("type")
        if kind == "motion":
            return self._on_motion(msg)
        if kind == "move":
            self._on_touch_move(msg)
        elif kind == "button":
            self._on_button(msg)
        elif kind == "scroll":
            self._on_scroll(msg)
        elif kind == "settings":
            return self._on_settings(msg)
        elif kind == "reaim":
            if self.laser.has_data:
                self.laser.aim(self.mouse.get_position())
            else:
                self.laser.anchor = None  # aim at the cursor once data arrives
            self._last_target = None
        elif kind == "ping":
            return {"type": "pong", "t": msg.get("t")}
        return None

    def release_all(self) -> None:
        """Let go of held buttons, e.g. when the phone disconnects mid-drag."""
        for button in sorted(self._held):
            self.mouse.release(button)
        self._held.clear()
        self.gyro.reset()
        self.slide.reset()
        self._move_acc.reset()

    @property
    def axes(self) -> tuple[str, str]:
        """(convention, source) used to read the phone's rotation rates."""
        s = self.settings
        if s.axes in AXIS_CONVENTIONS:
            return s.axes, "manual"
        detected = self.axis_detector.best
        if detected:
            return detected, "detected"
        return ("ios" if s.platform == "ios" else "w3c"), "assumed"

    def _status(self, lifted: bool | None = None) -> dict | None:
        payload: dict = {}
        axes = self.axes
        if axes != self._reported_axes:
            self._reported_axes = axes
            payload["axes"] = axes[0]
            payload["axesSource"] = axes[1]
        if lifted is not None and lifted != self._reported_lifted:
            self._reported_lifted = lifted
            payload["lifted"] = lifted
        if not payload:
            return None
        payload["type"] = "status"
        return payload

    def _emit_move(self, dx: float, dy: float, mode: str) -> None:
        s = self.settings.modes[mode]
        dx *= s.sensitivity * (-1 if s.invert_x else 1)
        dy *= s.sensitivity * (-1 if s.invert_y else 1)
        dx = _clamp(dx, -MAX_STEP_PX, MAX_STEP_PX)
        dy = _clamp(dy, -MAX_STEP_PX, MAX_STEP_PX)
        ix, iy = self._move_acc.take(dx, dy)
        if ix or iy:
            self.mouse.move(ix, iy)

    def _on_motion(self, msg: dict) -> dict | None:
        mode = self.settings.mode
        rot = _vec3(msg.get("rot"))
        dt = _clamp(_num(msg.get("dt"), 0.016), 0.0, MAX_DT)
        if mode == "touch" or rot is None or dt <= 0:
            return None
        active = msg.get("active", True) is not False
        acc = _vec3(msg.get("acc"))
        up = up_vector(_vec3(msg.get("grav")), acc)

        self.axis_detector.update(rot, up, dt)
        omega = device_rotation(rot, self.axes[0])

        if mode == "air":
            self._air(omega, dt, up, active)
        elif acc is not None:
            dx, dy = self.slide.update(acc, dt, omega, up)
            if not active:
                self.slide.reset()
            elif not self.slide.lifted:
                self._emit_move(dx, dy, "desk")
        lifted = self.slide.lifted if mode == "desk" else None
        return self._status(lifted)

    def _air(self, omega, dt, up, active: bool) -> None:
        s = self.settings.modes["air"]
        frozen = self.clock() < self._freeze_until
        # Always track aim so switching to laser style or re-aiming starts
        # from the phone's true orientation.
        self.laser.update(omega, dt, up)
        if s.style == "laser":
            if not active:
                return
            if self.laser.anchor is None:
                self.laser.aim(self.mouse.get_position())
            if frozen:
                self.laser.hold()
                return
            self.laser.pixels_per_degree = LaserPointer.pixels_per_degree * s.sensitivity
            self.laser.invert_x = s.invert_x
            self.laser.invert_y = s.invert_y
            tx, ty = self.laser.target()
            target = (round(tx), round(ty))
            if target != self._last_target:
                self.mouse.set_position(*target)
                self._last_target = target
            return

        dx, dy = self.gyro.update(omega, dt, up, s.pointer_accel)
        if active and not frozen:
            self._emit_move(dx, dy, "air")
        else:
            self.gyro.reset()
            self._move_acc.reset()

    def _on_touch_move(self, msg: dict) -> None:
        self._emit_move(
            _num(msg.get("dx")) * TOUCH_GAIN,
            _num(msg.get("dy")) * TOUCH_GAIN,
            "touch",
        )

    def _on_button(self, msg: dict) -> None:
        button = msg.get("button")
        if button not in BUTTONS:
            return
        action = msg.get("action")
        # Pressing a button on the screen jolts the phone; ignore that
        # motion briefly so clicks land where you were pointing.
        self._freeze_until = self.clock() + CLICK_FREEZE_S
        if action == "down":
            if button not in self._held:
                self._held.add(button)
                self.mouse.press(button)
        elif action == "up":
            if button in self._held:
                self._held.discard(button)
                self.mouse.release(button)
        elif action == "click":
            count = int(_clamp(_num(msg.get("count"), 1), 1, 3))
            self.mouse.click(button, count)

    def _on_scroll(self, msg: dict) -> None:
        # The strip behaves like a physical wheel: rolling it up (finger
        # moving up the screen) scrolls up, and the OS applies its own
        # "natural scrolling" preference just as it would for a real mouse.
        sx = _num(msg.get("dx")) / SCROLL_PX_PER_STEP
        sy = -_num(msg.get("dy")) / SCROLL_PX_PER_STEP
        ix, iy = self._scroll_acc.take(_clamp(sx, -20, 20), _clamp(sy, -20, 20))
        if ix or iy:
            self.mouse.scroll(ix, iy)

    def _on_settings(self, msg: dict) -> dict | None:
        s = self.settings
        mode = msg.get("mode")
        if mode in MODES and mode != s.mode:
            s.mode = mode
            self.gyro.reset()
            self.slide.reset()
            self._move_acc.reset()
        if msg.get("axes") in ("auto", *AXIS_CONVENTIONS):
            s.axes = msg["axes"]
        if msg.get("platform") in ("ios", "other"):
            s.platform = msg["platform"]
        modes = msg.get("modes")
        if isinstance(modes, dict):
            for name, values in modes.items():
                if name in MODES and isinstance(values, dict):
                    s.modes[name].apply(values)
        self.laser.hold()
        self._last_target = None
        return self._status()
