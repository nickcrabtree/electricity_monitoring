import json
import logging

from tuya_local_to_graphite import (
    load_local_device_overrides,
    merge_local_device_overrides,
)


def write_overrides(path, data):
    path.write_text(json.dumps(data))
    path.chmod(0o600)


def test_load_local_device_overrides_accepts_complete_device(tmp_path):
    path = tmp_path / 'tuya_local_devices.json'
    write_overrides(
        path,
        {
            'devices': [
                {
                    'id': 'device-id',
                    'name': 'Ensuite shower pump',
                    'ip': '192.168.1.155',
                    'key': '0123456789abcdef',
                    'version': '3.4',
                }
            ]
        },
    )

    assert load_local_device_overrides(str(path)) == {
        'device-id': {
            'name': 'Ensuite shower pump',
            'ip': '192.168.1.155',
            'key': '0123456789abcdef',
            'version': '3.4',
        }
    }


def test_load_local_device_overrides_rejects_missing_or_invalid_keys(tmp_path, caplog):
    path = tmp_path / 'tuya_local_devices.json'
    private_key = 'not-a-local-key'
    write_overrides(
        path,
        {
            'devices': [
                {
                    'id': 'missing-key',
                    'ip': '192.168.1.155',
                    'version': '3.4',
                },
                {
                    'id': 'short-key',
                    'name': 'Short key device',
                    'ip': '192.168.1.156',
                    'key': private_key,
                    'version': '3.4',
                },
            ]
        },
    )

    with caplog.at_level(logging.WARNING):
        assert load_local_device_overrides(str(path)) == {}

    assert private_key not in caplog.text


def test_load_local_device_overrides_rejects_missing_or_insecure_file(tmp_path):
    missing_path = tmp_path / 'missing.json'
    assert load_local_device_overrides(str(missing_path)) == {}

    insecure_path = tmp_path / 'insecure.json'
    insecure_path.write_text('{"devices": []}')
    insecure_path.chmod(0o644)

    assert load_local_device_overrides(str(insecure_path)) == {}


def test_merge_local_device_overrides_keeps_discovered_metadata():
    discovered = {
        'device-id': {
            'name': 'Unknown',
            'ip': '192.168.1.155',
            'version': '3.3',
            'mac': 'aa:bb:cc:dd:ee:ff',
        }
    }
    overrides = {
        'device-id': {
            'name': 'Ensuite shower pump',
            'ip': '192.168.1.155',
            'key': '0123456789abcdef',
            'version': '3.4',
        }
    }

    assert merge_local_device_overrides(discovered, overrides) == {
        'device-id': {
            'name': 'Ensuite shower pump',
            'ip': '192.168.1.155',
            'key': '0123456789abcdef',
            'version': '3.4',
            'mac': 'aa:bb:cc:dd:ee:ff',
        }
    }
