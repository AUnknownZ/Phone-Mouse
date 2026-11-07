import pytest

from phone_mouse.motion import (
    GyroPointer,
    SlidePointer,
    SubpixelAccumulator,
    pointer_acceleration,
)

FLAT = (0.0, 0.0, 9.81)  # lying screen-up
UPRIGHT = (0.0, 9.81, 0.0)  # held vertically, screen facing the user


def settle(pointer, *args, n=20):
    out = (0.0, 0.0)
    for _ in range(n):
        out = pointer.update(*args)
    return out


def test_subpixel_accumulator_carries_fractions():
    acc = SubpixelAccumulator()
    moves = [acc.take(0.4, -0.4) for _ in range(5)]
    assert sum(m[0] for m in moves) == 2
    assert sum(m[1] for m in moves) == -2


def test_pointer_acceleration_curve():
    assert pointer_acceleration(10, 60, 0.6) == 1.0
    assert pointer_acceleration(120, 60, 0.6) == pytest.approx(1.6)


class TestGyroPointer:
    def test_turning_left_moves_cursor_left_when_flat(self):
        # Positive alpha = counter-clockwise seen from above = turning left.
        dx, dy = settle(GyroPointer(smoothing=0), (30.0, 0.0, 0.0), 0.016, FLAT)
        assert dx < 0
        assert dy == 0

    def test_yaw_uses_world_vertical_when_upright(self):
        # Held upright, turning left is rotation about the device y axis
        # (gamma), and alpha (about the screen normal) is a roll, not a yaw.
        p = GyroPointer(smoothing=0)
        dx_yaw, _ = settle(p, (0.0, 0.0, 30.0), 0.016, UPRIGHT)
        p.reset()
        dx_roll, _ = settle(p, (30.0, 0.0, 0.0), 0.016, UPRIGHT)
        assert dx_yaw < 0
        assert dx_roll == 0

    def test_tilting_top_edge_up_moves_cursor_up(self):
        _, dy = settle(GyroPointer(smoothing=0), (0.0, 30.0, 0.0), 0.016, FLAT)
        assert dy < 0

    def test_deadzone_suppresses_hand_tremor(self):
        dx, dy = settle(GyroPointer(), (0.8, -1.0, 0.5), 0.016, FLAT)
        assert (dx, dy) == (0.0, 0.0)

    def test_scale_is_pixels_per_degree(self):
        p = GyroPointer(smoothing=0, deadzone_dps=0, accel_factor=0, pixels_per_degree=10)
        dx, _ = p.update((-20.0, 0.0, 0.0), 0.5, FLAT)
        assert dx == pytest.approx(100.0)

    def test_missing_gravity_assumes_flat(self):
        p = GyroPointer(smoothing=0)
        assert p.update((30.0, 0, 0), 0.016, None) == p.update((30.0, 0, 0), 0.016, FLAT)


class TestSlidePointer:
    def test_at_rest_produces_no_motion(self):
        p = SlidePointer()
        total = [0.0, 0.0]
        for _ in range(200):
            dx, dy = p.update((0.03, -0.02, 0.01), 0.016, (0.2, 0.1, 0.0))
            total[0] += dx
            total[1] += dy
        assert abs(total[0]) < 1 and abs(total[1]) < 1

    def test_push_right_then_stop(self):
        p = SlidePointer()
        total_x = 0.0
        for _ in range(10):  # accelerate right
            total_x += p.update((2.0, 0.0, 0.0), 0.016)[0]
        for _ in range(10):  # decelerate
            total_x += p.update((-2.0, 0.0, 0.0), 0.016)[0]
        assert total_x > 5
        for _ in range(p.still_samples):
            p.update((0.0, 0.0, 0.0), 0.016)
        assert p.velocity == (0.0, 0.0)

    def test_push_towards_top_edge_moves_cursor_up(self):
        p = SlidePointer()
        dy = sum(p.update((0.0, 2.0, 0.0), 0.016)[1] for _ in range(10))
        assert dy < 0

    def test_bias_is_learned_while_still(self):
        p = SlidePointer()
        for _ in range(500):
            p.update((0.1, 0.0, 0.0), 0.016)
        assert p._bias[0] == pytest.approx(0.1, abs=0.01)

    def test_rotation_prevents_false_rest_detection(self):
        p = SlidePointer()
        for _ in range(10):
            p.update((0.0, 0.0, 0.0), 0.016, (0.0, 0.0, 40.0))
        assert p._still == 0
