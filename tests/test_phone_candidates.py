"""Tests for tools/phone_candidates: read-only report of likely replacement phones."""

import json
import sys
from pathlib import Path

import yaml

# tools/ is not on the default path
sys.path.insert(0, str(Path(__file__).parent.parent / 'tools'))
import phone_candidates as pc

H = 3600.0
# Charlie's real case, 2026-11-04: old phone last seen 08:56 UTC, new one joined 12:07 UTC
OLD_LAST = 1793782560.0  # 2026-11-04T08:56:00Z
NOW = OLD_LAST + 13 * 24 * H

CONFIG = """
people:
  - person: charlie
    wifi_macs:
      - "6a:27:13:df:b6:52"
    ha_device_tracker: "device_tracker.charlies_iphone_3"
  - person: nick
    wifi_macs:
      - "8A:DD:F9:79:CD:3D"
    ha_device_tracker: "device_tracker.nick_s_iphone"
  - person: tom
    wifi_macs: []
    ha_device_tracker: "device_tracker.toms_iphone"
"""


def fp(first, last, hostname=None):
    return {'hostname_pattern': hostname, 'first_seen': first, 'last_seen': last, 'vendor': None}


def learning(**extra):
    devices = {
        '6A:27:13:DF:B6:52': fp(OLD_LAST - 300 * 24 * H, OLD_LAST, 'iphone.lan'),
        '8A:DD:F9:79:CD:3D': fp(OLD_LAST - 30 * 24 * H, NOW - 60, 'iphone.lan'),
        '78:3F:4D:A8:1B:1A': fp(OLD_LAST + 3 * H + 11 * 60, NOW - 30, 'charliesiphone2.lan'),
        '7E:85:3D:99:17:D5': fp(OLD_LAST + 21 * 60, NOW - 3 * 24 * H, 'iphone.lan'),
        # joined well before the old phone vanished: a resident device, not a replacement
        'AA:00:00:00:00:01': fp(OLD_LAST - 10 * 24 * H, NOW - 10, 'tv.lan'),
        # joined in the window but gone before the old phone vanished
        'AA:00:00:00:00:02': fp(OLD_LAST - 2 * H, OLD_LAST - H, None),
        # joined long after the window
        'AA:00:00:00:00:03': fp(OLD_LAST + 6 * 24 * H, NOW - 10, 'visitor.lan'),
    }
    devices.update(extra)
    return {'device_fingerprints': devices, 'mac_history': {}}


def state():
    return {'last_seen_wifi': {'6A:27:13:DF:B6:52': OLD_LAST, '8A:DD:F9:79:CD:3D': NOW - 20}}


def report(**kw):
    return pc.build_report(yaml.safe_load(CONFIG), kw.pop('learning', learning()), state(), now=NOW, **kw)


def person(rep, name):
    return next(p for p in rep['people'] if p['person'] == name)


class TestReport:
    def test_format_version_and_generated_time(self):
        rep = report()
        assert rep['format_version'] == 1
        assert rep['generated'] == pc.iso(NOW)

    def test_stale_person_lists_devices_that_joined_around_the_vanishing(self):
        charlie = person(report(), 'charlie')
        assert charlie['status'] == 'stale'
        assert charlie['ha_device_tracker'] == 'device_tracker.charlies_iphone_3'
        assert charlie['wifi_last_seen'] == pc.iso(OLD_LAST)
        assert charlie['configured_macs'] == [
            {'mac': '6A:27:13:DF:B6:52', 'hostname': 'iphone.lan', 'last_seen': pc.iso(OLD_LAST)}
        ]
        macs = [c['mac'] for c in charlie['candidates']]
        # nearest first-joining time first; resident, vanished-early and much-later devices left out
        assert macs == ['7E:85:3D:99:17:D5', '78:3F:4D:A8:1B:1A']
        new = charlie['candidates'][1]
        assert new == {
            'mac': '78:3F:4D:A8:1B:1A',
            'hostname': 'charliesiphone2.lan',
            'first_seen': pc.iso(OLD_LAST + 3 * H + 11 * 60),
            'last_seen': pc.iso(NOW - 30),
            'hours_after_old_last_seen': 3.2,
            'provenance': 'device_fingerprints',
            'conflicts': [],
        }

    def test_person_seen_recently_gets_no_candidates(self):
        nick = person(report(), 'nick')
        assert nick['status'] == 'ok'
        assert nick['candidates'] == []

    def test_person_without_mapping(self):
        tom = person(report(), 'tom')
        assert tom['status'] == 'no_mapping'
        assert tom['wifi_last_seen'] is None
        assert tom['candidates'] == []

    def test_mapped_devices_are_never_candidates(self):
        extra = {'8A:DD:F9:79:CD:3D': fp(OLD_LAST + H, NOW - 60, 'iphone.lan')}
        macs = [c['mac'] for c in person(report(learning=learning(**extra)), 'charlie')['candidates']]
        assert '8A:DD:F9:79:CD:3D' not in macs

    def test_candidate_in_two_peoples_windows_is_a_conflict(self):
        cfg = yaml.safe_load(CONFIG)
        cfg['people'][1]['wifi_macs'] = ['BB:00:00:00:00:01']
        st = state()
        st['last_seen_wifi']['BB:00:00:00:00:01'] = OLD_LAST + H
        rep = pc.build_report(cfg, learning(), st, now=NOW)
        new_for_charlie = next(c for c in person(rep, 'charlie')['candidates'] if c['mac'] == '78:3F:4D:A8:1B:1A')
        new_for_nick = next(c for c in person(rep, 'nick')['candidates'] if c['mac'] == '78:3F:4D:A8:1B:1A')
        assert new_for_charlie['conflicts'] == ['nick']
        assert new_for_nick['conflicts'] == ['charlie']

    def test_never_seen_mapping(self):
        cfg = yaml.safe_load(CONFIG)
        cfg['people'][1]['wifi_macs'] = ['CC:00:00:00:00:09']
        nick = person(pc.build_report(cfg, learning(), {'last_seen_wifi': {}}, now=NOW), 'nick')
        assert nick['status'] == 'never_seen'
        assert nick['candidates'] == []

    def test_fingerprint_last_seen_counts_when_state_lacks_the_mac(self):
        rep = pc.build_report(yaml.safe_load(CONFIG), learning(), {'last_seen_wifi': {}}, now=NOW)
        assert person(rep, 'charlie')['wifi_last_seen'] == pc.iso(OLD_LAST)
        assert person(rep, 'nick')['status'] == 'ok'

    def test_no_ip_addresses_in_report(self):
        text = json.dumps(report())
        assert '192.168.' not in text


class TestCli:
    def test_main_prints_json_and_writes_nothing(self, tmp_path, capsys):
        cfg = tmp_path / 'people_config.yaml'
        cfg.write_text(CONFIG)
        ls = tmp_path / 'mac_learning_state.json'
        ls.write_text(json.dumps(learning()))
        st = tmp_path / 'state.json'
        st.write_text(json.dumps(state()))
        before = sorted((p.name, p.stat().st_mtime_ns) for p in tmp_path.iterdir())
        rc = pc.main(['--config', str(cfg), '--learning-state', str(ls), '--state', str(st), '--now', str(NOW)])
        assert rc == 0
        out = json.loads(capsys.readouterr().out)
        assert out['format_version'] == 1
        assert [c['mac'] for c in person(out, 'charlie')['candidates']] == ['7E:85:3D:99:17:D5', '78:3F:4D:A8:1B:1A']
        assert sorted((p.name, p.stat().st_mtime_ns) for p in tmp_path.iterdir()) == before

    def test_unreadable_input_fails_visibly(self, tmp_path, capsys):
        rc = pc.main(['--config', str(tmp_path / 'missing.yaml')])
        assert rc == 2
        assert 'missing.yaml' in capsys.readouterr().err
