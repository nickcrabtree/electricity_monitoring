"""Tests for tools/repair_energy_after_rename.py on synthetic series (no Graphite, no carbon, no blackpi2)."""

from __future__ import annotations

import json
import sys
import time
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


# --- the repair at 60 s (2026-11-09): snapshot 5 s, live 60 s, live 5 s ---------------------------------------------

DAY = 86400
M = 'home.electricity.aggregate.energy_kwh_yearly'


def minute_points(start: int, n: int, value=lambda t: 1.0, offset: int = 5) -> list:
    """5 s-bucketed points every 10 s, as the aggregator writes them."""
    return [(t, value(t)) for t in range(start + offset, start + n * 60, 10)]


class TestRollup:
    def test_partly_changed_minute_is_the_mean_of_old_and_corrected_points(self):
        m = T0 + 47 * 60
        pts = [(m + 5, 1.0), (m + 15, 2.0), (m + 25, 3.0), (m + 35, 4.0), (m + 45, 5.0), (m + 55, 6.0)]
        changes = [rep.Change(M, t, v, v - 1.0) for t, v in pts if t >= m + 35]
        (minute,) = rep.rollup(M, pts, changes)
        assert (minute.metric, minute.ts, minute.step) == (M, m, 60)
        assert minute.old == pytest.approx(3.5)
        assert minute.new == pytest.approx((1 + 2 + 3 + 3 + 4 + 5) / 6)

    def test_only_minutes_holding_a_change(self):
        pts = minute_points(T0, 3)
        changes = [rep.Change(M, pts[-1][0], 1.0, 0.5)]
        assert [c.ts for c in rep.rollup(M, pts, changes)] == [T0 + 120]


class TestGate:
    def rollups(self):
        return [rep.Change(M, T0, 10.0, 9.0, 60), rep.Change(M, T0 + 60, 11.0, 10.0, 60)]

    def test_passes_when_live_60s_equals_the_snapshot_minute_means(self):
        rep.gate(self.rollups(), {M: {T0: 10.0, T0 + 60: 11.0}})

    def test_refuses_when_the_repair_is_already_in_graphite(self):
        with pytest.raises(rep.Refused, match='already applied'):
            rep.gate(self.rollups(), {M: {T0: 9.0, T0 + 60: 10.0}})

    def test_refuses_when_live_differs_from_the_snapshot(self):
        with pytest.raises(rep.Refused, match='differ'):
            rep.gate(self.rollups(), {M: {T0: 10.0, T0 + 60: 10.5}})

    def test_refuses_a_missing_live_minute(self):
        with pytest.raises(rep.Refused):
            rep.gate(self.rollups(), {M: {T0: 10.0}})


class TestSplitForSend:
    """One representation per minute: 5 s points while the minute is safely in the 5 s archive, else its 60 s value."""

    def test_recent_minute_is_sent_as_its_5s_points_only(self):
        now = T0 + DAY
        m = now - 3600
        changes = [rep.Change(M, m + 5, 1.0, 0.5), rep.Change(M, m + 15, 1.0, 0.5), rep.Change(M, m, 1.0, 0.5, 60)]
        send, later = rep.split_for_send(changes, now)
        assert sorted(c.ts for c in send) == [m + 5, m + 15] and all(c.step == 5 for c in send)
        assert later == []

    def test_minute_older_than_the_5s_archive_is_sent_as_its_60s_value(self):
        now = T0 + 3 * DAY
        changes = [rep.Change(M, T0 + 5, 1.0, 0.5), rep.Change(M, T0, 1.0, 0.5, 60)]
        send, later = rep.split_for_send(changes, now)
        assert send == [rep.Change(M, T0, 1.0, 0.5, 60)] and later == []

    def test_minute_near_the_edge_is_deferred_as_its_60s_value(self):
        now = T0 + DAY + 100  # T0 left the 5 s archive 100 s ago; T0 + 120 is within the guard
        edge = T0 + 120
        changes = [
            rep.Change(M, edge + 5, 1.0, 0.5),
            rep.Change(M, edge, 1.0, 0.5, 60),
            rep.Change(M, T0, 2.0, 1.5, 60),
        ]
        send, later = rep.split_for_send(changes, now)
        assert send == [rep.Change(M, T0, 2.0, 1.5, 60)]
        assert later == [rep.Change(M, edge, 1.0, 0.5, 60)]

    def test_refuses_an_aged_minute_with_only_5s_values(self):
        with pytest.raises(rep.Refused, match='60 s'):
            rep.split_for_send([rep.Change(M, T0 + 5, 1.0, 0.5)], T0 + 3 * DAY)

    def test_send_deferred_waits_until_every_minute_has_left_the_5s_archive(self):
        sent, slept = [], []
        clock = [T0 + DAY + 100.0]

        def sleep(s):
            slept.append(s)
            clock[0] += s

        later = [rep.Change(M, T0 + 120, 1.0, 0.5, 60), rep.Change(M, T0 + 180, 1.0, 0.5, 60)]
        rep.send_deferred(later, lambda lines: sent.append((clock[0], lines)), now=lambda: clock[0], sleep=sleep)
        ((when, lines),) = sent
        assert when > T0 + 180 + DAY
        assert lines == [f'{M} 0.5 {T0 + 120}\n', f'{M} 0.5 {T0 + 180}\n']


class TestCorrector:
    def corrector(self, until=None):
        cut = T0 + 1120
        cum = rep.cumulative_energy([(t, 3600.0) for t in range(cut, T0 + 5 * DAY, 10)], cut, 300)
        renames = (rep.Rename('tuya.boot_room_freezer', 'tuya.dishwasher', cut, cut + 1645),)
        return rep.Corrector(1.6, T0 + 47 * 60 + 35, {'tuya.dishwasher': cum}, renames, until or T0 + 4 * DAY), cut

    def test_aggregate_counters_lose_the_excess_until_their_reset(self):
        cor, _ = self.corrector()
        day = 'home.electricity.aggregate.energy_kwh_daily'
        week = 'home.electricity.aggregate.energy_kwh_weekly'
        midnight = T0 + 12 * H  # 2026-11-07 00:00
        monday_1am = T0 + 2 * DAY + 13 * H  # 2026-11-09 01:00, the week reset
        assert cor.value(day, midnight - 60, 30.0, 60) == pytest.approx(28.4)
        assert cor.value(day, midnight, 0.1, 60) is None
        assert cor.value(week, monday_1am - 60, 99.0, 60) == pytest.approx(97.4)
        assert cor.value(week, monday_1am, 0.1, 60) is None
        assert cor.value(M, T0 + 3 * DAY, 3500.0, 0) == pytest.approx(3498.4)

    def test_nothing_after_until_and_no_power_change_outside_the_window(self):
        cor, _ = self.corrector(until=T0 + 2 * DAY)
        assert cor.value(M, T0 + 2 * DAY + 5, 3500.0, 0) is None
        assert cor.value('home.electricity.aggregate.power_watts', T0 + DAY, 900.0, 60) is None

    def test_new_name_counters_are_the_integral_and_post_reset_periods_are_left(self):
        cor, cut = self.corrector()
        yearly = 'home.electricity.tuya.dishwasher.energy_kwh_yearly'
        daily = 'home.electricity.tuya.dishwasher.energy_kwh_daily'
        m = T0 + DAY  # 3.6 kW since the cutover
        assert cor.value(yearly, m, 77.0, 60) == pytest.approx(3.6 * (m + 27.5 - cut) / 3600)
        assert cor.value(yearly, m + 5, 77.0, 0) == pytest.approx(3.6 * (m + 5 - cut) / 3600)
        assert cor.value(daily, T0 + 11 * H, 5.0, 0) == pytest.approx(3.6 * (T0 + 11 * H - cut) / 3600)
        assert cor.value(daily, m, 5.0, 60) is None  # 2026-11-07: the aggregator's own post-reset counter
        assert cor.value(yearly, cut - 60, 76.0, 60) is None


class TestComposePower:
    def test_snapshot_then_60s_means_at_minute_midpoints_then_live_5s(self):
        split, fine = T0 + H, T0 + 2 * H
        snap = [(t, 1000.0) for t in range(T0, T0 + 3 * H, 10)]
        coarse = [(m, 1000.0) for m in range(T0, T0 + 3 * H, 60)]
        live = [(t, 1000.0) for t in range(fine - 600, T0 + 3 * H, 10)]
        pts = rep.compose_power(snap, coarse, live, split, fine)
        assert max(t for t, _ in pts if t < split) == split - 10
        assert [t for t, _ in pts if split <= t < fine][:2] == [split + 30, split + 90]
        assert min(t for t, _ in pts if t >= fine) == fine
        cum = rep.cumulative_energy(pts, T0, 300)
        assert rep.energy_at(cum, T0 + 3 * H - 10) == pytest.approx(kwh(1000, 3 * H - 10))


class TestCoarserArchives:
    def test_reports_a_bucket_that_is_not_the_mean_of_its_finer_points(self):
        finer = [(T0 + i * 60, float(i)) for i in range(30)]
        coarser = [(T0, 7.0), (T0 + 900, 22.0)]
        assert rep.coarser_mismatches(finer, coarser, 900) == []
        assert rep.coarser_mismatches(finer, [(T0, 7.5)], 900) == [(T0, 7.5, 7.0)]


class TestSnapshotFiles:
    def test_refuses_a_changed_file(self, tmp_path):
        (tmp_path / 'a.json').write_text('[]')
        good = rep.sha256(tmp_path / 'a.json')
        rep.check_snapshot(tmp_path, {'a.json': good})
        with pytest.raises(rep.Refused, match='a.json'):
            rep.check_snapshot(tmp_path, {'a.json': '0' * 64})
        with pytest.raises(rep.Refused):
            rep.check_snapshot(tmp_path, {'b.json': good})

    def test_loads_render_json_without_nulls(self, tmp_path):
        (tmp_path / 'a.json').write_text(json.dumps([{'target': 'x.y', 'datapoints': [[1.0, T0], [None, T0 + 5]]}]))
        assert rep.load_render_files(tmp_path, ['a.json']) == {'x.y': [(T0, 1.0)]}


class TestLogSteps:
    def test_step_round_trips_and_old_logs_read_as_5s(self, tmp_path):
        changes = [rep.Change('a.b', T0, 5.0, 4.0, 60), rep.Change('a.b', T0 + 5, 5.0, 4.0)]
        assert rep.read_log(rep.write_log(tmp_path, 'series', changes, {})) == changes
        old = tmp_path / 'old.json'
        old.write_text(json.dumps({'meta': {}, 'changes': [['a.b', T0, 5.0, 4.0]]}))
        assert rep.read_log(old) == [rep.Change('a.b', T0, 5.0, 4.0, 5)]

    def test_undo_keeps_the_step(self):
        assert rep.undo_changes([rep.Change('a', T0, 1.0, 2.0, 60)]) == [rep.Change('a', T0, 2.0, 1.0, 60)]


class TestStateAt60s:
    def test_new_names_get_only_counters_whose_period_began_before_the_cutover(self):
        cut = T0 + 1120
        cum = rep.cumulative_energy([(t, 1000.0) for t in range(cut, T0 + 4 * DAY, 10)], cut, 300)
        renames = (rep.Rename('tuya.boot_room_freezer', 'tuya.dishwasher', cut, cut + 1645),)
        last_ts = T0 + 3 * DAY + 10 * H  # Monday 2026-11-09 22:00
        rebuilt, compare = rep.rebuilt_state_values({'tuya.dishwasher': cum}, renames, last_ts)
        assert set(rebuilt['tuya.dishwasher']) == {'month_kwh', 'year_kwh'}
        assert rebuilt['tuya.dishwasher']['year_kwh'] == pytest.approx(rep.energy_at(cum, last_ts))
        assert compare['tuya.dishwasher']['day_kwh'] == pytest.approx(22.0)  # since midnight
        assert compare['tuya.dishwasher']['week_kwh'] == pytest.approx(21.0)  # since Monday 01:00

    def test_state_skips_counters_reset_since_the_window(self):
        state = state_dict(T0 + 3 * DAY + 10 * H)
        state['last_day_reset'] = T0 + 3 * DAY - 12 * H + 12 * H
        state['last_week_reset'] = T0 + 2 * DAY + 13 * H
        new, notes = rep.correct_state(state, 1.62, T0 + 47 * 60, {'tuya.dishwasher': {'year_kwh': 9.0}}, ())
        assert (new['day_kwh'], new['week_kwh']) == (17.0, 62.0)
        assert new['month_kwh'] == pytest.approx(74.0 - 1.62)
        assert new['devices']['tuya.dishwasher']['day_kwh'] == 1.0
        assert new['devices']['tuya.dishwasher']['year_kwh'] == 9.0


class TestBuildPlan:
    """2026-11-06 12:00 to 2026-11-09 22:00 in miniature: one aggregate counter pair and the dishwasher."""

    def data(self):
        cut, last_old = T0 + 1120, T0 + 2775  # on the 10 s cycle grid
        stale = 1648.9
        now = T0 + 3 * DAY + 10 * H
        until = now - 600
        snap_end = T0 + 8 * H + 44 * 60 + 35  # 20:44:35
        fine_from = now - DAY + 600
        start, end = last_old + 90, last_old + 3615
        cycles = list(range(T0 - 600 + 5, until + 1, 10))
        dish = {t: (stale if (t - cut) % 7200 < 3600 else 20.0) for t in cycles if t >= cut}
        agg_p, energy, year = [], 3470.0, []
        for i, t in enumerate(cycles):
            real = 300.0 + dish.get(t, 0.0)
            p = real + (stale if start <= t <= end else 0.0)
            if i:
                energy += kwh(p, 10)
            agg_p.append((t, p))
            year.append((t, energy))
        base = [(t, 300.0) for t in cycles]
        dpow = sorted(dish.items())
        dyear = [(t, 76.0 + i * 1e-4) for i, (t, _) in enumerate(dpow)]
        full = {
            'home.electricity.aggregate.power_watts': agg_p,
            'home.electricity.aggregate.energy_kwh_yearly': year,
            'home.electricity.aggregate.energy_kwh_daily': list(year),  # resets do not matter here
            'home.electricity.tuya.base.power_watts': base,
            'home.electricity.tuya.dishwasher.power_watts': dpow,
            'home.electricity.tuya.dishwasher.energy_kwh_yearly': dyear,
        }
        snapshot = {m: [(t, v) for t, v in pts if t <= snap_end] for m, pts in full.items()}
        live60 = {m: sorted(rep.minute_means(pts).items()) for m, pts in full.items()}
        fine = {m: [(t, v) for t, v in pts if t >= fine_from] for m, pts in full.items()}
        renames = (rep.Rename('tuya.boot_room_freezer', 'tuya.dishwasher', cut, last_old),)
        counted = [t for t in cycles if start <= t <= end]
        return snapshot, live60, fine, renames, until, now, (counted[0], counted[-1]), stale

    def plan(self, **kw):
        snapshot, live60, fine, renames, until, now, window, stale = self.data()
        live60 = kw.get('live60', live60)
        p = rep.build_plan(snapshot, live60, fine, renames, until, now, {}, 300)
        return p, (snapshot, live60, fine, renames, until, now, window, stale)

    def test_excess_and_window_come_from_the_snapshot(self):
        p, (*_, until, now, (start, end), stale) = self.plan()
        (ep,) = p.repair.episodes
        assert (ep.start, ep.end) == (start, end)
        assert p.repair.excess_kwh == pytest.approx(kwh(stale, 10) * ((end - start) // 10 + 1))
        assert end < p.split

    def test_every_minute_has_a_60s_change_and_recent_minutes_also_their_5s_points(self):
        p, (snapshot, live60, fine, renames, until, now, *_) = self.plan()
        yearly = [c for c in p.changes if c.metric == M]
        minutes = {c.ts for c in yearly if c.step == 60}
        fives = [c for c in yearly if c.step == 5]
        assert min(minutes) == T0 + 47 * 60 and max(minutes) == until - 60  # the last cycle is at until - 5
        assert fives and min(c.ts for c in fives) >= now - DAY + 600 and max(c.ts for c in fives) <= until
        assert {c.ts - c.ts % 60 for c in fives} <= minutes
        last = max((c for c in yearly if c.step == 5), key=lambda c: c.ts)
        assert last.old - last.new == pytest.approx(p.repair.excess_kwh)

    def test_post_snapshot_60s_minute_loses_exactly_the_excess(self):
        p, _ = self.plan()
        c = next(c for c in p.changes if c.metric == M and c.step == 60 and c.ts == T0 + DAY)
        assert c.old - c.new == pytest.approx(p.repair.excess_kwh)

    def test_new_name_ends_at_the_integral_of_its_own_power(self):
        p, (snapshot, live60, fine, renames, until, *_) = self.plan()
        dish = [
            c for c in p.changes if c.metric == 'home.electricity.tuya.dishwasher.energy_kwh_yearly' and c.step == 5
        ]
        last = max(dish, key=lambda c: c.ts)
        # 1648.9 W for one hour in every two since the cutover, 20 W otherwise
        hours = (last.ts - renames[0].cutover) / 3600
        on = hours // 2 + min(hours % 2, 1.0)
        assert last.new == pytest.approx(on * 1.6489 + (hours - on) * 0.02, rel=2e-3)
        assert p.cums['tuya.dishwasher'][-1][0] <= until

    def test_refuses_when_already_applied(self):
        p, (snapshot, live60, *_rest) = self.plan()
        applied = {m: list(pts) for m, pts in live60.items()}
        fixes = {(c.metric, c.ts): c.new for c in p.changes if c.step == 60}
        applied = {m: [(t, fixes.get((m, t), v)) for t, v in pts] for m, pts in applied.items()}
        with pytest.raises(rep.Refused, match='already applied'):
            self.plan(live60=applied)

    def test_refuses_when_until_has_left_the_5s_archive(self):
        snapshot, live60, fine, renames, until, now, *_ = self.data()
        with pytest.raises(rep.Refused, match='5 s archive'):
            rep.build_plan(snapshot, live60, fine, renames, now - DAY, now, {}, 300)


class TestInputs:
    def test_refuses_snapshot_power_that_differs_from_the_live_60s_archive(self):
        pts = minute_points(T0, 3, lambda t: float(t % 7))
        live = sorted(rep.minute_means(pts).items())
        rep.check_inputs({'p': pts}, {'p': live}, ['p'], T0 + 180)
        live[1] = (live[1][0], live[1][1] + 0.5)
        with pytest.raises(rep.Refused, match='p'):
            rep.check_inputs({'p': pts}, {'p': live}, ['p'], T0 + 180)

    def test_ignores_the_incomplete_minute_at_the_split(self):
        pts = minute_points(T0, 3)
        live = [(T0, 1.0), (T0 + 60, 1.0), (T0 + 120, 9.0)]
        rep.check_inputs({'p': pts}, {'p': live}, ['p'], T0 + 120)


class TestProbe:
    def test_expected_values_follow_whisper_averaging(self):
        now = T0 + 3 * DAY
        probe = rep.probe_plan('test.x', now)
        exp = probe.expected
        aligned = [c for c in probe.phases[0] if c.startswith('test.x.aligned ')]
        assert len(aligned) == 60
        hour = int(aligned[0].split()[2])
        assert hour % 3600 == 0 and now - hour > DAY + 3600
        assert exp[('test.x.aligned', 60, hour + 120)] == 2.0
        assert exp[('test.x.aligned', 900, hour)] == pytest.approx(7.0)
        assert exp[('test.x.aligned', 3600, hour)] == pytest.approx(29.5)
        assert exp[('test.x.unaligned', 60, hour)] == 5.0
        assert exp[('test.x.overwrite', 60, hour)] == 5.0 and exp[('test.x.overwrite', 900, hour)] == 4.0
        fine = [k for k in exp if k[0] == 'test.x.fine' and k[1] == 60]
        (fine_minute,) = [k[2] for k in fine]
        assert now - fine_minute < DAY - 1800 and exp[fine[0]] == pytest.approx(18.5)


class TestCheckOnce:
    """verify on a fake render that serves the archive Graphite would pick for each `from`."""

    def archives(self, now, fine_value=0.5, sixty_value=1.5):
        recent = rep.minute_of(now - 7200)  # its hour has closed
        old = rep.minute_of(now - 2 * DAY)
        five = {recent + 5 * i: fine_value for i in range(12)}
        sixty = {old: sixty_value, recent: fine_value, old + 60: 9.0}
        return recent, old, five, sixty

    def fake(self, now, five, sixty, wrong_900=False):
        def means(step, extra=0.0):
            buckets: dict = {}
            for u, v in sixty.items():
                buckets.setdefault(u - u % step, []).append(v)
            return {t: rep.mean(vs) + extra for t, vs in buckets.items()}

        def render(targets, start, end, url=None):
            age = now - start
            step = 3600 if age > 5 * 365 * DAY else 900 if age > 60 * DAY else 0
            if step:  # contiguous, None where empty, as graphite-web returns it
                values = means(step, 1.0 if wrong_900 and step == 900 else 0.0)
                return {M: [(t, values.get(t)) for t in range(start - start % step, end, step)]}
            if age > DAY:
                return {M: sorted(sixty.items())}
            return {M: sorted(five.items())}

        return render

    def test_passes_when_every_archive_holds_the_new_values(self, monkeypatch):
        now = T0 + 3 * DAY
        recent, old, five, sixty = self.archives(now)
        monkeypatch.setattr(rep, 'render', self.fake(now, five, sixty))
        changes = [rep.Change(M, t, 9.0, 0.5) for t in five] + [rep.Change(M, recent, 9.0, 0.5, 60)]
        changes.append(rep.Change(M, old, 9.0, 1.5, 60))
        res = rep.check_once(changes, 'url', now)
        assert res.ok, res

    def test_reports_wrong_values_and_coarser_buckets(self, monkeypatch):
        now = T0 + 3 * DAY
        recent, old, five, sixty = self.archives(now)
        five[recent + 5] = 7.0
        monkeypatch.setattr(rep, 'render', self.fake(now, five, sixty, wrong_900=True))
        changes = [rep.Change(M, t, 9.0, 0.5) for t in sorted(five)] + [rep.Change(M, old, 9.0, 2.5, 60)]
        res = rep.check_once(changes, 'url', now)
        assert [k[1] for k in res.bad5] == [recent + 5]
        assert {k[1] for k in res.bad60} == {recent, old}  # recent: 60 s is not the mean of its 5 s points
        wrong = {(900, old - old % 900), (900, recent - recent % 900)}
        wrong |= {(3600, old - old % 3600), (3600, recent - recent % 3600)}  # their hours no longer average them
        assert {(k[1], k[2]) for k in res.coarser} == wrong

    def test_buckets_still_being_written_are_reported_open_not_wrong(self, monkeypatch):
        now = T0 + 3 * DAY + 600  # 10 min into an hour (and a 900 s bucket) the aggregator is still writing
        current = rep.minute_of(now - 300)
        five = {current + 5 * i: 0.5 for i in range(12)}
        sixty = {current: 0.5}
        monkeypatch.setattr(rep, 'render', self.fake(now, five, sixty, wrong_900=True))
        changes = [rep.Change(M, t, 9.0, 0.5) for t in sorted(five)] + [rep.Change(M, current, 9.0, 0.5, 60)]
        res = rep.check_once(changes, 'url', now)
        assert res.ok and res.coarser == []
        assert res.open_buckets == 2  # its 900 s and 3600 s buckets


class TestSendChanges:
    def args(self, tmp_path):
        import argparse

        return argparse.Namespace(log_dir=tmp_path, carbon_host='h', carbon_port=1, render_url='u')

    def test_logs_everything_before_sending_one_form_per_minute(self, tmp_path, monkeypatch):
        now = time.time()
        recent, old = rep.minute_of(now - 3600), rep.minute_of(now - 2 * DAY)
        changes = [rep.Change(M, recent + 5, 2.0, 1.0), rep.Change(M, recent, 2.0, 1.0, 60)]
        changes.append(rep.Change(M, old, 3.0, 2.0, 60))
        sent = []

        def carbon_send(lines, host, port):
            assert list(tmp_path.glob('series_*.json')), 'log written first'
            sent.extend(lines)

        monkeypatch.setattr(rep, 'carbon_send', carbon_send)
        monkeypatch.setattr(rep, 'readback', lambda changes, url, wait: rep.Check(points=len(changes)))
        assert rep.send_changes(changes, self.args(tmp_path), 'series', {}, fetched=now) == 0
        assert sorted(sent) == sorted([f'{M} 1.0 {recent + 5}\n', f'{M} 2.0 {old}\n'])
        assert rep.read_log(next(tmp_path.glob('series_*.json'))) == changes

    def test_refuses_a_stale_plan_and_sends_nothing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rep, 'carbon_send', lambda *a: pytest.fail('sent'))
        with pytest.raises(rep.Refused, match='old'):
            rep.send_changes(
                [rep.Change(M, T0, 1.0, 2.0, 60)], self.args(tmp_path), 'series', {}, fetched=time.time() - 400
            )
        assert not list(tmp_path.iterdir())
