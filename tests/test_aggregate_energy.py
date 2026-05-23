"""Tests for aggregate_energy pure functions: boundary helpers and apply_resets."""

from datetime import datetime, timezone
import pytest

from aggregate_energy import (
    EnergyState,
    DeviceEnergyState,
    apply_resets,
    current_day_boundary,
    next_day_boundary,
    current_week_boundary,
    next_week_boundary,
    current_month_boundary,
    next_month_boundary,
    current_year_boundary,
    next_year_boundary,
)


def dt(year, month, day, hour=0, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


class TestBoundaryHelpers:
    def test_current_day_boundary_truncates_to_midnight(self):
        now = dt(2024, 6, 15, 14, 30)
        assert current_day_boundary(now) == dt(2024, 6, 15, 0, 0)

    def test_next_day_boundary_is_tomorrow(self):
        now = dt(2024, 6, 15, 14, 30)
        assert next_day_boundary(now) == dt(2024, 6, 16, 0, 0)

    def test_current_week_boundary_on_monday(self):
        monday = dt(2024, 6, 17, 12, 0)  # Monday
        assert current_week_boundary(monday) == dt(2024, 6, 17, 1, 0)

    def test_current_week_boundary_on_wednesday(self):
        wednesday = dt(2024, 6, 19, 12, 0)
        assert current_week_boundary(wednesday) == dt(2024, 6, 17, 1, 0)

    def test_next_week_boundary_is_7_days_ahead(self):
        now = dt(2024, 6, 19, 12, 0)
        assert next_week_boundary(now) == dt(2024, 6, 24, 1, 0)

    def test_current_month_boundary_first_of_month(self):
        now = dt(2024, 6, 15, 14, 0)
        assert current_month_boundary(now) == dt(2024, 6, 1, 1, 0)

    def test_next_month_boundary_wraps_december(self):
        now = dt(2024, 12, 15, 14, 0)
        assert next_month_boundary(now) == dt(2025, 1, 1, 1, 0)

    def test_current_year_boundary_jan_1(self):
        now = dt(2024, 6, 15, 14, 0)
        assert current_year_boundary(now) == dt(2024, 1, 1, 1, 0)

    def test_next_year_boundary(self):
        now = dt(2024, 6, 15, 14, 0)
        assert next_year_boundary(now) == dt(2025, 1, 1, 1, 0)


class TestApplyResets:
    def _state_with_counts(self, kwh=1.0) -> EnergyState:
        s = EnergyState()
        s.day_kwh = s.week_kwh = s.month_kwh = s.year_kwh = kwh
        return s

    def test_initializes_reset_timestamps_on_first_call(self):
        state = EnergyState()
        now = dt(2024, 6, 19, 12, 0)
        apply_resets(state, now)
        assert state.last_day_reset is not None
        assert state.last_week_reset is not None
        assert state.last_month_reset is not None
        assert state.last_year_reset is not None

    def test_does_not_reset_within_same_period(self):
        now = dt(2024, 6, 19, 12, 0)
        state = self._state_with_counts(2.0)
        apply_resets(state, now)  # initialize
        apply_resets(state, now)  # same moment — no reset
        assert state.day_kwh == 2.0

    def test_resets_day_kwh_after_midnight(self):
        state = self._state_with_counts(5.0)
        before = dt(2024, 6, 19, 23, 59)
        apply_resets(state, before)  # initialize last_day_reset to 2024-06-19 00:00

        after = dt(2024, 6, 20, 0, 1)
        apply_resets(state, after)
        assert state.day_kwh == 0.0
        # Other periods should not have reset
        assert state.week_kwh == 5.0

    def test_resets_device_day_counters(self):
        state = self._state_with_counts(3.0)
        state.devices['plug1'] = DeviceEnergyState(day_kwh=1.5, week_kwh=2.0)

        before = dt(2024, 6, 19, 23, 59)
        apply_resets(state, before)

        after = dt(2024, 6, 20, 0, 1)
        apply_resets(state, after)

        assert state.devices['plug1'].day_kwh == 0.0
        assert state.devices['plug1'].week_kwh == 2.0  # untouched
