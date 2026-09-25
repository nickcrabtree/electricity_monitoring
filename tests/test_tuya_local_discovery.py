"""Tests for cross-subnet discovery helpers in tuya_local_to_graphite.

Devices on the OpenWrt subnet (192.168.1.0/24, polled by flint) never answer a
tinytuya broadcast on the main LAN, so ``--discover`` on quartz/blackpi2 used to
miss them entirely. These helpers merge a raw ``tinytuya.deviceScan`` result
obtained over SSH from another host with the local ``devices.json`` metadata,
and report which known devices were not seen on any subnet.
"""

import subprocess

import tuya_local_to_graphite as tl

DEVICES_JSON = [
    {'id': 'bf52baba2305f136datzwi', 'name': 'Main Oven', 'key': 'k-main', 'version': '3.4'},
    {'id': 'bf64fd8c51015ede6db23j', 'name': 'Top Oven', 'key': 'k-top', 'version': '3.4'},
    {'id': 'bfc2c15f07d2b072d5rchn', 'name': 'Kettle', 'key': 'k-kettle', 'version': '3.3'},
]

# Shape of tinytuya.deviceScan(False, N): keyed by IP.
RAW_REMOTE_SCAN = {
    '192.168.1.157': {
        'ip': '192.168.1.157',
        'gwId': 'bf52baba2305f136datzwi',
        'version': '3.4',
        'productKey': 'keyuh3jxk9wu8ruj',
    },
    '192.168.1.135': {'ip': '192.168.1.135', 'id': 'bf64fd8c51015ede6db23j', 'version': '3.4'},
    '192.168.1.9': {'ip': '192.168.1.9', 'version': '3.3'},  # no ID: skipped
}


class TestRemoteDiscoveryHostsFor:
    CFG = {
        'quartz': [{'label': 'flint', 'ssh': ['ssh', '-p', '2222', 'nickc@localhost']}],
    }

    def test_hosts_for_configured_hostname(self):
        assert tl.remote_discovery_hosts_for('quartz', self.CFG) == self.CFG['quartz']

    def test_unconfigured_hostname_gets_nothing(self):
        assert tl.remote_discovery_hosts_for('flint', self.CFG) == []

    def test_fqdn_matches_short_hostname(self):
        assert tl.remote_discovery_hosts_for('quartz.lan', self.CFG) == self.CFG['quartz']

    def test_missing_or_malformed_config_is_empty(self):
        assert tl.remote_discovery_hosts_for('quartz', None) == []
        assert tl.remote_discovery_hosts_for('quartz', 'not-a-dict') == []


class TestMergeRemoteScan:
    def test_devices_get_name_key_and_ip_from_scan_and_devices_json(self):
        merged = tl.merge_remote_scan(RAW_REMOTE_SCAN, DEVICES_JSON, 'flint')
        assert set(merged) == {'bf52baba2305f136datzwi', 'bf64fd8c51015ede6db23j'}
        main = merged['bf52baba2305f136datzwi']
        assert main['ip'] == '192.168.1.157'
        assert main['name'] == 'Main Oven'
        assert main['key'] == 'k-main'
        assert main['version'] == '3.4'
        assert main['seen_on'] == 'flint'

    def test_id_field_accepted_as_well_as_gwid(self):
        merged = tl.merge_remote_scan(RAW_REMOTE_SCAN, DEVICES_JSON, 'flint')
        assert merged['bf64fd8c51015ede6db23j']['name'] == 'Top Oven'

    def test_unknown_device_keeps_id_as_name_and_empty_key(self):
        raw = {'192.168.1.50': {'ip': '192.168.1.50', 'gwId': 'bfnew000000000000000', 'version': '3.3'}}
        merged = tl.merge_remote_scan(raw, DEVICES_JSON, 'flint')
        assert merged['bfnew000000000000000']['name'] == 'bfnew000000000000000'
        assert merged['bfnew000000000000000']['key'] == ''

    def test_empty_or_malformed_scan(self):
        assert tl.merge_remote_scan({}, DEVICES_JSON, 'flint') == {}
        assert tl.merge_remote_scan(None, DEVICES_JSON, 'flint') == {}
        assert tl.merge_remote_scan(['nonsense'], DEVICES_JSON, 'flint') == {}


class TestKnownDevicesNotSeen:
    def test_reports_known_devices_absent_from_found_set(self):
        missing = tl.known_devices_not_seen(DEVICES_JSON, {'bfc2c15f07d2b072d5rchn'})
        assert [d['name'] for d in missing] == ['Main Oven', 'Top Oven']

    def test_nothing_missing(self):
        ids = {d['id'] for d in DEVICES_JSON}
        assert tl.known_devices_not_seen(DEVICES_JSON, ids) == []

    def test_entries_without_id_are_ignored(self):
        assert tl.known_devices_not_seen([{'name': 'no id'}], set()) == []


class TestScanRemoteHosts:
    HOST = {'label': 'flint', 'ssh': ['ssh', '-p', '2222', 'nickc@localhost']}

    def test_successful_scan_is_merged(self, monkeypatch):
        import json

        def fake_run(cmd, **kw):
            assert cmd[:4] == ['ssh', '-p', '2222', 'nickc@localhost']
            # ssh re-parses its remote command through a shell: the python
            # snippet must travel as one quoted word, not bare argv pieces.
            assert len(cmd) == 5
            assert cmd[4].startswith("python3 -c '") and cmd[4].endswith("'")
            assert 'deviceScan' in cmd[4]
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(RAW_REMOTE_SCAN), stderr='')

        monkeypatch.setattr(subprocess, 'run', fake_run)
        found, failures = tl.scan_remote_hosts([self.HOST], DEVICES_JSON)
        assert set(found) == {'bf52baba2305f136datzwi', 'bf64fd8c51015ede6db23j'}
        assert failures == []

    def test_ssh_failure_is_reported_not_raised(self, monkeypatch):
        def fake_run(cmd, **kw):
            return subprocess.CompletedProcess(
                cmd, 255, stdout='', stderr='ssh: connect to host localhost port 2222: Connection refused'
            )

        monkeypatch.setattr(subprocess, 'run', fake_run)
        found, failures = tl.scan_remote_hosts([self.HOST], DEVICES_JSON)
        assert found == {}
        assert len(failures) == 1
        assert failures[0][0] == 'flint'
        assert 'Connection refused' in failures[0][1]

    def test_timeout_is_reported_not_raised(self, monkeypatch):
        def fake_run(cmd, **kw):
            raise subprocess.TimeoutExpired(cmd, kw.get('timeout', 0))

        monkeypatch.setattr(subprocess, 'run', fake_run)
        found, failures = tl.scan_remote_hosts([self.HOST], DEVICES_JSON)
        assert found == {}
        assert failures[0][0] == 'flint'

    def test_garbage_output_is_reported_not_raised(self, monkeypatch):
        def fake_run(cmd, **kw):
            return subprocess.CompletedProcess(cmd, 0, stdout='Scanning...\nnot json', stderr='')

        monkeypatch.setattr(subprocess, 'run', fake_run)
        found, failures = tl.scan_remote_hosts([self.HOST], DEVICES_JSON)
        assert found == {}
        assert failures[0][0] == 'flint'
