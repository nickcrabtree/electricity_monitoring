"""Tests for device_names: load/save/get/set with file-based persistence."""

import sys
import os
import json
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
import device_names as dn


@pytest.fixture(autouse=True)
def reset_cache(tmp_path, monkeypatch):
    """Point DEVICE_NAMES_FILE at a temp path and reset module cache before each test."""
    tmp_file = str(tmp_path / "device_names.json")
    monkeypatch.setattr(dn, "DEVICE_NAMES_FILE", tmp_file)
    monkeypatch.setattr(dn, "_cached_names", None)
    monkeypatch.setattr(dn, "_cache_mtime", 0)
    yield


class TestLoadDeviceNames:
    def test_returns_empty_dict_when_file_missing(self):
        assert dn.load_device_names() == {}

    def test_loads_valid_json(self, tmp_path):
        data = {"AA:BB:CC:DD:EE:FF": "kitchen plug"}
        dn.DEVICE_NAMES_FILE = str(tmp_path / "device_names.json")
        with open(dn.DEVICE_NAMES_FILE, "w") as f:
            json.dump(data, f)
        assert dn.load_device_names() == data

    def test_returns_cached_data_when_file_unchanged(self, tmp_path):
        data = {"id1": "name1"}
        dn.DEVICE_NAMES_FILE = str(tmp_path / "device_names.json")
        with open(dn.DEVICE_NAMES_FILE, "w") as f:
            json.dump(data, f)
        first = dn.load_device_names()
        # Corrupt the file — cache should still return original
        with open(dn.DEVICE_NAMES_FILE, "w") as f:
            f.write("CORRUPT")
        second = dn.load_device_names()
        assert second == first

    def test_returns_cache_on_invalid_json(self, tmp_path):
        dn.DEVICE_NAMES_FILE = str(tmp_path / "device_names.json")
        with open(dn.DEVICE_NAMES_FILE, "w") as f:
            json.dump({"id1": "name1"}, f)
        dn.load_device_names()  # populate cache
        # Now write bad JSON and bump mtime
        import time; time.sleep(0.01)
        with open(dn.DEVICE_NAMES_FILE, "w") as f:
            f.write("{bad json")
        result = dn.load_device_names()
        assert result == {"id1": "name1"}


class TestSaveDeviceNames:
    def test_saves_and_reloads(self):
        data = {"id1": "my device"}
        assert dn.save_device_names(data) is True
        assert dn.load_device_names() == data

    def test_refuses_empty_dict(self):
        assert dn.save_device_names({}) is False

    def test_filters_out_empty_values(self):
        assert dn.save_device_names({"id1": "name", "id2": "", "id3": None}) is True
        result = dn.load_device_names()
        assert "id1" in result
        assert "id2" not in result
        assert "id3" not in result

    def test_refuses_non_dict(self):
        assert dn.save_device_names(["not", "a", "dict"]) is False


class TestGetDeviceName:
    def test_returns_empty_string_for_empty_id(self):
        assert dn.get_device_name("") == ""

    def test_returns_existing_mapping(self):
        dn.save_device_names({"mac1": "Living Room"})
        assert dn.get_device_name("mac1") == "Living Room"

    def test_saves_and_returns_fallback_for_new_device(self):
        result = dn.get_device_name("new_mac", fallback_name="New Plug")
        assert result == "New Plug"
        assert dn.load_device_names().get("new_mac") == "New Plug"

    def test_does_not_overwrite_existing_with_fallback(self):
        dn.save_device_names({"mac1": "Original Name"})
        result = dn.get_device_name("mac1", fallback_name="Different Name")
        assert result == "Original Name"

    def test_returns_device_id_as_last_resort(self):
        assert dn.get_device_name("mac_unknown") == "mac_unknown"


class TestSetDeviceName:
    def test_sets_new_name(self):
        assert dn.set_device_name("mac1", "My Plug") is True
        assert dn.get_device_name("mac1") == "My Plug"

    def test_updates_existing_name(self):
        dn.save_device_names({"mac1": "Old Name"})
        dn.set_device_name("mac1", "New Name")
        assert dn.get_device_name("mac1") == "New Name"

    def test_refuses_empty_id_or_name(self):
        assert dn.set_device_name("", "name") is False
        assert dn.set_device_name("id", "") is False
