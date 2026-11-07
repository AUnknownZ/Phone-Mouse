import math

import pytest

from phone_mouse.motion import (
    AxisDetector,
    GyroPointer,
    LaserPointer,
    PointingFrame,
    SlidePointer,
    SubpixelAccumulator,
    device_rotation,
    pointer_acceleration,
    up_vector,
)
from sim import DT, desk_stroke_samples, rotation_samples

FLAT = (0.0, 0.0, 1.0)  # lying screen-up / held like a remote
UPRIGHT = (0.0, 1.0, 0.0)  # held vertically, screen facing the user
TILTED = (0.0, math.sin(math.radians(30)), math.cos(math.radians(30)))  # remote aimed 30° up

# Device-frame axes for the motions a user makes while pointing.
TILT_UP = (1.0, 0.0, 0.0)  # about x: top edge rises
ROLL = (0.0, 1.0, 0.0)  # about y: twisting around the top-edge axis


def to_browser(omega, convention):
    """Inverse of device_rotation: what a browser would report."""
    x, y, z = omega
    return (x, y, z) if convention == "ios" else (z, x, y)


def sweep(pointer, axis, rate, seconds, up0):
    total = [0.0, 0.0]
    for omega, up in rotation_samples(axis, rate, seconds, up0):
        dx, dy = pointer.update(omega, DT, up)
        total[0] += dx
        total[1] += dy
    return total


def test_subpixel_accumulator_carries_fractions():
    acc = SubpixelAccumulator()
    moves = [acc.take(0.4, -0.4) for _ in range(5)]
    assert sum(m[0] for m in moves) == 2
    assert sum(m[1] for m in moves) == -2


def test_pointer_acceleration_curve():
    assert pointer_acceleration(10, 60, 0.6) == 1.0
    assert pointer_acceleration(120, 60, 0.6) == pytest.approx(1.6)


@pytest.mark.parametrize("convention", ["w3c", "ios"])
def test_device_rotation_round_trip(convention):
    assert device_rotation(to_browser((1.0, 2.0, 3.0), convention), convention) == (1.0, 2.0, 3.0)


def test_up_vector_prefers_fused_gravity():
    up = up_vector((0.0, 3.0, 9.8), (0.0, 3.0, 0.0))
    assert up == pytest.approx((0.0, 0.0, 1.0))
    assert up_vector((0.0, 0.0, 1.0)) is None
    assert up_vector(None) is None


class TestAxisDetector:
    @pytest.mark.parametrize("truth", ["w3c", "ios"])
    @pytest.mark.parametrize("axis", [TILT_UP, ROLL, (0.0, 0.0, 1.0), (1.0, 1.0, 0.3)])
    def test_identifies_convention_from_how_gravity_turns(self, truth, axis):
        det = AxisDetector()
        for omega, up in rotation_samples(axis, 60, 1.0, TILTED):
            det.update(to_browser(omega, truth), up, DT)
        assert det.best == truth

    def test_decision_survives_glitches(self):
        det = AxisDetector()
        up = TILTED
        for omega, up in rotation_samples(TILT_UP, 60, 0.5, TILTED):
            det.update(to_browser(omega, "ios"), up, DT)
        assert det.best == "ios"
        det.update((80.0, 0.0, 0.0), UPRIGHT, DT)  # jump: dropped samples
        det.update((80.0, 0.0, 0.0), FLAT, DT)
        assert det.best == "ios"

    def test_undecided_without_rotation(self):
        det = AxisDetector()
        for _ in range(100):
            det.update((0.5, -0.3, 0.2), FLAT, DT)
        assert det.best is None


class TestPointingFrame:
    def test_switches_to_back_camera_axis_when_upright(self):
        f = PointingFrame()
        assert not f.upright
        f.observe(UPRIGHT)
        assert f.upright
        f.observe(TILTED)
        assert not f.upright


class TestGyroPointer:
    @pytest.mark.parametrize("up0", [FLAT, TILTED, UPRIGHT])
    def test_turning_right_moves_right(self, up0):
        # Turning right = clockwise seen from above = negative rotation
        # about the world vertical, whatever the grip.
        dx, dy = sweep(GyroPointer(), up0, -40, 0.5, up0)
        assert dx > 200
        assert abs(dy) < 0.05 * dx

    @pytest.mark.parametrize("up0", [FLAT, TILTED, UPRIGHT])
    def test_tilting_up_moves_up(self, up0):
        dx, dy = sweep(GyroPointer(), TILT_UP, 40, 0.5, up0)
        assert dy < -200
        assert abs(dx) < 0.05 * abs(dy)

    @pytest.mark.parametrize("up0", [FLAT, TILTED])
    def test_rolling_does_not_move(self, up0):
        dx, dy = sweep(GyroPointer(), ROLL, 60, 0.5, up0)
        assert abs(dx) < 5 and abs(dy) < 5

    def test_deadzone_suppresses_hand_tremor(self):
        p = GyroPointer()
        out = [p.update((0.8, -1.0, 0.5), DT, FLAT) for _ in range(30)]
        assert out[-1] == (0.0, 0.0)

    def test_scale_is_pixels_per_degree(self):
        p = GyroPointer(smoothing=0, deadzone_dps=0, pixels_per_degree=10)
        dx, _ = p.update((0.0, 0.0, -20.0), 0.5, FLAT, pointer_accel=False)
        assert dx == pytest.approx(100.0)


class TestLaserPointer:
    def test_cursor_tracks_aim_angle(self):
        laser = LaserPointer(pixels_per_degree=40)
        laser.update((0, 0, 0), DT, TILTED)
        laser.aim((500.0, 400.0))
        # Turn right 10 degrees.
        up = TILTED
        for omega, up in rotation_samples(TILTED, -40, 0.25, TILTED):
            laser.update(omega, DT, up)
        x, y = laser.target()
        expected = 40 / (math.pi / 180) * math.tan(math.radians(10))
        assert x - 500 == pytest.approx(expected, rel=0.05)
        assert y == pytest.approx(400, abs=3)

    def test_pointing_back_returns_to_same_spot(self):
        laser = LaserPointer()
        laser.update((0, 0, 0), DT, FLAT)
        laser.aim((300.0, 300.0))
        up = FLAT
        for omega, up in rotation_samples(TILT_UP, 50, 0.4, up):
            laser.update(omega, DT, up)
        assert laser.target()[1] < 0
        for omega, up in rotation_samples(TILT_UP, -50, 0.4, up):
            laser.update(omega, DT, up)
        assert laser.target() == pytest.approx((300.0, 300.0), abs=2)

    def test_hold_keeps_target_while_aim_moves(self):
        laser = LaserPointer()
        laser.update((0, 0, 0), DT, FLAT)
        laser.aim((300.0, 300.0))
        for omega, up in rotation_samples(FLAT, 50, 0.2, FLAT):
            laser.update(omega, DT, up)
            laser.hold()
        assert laser.target() == pytest.approx((300.0, 300.0))


def run_desk(strokes, **kw):
    p = SlidePointer()
    xs, x = [], 0.0
    for sample in desk_stroke_samples(strokes, **kw):
        x += p.update(sample, DT)[0]
        xs.append(x)
    return xs


class TestSlidePointer:
    @pytest.mark.parametrize(
        "distance,duration",
        [(0.08, 0.35), (0.03, 0.3), (0.15, 0.5), (0.02, 0.6), (0.05, 1.0)],
    )
    @pytest.mark.parametrize("seed", [1, 2, 3])
    def test_travel_matches_distance_without_bounce_back(self, distance, duration, seed):
        xs = run_desk([(distance, 0, duration, 0.5)], noise=0.04, lag_samples=2, seed=seed)
        true_px = distance * SlidePointer.pixels_per_meter
        assert xs[-1] == pytest.approx(true_px, rel=0.15)
        assert max(xs) - xs[-1] < 0.03 * true_px
        assert min(xs) >= -0.01 * true_px

    def test_push_towards_top_edge_moves_cursor_up(self):
        p = SlidePointer()
        dy = sum(p.update(s, DT)[1] for s in desk_stroke_samples([(0, 0.06, 0.3, 0.3)]))
        assert dy < -500

    @pytest.mark.parametrize("pause", [0.3, 0.0])
    def test_out_and_back_returns_home(self, pause):
        xs = run_desk([(0.06, 0, 0.3, pause), (-0.06, 0, 0.3, 0.5)], noise=0.04, seed=3)
        assert max(xs) > 0.9 * 0.06 * SlidePointer.pixels_per_meter
        assert abs(xs[-1]) < 0.1 * max(xs)

    def test_resting_phone_does_not_drift(self):
        xs = run_desk([(0, 0, 0.01, 5.0)], noise=0.05, seed=7)
        assert max(abs(x) for x in xs) < 5

    def test_bias_is_learned_while_resting(self):
        p = SlidePointer()
        for _ in range(400):
            p.update((0.08, 0.0, 0.0), DT)
        assert p._bias[0] == pytest.approx(0.08, abs=0.01)

    def test_reset_cancels_motion_in_progress(self):
        p = SlidePointer()
        for _ in range(5):
            p.update((2.0, 0.0, 0.0), DT)
        assert p.moving
        p.reset()
        assert not p.moving and p.velocity == (0.0, 0.0)
