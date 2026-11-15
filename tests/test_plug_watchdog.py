"""Isolated plug watchdog behaviour; no real network or mail."""

import json
import sys
from unittest.mock import MagicMock

import pytest

import plug_watchdog as pw

NOW = 1_800_000_000
DAY = 86400


@pytest.mark.parametrize(
    'age,status',
    [
        (0, 'ok'),
        (3600, 'ok'),
        (3601, 'silent'),
        (604799, 'silent'),
        (604800, 'retired'),
        (604801, 'retired'),
        (None, 'retired'),
    ],
)
def test_classification(age, status):
    assert pw.classify(None if age is None else NOW - age, NOW) == status


def test_fetch(monkeypatch):
    response = MagicMock()
    response.__enter__.return_value.read.return_value = json.dumps(
        [
            {
                'target': 'home.electricity.tuya.oven.power_watts',
                'datapoints': [[0, NOW], [None, NOW + 10], [2, NOW - 10]],
            },
            {'target': 'home.electricity.kasa.empty.power_watts', 'datapoints': []},
            {'target': 'home.electricity.kasa.null.power_watts', 'datapoints': [[None, NOW]]},
        ]
    )
    opener = MagicMock(return_value=response)
    monkeypatch.setattr(pw.urllib.request, 'urlopen', opener)
    assert pw.fetch() == {'tuya.oven': NOW, 'kasa.empty': None, 'kasa.null': None}
    url = opener.call_args.args[0]
    assert 'noCache=1' in url and 'from=-604800s' in url
    assert 'maxDataPoints=2016' in url  # 5-minute buckets keep the 7-day response small
    assert 'home.electricity.%2A.%2A.power_watts' in url


@pytest.fixture
def setup(tmp_path):
    clock = [NOW]
    mail = MagicMock(return_value=True)
    episode = pw.Episode(str(tmp_path), now=lambda: clock[0], send=mail)
    return episode, clock, mail


def test_grouped_alert_reminder_and_recovery(setup):
    e, clock, mail = setup
    devices = {'tuya.oven': NOW - 4000, 'kasa.fridge': NOW - 5000}
    e.observe(devices)
    assert mail.call_count == 1
    subject, body = mail.call_args.args
    assert 'FAULT' in subject and all(d in body for d in devices)
    assert 'GMT' in body or 'BST' in body
    assert 'ago' in body and '7 days' in body
    clock[0] += DAY - 1
    e.observe(devices)
    assert mail.call_count == 1
    clock[0] += 1
    e.observe(devices)
    assert mail.call_count == 2 and 'reminder' in mail.call_args.args[0]
    e.observe(dict.fromkeys(devices, clock[0]))
    assert mail.call_count == 3 and 'RECOVERED' in mail.call_args.args[0]
    assert all(d in mail.call_args.args[1] for d in devices)


def test_retired_and_missing_dropped(setup):
    e, clock, mail = setup
    e.observe({'tuya.oven': NOW - 4000, 'kasa.fridge': NOW - 4000})
    clock[0] += pw.RETIRED_AFTER_SECONDS
    e.observe({'tuya.oven': NOW - 4000})
    assert mail.call_count == 1
    assert e.state['devices'] == {}


def test_flapping_ceiling_survives_restart(setup):
    e, clock, mail = setup
    e.observe({'tuya.oven': NOW - 4000})
    e.observe({'tuya.oven': NOW})
    clock[0] += 4000
    e = pw.Episode(e.state_dir, now=lambda: clock[0], send=mail)
    e.observe({'tuya.oven': NOW})
    assert mail.call_count == 2
    clock[0] = NOW + DAY
    e.observe({'tuya.oven': NOW})
    assert mail.call_count == 3


def test_new_device_does_not_repeat_recent_fault(setup):
    e, clock, mail = setup
    e.observe({'tuya.oven': NOW - 4000})
    clock[0] += 300
    e.observe({'tuya.oven': NOW - 4000, 'kasa.fridge': NOW - 5000})
    assert mail.call_count == 2
    assert 'tuya.oven' not in mail.call_args.args[1]


def test_graphite_outage_logs_once_and_no_return_burst(setup, monkeypatch, caplog):
    e, clock, mail = setup
    fetch = MagicMock(side_effect=OSError('offline'))
    monkeypatch.setattr(pw, 'fetch', fetch)
    pw.check(e)
    pw.check(e)
    assert mail.call_count == 0
    assert caplog.text.count('offline') == 1
    clock[0] += DAY
    fetch.side_effect = None
    fetch.return_value = {'tuya.oven': NOW}
    pw.check(e)
    assert mail.call_count == 0
    clock[0] += pw.SILENT_AFTER_SECONDS
    fetch.return_value = {'tuya.oven': clock[0]}
    pw.check(e)
    assert mail.call_count == 0


@pytest.mark.parametrize('content', ['broken', '[]', '{"devices": {"x": null}}'])
def test_corrupt_state(tmp_path, content):
    (tmp_path / 'state.json').write_text(content)
    mail = MagicMock(return_value=True)
    e = pw.Episode(str(tmp_path), now=lambda: NOW, send=mail)
    e.observe({'tuya.oven': NOW - 4000})
    assert mail.call_count == 1
    assert json.loads((tmp_path / 'state.json').read_text())['devices']


def test_failed_mail_retried(setup):
    e, _, mail = setup
    mail.return_value = False
    e.observe({'tuya.oven': NOW - 4000})
    mail.return_value = True
    e.observe({'tuya.oven': NOW - 4000})
    assert mail.call_count == 2


def test_test_mail(monkeypatch):
    smtp = MagicMock()
    monkeypatch.setattr(pw.smtplib, 'SMTP', smtp)
    monkeypatch.setattr(sys, 'argv', ['plug_watchdog.py', '--test-mail'])
    assert pw.main() == 0
    msg = smtp.return_value.__enter__.return_value.send_message.call_args.args[0]
    assert msg['To'] == pw.ALERT_RCPT
    assert msg['From'] == pw.ALERT_FROM
    assert pw.ALERT_FROM.startswith('plug-watchdog@')
    assert 'test' in msg['Subject']


def test_once(setup, monkeypatch):
    _, _, mail = setup
    monkeypatch.setattr(pw, 'fetch', lambda: {})
    monkeypatch.setattr(sys, 'argv', ['plug_watchdog.py', '--once', '--state-dir', setup[0].state_dir])
    monkeypatch.setattr(pw.time, 'sleep', lambda _: pytest.fail('once must not sleep'))
    assert pw.main() == 0
    mail.assert_not_called()


def test_reminders_group_staggered_outages_once_daily(setup):
    e, clock, mail = setup
    e.observe({'tuya.oven': NOW - 4000})
    clock[0] += 300
    devices = {'tuya.oven': NOW - 4000, 'kasa.fridge': NOW - 5000}
    e.observe(devices)
    clock[0] = NOW + DAY
    e.observe(devices)
    clock[0] += 300
    e.observe(devices)
    assert mail.call_count == 3
