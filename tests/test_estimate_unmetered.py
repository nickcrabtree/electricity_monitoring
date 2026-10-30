"""Tests for tools/estimate_unmetered: pull an unmetered device's draw out of the unexplained house load."""

import random
import sys
from pathlib import Path

# tools/ is not on the default path
sys.path.insert(0, str(Path(__file__).parent.parent / 'tools'))
import estimate_unmetered as eu

MINUTE = 60
T0 = 1_700_000_000 // MINUTE * MINUTE


def minute(n: int) -> int:
    return T0 + n * MINUTE


def toggling_state(period: int, minutes: int) -> dict[int, bool]:
    """Device on for `period` minutes, off for `period` minutes, starting off."""
    return {minute(n): (n // period) % 2 == 1 for n in range(minutes)}


def unexplained_for(state: dict[int, bool], watts: float, baseline: float = 300.0, noise: float = 20.0, seed: int = 1):
    rng = random.Random(seed)
    return {t: baseline + (watts if on else 0.0) + rng.gauss(0, noise) for t, on in state.items()}


class TestEstimateDraw:
    def test_recovers_step_size_from_many_cycles(self):
        state = toggling_state(30, 30 * 24)
        result = eu.estimate_draw(state, unexplained_for(state, 250.0))
        assert result.n_on == 12
        assert result.n_off == 11
        assert abs(result.median_watts - 250.0) < 15.0
        assert result.confidence == 'good'

    def test_reports_on_fraction_and_mean_watts(self):
        state = toggling_state(30, 30 * 24)
        result = eu.estimate_draw(state, unexplained_for(state, 250.0))
        assert abs(result.on_fraction - 0.5) < 0.02
        assert abs(result.mean_watts - result.median_watts * result.on_fraction) < 1e-9

    def test_unrelated_big_steps_in_a_few_events_do_not_move_the_median(self):
        state = toggling_state(30, 30 * 24)
        unexplained = unexplained_for(state, 250.0)
        # a 2 kW appliance switches on at the same time as three of the device's on-events
        starts = [n for n in range(1, 30 * 24) if state[minute(n)] and not state[minute(n - 1)]]
        for n in starts[:3]:
            for k in range(n, n + 20):
                unexplained[minute(k)] += 2000.0
        result = eu.estimate_draw(state, unexplained)
        assert abs(result.median_watts - 250.0) < 40.0

    def test_flapping_events_are_not_usable(self):
        # on for one minute at a time, so no clean before/after window exists
        state = {minute(n): n % 2 == 1 for n in range(200)}
        result = eu.estimate_draw(state, unexplained_for(state, 250.0))
        assert result.n_on == 0
        assert result.n_off == 0
        assert result.confidence == 'insufficient'

    def test_too_few_events_is_insufficient(self):
        state = toggling_state(30, 90)
        result = eu.estimate_draw(state, unexplained_for(state, 250.0))
        assert result.confidence == 'insufficient'

    def test_events_without_load_data_are_skipped(self):
        state = toggling_state(30, 30 * 24)
        unexplained = unexplained_for(state, 250.0)
        for n in range(0, 30 * 12):
            del unexplained[minute(n)]
        result = eu.estimate_draw(state, unexplained)
        assert result.n_on <= 6

    def test_device_with_no_effect_is_not_detectable(self):
        state = toggling_state(30, 30 * 24)
        result = eu.estimate_draw(state, unexplained_for(state, 0.0, noise=60.0))
        assert result.confidence == 'not detectable'

    def test_off_drop_is_reported_positive(self):
        state = toggling_state(30, 30 * 24)
        result = eu.estimate_draw(state, unexplained_for(state, 250.0))
        assert result.median_off_watts > 200.0
        assert result.median_on_watts > 200.0


class TestBuildUnexplained:
    def test_meter_minus_aggregate(self):
        assert eu.build_unexplained({minute(0): 1000.0}, {minute(0): 400.0}) == {minute(0): 600.0}

    def test_minutes_missing_from_either_series_are_dropped(self):
        meter = {minute(0): 1000.0, minute(1): 1000.0}
        aggregate = {minute(1): 400.0, minute(2): 400.0}
        assert eu.build_unexplained(meter, aggregate) == {minute(1): 600.0}

    def test_meter_outage_minutes_are_dropped(self):
        # the meter reported 0 W for whole days in early October; that is an outage, not a negative load
        meter = {minute(0): 0.0, minute(1): 900.0}
        aggregate = {minute(0): 500.0, minute(1): 500.0}
        assert eu.build_unexplained(meter, aggregate) == {minute(1): 400.0}


class TestSampleInterval:
    def test_one_minute_series(self):
        assert eu.sample_interval({minute(n): True for n in range(10)}) == MINUTE

    def test_five_minute_series_is_flagged_by_its_interval(self):
        assert eu.sample_interval({minute(5 * n): True for n in range(10)}) == 5 * MINUTE

    def test_too_few_samples(self):
        assert eu.sample_interval({minute(0): True}) == 0


class TestFormat:
    def test_report_line_names_device_and_confidence(self):
        state = toggling_state(30, 30 * 24)
        result = eu.estimate_draw(state, unexplained_for(state, 250.0))
        line = eu.format_result('towel_rail', result)
        assert 'towel_rail' in line
        assert 'good' in line
        assert 'W' in line
