"""Tests for tools/repair_energy_after_rename.py on synthetic series (no Graphite, no carbon, no blackpi2)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'tools'))
import repair_energy_after_rename as rep  # noqa: E402

T0 = 1793966400  # 2026-11-06 12:00:00 UTC (GMT, so local time too)
H = 3600


def kwh(watts: float, seconds: float) -> float:
    return watts * seconds / 3.6e6


class TestIntegration:
    def test_trapezoid_between_points_from_start(self):
        cum = rep.cumulative_energy([(T0 - 10, 999.0), (T0, 100.0), (T0 + 10, 300.0), (T0 + 20, 300.0)], T0, 300)
        assert cum[0] == (T0, 0.0)
        assert rep.energy_at(cum, T0 + 10) == pytest.approx(kwh(200, 10))
        assert rep.energy_at(cum, T0 + 20) == pytest.approx(kwh(200, 10) + kwh(300, 10))

    def test_interpolates_inside_a_segment_and_holds_after_the_last_point(self):
        cum = rep.cumulative_energy([(T0, 0.0), (T0 + 10, 360.0)], T0, 300)
        assert rep.energy_at(cum, T0 + 5) == pytest.approx(kwh(180, 10) / 2)
        assert rep.energy_at(cum, T0 + 500) == pytest.approx(kwh(180, 10))
        assert rep.energy_at(cum, T0 - 5) == 0.0

    def test_gap_longer_than_stale_limit_adds_nothing(self):
        cum = rep.cumulative_energy([(T0, 1000.0), (T0 + 301, 1000.0), (T0 + 311, 1000.0)], T0, 300)
        assert rep.energy_at(cum, T0 + 311) == pytest.approx(kwh(1000, 10))

    def test_counter_values_restart_at_period_boundaries(self):
        # one point per hour at 1 kW from 22:00 to 02:00: the day counter restarts at midnight
        start = T0 + 10 * H
        cum = rep.cumulative_energy([(start + i * H, 1000.0) for i in range(5)], start, 2 * H)
        day = rep.COUNTERS[0]
        assert day.attr == 'day_kwh'
        assert rep.counter_value(cum, start + 4 * H, day) == pytest.approx(2.0)  # 00:00-02:00
        week = rep.COUNTERS[1]
        assert rep.counter_value(cum, start + 4 * H, week) == pytest.approx(4.0)


class TestDeviceSum:
    def test_newest_point_within_limit_with_hierarchy(self):
        devices = {
            'tuya.shed': [(T0, 100.0)],
            'tuya.shed_child': [(T0, 40.0)],
            'tuya.kettle': [(T0 - 400, 2000.0), (T0 + 10, 5.0)],
        }
        sums = rep.device_sums(devices, [T0, T0 + 10], {'tuya.shed': ['tuya.shed_child']}, 300)
        assert sums == [100.0, 105.0]  # kettle's T0-400 point is stale at T0

    def test_parent_missing_counts_children(self):
        sums = rep.device_sums({'tuya.shed_child': [(T0, 40.0)]}, [T0], {'tuya.shed': ['tuya.shed_child']}, 300)
        assert sums == [40.0]


def plateau_excess(stale_w: float, last_old: int, start_delay: int = 90, end_delay: int = 3615) -> list:
    """Aggregate minus device sum as seen on 2026-11-06: 0, then the stale value while both names count, then 0."""
    out = []
    for ts in range(last_old - 300, last_old + 3900, 10):
        counted = last_old + start_delay <= ts <= last_old + end_delay
        out.append((ts, stale_w + (3.0 if counted else 0.0) if counted else 2.0))
    return out


class TestDoubleCountWindow:
    def test_finds_first_and_last_cycle_counting_both_names(self):
        last_old = T0 + 2765
        window = rep.double_count_window(plateau_excess(1648.9, last_old), 1648.9, last_old)
        assert window == (last_old + 90, last_old + 3610)

    def test_noise_inside_the_window_does_not_move_its_edges(self):
        last_old = T0
        excess = plateau_excess(1648.9, last_old)
        excess = [(ts, -429.0) if ts == last_old + 2005 else (ts, v) for ts, v in excess]
        assert rep.double_count_window(excess, 1648.9, last_old) == (last_old + 90, last_old + 3610)

    def test_ignores_a_matching_step_long_after_the_render_window(self):
        last_old = T0
        excess = plateau_excess(1648.9, last_old) + [(last_old + 4000, 1650.0)]
        assert rep.double_count_window(excess, 1648.9, last_old) == (last_old + 90, last_old + 3610)

    def test_zero_stale_value_has_no_window(self):
        assert rep.double_count_window(plateau_excess(0.0, T0), 0.0, T0) is None

    def test_refuses_when_no_plateau_is_found(self):
        flat = [(ts, 0.0) for ts in range(T0, T0 + 4000, 10)]
        with pytest.raises(rep.Refused):
            rep.double_count_window(flat, 1648.9, T0)


class TestCycleExcess:
    def test_uses_counter_increment_over_power_for_each_cycle(self):
        # cycles 10.6 s apart but bucketed to 5 s timestamps; counter grows by P * true dt
        power = [(T0, 2000.0), (T0 + 10, 3000.0), (T0 + 20, 3000.0), (T0 + 30, 1000.0)]
        daily = [(T0, 1.0), (T0 + 10, 1.0 + kwh(3000, 10.6)), (T0 + 20, 1.0 + kwh(3000, 21.2)), (T0 + 30, 2.0)]
        rows = rep.cycle_excess(power, daily, {T0 + 10: 1648.9, T0 + 20: 1648.9})
        assert [r.ts for r in rows] == [T0 + 10, T0 + 20]
        assert rows[0].kwh == pytest.approx(kwh(1648.9, 10.6))
        assert rows[0].dt_counter == pytest.approx(10.6)
        assert rows[0].dt_stamps == 10
        assert sum(r.kwh for r in rows) == pytest.approx(kwh(1648.9, 21.2))

    def test_falls_back_to_timestamps_across_a_reset(self):
        power = [(T0, 2000.0), (T0 + 10, 3000.0)]
        daily = [(T0, 9.0), (T0 + 10, 0.01)]
        rows = rep.cycle_excess(power, daily, {T0 + 10: 1000.0})
        assert rows[0].kwh == pytest.approx(kwh(1000, 10))


class TestCorrections:
    def test_counter_correction_subtracts_running_excess_until_the_period_resets(self):
        # excess accrues 12:47-13:46; the day counter resets at midnight, the week counter does not
        start = T0 + 47 * 60
        excess = [(start, 0.5), (start + 1800, 1.0)]
        points = [(start - 10, 5.0), (start, 5.6), (start + 900, 6.0), (start + 1800, 7.0), (T0 + 12 * H + 10, 0.1)]
        day, week = rep.COUNTERS[0], rep.COUNTERS[1]
        changes = rep.correct_counter('m', points, excess, day)
        assert [(c.ts, c.new) for c in changes] == [
            (start, pytest.approx(5.1)),
            (start + 900, pytest.approx(5.5)),
            (start + 1800, pytest.approx(6.0)),
        ]
        week_changes = rep.correct_counter('m', points, excess, week)
        assert week_changes[-1].ts == T0 + 12 * H + 10 and week_changes[-1].new == pytest.approx(0.1 - 1.0)

    def test_power_correction_only_inside_the_window(self):
        changes = rep.correct_power('p', [(T0, 3000.0), (T0 + 10, 3500.0)], {T0 + 10: 1648.9})
        assert [(c.ts, c.old, c.new) for c in changes] == [(T0 + 10, 3500.0, pytest.approx(1851.1))]

    def test_rebuild_counter_replaces_points_from_cutover_to_until(self):
        cut = T0
        cum = rep.cumulative_energy([(cut + i * 10, 360.0) for i in range(100)], cut, 300)
        existing = [(cut - 10, 76.0), (cut, 76.0), (cut + 500, 76.5), (cut + 900, 0.9), (cut + 2000, 9.0)]
        changes = rep.rebuild_counter('d', existing, cum, rep.COUNTERS[3], cut, cut + 900)
        assert [c.ts for c in changes] == [cut, cut + 500, cut + 900]
        assert changes[0].new == 0.0
        assert changes[1].new == pytest.approx(kwh(360, 500))
        assert changes[1].old == 76.5


def state_dict(last_ts: float) -> dict:
    dev = {'last_power_w': 0.0, 'day_kwh': 1.0, 'week_kwh': 2.0, 'month_kwh': 3.0, 'year_kwh': 4.0}
    return {
        'last_ts': last_ts,
        'day_kwh': 17.0,
        'week_kwh': 62.0,
        'month_kwh': 74.0,
        'year_kwh': 3484.0,
        'last_day_reset': T0 - 12 * H,
        'last_week_reset': T0 - 4 * 24 * H - 11 * H,
        'last_month_reset': T0 - 5 * 24 * H - 11 * H,
        'last_year_reset': T0 - 300 * 24 * H,
        'devices': {
            'tuya.boot_room_freezer': dict(dev, last_power_w=1648.9),
            'tuya.garage_power_a': dict(dev),
            'kasa.tumble_dryer': dict(dev),
            'tuya.dishwasher': dict(dev, last_power_w=22.5, year_kwh=372.2),
            'tuya.kettle': dict(dev),
        },
    }


class TestCorrectState:
    def test_subtracts_excess_rebuilds_new_names_and_drops_dead_entries(self):
        state = state_dict(T0 + 8 * H)
        rebuilt = {'tuya.dishwasher': {'day_kwh': 2.0, 'week_kwh': 2.0, 'month_kwh': 2.0, 'year_kwh': 2.0}}
        new, notes = rep.correct_state(state, 1.62, T0 + 47 * 60, rebuilt, rep.DROP_STATE_KEYS)
        assert new['day_kwh'] == pytest.approx(17.0 - 1.62)
        assert new['year_kwh'] == pytest.approx(3484.0 - 1.62)
        assert new['devices']['tuya.dishwasher']['year_kwh'] == 2.0
        assert new['devices']['tuya.dishwasher']['last_power_w'] == 22.5
        assert set(new['devices']) == {'tuya.dishwasher', 'tuya.kettle'}
        assert new['devices']['tuya.kettle'] == state['devices']['tuya.kettle']
        assert state['day_kwh'] == 17.0  # input untouched
        assert notes

    def test_skips_a_counter_reset_since_the_excess(self):
        state = state_dict(T0 + 13 * H)
        state['last_day_reset'] = T0 + 12 * H  # midnight passed after the incident
        new, notes = rep.correct_state(state, 1.62, T0 + 47 * 60, {}, ())
        assert new['day_kwh'] == 17.0
        assert new['week_kwh'] == pytest.approx(62.0 - 1.62)
        assert any('day_kwh' in n for n in notes)

    def test_refuses_a_rebuild_for_a_device_missing_from_state(self):
        with pytest.raises(rep.Refused):
            rep.correct_state(state_dict(T0 + H), 0.0, T0, {'tuya.nothing': {'day_kwh': 1.0}}, ())


class TestSafety:
    def test_refuses_points_that_left_the_fine_archive(self):
        rep.check_fine_archive(T0, now=T0 + 86400 - 601)
        with pytest.raises(rep.Refused):
            rep.check_fine_archive(T0, now=T0 + 86400 - 599)

    def test_carbon_lines_keep_full_precision_and_timestamp(self):
        lines = rep.carbon_lines([rep.Change('a.b', T0, 1.0, 0.1 + 0.2)])
        assert lines == [f'a.b 0.30000000000000004 {T0}\n']

    def test_log_round_trip_and_undo(self, tmp_path):
        changes = [rep.Change('a.b', T0, 5.0, 4.0), rep.Change('a.c', T0 + 5, 1.0, 2.0)]
        path = rep.write_log(tmp_path, 'series', changes, {'note': 'x'})
        assert path.parent == tmp_path and path.name.startswith('series_')
        loaded = rep.read_log(path)
        assert loaded == changes
        undo = rep.undo_changes(loaded)
        assert [(c.metric, c.ts, c.old, c.new) for c in undo] == [('a.b', T0, 4.0, 5.0), ('a.c', T0 + 5, 2.0, 1.0)]
        assert json.loads(path.read_text())['meta'] == {'note': 'x'}

    def test_redo_sends_the_logged_new_values(self, tmp_path):
        changes = [rep.Change('a.b', T0, 5.0, 4.0)]
        loaded = rep.read_log(rep.write_log(tmp_path, 'series', changes, {}))
        assert rep.carbon_lines(rep.redo_changes(loaded)) == [f'a.b 4.0 {T0}\n']

    def test_refusal_after_apply_points_to_the_log(self):
        with pytest.raises(rep.Refused, match='verify, redo or undo'):
            rep.double_count_window([(T0 + 100, 0.0)], 1648.9, T0)

    def test_minute_means_for_the_60s_check(self):
        five = [(T0, 1.0), (T0 + 5, 3.0), (T0 + 55, None), (T0 + 60, 10.0)]
        assert rep.minute_means(five) == {T0: 2.0, T0 + 60: 10.0}

    def test_state_output_never_overwrites_input_or_existing_file(self, tmp_path):
        src = tmp_path / 'energy_state.json'
        src.write_text('{}')
        with pytest.raises(rep.Refused):
            rep.check_output_path(src, src)
        out = tmp_path / 'fixed.json'
        rep.check_output_path(src, out)
        out.write_text('{}')
        with pytest.raises(rep.Refused):
            rep.check_output_path(src, out)


class TestBuildRepair:
    """End to end on a small synthetic incident shaped like 2026-11-06."""

    def data(self):
        cut = T0 + 1120  # 12:18:40
        last_old = T0 + 2770  # 12:46:10, on the plug's 10 s grid
        stale = 1648.9
        until = T0 + 2 * H
        cycles = list(range(T0 - 600, until + 1, 10))
        new_power = [(t, stale if t <= last_old + 600 else 20.0) for t in range(cut, until + 1, 10)]
        start, end = last_old + 90, last_old + 3615
        agg_power, daily = [], []
        energy = 5.0
        for i, t in enumerate(cycles):
            real = 300.0 + (stale if cut <= t <= last_old + 600 else 20.0 if t > last_old + 600 else 0.0)
            p = real + (stale if start <= t <= end else 0.0)
            if i:
                energy += kwh(p, 10)
            agg_power.append((t, p))
            daily.append((t, energy))
        series = {
            'home.electricity.aggregate.power_watts': agg_power,
            'home.electricity.tuya.base.power_watts': [(t, 300.0) for t in cycles],
            'home.electricity.tuya.dishwasher.power_watts': new_power,
        }
        for c in rep.COUNTERS:
            series[f'home.electricity.aggregate.{c.metric}'] = list(daily)
            series[f'home.electricity.tuya.dishwasher.{c.metric}'] = [(t, 76.0) for t in range(cut, until + 1, 10)]
        renames = (rep.Rename('tuya.boot_room_freezer', 'tuya.dishwasher', cut, last_old),)
        counted = [t for t in cycles if start <= t <= end]
        return series, renames, until, (counted[0], counted[-1]), stale

    def test_measures_excess_and_corrects_everything(self):
        series, renames, until, (start, end), stale = self.data()
        repair = rep.build_repair(series, renames, until, hierarchy={}, stale_after=300)
        (episode,) = repair.episodes
        assert (episode.start, episode.end) == (start, end)
        assert episode.stale_w == stale
        n_cycles = (end - start) // 10 + 1  # cycles are 10 s apart
        assert repair.excess_kwh == pytest.approx(kwh(stale, 10) * n_cycles)
        power = [c for c in repair.changes if c.metric.endswith('aggregate.power_watts')]
        assert len(power) == n_cycles and all(c.new == pytest.approx(c.old - stale) for c in power)
        yearly = [c for c in repair.changes if c.metric == 'home.electricity.aggregate.energy_kwh_yearly']
        assert yearly[-1].ts == until
        assert yearly[-1].old - yearly[-1].new == pytest.approx(repair.excess_kwh)
        dish = [c for c in repair.changes if c.metric == 'home.electricity.tuya.dishwasher.energy_kwh_yearly']
        assert dish[0].ts == renames[0].cutover and dish[0].new == 0.0
        assert dish[-1].new == pytest.approx(repair.device_kwh['tuya.dishwasher'])
        assert all(c.ts <= until for c in repair.changes)
