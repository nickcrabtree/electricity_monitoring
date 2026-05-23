"""Tests for graphite_helper: format_device_name normalization."""

import pytest
from graphite_helper import format_device_name


class TestFormatDeviceName:
    def test_lowercases(self):
        assert format_device_name("Lamp") == "lamp"

    def test_replaces_spaces_with_underscores(self):
        assert format_device_name("Living Room Lamp") == "living_room_lamp"

    def test_replaces_dashes_with_underscores(self):
        assert format_device_name("kitchen-lamp") == "kitchen_lamp"

    def test_removes_special_chars(self):
        assert format_device_name("lamp (main)") == "lamp_main"

    def test_collapses_consecutive_underscores(self):
        assert format_device_name("lamp  room") == "lamp_room"

    def test_strips_leading_trailing_underscores(self):
        assert format_device_name("_lamp_") == "lamp"

    def test_empty_string(self):
        assert format_device_name("") == ""

    def test_already_normalized(self):
        assert format_device_name("living_room_lamp") == "living_room_lamp"

    def test_numbers_preserved(self):
        assert format_device_name("plug 2") == "plug_2"
