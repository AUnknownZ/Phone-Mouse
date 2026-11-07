import math

import pytest

from phone_mouse.controller import Controller
from phone_mouse.mouse import RecordingMouse
from sim import DT, desk_stroke_samples, rotation_samples

TILTED = (0.0, math.sin(math.radians(25)), math.cos(math.radians(25)))
TILT_UP = (1.0, 0.0, 0.0)
ROLL = (0.0, 1.0, 0.0)


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def ctl(clock):
    return Controller(RecordingMouse(), clock=clock)


def kinds(ctl):
    return [e[0] for e in ctl.mouse.events]


def browser_rot(omega, convention):
    x, y, z = omega
    return [x, y, z] if convention == "ios" else [z, x, y]


def gesture(ctl, clock, axis, rate, seconds, convention, up0=TILTED, active=True):
    """Feed a rotation as the phone's browser would report it."""
    replies = []
    up = up0
    for omega, up in rotation_samples(axis, rate, seconds, up0):
        clock.t += DT
        reply = ctl.handle(
            {
                "type": "motion",
                "dt": DT,
                "rot": browser_rot(omega, convention),
                "acc": [0.0, 0.0, 0.0],
                "grav": [9.81 * c for c in up],
                "active": active,
            }
        )
        if reply:
            replies.append(reply)
    return replies


def moved(ctl, since=0):
    xs = [e for e in ctl.mouse.events[since:] if e[0] == "move"]
    return sum(e[1] for e in xs), sum(e[2] for e in xs)


# ------------------------------------------------------------------ air mode


@pytest.mark.parametrize("convention,platform", [("ios", "ios"), ("w3c", "other")])
class TestAirDirections:
    """The cursor must follow the phone regardless of browser convention."""

    def test_tilt_up_moves_up_only(self, ctl, clock, convention, platform):
        ctl.handle({"type": "settings", "platform": platform})
        gesture(ctl, clock, TILT_UP, 40, 0.5, convention)
        dx, dy = moved(ctl)
        assert dy < -200
        assert abs(dx) < 0.05 * abs(dy)

    def test_turn_right_moves_right_only(self, ctl, clock, convention, platform):
        ctl.handle({"type": "settings", "platform": platform})
        gesture(ctl, clock, TILTED, -40, 0.5, convention)
        dx, dy = moved(ctl)
        assert dx > 200
        assert abs(dy) < 0.05 * dx

    def test_roll_does_not_move(self, ctl, clock, convention, platform):
        ctl.handle({"type": "settings", "platform": platform})
        gesture(ctl, clock, ROLL, 60, 0.5, convention)
        dx, dy = moved(ctl)
        assert abs(dx) < 10 and abs(dy) < 10


def test_ios_status_is_reported(ctl):
    reply = ctl.handle({"type": "settings", "platform": "ios"})
    assert reply == {"type": "status", "axes": "ios", "axesSource": "assumed"}


def test_auto_detection_fixes_wrong_platform_guess(ctl, clock):
    # The phone claims a standard browser but actually reports iOS axes.
    ctl.handle({"type": "settings", "platform": "other"})
    replies = gesture(ctl, clock, TILT_UP, 50, 0.6, "ios")
    assert {"type": "status", "axes": "ios", "axesSource": "detected"} in replies

    before = len(ctl.mouse.events)
    gesture(ctl, clock, (1.0, 0.0, 0.0), -40, 0.5, "ios")  # tilt down
    dx, dy = moved(ctl, before)
    assert dy > 200 and abs(dx) < 0.05 * dy


def test_manual_axes_override(ctl, clock):
    ctl.handle({"type": "settings", "platform": "ios", "axes": "w3c"})
    assert ctl.axes == ("w3c", "manual")


def test_air_mode_moves_only_while_active(ctl, clock):
    gesture(ctl, clock, TILT_UP, 40, 0.3, "w3c", active=False)
    assert ctl.mouse.events == []
    gesture(ctl, clock, TILT_UP, 40, 0.3, "w3c", active=True)
    assert ctl.mouse.y < 0


def test_click_briefly_freezes_air_motion(ctl, clock):
    ctl.handle({"type": "button", "button": "left", "action": "down"})
    gesture(ctl, clock, TILT_UP, 60, 0.1, "w3c")
    assert "move" not in kinds(ctl)
    gesture(ctl, clock, TILT_UP, 60, 0.3, "w3c")
    assert "move" in kinds(ctl)


class TestLaser:
    def setup(self, ctl):
        ctl.handle({"type": "settings", "modes": {"air": {"style": "laser"}}})
        ctl.mouse.set_position(800, 500)
        ctl.mouse.events.clear()

    def test_first_activation_aims_at_current_cursor(self, ctl, clock):
        self.setup(ctl)
        gesture(ctl, clock, TILTED, -40, 0.25, "w3c")  # 10 degrees right
        x, y = ctl.mouse.get_position()
        assert x - 800 == pytest.approx(40 * 57.3 * math.tan(math.radians(10)), rel=0.1)
        assert y == pytest.approx(500, abs=5)

    def test_absolute_aim_survives_pad_release(self, ctl, clock):
        self.setup(ctl)
        gesture(ctl, clock, TILT_UP, 0.0, 0.05, "w3c")  # anchor while level
        # Pad released, phone tilted up 10 degrees: the cursor stays put...
        gesture(ctl, clock, TILT_UP, 40, 0.25, "w3c", active=False)
        assert ctl.mouse.get_position() == (800, 500)
        # ...and pressing again puts it where the phone now points.
        gesture(ctl, clock, TILT_UP, 0.0, 0.05, "w3c", up0=_tilted(10))
        assert ctl.mouse.get_position()[1] < 500 - 300

    def test_reaim_anchors_at_cursor(self, ctl, clock):
        self.setup(ctl)
        gesture(ctl, clock, TILTED, -40, 0.25, "w3c")
        ctl.mouse.set_position(100, 100)
        ctl.handle({"type": "reaim"})
        gesture(ctl, clock, TILTED, 0.0, 0.05, "w3c")
        assert ctl.mouse.get_position() == pytest.approx((100, 100), abs=2)


def test_laser_reaim_before_any_motion_waits_for_data(ctl, clock):
    ctl.handle({"type": "settings", "modes": {"air": {"style": "laser"}}})
    ctl.mouse.set_position(960, 540)
    ctl.handle({"type": "reaim"})
    gesture(ctl, clock, TILTED, 0.0, 0.1, "w3c")
    assert ctl.mouse.get_position() == pytest.approx((960, 540), abs=2)


def _tilted(extra_deg):
    a = math.radians(25 + extra_deg)
    return (0.0, math.sin(a), math.cos(a))


# ----------------------------------------------------------------- desk mode


def test_desk_stroke_moves_without_bounce_back(ctl, clock):
    ctl.handle({"type": "settings", "mode": "desk"})
    xs = []
    for acc in desk_stroke_samples([(0.06, 0, 0.3, 0.5)], noise=0.04, lag_samples=2):
        ctl.handle({"type": "motion", "dt": DT, "rot": [0, 0, 0], "acc": list(acc),
                    "grav": [0, 0, 9.81]})
        xs.append(ctl.mouse.x)
    assert xs[-1] > 700
    assert max(xs) - xs[-1] < 20


def test_desk_lifted_ignores_motion(ctl):
    ctl.handle({"type": "settings", "mode": "desk"})
    for acc in desk_stroke_samples([(0.06, 0, 0.3, 0.5)]):
        ctl.handle({"type": "motion", "dt": DT, "rot": [0, 0, 0], "acc": list(acc),
                    "grav": [0, 0, 9.81], "active": False})
    assert ctl.mouse.events == []


def test_touch_mode_ignores_motion(ctl, clock):
    ctl.handle({"type": "settings", "mode": "touch"})
    gesture(ctl, clock, TILT_UP, 60, 0.3, "w3c")
    assert ctl.mouse.events == []


# ------------------------------------------------------------------ settings


def test_settings_are_per_mode(ctl):
    ctl.handle({
        "type": "settings",
        "mode": "touch",
        "modes": {
            "touch": {"sensitivity": 2, "invertX": True},
            "air": {"sensitivity": 0.5},
        },
    })
    ctl.handle({"type": "move", "dx": 5, "dy": -2})
    assert ctl.mouse.events == [("move", -20, -8)]
    assert ctl.settings.modes["air"].sensitivity == 0.5
    assert ctl.settings.modes["desk"].sensitivity == 1.0


def test_sensitivity_is_clamped(ctl):
    ctl.handle({"type": "settings", "modes": {"air": {"sensitivity": 1000}}})
    assert ctl.settings.modes["air"].sensitivity == 5.0


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
        {"type": "settings", "mode": "warp", "axes": "sideways", "modes": {"air": {"style": "x", "sensitivity": "fast"}}},
        {"type": "settings", "modes": ["air"]},
    ],
)
def test_malformed_messages_are_ignored(ctl, msg):
    ctl.handle(msg)
    assert ctl.mouse.events == []
    assert ctl.settings.mode == "air"
    assert ctl.settings.axes == "auto"
    assert ctl.settings.modes["air"].style == "mouse"
    assert ctl.settings.modes["air"].sensitivity == 1.0


# ------------------------------------------------------------------- buttons


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


def test_huge_moves_are_clamped(ctl):
    ctl.handle({"type": "settings", "mode": "touch"})
    ctl.handle({"type": "move", "dx": 1e9, "dy": -1e9})
    assert ctl.mouse.events == [("move", 400, -400)]


def test_scroll_behaves_like_a_wheel(ctl):
    for _ in range(4):
        ctl.handle({"type": "scroll", "dx": 0, "dy": -9})  # finger moves up
    assert ctl.mouse.events == [("scroll", 0, 1), ("scroll", 0, 1)]


def test_ping_is_answered(ctl):
    assert ctl.handle({"type": "ping", "t": 42}) == {"type": "pong", "t": 42}
