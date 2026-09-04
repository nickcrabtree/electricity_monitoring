"""Tests for build_metrics() and energy-delta power derivation in tuya_ha_bridge_to_graphite.py."""

import pytest

from tuya_ha_bridge_to_graphite import add_derived_power, build_metrics, derive_power_from_energy


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
