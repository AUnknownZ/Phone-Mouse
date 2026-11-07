"""Synthetic phone sensor streams for testing the motion filters."""

from __future__ import annotations

import math
import random

RATE_HZ = 60.0
DT = 1.0 / RATE_HZ


def min_jerk_accel(distance: float, duration: float, t: float) -> float:
    """Acceleration of a minimum-jerk move (how people move a mouse)."""
    if t < 0 or t > duration:
        return 0.0
    tau = t / duration
    return distance / duration**2 * (60 * tau - 180 * tau**2 + 120 * tau**3)


def desk_stroke_samples(
    strokes: list[tuple[float, float, float, float]],
    noise: float = 0.03,
    bias: tuple[float, float] = (0.0, 0.0),
    lag_samples: int = 1,
    seed: int = 1,
) -> list[tuple[float, float, float]]:
    """Linear acceleration samples for a sequence of desk strokes.

    Each stroke is (dx_m, dy_m, duration_s, pause_after_s) in device axes.
    """
    rng = random.Random(seed)
    clean: list[tuple[float, float]] = []
    for dx, dy, duration, pause in strokes:
        n = int(round((duration + pause) * RATE_HZ))
        for i in range(n):
            t = i * DT
            clean.append((min_jerk_accel(dx, duration, t), min_jerk_accel(dy, duration, t)))
    clean = [(0.0, 0.0)] * lag_samples + clean
    return [
        (ax + bias[0] + rng.gauss(0, noise), ay + bias[1] + rng.gauss(0, noise), 0.0)
        for ax, ay in clean
    ]


def rotation_samples(
    axis: tuple[float, float, float],
    rate_dps: float,
    seconds: float,
    up0: tuple[float, float, float],
):
    """Rotate a phone at constant rate about a device-frame axis.

    Yields (omega_device_dps, up_device) per sample, with ``up`` evolving
    exactly as physics dictates (d(up)/dt = -omega x up).
    """
    n = math.sqrt(sum(a * a for a in axis))
    k = tuple(a / n for a in axis)
    up = up0
    w = rate_dps * math.pi / 180
    for _ in range(int(seconds * RATE_HZ)):
        # Rodrigues rotation of `up` by -w*DT about k.
        theta = -w * DT
        c, s = math.cos(theta), math.sin(theta)
        kxu = (k[1] * up[2] - k[2] * up[1], k[2] * up[0] - k[0] * up[2], k[0] * up[1] - k[1] * up[0])
        kdu = sum(k[i] * up[i] for i in range(3))
        up = tuple(up[i] * c + kxu[i] * s + k[i] * kdu * (1 - c) for i in range(3))
        yield tuple(rate_dps * ki for ki in k), up
