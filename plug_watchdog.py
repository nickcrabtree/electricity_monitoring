#!/usr/bin/env python3
"""Email grouped plug-silence faults, daily reminders and recoveries; stdlib only.

Read-only against Graphite. --once checks once; --test-mail sends a test email.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import smtplib
import socket
import time
import urllib.parse
import urllib.request
from datetime import datetime
from email.message import EmailMessage
from zoneinfo import ZoneInfo

import config
from meter_watchdog import ALERT_RCPT, SMTP_HOST, SMTP_PORT

ALERT_FROM = f'plug-watchdog@{socket.gethostname()}'

POLL_SECONDS = 300
# 5-minute buckets over 7 days: about 1 MB of JSON instead of 7 MB at 60 s. The newest bucket's timestamp is its
# start, so a device's last-seen time can read up to 5 minutes early.
MAX_DATA_POINTS = 2016
SILENT_AFTER_SECONDS = 60 * 60
RETIRED_AFTER_SECONDS = 7 * 24 * 60 * 60
MIN_ALERT_INTERVAL_SECONDS = 24 * 60 * 60
STATE_DIR = '/var/tmp/plug-watchdog'  # noqa: S108 - persistent watchdog state
TARGET = 'home.electricity.*.*.power_watts'
GRAFANA_HINT = 'Grafana: Electricity Monitoring v2, Usage panel.'
FIX_HINTS = (
    'The plug may be unplugged, switched off at the wall or off Wi-Fi.\n'
    'Main LAN: check blackpi2 pollers (tuya-monitoring, graphite-kasa, graphite-tuya-local).\n'
    'The two ovens and towel rail are polled by flint on the OpenWrt subnet (tuya-monitoring).\n'
    'If all plugs go silent together, check the poller rather than the individual plugs.\n'
    'Devices silent for 7 days are treated as retired and no longer reported.\n'
)
log = logging.getLogger('plug_watchdog')


def fetch() -> dict[str, float | None]:
    """Fetch the newest non-null point per device without cached render results."""
    query = urllib.parse.urlencode(
        {
            'target': TARGET,
            'from': f'-{RETIRED_AFTER_SECONDS}s',
            'format': 'json',
            'noCache': 1,
            'maxDataPoints': MAX_DATA_POINTS,
        }
    )
    with urllib.request.urlopen(  # noqa: S310 - configured Graphite URL
        f'{config.GRAPHITE_RENDER_URL}?{query}', timeout=config.GRAPHITE_SSH_TIMEOUT
    ) as response:
        payload = json.load(response)
    devices = {}
    for series in payload:
        nodes = series['target'].split('.')
        if len(nodes) != 5 or nodes[:2] != ['home', 'electricity'] or nodes[-1] != 'power_watts':
            raise ValueError('unexpected Graphite target')
        times = [float(t) for v, t in series['datapoints'] if v is not None]
        if any(not math.isfinite(t) for t in times):
            raise ValueError('invalid Graphite timestamp')
        devices['.'.join(nodes[2:4])] = max(times, default=None)
    return devices


def classify(last_seen: float | None, now: float) -> str:
    if last_seen is None or now - last_seen >= RETIRED_AFTER_SECONDS:
        return 'retired'
    return 'ok' if now - last_seen <= SILENT_AFTER_SECONDS else 'silent'


def send_alert_email(subject: str, body: str) -> bool:
    """Send through quartz's postfix relay to the meter watchdog's recipient."""
    try:
        msg = EmailMessage()
        msg['From'], msg['To'], msg['Subject'] = ALERT_FROM, ALERT_RCPT, subject
        msg.set_content(body)
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as smtp:
            smtp.send_message(msg)
        log.info('alert email sent to %s: %s', ALERT_RCPT, subject)
        return True
    except (OSError, smtplib.SMTPException) as exc:
        log.warning('could not send alert email: %s', exc)
        return False


def describe(devices: dict[str, float], now: float) -> str:
    lines = []
    for name, seen in sorted(devices.items()):
        when = datetime.fromtimestamp(seen, ZoneInfo('Europe/London')).strftime('%Y-%m-%d %H:%M:%S %Z')
        lines.append(f'{name}: last seen {when}, {max(0, int((now - seen) / 60))} minutes ago.')
    return '\n'.join(lines) + '\n\n' + FIX_HINTS + GRAFANA_HINT


class Episode:
    """Atomic markers retain per-device mail times across recoveries and restarts."""

    def __init__(self, state_dir: str, now=time.time, send=send_alert_email):
        self.state_dir, self.now, self.send = state_dir, now, send
        self.path = os.path.join(state_dir, 'state.json')
        self.state = self._read()

    def _read(self):
        try:
            with open(self.path) as f:
                state = json.load(f)
            if not isinstance(state, dict) or not isinstance(state['devices'], dict):
                raise ValueError('invalid state')
            for entry in state['devices'].values():
                if not isinstance(entry, dict) or entry['status'] not in ('ok', 'silent'):
                    raise ValueError('invalid device state')
                if not isinstance(entry['alerted'], bool):
                    raise ValueError('invalid alert state')
                stamp = entry['last_alert']
                if stamp is not None and (not isinstance(stamp, (float, int)) or not math.isfinite(stamp)):
                    raise ValueError('invalid alert time')
            if not isinstance(state['graphite_down'], bool) or not math.isfinite(state['resume_after']):
                raise ValueError('invalid Graphite state')
            return state
        except (OSError, ValueError, KeyError, TypeError):
            return {'devices': {}, 'graphite_down': False, 'resume_after': 0}

    def save(self):
        os.makedirs(self.state_dir, exist_ok=True)
        temporary = self.path + '.tmp'
        with open(temporary, 'w') as f:
            json.dump(self.state, f)
        os.replace(temporary, self.path)

    def observe(self, devices):
        now = self.now()
        entries = self.state['devices']
        recovered, eligible = {}, {}
        for name in list(entries):
            if classify(devices.get(name), now) == 'retired':
                del entries[name]
        for name, seen in devices.items():
            status = classify(seen, now)
            if status == 'retired':
                continue
            entry = entries.setdefault(name, {'status': 'ok', 'alerted': False, 'last_alert': None})
            if status == 'ok':
                if entry['alerted']:
                    recovered[name] = seen
            elif now >= self.state['resume_after']:
                last = entry['last_alert']
                if last is None or now - last >= MIN_ALERT_INTERVAL_SECONDS:
                    eligible[name] = seen
            entry['status'] = status
        # Wait until every still-silent device is eligible before a grouped reminder.
        # New faults still alert promptly, without repeating recently alerted devices.
        if any(e['status'] == 'silent' and name not in eligible for name, e in entries.items()):
            eligible = {name: seen for name, seen in eligible.items() if not entries[name]['alerted']}
        if recovered and self.send('[plugs] RECOVERED - reporting again', describe(recovered, now)):
            for name in recovered:
                entries[name]['alerted'] = False
        if eligible:
            reminder = all(entries[name]['alerted'] for name in eligible)
            subject = '[plugs] FAULT - ' + ('daily reminder: still silent' if reminder else 'plugs not reporting')
            if self.send(subject, describe(eligible, now)):
                for name in eligible:
                    entries[name]['alerted'] = True
                    entries[name]['last_alert'] = now
        self.save()
        log.info('checked %d devices: %d silent', len(devices), sum(e['status'] == 'silent' for e in entries.values()))


def check(episode: Episode) -> None:
    try:
        devices = fetch()
    except (OSError, ValueError, KeyError, IndexError, TypeError) as exc:
        if not episode.state['graphite_down']:
            log.warning('Graphite check failed: %s', exc)
            episode.state['graphite_down'] = True
            episode.save()
        return
    if episode.state['graphite_down']:
        log.info('Graphite recovered; allowing one hour for fresh plug data before fault alerts')
        episode.state['graphite_down'] = False
        episode.state['resume_after'] = episode.now() + SILENT_AFTER_SECONDS
    episode.observe(devices)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--once', action='store_true', help='one check, then exit')
    parser.add_argument('--test-mail', action='store_true', help='send one test email via quartz and exit')
    parser.add_argument('--state-dir', default=STATE_DIR)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    if args.test_mail:
        return (
            0
            if send_alert_email('[plugs] test email from plug_watchdog', f'Sent from {socket.gethostname()}.\n')
            else 1
        )
    episode = Episode(args.state_dir)
    while True:
        try:
            check(episode)
        except OSError as exc:
            log.warning('state update failed: %s', exc)
        if args.once:
            return 0
        time.sleep(POLL_SECONDS)


if __name__ == '__main__':
    raise SystemExit(main())
