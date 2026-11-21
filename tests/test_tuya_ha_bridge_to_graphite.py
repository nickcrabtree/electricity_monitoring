"""Tests for build_metrics() and energy-delta power derivation in tuya_ha_bridge_to_graphite.py."""

import json
from pathlib import Path

import pytest

from tuya_ha_bridge_to_graphite import (
    add_derived_power,
    build_metrics,
    configured_direct_power_bases,
    derive_power_from_energy,
    devices_covered_by_graphite,
    fetch_newest_point_ts,
    filter_devices_needing_fallback,
    load_recent_local_successes,
)

AMARYLLIS_HEATER = {
    'name': 'Amaryllis heater',
    'switch_entity': 'switch.amaryllis_heater_socket_1',
    'sensors': {
        'power_watts': 'sensor.amaryllis_heater_power',
        'voltage_volts': 'sensor.amaryllis_heater_voltage',
        'current_amps': 'sensor.amaryllis_heater_current',
        'total_kwh': 'sensor.amaryllis_heater_total_energy',
    },
}

WATER_BUTT_PUMP = {
    'name': 'Big water butt pump',
    'switch_entity': 'switch.antela_smrat_plug_4_socket_1',
    'sensors': {
        'total_kwh': 'sensor.antela_smrat_plug_4_total_energy',
    },
}


def _state(entity_id, state):
    return {'entity_id': entity_id, 'state': state}


class TestBuildMetrics:
    def test_full_metrics_device(self):
        states = [
            _state('switch.amaryllis_heater_socket_1', 'on'),
            _state('sensor.amaryllis_heater_power', '120.5'),
            _state('sensor.amaryllis_heater_voltage', '237.7'),
            _state('sensor.amaryllis_heater_current', '0.51'),
            _state('sensor.amaryllis_heater_total_energy', '0.001'),
        ]
        metrics = build_metrics(states, [AMARYLLIS_HEATER])
        by_name = dict(metrics)
        assert by_name['home.electricity.tuya.amaryllis_heater.is_on'] == 1
        assert by_name['home.electricity.tuya.amaryllis_heater.power_watts'] == 120.5
        assert by_name['home.electricity.tuya.amaryllis_heater.voltage_volts'] == 237.7
        assert by_name['home.electricity.tuya.amaryllis_heater.current_amps'] == 0.51
        assert by_name['home.electricity.tuya.amaryllis_heater.total_kwh'] == 0.001

    def test_switch_off_is_zero(self):
        states = [_state('switch.amaryllis_heater_socket_1', 'off')]
        metrics = build_metrics(states, [AMARYLLIS_HEATER])
        assert dict(metrics)['home.electricity.tuya.amaryllis_heater.is_on'] == 0

    def test_energy_only_device_has_no_is_on_without_switch_state(self):
        states = [_state('sensor.antela_smrat_plug_4_total_energy', '0.059')]
        metrics = build_metrics(states, [WATER_BUTT_PUMP])
        by_name = dict(metrics)
        assert by_name['home.electricity.tuya.big_water_butt_pump.total_kwh'] == 0.059
        assert 'home.electricity.tuya.big_water_butt_pump.is_on' not in by_name

    def test_missing_entity_skipped_not_crashed(self):
        metrics = build_metrics([], [AMARYLLIS_HEATER])
        assert metrics == []

    def test_unavailable_sensor_state_skipped(self):
        states = [_state('sensor.amaryllis_heater_power', 'unavailable')]
        metrics = build_metrics(states, [AMARYLLIS_HEATER])
        assert dict(metrics) == {}

    def test_unknown_sensor_state_skipped(self):
        states = [_state('sensor.amaryllis_heater_power', 'unknown')]
        metrics = build_metrics(states, [AMARYLLIS_HEATER])
        assert dict(metrics) == {}

    @pytest.mark.parametrize('value', ['nan', 'inf', '-inf'])
    def test_non_finite_sensor_state_skipped(self, value):
        states = [_state('sensor.amaryllis_heater_power', value)]
        metrics = build_metrics(states, [AMARYLLIS_HEATER])
        assert dict(metrics) == {}

    def test_zero_sensor_state_is_preserved(self):
        states = [_state('sensor.amaryllis_heater_power', '0')]
        metrics = build_metrics(states, [AMARYLLIS_HEATER])
        assert dict(metrics)['home.electricity.tuya.amaryllis_heater.power_watts'] == 0

    def test_multiple_devices(self):
        states = [
            _state('switch.amaryllis_heater_socket_1', 'on'),
            _state('sensor.amaryllis_heater_power', '120.5'),
            _state('sensor.antela_smrat_plug_4_total_energy', '0.059'),
        ]
        metrics = build_metrics(states, [AMARYLLIS_HEATER, WATER_BUTT_PUMP])
        by_name = dict(metrics)
        assert by_name['home.electricity.tuya.amaryllis_heater.power_watts'] == 120.5
        assert by_name['home.electricity.tuya.big_water_butt_pump.total_kwh'] == 0.059

    def test_device_without_name_skipped(self):
        bad_device = {'sensors': {'total_kwh': 'sensor.x'}}
        states = [_state('sensor.x', '1.0')]
        metrics = build_metrics(states, [bad_device])
        assert metrics == []

    def test_empty_device_list(self):
        states = [_state('sensor.amaryllis_heater_power', '120.5')]
        metrics = build_metrics(states, [])
        assert metrics == []


class TestDerivePowerFromEnergy:
    def test_basic_delta(self):
        # 0.1 kWh over 1 hour = 100 W
        power = derive_power_from_energy(prev_kwh=1.0, prev_ts=0, curr_kwh=1.1, curr_ts=3600)
        assert power == pytest.approx(100.0)

    def test_no_elapsed_time_returns_none(self):
        assert derive_power_from_energy(prev_kwh=1.0, prev_ts=1000, curr_kwh=1.1, curr_ts=1000) is None

    def test_negative_elapsed_time_returns_none(self):
        assert derive_power_from_energy(prev_kwh=1.0, prev_ts=2000, curr_kwh=1.1, curr_ts=1000) is None

    def test_counter_reset_returns_none(self):
        # curr < prev - device restarted / re-paired, counter reset to 0
        assert derive_power_from_energy(prev_kwh=5.0, prev_ts=0, curr_kwh=0.1, curr_ts=3600) is None

    def test_zero_delta_is_zero_watts(self):
        power = derive_power_from_energy(prev_kwh=1.0, prev_ts=0, curr_kwh=1.0, curr_ts=3600)
        assert power == 0.0


class TestAddDerivedPower:
    def test_first_poll_no_prior_state_no_derived_metric(self):
        metrics = [('home.electricity.tuya.shower.total_kwh', 1.0)]
        state = {}
        result = add_derived_power(metrics, state, now_ts=1000)
        assert result == metrics
        assert state['home.electricity.tuya.shower'] == {'total_kwh': 1.0, 'ts': 1000}

    def test_second_poll_derives_power(self):
        metrics = [('home.electricity.tuya.shower.total_kwh', 1.1)]
        state = {'home.electricity.tuya.shower': {'total_kwh': 1.0, 'ts': 1000}}
        result = add_derived_power(metrics, state, now_ts=4600)  # +3600s, +0.1 kWh
        by_name = dict(result)
        assert by_name['home.electricity.tuya.shower.power_watts'] == pytest.approx(100.0)
        assert state['home.electricity.tuya.shower'] == {'total_kwh': 1.1, 'ts': 4600}

    def test_does_not_override_real_power_sensor(self):
        metrics = [
            ('home.electricity.tuya.amaryllis_heater.total_kwh', 1.1),
            ('home.electricity.tuya.amaryllis_heater.power_watts', 55.0),
        ]
        state = {'home.electricity.tuya.amaryllis_heater': {'total_kwh': 1.0, 'ts': 1000}}
        result = add_derived_power(metrics, state, now_ts=4600)
        by_name = dict(result)
        assert by_name['home.electricity.tuya.amaryllis_heater.power_watts'] == 55.0

    def test_configured_direct_power_outage_does_not_get_derived_substitute(self):
        metrics = [('home.electricity.tuya.amaryllis_heater.total_kwh', 1.1)]
        state = {'home.electricity.tuya.amaryllis_heater': {'total_kwh': 1.0, 'ts': 1000}}
        result = add_derived_power(
            metrics,
            state,
            now_ts=4600,
            direct_power_bases={'home.electricity.tuya.amaryllis_heater'},
        )
        assert result == metrics

    def test_counter_reset_skips_derived_metric_but_updates_state(self):
        metrics = [('home.electricity.tuya.shower.total_kwh', 0.1)]
        state = {'home.electricity.tuya.shower': {'total_kwh': 5.0, 'ts': 1000}}
        result = add_derived_power(metrics, state, now_ts=4600)
        assert result == metrics  # no power_watts appended
        assert state['home.electricity.tuya.shower'] == {'total_kwh': 0.1, 'ts': 4600}

    def test_multiple_devices_independent_state(self):
        metrics = [
            ('home.electricity.tuya.shower.total_kwh', 1.1),
            ('home.electricity.tuya.big_water_butt_pump.total_kwh', 0.06),
        ]
        state = {
            'home.electricity.tuya.shower': {'total_kwh': 1.0, 'ts': 1000},
            'home.electricity.tuya.big_water_butt_pump': {'total_kwh': 0.059, 'ts': 1000},
        }
        result = add_derived_power(metrics, state, now_ts=4600)
        by_name = dict(result)
        assert by_name['home.electricity.tuya.shower.power_watts'] == pytest.approx(100.0)
        assert by_name['home.electricity.tuya.big_water_butt_pump.power_watts'] == pytest.approx(1.0)

    def test_direct_power_bases_are_read_from_device_configuration(self):
        devices = [AMARYLLIS_HEATER, WATER_BUTT_PUMP]
        assert configured_direct_power_bases(devices) == {
            'home.electricity.tuya.amaryllis_heater',
        }


SHOWER = {
    'name': 'Shower',
    'tuya_device_id': 'bf442ad41a4f6ea3205qwb',
    'switch_entity': 'switch.c_wf_ssfs_socket_1',
    'sensors': {'total_kwh': 'sensor.c_wf_ssfs_total_energy'},
}


def _write_local_state(path, devices):
    import json as _json

    with open(path, 'w') as f:
        _json.dump({'version': 1, 'devices': devices}, f)


class TestLoadRecentLocalSuccesses:
    def test_missing_file_returns_empty(self, tmp_path):
        result = load_recent_local_successes(str(tmp_path / 'nope.json'), now=1000, ttl_seconds=100)
        assert result == {}

    def test_fresh_entry_included(self, tmp_path):
        path = tmp_path / 'tuya_local_state.json'
        _write_local_state(path, {'bf442ad41a4f6ea3205qwb': {'last_success_ts': 950}})
        result = load_recent_local_successes(str(path), now=1000, ttl_seconds=100)
        assert result == {'bf442ad41a4f6ea3205qwb': 950}

    def test_stale_entry_excluded(self, tmp_path):
        path = tmp_path / 'tuya_local_state.json'
        _write_local_state(path, {'bf442ad41a4f6ea3205qwb': {'last_success_ts': 800}})
        result = load_recent_local_successes(str(path), now=1000, ttl_seconds=100)
        assert result == {}

    def test_malformed_file_returns_empty(self, tmp_path):
        path = tmp_path / 'tuya_local_state.json'
        path.write_text('not json')
        result = load_recent_local_successes(str(path), now=1000, ttl_seconds=100)
        assert result == {}


class TestFilterDevicesNeedingFallback:
    def test_device_with_no_local_coverage_needs_fallback(self):
        result = filter_devices_needing_fallback([SHOWER], recent_local_successes={})
        assert result == [SHOWER]

    def test_device_with_recent_local_success_skipped(self):
        recent = {'bf442ad41a4f6ea3205qwb': 950}
        result = filter_devices_needing_fallback([SHOWER], recent_local_successes=recent)
        assert result == []

    def test_device_without_tuya_device_id_always_needs_fallback(self):
        no_id_device = {'name': 'Mystery', 'sensors': {'total_kwh': 'sensor.mystery_energy'}}
        recent = {'bf442ad41a4f6ea3205qwb': 950}  # unrelated device
        result = filter_devices_needing_fallback([no_id_device], recent_local_successes=recent)
        assert result == [no_id_device]

    def test_mixed_devices(self):
        recent = {'bf442ad41a4f6ea3205qwb': 950}  # covers SHOWER
        water_butt = {
            'name': 'Big water butt pump',
            'tuya_device_id': 'bf5cbebcc1b18d8615zhvq',
            'sensors': {'total_kwh': 'sensor.antela_smrat_plug_4_total_energy'},
        }
        result = filter_devices_needing_fallback([SHOWER, water_butt], recent_local_successes=recent)
        assert result == [water_butt]


def test_ensuite_shower_uses_verified_tuya_identity():
    devices = json.loads((Path(__file__).parent.parent / 'ha_bridge_devices.json').read_text())
    ensuite = next(device for device in devices if device['name'] == 'Ensuite shower pump')
    assert ensuite['tuya_device_id'] == 'bf0ea1e2ab51599d86ckjp'
    assert ensuite['switch_entity'] == 'switch.ensuite_shower_pump_socket_1'
    assert ensuite['sensors'] == {
        'current_amps': 'sensor.ensuite_shower_pump_current',
        'power_watts': 'sensor.ensuite_shower_pump_power',
    }


ENSUITE = {
    'name': 'Ensuite shower pump',
    'tuya_device_id': 'bf0ea1e2ab51599d86ckjp',
    'switch_entity': 'switch.ensuite_shower_pump_socket_1',
    'sensors': {
        'current_amps': 'sensor.ensuite_shower_pump_current',
        'power_watts': 'sensor.ensuite_shower_pump_power',
    },
}
ENSUITE_VOLTAGE_SERIES = 'home.electricity.tuya.ensuite_shower_pump.voltage_volts'


class TestDevicesCoveredByGraphite:
    """A device polled locally by another host (flint) is seen as fresh points the bridge never writes."""

    def test_fresh_voltage_point_means_covered(self):
        asked = []

        def fetch(series, window_seconds):
            asked.append(series)
            return 990

        result = devices_covered_by_graphite([ENSUITE], already_covered={}, now=1000, ttl_seconds=100, fetch=fetch)
        assert result == {'bf0ea1e2ab51599d86ckjp': 990}
        # the voltage series: the bridge writes current, power and is_on for this device but never voltage
        assert asked == [ENSUITE_VOLTAGE_SERIES]

    def test_stale_point_means_not_covered(self):
        result = devices_covered_by_graphite(
            [ENSUITE], already_covered={}, now=1000, ttl_seconds=100, fetch=lambda s, w: 800
        )
        assert result == {}

    def test_no_point_or_unreachable_graphite_means_not_covered(self):
        result = devices_covered_by_graphite(
            [ENSUITE], already_covered={}, now=1000, ttl_seconds=100, fetch=lambda s, w: None
        )
        assert result == {}

    def test_device_already_covered_locally_is_not_queried(self):
        def fetch(series, window_seconds):
            raise AssertionError('must not query Graphite for a locally covered device')

        result = devices_covered_by_graphite(
            [ENSUITE], already_covered={'bf0ea1e2ab51599d86ckjp': 990}, now=1000, ttl_seconds=100, fetch=fetch
        )
        assert result == {}

    def test_device_whose_bridge_writes_voltage_is_not_checked(self):
        # The bridge's own voltage points would read as another host's coverage and make it flap.
        def fetch(series, window_seconds):
            raise AssertionError('voltage is a bridge series for this device')

        heater = dict(AMARYLLIS_HEATER, tuya_device_id='bf5fe742cb004620efteys')
        assert devices_covered_by_graphite([heater], already_covered={}, now=1000, ttl_seconds=100, fetch=fetch) == {}

    def test_device_without_tuya_device_id_is_not_checked(self):
        def fetch(series, window_seconds):
            raise AssertionError('no id to report coverage under')

        no_id = {'name': 'Mystery', 'sensors': {'total_kwh': 'sensor.mystery_energy'}}
        assert devices_covered_by_graphite([no_id], already_covered={}, now=1000, ttl_seconds=100, fetch=fetch) == {}

    def test_covered_device_is_skipped_by_the_fallback_filter(self):
        covered = devices_covered_by_graphite(
            [ENSUITE], already_covered={}, now=1000, ttl_seconds=100, fetch=lambda s, w: 990
        )
        assert filter_devices_needing_fallback([ENSUITE], covered) == []


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def read(self, *args):
        return json.dumps(self._payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TestFetchNewestPointTs:
    def test_returns_timestamp_of_newest_non_null_point(self, monkeypatch):
        payload = [{'target': ENSUITE_VOLTAGE_SERIES, 'datapoints': [[239.0, 900], [240.0, 910], [None, 920]]}]
        urls = []

        def fake_urlopen(url, timeout=None):
            urls.append(url)
            return _FakeResponse(payload)

        monkeypatch.setattr('tuya_ha_bridge_to_graphite.urllib.request.urlopen', fake_urlopen)
        assert fetch_newest_point_ts(ENSUITE_VOLTAGE_SERIES, 300) == 910
        assert 'noCache=1' in urls[0]
        assert 'format=json' in urls[0]
        assert 'from=-300s' in urls[0]
        assert ENSUITE_VOLTAGE_SERIES in urls[0]

    def test_no_points_returns_none(self, monkeypatch):
        payload = [{'target': ENSUITE_VOLTAGE_SERIES, 'datapoints': [[None, 900]]}]
        monkeypatch.setattr(
            'tuya_ha_bridge_to_graphite.urllib.request.urlopen', lambda url, timeout=None: _FakeResponse(payload)
        )
        assert fetch_newest_point_ts(ENSUITE_VOLTAGE_SERIES, 300) is None

    def test_network_error_returns_none(self, monkeypatch):
        def boom(url, timeout=None):
            raise OSError('unreachable')

        monkeypatch.setattr('tuya_ha_bridge_to_graphite.urllib.request.urlopen', boom)
        assert fetch_newest_point_ts(ENSUITE_VOLTAGE_SERIES, 300) is None

    def test_garbage_response_returns_none(self, monkeypatch):
        monkeypatch.setattr(
            'tuya_ha_bridge_to_graphite.urllib.request.urlopen', lambda url, timeout=None: _FakeResponse('nope')
        )
        assert fetch_newest_point_ts(ENSUITE_VOLTAGE_SERIES, 300) is None
