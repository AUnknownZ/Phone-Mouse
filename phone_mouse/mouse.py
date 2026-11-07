"""Mouse output backends."""

from __future__ import annotations

from typing import Protocol

BUTTONS = ("left", "right", "middle")


class MouseBackend(Protocol):
    def move(self, dx: int, dy: int) -> None: ...
    def press(self, button: str) -> None: ...
    def release(self, button: str) -> None: ...
    def click(self, button: str, count: int = 1) -> None: ...
    def scroll(self, dx: int, dy: int) -> None: ...
    def get_position(self) -> tuple[int, int]: ...
    def set_position(self, x: int, y: int) -> None: ...


class PynputMouse:
    """Drives the real OS cursor (macOS, Windows, Linux/X11).

    On macOS the terminal running the server needs Accessibility permission
    (System Settings > Privacy & Security > Accessibility).
    """

    def __init__(self) -> None:
        from pynput.mouse import Button, Controller

        self._mouse = Controller()
        self._buttons = {
            "left": Button.left,
            "right": Button.right,
            "middle": Button.middle,
        }

    def move(self, dx: int, dy: int) -> None:
        self._mouse.move(dx, dy)

    def press(self, button: str) -> None:
        self._mouse.press(self._buttons[button])

    def release(self, button: str) -> None:
        self._mouse.release(self._buttons[button])

    def click(self, button: str, count: int = 1) -> None:
        self._mouse.click(self._buttons[button], count)

    def scroll(self, dx: int, dy: int) -> None:
        self._mouse.scroll(dx, dy)

    def get_position(self) -> tuple[int, int]:
        x, y = self._mouse.position
        return int(x), int(y)

    def set_position(self, x: int, y: int) -> None:
        self._mouse.position = (x, y)


class RecordingMouse:
    """In-memory backend for tests and ``--dry-run``."""

    def __init__(self, verbose: bool = False) -> None:
        self.events: list[tuple] = []
        self.x = 0
        self.y = 0
        self.held: set[str] = set()
        self.verbose = verbose

    def _log(self, *event) -> None:
        self.events.append(event)
        if self.verbose:
            print("[dry-run]", *event, flush=True)

    def move(self, dx: int, dy: int) -> None:
        self.x += dx
        self.y += dy
        self._log("move", dx, dy)

    def press(self, button: str) -> None:
        self.held.add(button)
        self._log("press", button)

    def release(self, button: str) -> None:
        self.held.discard(button)
        self._log("release", button)

    def click(self, button: str, count: int = 1) -> None:
        self._log("click", button, count)

    def scroll(self, dx: int, dy: int) -> None:
        self._log("scroll", dx, dy)

    def get_position(self) -> tuple[int, int]:
        return self.x, self.y

    def set_position(self, x: int, y: int) -> None:
        self.x, self.y = x, y
        self._log("moveto", x, y)
