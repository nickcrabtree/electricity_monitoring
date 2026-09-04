"""Tests for build_metrics() in tuya_ha_bridge_to_graphite.py."""

from tuya_ha_bridge_to_graphite import build_metrics


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
