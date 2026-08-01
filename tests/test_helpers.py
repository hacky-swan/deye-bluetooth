"""Tests for the pure daily_calc and infer_grid_connected helpers."""
from __future__ import annotations

from datetime import date

from custom_components.deye_ble.helpers import (
    daily_calc,
    hold_spurious_total_resets,
    infer_grid_connected,
)

_TODAY = date(2025, 6, 15)


def test_first_run_of_day():
    baseline, day, value = daily_calc(None, None, 1234.5, _TODAY)
    assert baseline == 1234.5
    assert day == _TODAY
    assert value == 0.0


def test_same_day_increment():
    baseline, day, value = daily_calc(1234.5, _TODAY, 1240.7, _TODAY)
    assert baseline == 1234.5
    assert day == _TODAY
    assert value == 6.2


def test_new_day_rebaseline():
    tomorrow = date(2025, 6, 16)
    baseline, day, value = daily_calc(1240.7, _TODAY, 1260.0, tomorrow)
    assert baseline == 1260.0
    assert day == tomorrow
    assert value == 0.0


def test_counter_backwards_rebaseline():
    baseline, day, value = daily_calc(1240.7, _TODAY, 1200.0, _TODAY)
    assert baseline == 1200.0
    assert day == _TODAY
    assert value == 0.0


def test_total_none_returns_none():
    baseline, day, value = daily_calc(1234.5, _TODAY, None, _TODAY)
    assert baseline == 1234.5
    assert day == _TODAY
    assert value is None


def test_rounding():
    baseline, day, value = daily_calc(1000.0, _TODAY, 1000.005, _TODAY)
    assert value == 0.0  # rounds to 2 dp
    baseline, day, value = daily_calc(1000.0, _TODAY, 1000.006, _TODAY)
    assert value == 0.01


# --- infer_grid_connected ---------------------------------------------------

def test_grid_connected_when_any_phase_energised():
    data = {"grid_voltage_l1": 232.3, "grid_voltage_l2": 233.2, "grid_voltage_l3": 233.8}
    assert infer_grid_connected(data) is True


def test_grid_disconnected_when_all_phases_collapsed():
    data = {"grid_voltage_l1": 0.0, "grid_voltage_l2": 0.3, "grid_voltage_l3": 0.0}
    assert infer_grid_connected(data) is False


def test_grid_connected_true_if_a_single_phase_present():
    # Single-phase grid / one phase still up -> connected.
    data = {"grid_voltage_l1": 230.0, "grid_voltage_l2": 0.0, "grid_voltage_l3": 0.0}
    assert infer_grid_connected(data) is True


def test_grid_connected_none_when_no_voltage_keys():
    # No grid-voltage reading -> unknown, not a false "disconnected".
    assert infer_grid_connected({}) is None
    assert infer_grid_connected({"solar_power": 100}) is None


# --- hold_spurious_total_resets ---------------------------------------------

def test_spurious_zero_total_held_at_last_good():
    prev = {"total_grid_import": 2644.6}
    current = hold_spurious_total_resets(prev, {"total_grid_import": 0.0})
    assert current["total_grid_import"] == 2644.6


def test_all_four_blipped_totals_held_simultaneously():
    prev = {
        "total_grid_import": 2644.6,
        "total_battery_charge": 2377.7,
        "total_battery_discharge": 2236.5,
        "total_grid_export": 635.1,
    }
    current = hold_spurious_total_resets(prev, {k: 0.0 for k in prev})
    assert current == prev


def test_genuine_increase_passes_through():
    prev = {"total_grid_import": 2644.6}
    current = hold_spurious_total_resets(prev, {"total_grid_import": 2674.9})
    assert current["total_grid_import"] == 2674.9


def test_non_total_zero_is_not_held():
    # Instantaneous power legitimately reads 0 — never held.
    prev = {"grid_power": 1300}
    current = hold_spurious_total_resets(prev, {"grid_power": 0})
    assert current["grid_power"] == 0


def test_no_prev_passes_through_unchanged():
    # First cycle: nothing to hold against.
    current = hold_spurious_total_resets(None, {"total_grid_import": 0.0})
    assert current["total_grid_import"] == 0.0
    current = hold_spurious_total_resets({}, {"total_grid_import": 0.0})
    assert current["total_grid_import"] == 0.0


def test_missing_total_in_current_is_skipped():
    # Key not read this cycle -> no KeyError, left absent.
    prev = {"total_grid_import": 2644.6}
    current = hold_spurious_total_resets(prev, {"grid_power": 100})
    assert "total_grid_import" not in current


def test_zero_prev_does_not_hold():
    # Legitimate cold start at 0 -> a later 0 is not treated as a reset.
    prev = {"total_grid_import": 0.0}
    current = hold_spurious_total_resets(prev, {"total_grid_import": 0.0})
    assert current["total_grid_import"] == 0.0
