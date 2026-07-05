"""
Unit tests for the fuzzy membership functions in `normalize.py`.

These are the mathematical core of the suitability engine: every raw raster
value is passed through one of these functions to produce a 0-100 score.
The expected values below are derived by hand from the piecewise definitions
documented in DESIGN_AND_TESTING.md Part I §3 (Data Pipeline).
"""

import numpy as np
import pytest

from normalize import trapezoidal, gaussian, linear_descending, FUZZY_FUNCTIONS


# ── Trapezoidal ────────────────────────────────────────────────────────────────
# Window: 0 before a | rise a→b | plateau b→c | fall c→d | 0 after d
# Fixture window: a=500, b=600, c=900, d=1000  (e.g. rainfall mm)

def test_trapezoidal_plateau_is_100():
    out = trapezoidal(np.array([600.0, 750.0, 900.0]), 500, 600, 900, 1000)
    assert np.allclose(out, 100.0)


def test_trapezoidal_rising_edge_midpoint_is_50():
    out = trapezoidal(np.array([550.0]), 500, 600, 900, 1000)
    assert np.isclose(out[0], 50.0)


def test_trapezoidal_falling_edge_midpoint_is_50():
    out = trapezoidal(np.array([950.0]), 500, 600, 900, 1000)
    assert np.isclose(out[0], 50.0)


def test_trapezoidal_outside_window_is_zero():
    out = trapezoidal(np.array([400.0, 1100.0]), 500, 600, 900, 1000)
    assert np.allclose(out, 0.0)


def test_trapezoidal_always_within_0_100():
    values = np.linspace(-100, 2000, 500)
    out = trapezoidal(values, 500, 600, 900, 1000)
    assert out.min() >= 0.0
    assert out.max() <= 100.0


# ── Gaussian ───────────────────────────────────────────────────────────────────
# 100 at the optimum, symmetric bell-curve decline. Fixture: optimal=27, spread=5.

def test_gaussian_peak_at_optimum():
    out = gaussian(np.array([27.0]), 27, 5)
    assert np.isclose(out[0], 100.0)


def test_gaussian_is_symmetric_about_optimum():
    out = gaussian(np.array([22.0, 32.0]), 27, 5)
    assert np.isclose(out[0], out[1])


def test_gaussian_decreases_away_from_optimum():
    out = gaussian(np.array([27.0, 30.0, 35.0]), 27, 5)
    assert out[0] > out[1] > out[2]


def test_gaussian_within_0_100():
    out = gaussian(np.linspace(-50, 100, 300), 27, 5)
    assert out.min() >= 0.0
    assert out.max() <= 100.0


# ── Linear descending ──────────────────────────────────────────────────────────
# 100 at min_val, 0 at max_val (e.g. slope: flatter is better). Fixture 0→30.

def test_linear_descending_endpoints():
    out = linear_descending(np.array([0.0, 30.0]), 0, 30)
    assert np.isclose(out[0], 100.0)
    assert np.isclose(out[1], 0.0)


def test_linear_descending_midpoint_is_50():
    out = linear_descending(np.array([15.0]), 0, 30)
    assert np.isclose(out[0], 50.0)


def test_linear_descending_clamps_out_of_range():
    # Below min → would exceed 100; above max → would go negative. Both clamp.
    out = linear_descending(np.array([-5.0, 40.0]), 0, 30)
    assert np.isclose(out[0], 100.0)
    assert np.isclose(out[1], 0.0)


# ── Registry ───────────────────────────────────────────────────────────────────

def test_fuzzy_registry_covers_all_three_types():
    assert set(FUZZY_FUNCTIONS) == {"trapezoidal", "gaussian", "linear_descending"}


@pytest.mark.parametrize("name", ["trapezoidal", "gaussian", "linear_descending"])
def test_registry_entries_are_callable(name):
    assert callable(FUZZY_FUNCTIONS[name])
