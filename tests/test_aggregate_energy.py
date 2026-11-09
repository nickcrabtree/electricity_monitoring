"""Tests for aggregate_energy pure functions: boundary helpers and apply_resets."""

from datetime import datetime, timezone

from aggregate_energy import (
    DeviceEnergyState,
    EnergyState,
    apply_resets,
    current_day_boundary,
    current_month_boundary,
    current_week_boundary,
    current_year_boundary,
    next_day_boundary,
    next_month_boundary,
    next_week_boundary,
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


class TestApplyHierarchy:
    HIER = {'tuya.shed': ['tuya.fridge', 'tuya.freezer', 'tuya.not_monitored']}

    def test_children_excluded_when_parent_present(self):
        from aggregate_energy import apply_hierarchy

        devices = {'tuya.shed': 150.0, 'tuya.fridge': 70.0, 'tuya.freezer': 60.0, 'tuya.kettle': 10.0}
        counted, excluded = apply_hierarchy(devices, self.HIER)
        assert counted == {'tuya.shed': 150.0, 'tuya.kettle': 10.0}
        assert sorted(excluded) == ['tuya.freezer', 'tuya.fridge']

    def test_children_included_when_parent_absent(self):
        from aggregate_energy import apply_hierarchy

        devices = {'tuya.fridge': 70.0, 'tuya.freezer': 60.0}
        counted, excluded = apply_hierarchy(devices, self.HIER)
        assert counted == devices
        assert excluded == []

    def test_unknown_names_ignored(self):
        from aggregate_energy import apply_hierarchy

        counted, excluded = apply_hierarchy({'tuya.kettle': 5.0}, {'tuya.ghost': ['tuya.nothing']})
        assert counted == {'tuya.kettle': 5.0}
        assert excluded == []

    def test_input_not_mutated(self):
        from aggregate_energy import apply_hierarchy

        devices = {'tuya.shed': 1.0, 'tuya.fridge': 2.0}
        apply_hierarchy(devices, self.HIER)
        assert 'tuya.fridge' in devices


class TestLoadHierarchy:
    def test_missing_file_gives_empty(self, tmp_path):
        from aggregate_energy import load_hierarchy

        assert load_hierarchy(str(tmp_path / 'nope.json')) == {}

    def test_loads_mapping(self, tmp_path):
        import json

        from aggregate_energy import load_hierarchy

        p = tmp_path / 'h.json'
        p.write_text(json.dumps({'tuya.shed': ['tuya.a']}))
        assert load_hierarchy(str(p)) == {'tuya.shed': ['tuya.a']}

    def test_shipped_file_is_valid(self):
        from aggregate_energy import HIERARCHY_FILE, load_hierarchy

        h = load_hierarchy(HIERARCHY_FILE)
        assert 'tuya.shed' in h
        assert 'tuya.shed_freezer_2026_10_27' in h['tuya.shed']
        assert 'tuya.quartz_server_shed' in h['tuya.shed']


class TestGraphiteRender:
    PAYLOAD = [
        {'target': 'home.electricity.tuya.shed.power_watts', 'datapoints': [[None, 1], [110.7, 2], [None, 3]]},
        {'target': 'home.electricity.kasa.fridge.power_watts', 'datapoints': [[5.0, 1], [6.5, 2]]},
        {'target': 'home.electricity.tuya.dead.power_watts', 'datapoints': [[None, 1], [None, 2]]},
        {'target': 'home.electricity.meter.power_watts', 'datapoints': [[1.0, 1]]},
        {'target': 'home.electricity.aggregate.power_watts', 'datapoints': [[1.0, 1]]},
    ]

    def test_parse_takes_last_non_null_point_per_device(self):
        from aggregate_energy import parse_render_power

        got = parse_render_power(self.PAYLOAD)
        assert got == {'tuya.shed': 110.7, 'kasa.fridge': 6.5}

    def test_parse_ignores_malformed_entries(self):
        from aggregate_energy import parse_render_power

        got = parse_render_power([{'target': 'home.electricity.tuya.x.power_watts'}, 'junk', {'datapoints': [[1, 1]]}])
        assert got == {}

    def test_get_power_uses_one_http_fetch_and_no_ssh(self, monkeypatch):
        import subprocess

        import aggregate_energy

        calls = []

        def fake_fetch(url, timeout):
            calls.append(url)
            return self.PAYLOAD

        def no_ssh(*a, **kw):
            raise AssertionError('ssh/subprocess must not be used')

        monkeypatch.setattr(aggregate_energy, 'fetch_render_json', fake_fetch)
        monkeypatch.setattr(subprocess, 'run', no_ssh)
        monkeypatch.setattr(aggregate_energy.time, 'time', lambda: 3.0)  # the payload's clock, so no point is stale
        got = aggregate_energy.get_device_power_from_graphite()
        assert got == {'tuya.shed': 110.7, 'kasa.fridge': 6.5}
        assert len(calls) == 1
        assert 'format=json' in calls[0] and 'power_watts' in calls[0]

    def test_get_power_returns_empty_on_fetch_error(self, monkeypatch):
        import aggregate_energy

        def boom(url, timeout):
            raise OSError('down')

        monkeypatch.setattr(aggregate_energy, 'fetch_render_json', boom)
        assert aggregate_energy.get_device_power_from_graphite() == {}


class TestStaleDevices:
    """A device whose newest point is older than the staleness limit must not count (2026-11-06 rename incident)."""

    NOW = 1_793_973_000  # 2026-11-06 12:50:00 UTC
    LIMIT = 300

    def payload(self, fresh_age=10, stale_age=301):
        return [
            {
                'target': 'home.electricity.tuya.dishwasher.power_watts',
                'datapoints': [[1600.0, self.NOW - 60], [1648.9, self.NOW - fresh_age], [None, self.NOW]],
            },
            {
                'target': 'home.electricity.tuya.boot_room_freezer.power_watts',
                'datapoints': [[1648.9, self.NOW - stale_age], [None, self.NOW - 5], [None, self.NOW]],
            },
        ]

    def test_limit_is_a_named_config_constant_above_the_slowest_healthy_gap(self):
        import config

        # Healthy collectors leave gaps of up to 240 s (main_oven via flint); real outages are longer.
        assert 240 < config.GRAPHITE_STALE_AFTER_SECONDS <= 600

    def test_parse_drops_device_whose_newest_point_is_too_old(self):
        from aggregate_energy import parse_render_power

        got = parse_render_power(self.payload(), now=self.NOW, stale_after=self.LIMIT)
        assert got == {'tuya.dishwasher': 1648.9}

    def test_parse_keeps_device_exactly_at_the_limit(self):
        from aggregate_energy import parse_render_power

        got = parse_render_power(self.payload(stale_age=self.LIMIT), now=self.NOW, stale_after=self.LIMIT)
        assert got == {'tuya.dishwasher': 1648.9, 'tuya.boot_room_freezer': 1648.9}

    def test_parse_reports_stale_keys(self):
        from aggregate_energy import parse_render_points, split_stale

        points = parse_render_points(self.payload())
        assert points['tuya.boot_room_freezer'] == (self.NOW - 301, 1648.9)
        fresh, stale = split_stale(points, now=self.NOW, stale_after=self.LIMIT)
        assert fresh == {'tuya.dishwasher': 1648.9}
        assert stale == ['tuya.boot_room_freezer']

    def test_get_power_uses_wall_clock_and_config_limit(self, monkeypatch):
        import aggregate_energy

        monkeypatch.setattr(aggregate_energy, 'fetch_render_json', lambda url, timeout: self.payload())
        monkeypatch.setattr(aggregate_energy.time, 'time', lambda: float(self.NOW))
        monkeypatch.setattr(aggregate_energy.config, 'GRAPHITE_STALE_AFTER_SECONDS', self.LIMIT)
        assert aggregate_energy.get_device_power_from_graphite() == {'tuya.dishwasher': 1648.9}

    def test_stale_device_not_summed_not_sent_and_state_untouched(self, monkeypatch, tmp_path):
        import asyncio

        import aggregate_energy

        state = aggregate_energy.EnergyState(last_ts=self.NOW - 10.0)
        frozen = aggregate_energy.DeviceEnergyState(
            last_power_w=1648.9, day_kwh=1.5, week_kwh=2, month_kwh=3, year_kwh=4
        )
        state.devices['tuya.boot_room_freezer'] = aggregate_energy.DeviceEnergyState(**vars(frozen))
        state.devices['tuya.dishwasher'] = aggregate_energy.DeviceEnergyState(last_power_w=1648.9)
        sent = []
        monkeypatch.setattr(aggregate_energy, 'fetch_render_json', lambda url, timeout: self.payload())
        monkeypatch.setattr(aggregate_energy.time, 'time', lambda: float(self.NOW))
        monkeypatch.setattr(aggregate_energy.config, 'GRAPHITE_STALE_AFTER_SECONDS', self.LIMIT)
        monkeypatch.setattr(aggregate_energy, 'load_hierarchy', lambda: {})
        monkeypatch.setattr(aggregate_energy, 'send_metrics', lambda host, port, metrics: sent.extend(metrics) or 0)
        monkeypatch.setattr(aggregate_energy, 'STATE_FILE', str(tmp_path / 'energy_state.json'))
        monkeypatch.setattr(aggregate_energy, 'local_now', lambda: datetime.fromtimestamp(self.NOW, tz=timezone.utc))

        asyncio.run(aggregate_energy.compute_and_send(state))

        names = dict(sent)
        assert names['home.electricity.aggregate.power_watts'] == 1648.9
        assert not any('boot_room_freezer' in name for name in names)
        assert 'home.electricity.tuya.dishwasher.energy_kwh_daily' in names
        assert state.devices['tuya.boot_room_freezer'] == frozen
        # whole-house energy grew by the fresh device only: 1648.9 W for 10 s
        assert abs(state.day_kwh - 1648.9 * 10 / 3.6e6) < 1e-12
