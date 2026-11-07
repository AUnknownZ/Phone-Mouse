import pytest

from phone_mouse.controller import Controller
from phone_mouse.mouse import RecordingMouse


@pytest.fixture
def ctl():
    return Controller(RecordingMouse())


def kinds(ctl):
    return [e[0] for e in ctl.mouse.events]


def test_button_down_up_maps_to_press_release(ctl):
    ctl.handle({"type": "button", "button": "left", "action": "down"})
    ctl.handle({"type": "button", "button": "left", "action": "down"})
    ctl.handle({"type": "button", "button": "left", "action": "up"})
    assert ctl.mouse.events == [("press", "left"), ("release", "left")]


def test_click_count_is_clamped(ctl):
    ctl.handle({"type": "button", "button": "right", "action": "click", "count": 99})
    assert ctl.mouse.events == [("click", "right", 3)]


def test_release_all_lets_go_of_held_buttons(ctl):
    ctl.handle({"type": "button", "button": "left", "action": "down"})
    ctl.handle({"type": "button", "button": "middle", "action": "down"})
    ctl.release_all()
    assert ctl.mouse.held == set()
    ctl.release_all()
    assert kinds(ctl).count("release") == 2


@pytest.mark.parametrize(
    "msg",
    [
        None,
        [],
        "hello",
        {"type": "button", "button": "evil", "action": "down"},
        {"type": "motion", "rot": "nope"},
        {"type": "motion", "rot": [1, 2]},
        {"type": "motion", "rot": [float("nan"), 0, 0]},
        {"type": "move", "dx": "1e9", "dy": None},
        {"type": "settings", "mode": "warp", "sensitivity": "fast"},
    ],
)
def test_malformed_messages_are_ignored(ctl, msg):
    ctl.handle(msg)
    assert ctl.mouse.events == []
    assert ctl.settings.mode == "air"


def test_touch_move_is_scaled_and_sensitive(ctl):
    ctl.handle({"type": "settings", "mode": "touch", "sensitivity": 2})
    ctl.handle({"type": "move", "dx": 5, "dy": -2})
    assert ctl.mouse.events == [("move", 20, -8)]


def test_invert_axes(ctl):
    ctl.handle({"type": "settings", "invertX": True, "invertY": True})
    ctl.handle({"type": "move", "dx": 5, "dy": 5})
    assert ctl.mouse.events == [("move", -10, -10)]


def test_huge_moves_are_clamped(ctl):
    ctl.handle({"type": "move", "dx": 1e9, "dy": -1e9})
    assert ctl.mouse.events == [("move", 400, -400)]


def test_sensitivity_is_clamped(ctl):
    ctl.handle({"type": "settings", "sensitivity": 1000})
    assert ctl.settings.sensitivity == 5.0


def test_air_mode_moves_only_while_active(ctl):
    sample = {"type": "motion", "dt": 0.016, "rot": [-60, 0, 0], "grav": [0, 0, 9.8]}
    for _ in range(10):
        ctl.handle(dict(sample, active=False))
    assert ctl.mouse.events == []
    for _ in range(10):
        ctl.handle(dict(sample, active=True))
    assert ctl.mouse.x > 0
    assert ctl.mouse.y == 0


def test_desk_mode_uses_accelerometer(ctl):
    ctl.handle({"type": "settings", "mode": "desk"})
    for _ in range(15):
        ctl.handle({"type": "motion", "dt": 0.016, "rot": [0, 0, 0], "acc": [3, 0, 0]})
    assert ctl.mouse.x > 0


def test_touch_mode_ignores_motion(ctl):
    ctl.handle({"type": "settings", "mode": "touch"})
    for _ in range(10):
        ctl.handle({"type": "motion", "dt": 0.016, "rot": [-90, 90, 0], "acc": [3, 3, 0]})
    assert ctl.mouse.events == []


def test_scroll_behaves_like_a_wheel(ctl):
    for _ in range(4):
        ctl.handle({"type": "scroll", "dx": 0, "dy": -9})  # finger moves up
    assert ctl.mouse.events == [("scroll", 0, 1), ("scroll", 0, 1)]


def test_ping_is_answered(ctl):
    assert ctl.handle({"type": "ping", "t": 42}) == {"type": "pong", "t": 42}
