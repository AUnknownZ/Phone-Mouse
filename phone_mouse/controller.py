"""Translate phone messages into mouse actions.

Wire protocol (JSON objects sent by the phone over the WebSocket):

``{"type": "settings", "mode": "air"|"desk"|"touch", "sensitivity": 1.0,
   "invertX": false, "invertY": false}``
``{"type": "motion", "dt": 0.016, "rot": [alpha, beta, gamma],
   "acc": [x, y, z] | null, "grav": [x, y, z] | null, "active": true}``
``{"type": "move", "dx": 3.5, "dy": -1.0}``            (touchpad, CSS px)
``{"type": "button", "button": "left", "action": "down"|"up"|"click"}``
``{"type": "scroll", "dx": 0, "dy": -12.0}``           (finger travel, CSS px)
``{"type": "ping", "t": 123}``                          (answered with pong)
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from .motion import GyroPointer, SlidePointer, SubpixelAccumulator
from .mouse import BUTTONS, MouseBackend

MODES = ("air", "desk", "touch")

MAX_DT = 0.1
MAX_STEP_PX = 400
TOUCH_GAIN = 2.0
SCROLL_PX_PER_STEP = 18.0


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
class Settings:
    mode: str = "air"
    sensitivity: float = 1.0
    invert_x: bool = False
    invert_y: bool = False


class Controller:
    def __init__(self, mouse: MouseBackend) -> None:
        self.mouse = mouse
        self.settings = Settings()
        self.gyro = GyroPointer()
        self.slide = SlidePointer()
        self._move_acc = SubpixelAccumulator()
        self._scroll_acc = SubpixelAccumulator()
        self._held: set[str] = set()

    def handle(self, msg: Any) -> dict | None:
        """Apply one message. Returns a reply to send back, if any."""
        if not isinstance(msg, dict):
            return None
        kind = msg.get("type")
        if kind == "motion":
            self._on_motion(msg)
        elif kind == "move":
            self._on_touch_move(msg)
        elif kind == "button":
            self._on_button(msg)
        elif kind == "scroll":
            self._on_scroll(msg)
        elif kind == "settings":
            self._on_settings(msg)
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

    def _emit_move(self, dx: float, dy: float) -> None:
        s = self.settings
        dx *= s.sensitivity * (-1 if s.invert_x else 1)
        dy *= s.sensitivity * (-1 if s.invert_y else 1)
        dx = _clamp(dx, -MAX_STEP_PX, MAX_STEP_PX)
        dy = _clamp(dy, -MAX_STEP_PX, MAX_STEP_PX)
        ix, iy = self._move_acc.take(dx, dy)
        if ix or iy:
            self.mouse.move(ix, iy)

    def _on_motion(self, msg: dict) -> None:
        mode = self.settings.mode
        if mode == "touch":
            return
        rot = _vec3(msg.get("rot"))
        dt = _clamp(_num(msg.get("dt"), 0.016), 0.0, MAX_DT)
        if rot is None or dt <= 0:
            return
        active = msg.get("active", True) is not False

        if mode == "air":
            dx, dy = self.gyro.update(rot, dt, _vec3(msg.get("grav")))
        else:
            acc = _vec3(msg.get("acc"))
            if acc is None:
                return
            dx, dy = self.slide.update(acc, dt, rot)

        if active:
            self._emit_move(dx, dy)
        else:
            self.gyro.reset()
            self._move_acc.reset()

    def _on_touch_move(self, msg: dict) -> None:
        self._emit_move(
            _num(msg.get("dx")) * TOUCH_GAIN,
            _num(msg.get("dy")) * TOUCH_GAIN,
        )

    def _on_button(self, msg: dict) -> None:
        button = msg.get("button")
        if button not in BUTTONS:
            return
        action = msg.get("action")
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

    def _on_settings(self, msg: dict) -> None:
        s = self.settings
        mode = msg.get("mode")
        if mode in MODES and mode != s.mode:
            s.mode = mode
            self.gyro.reset()
            self.slide.reset()
            self._move_acc.reset()
        if "sensitivity" in msg:
            s.sensitivity = _clamp(_num(msg["sensitivity"], s.sensitivity), 0.1, 5.0)
        if isinstance(msg.get("invertX"), bool):
            s.invert_x = msg["invertX"]
        if isinstance(msg.get("invertY"), bool):
            s.invert_y = msg["invertY"]
