"""Tests for MetricScaler: canonical code mapping and normalization."""

import pytest
from metric_scaling import MetricScaler, CURRENT_MA_TO_AMPS_DIVISOR


class TestCanonicalCode:
    def setup_method(self):
        self.scaler = MetricScaler(devices_json_path="/dev/null")

    def test_power_variants_map_to_cur_power(self):
        for code in ('power', 'power_w', 'add_ele'):
            assert self.scaler._canonical_code(code) == 'cur_power', code

    def test_voltage_variants_map_to_cur_voltage(self):
        for code in ('voltage', 'va_voltage'):
            assert self.scaler._canonical_code(code) == 'cur_voltage', code

    def test_current_variants_map_to_cur_current(self):
        for code in ('electric_current', 'i_current'):
            assert self.scaler._canonical_code(code) == 'cur_current', code

    def test_unknown_code_returned_unchanged(self):
        assert self.scaler._canonical_code('add_ele_ampere') == 'add_ele_ampere'


class TestNormalizeByCode:
    def setup_method(self):
        self.scaler = MetricScaler(devices_json_path="/dev/null")

    def test_none_returns_none(self):
        assert self.scaler.normalize_by_code('dev1', 'cur_power', None) is None

    def test_non_numeric_returns_none(self):
        assert self.scaler.normalize_by_code('dev1', 'cur_power', 'bad') is None

    def test_cur_power_divides_by_10(self):
        result = self.scaler.normalize_by_code('dev1', 'cur_power', 100)
        assert result == pytest.approx(10.0)

    def test_cur_voltage_divides_by_10(self):
        result = self.scaler.normalize_by_code('dev1', 'cur_voltage', 2300)
        assert result == pytest.approx(230.0)

    def test_cur_current_converts_ma_to_amps(self):
        # scale=0 (no divide), then /1000 for mA→A
        result = self.scaler.normalize_by_code('dev1', 'cur_current', 500)
        assert result == pytest.approx(500 / CURRENT_MA_TO_AMPS_DIVISOR)

    def test_unknown_metric_returns_raw_value(self):
        result = self.scaler.normalize_by_code('dev1', 'some_unknown_metric', 42.0)
        assert result == pytest.approx(42.0)


class TestNormalizeByDps:
    def setup_method(self):
        self.scaler = MetricScaler(devices_json_path="/dev/null")

    def test_dps_19_is_power(self):
        result = self.scaler.normalize_by_dps('dev1', '19', 500)
        assert result == pytest.approx(50.0)

    def test_dps_20_is_voltage(self):
        result = self.scaler.normalize_by_dps('dev1', '20', 2300)
        assert result == pytest.approx(230.0)

    def test_dps_18_is_current_in_amps(self):
        result = self.scaler.normalize_by_dps('dev1', '18', 1000)
        assert result == pytest.approx(1000 / CURRENT_MA_TO_AMPS_DIVISOR)

    def test_none_returns_none(self):
        assert self.scaler.normalize_by_dps('dev1', '19', None) is None
