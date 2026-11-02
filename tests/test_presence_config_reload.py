"""Tests that the presence service picks up edits to people_config.yaml without a restart.

On 2026-10-12 new phone MACs were added to the config, but the running service only read the file at startup, so Wi-Fi
presence saw nobody until quartz rebooted on 2026-10-28. The main loop now reloads the file when its mtime changes; a
file that fails to parse (e.g. saved mid-edit) is ignored and the previous config kept. Nobody reads the log, so a
failed reload also emails Nick, at most once a week.
"""

import os

import pytest

from presence_to_graphite import ALERT_INTERVAL_SECONDS, PresenceMonitor

CONFIG = """
wifi: {{cidr: 192.168.86.0/24, scan_interval_seconds: 30, offline_grace_seconds: 300}}
tado: {{enabled: false, poll_interval_seconds: 300}}
people:
  - person: nick
    wifi_macs: ["{mac}"]
"""


def _write(path, text, mtime):
    path.write_text(text)
    os.utime(path, (mtime, mtime))


class FakeMail:
    def __init__(self, works=True):
        self.works = works
        self.sent = []

    def __call__(self, subject, body):
        self.sent.append((subject, body))
        return self.works


@pytest.fixture(autouse=True)
def mail(tmp_path, monkeypatch):
    fake = FakeMail()
    monkeypatch.setattr(PresenceMonitor, 'send_email', staticmethod(fake))
    monkeypatch.setattr(PresenceMonitor, 'ALERT_MARKER', str(tmp_path / 'alerts' / 'last_alert'))
    return fake


def _monitor(path):
    monitor = PresenceMonitor.__new__(PresenceMonitor)
    monitor.config_file = str(path)
    monitor.config = None
    monitor._load_config()
    return monitor


def _break(path, mtime):
    _write(path, 'people: [unclosed\n', mtime=mtime)


def _macs(monitor):
    return set(monitor._build_person_mappings()['mac_to_person'])


def test_changed_file_is_reloaded(tmp_path):
    path = tmp_path / 'people_config.yaml'
    _write(path, CONFIG.format(mac='AA:AA:AA:AA:AA:AA'), mtime=1000)
    monitor = _monitor(path)
    _write(path, CONFIG.format(mac='BB:BB:BB:BB:BB:BB'), mtime=2000)
    assert monitor._reload_config_if_changed() is True
    assert _macs(monitor) == {'BB:BB:BB:BB:BB:BB'}


def test_unchanged_file_is_not_reloaded(tmp_path):
    path = tmp_path / 'people_config.yaml'
    _write(path, CONFIG.format(mac='AA:AA:AA:AA:AA:AA'), mtime=1000)
    monitor = _monitor(path)
    assert monitor._reload_config_if_changed() is False
    assert _macs(monitor) == {'AA:AA:AA:AA:AA:AA'}


def test_unparseable_edit_keeps_previous_config(tmp_path):
    path = tmp_path / 'people_config.yaml'
    _write(path, CONFIG.format(mac='AA:AA:AA:AA:AA:AA'), mtime=1000)
    monitor = _monitor(path)
    _write(path, 'people: [unclosed\n', mtime=2000)
    assert monitor._reload_config_if_changed() is False
    assert _macs(monitor) == {'AA:AA:AA:AA:AA:AA'}


def test_edit_without_people_keeps_previous_config(tmp_path):
    path = tmp_path / 'people_config.yaml'
    _write(path, CONFIG.format(mac='AA:AA:AA:AA:AA:AA'), mtime=1000)
    monitor = _monitor(path)
    _write(path, '', mtime=2000)
    assert monitor._reload_config_if_changed() is False
    assert _macs(monitor) == {'AA:AA:AA:AA:AA:AA'}


def test_missing_file_keeps_previous_config(tmp_path):
    path = tmp_path / 'people_config.yaml'
    _write(path, CONFIG.format(mac='AA:AA:AA:AA:AA:AA'), mtime=1000)
    monitor = _monitor(path)
    path.unlink()
    assert monitor._reload_config_if_changed() is False
    assert _macs(monitor) == {'AA:AA:AA:AA:AA:AA'}


def test_bad_edit_is_retried_once_fixed(tmp_path):
    path = tmp_path / 'people_config.yaml'
    _write(path, CONFIG.format(mac='AA:AA:AA:AA:AA:AA'), mtime=1000)
    monitor = _monitor(path)
    _write(path, 'people: [unclosed\n', mtime=2000)
    monitor._reload_config_if_changed()
    _write(path, CONFIG.format(mac='BB:BB:BB:BB:BB:BB'), mtime=3000)
    assert monitor._reload_config_if_changed() is True
    assert _macs(monitor) == {'BB:BB:BB:BB:BB:BB'}


def test_failed_reload_emails_nick(tmp_path, mail):
    path = tmp_path / 'people_config.yaml'
    _write(path, CONFIG.format(mac='AA:AA:AA:AA:AA:AA'), mtime=1000)
    monitor = _monitor(path)
    _break(path, mtime=2000)
    monitor._reload_config_if_changed(now=10_000_000)
    assert len(mail.sent) == 1
    subject, body = mail.sent[0]
    assert 'presence' in subject.lower()
    assert str(path) in body


def test_successful_reload_sends_no_email(tmp_path, mail):
    path = tmp_path / 'people_config.yaml'
    _write(path, CONFIG.format(mac='AA:AA:AA:AA:AA:AA'), mtime=1000)
    monitor = _monitor(path)
    _write(path, CONFIG.format(mac='BB:BB:BB:BB:BB:BB'), mtime=2000)
    monitor._reload_config_if_changed(now=10_000_000)
    assert mail.sent == []


def test_alert_email_at_most_once_a_week(tmp_path, mail):
    path = tmp_path / 'people_config.yaml'
    _write(path, CONFIG.format(mac='AA:AA:AA:AA:AA:AA'), mtime=1000)
    monitor = _monitor(path)
    start = 10_000_000
    _break(path, mtime=2000)
    monitor._reload_config_if_changed(now=start)
    _write(path, '', mtime=3000)
    monitor._reload_config_if_changed(now=start + ALERT_INTERVAL_SECONDS - 60)
    assert len(mail.sent) == 1
    _break(path, mtime=4000)
    monitor._reload_config_if_changed(now=start + ALERT_INTERVAL_SECONDS + 60)
    assert len(mail.sent) == 2


def test_failed_send_is_retried_next_time(tmp_path, mail):
    mail.works = False
    path = tmp_path / 'people_config.yaml'
    _write(path, CONFIG.format(mac='AA:AA:AA:AA:AA:AA'), mtime=1000)
    monitor = _monitor(path)
    _break(path, mtime=2000)
    monitor._reload_config_if_changed(now=10_000_000)
    mail.works = True
    _write(path, '', mtime=3000)
    monitor._reload_config_if_changed(now=10_000_060)
    assert len(mail.sent) == 2


def test_missing_file_emails_once_not_every_scan(tmp_path, mail):
    path = tmp_path / 'people_config.yaml'
    _write(path, CONFIG.format(mac='AA:AA:AA:AA:AA:AA'), mtime=1000)
    monitor = _monitor(path)
    path.unlink()
    for i in range(5):
        monitor._reload_config_if_changed(now=10_000_000 + 30 * i)
    assert len(mail.sent) == 1
