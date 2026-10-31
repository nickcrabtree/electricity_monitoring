#!/usr/bin/env python3
"""Whole-house meter watchdog: email Nick when the meter stops reporting or stops seeing pulses.

The meter is an ESP32 in the garage (`mpy-esp32.local`) counting flashes of the electricity meter's pulse LED with an
LM393 light sensor and writing `home.electricity.meter.{power_watts,pulse_count,...}` to Graphite every 10 s. The sensor
is easily knocked off the LED: the board keeps reporting but the pulse count goes flat and power reads 0 W, and that
went unnoticed for days in September and October 2026. The house never draws nothing (fridges, freezers, quartz), so
a flat pulse count over 30 minutes is unambiguous. Two faults are detected:

  silent   nothing reported for the whole window (board or Wi-Fi down)
  zero     reporting, but no pulses (sensor knocked or stuck)

Alert semantics follow ~/scripts/temp_humid_graphite_panicboard.py on blackpi2: alert only after a sustained fault, one
alert per episode, a hard ceiling of one alert per 24 h even when the fault flaps, a RECOVERED email when data returns,
plus (this watchdog only) a reminder every 24 h while the fault persists, because past outages lasted days. Mail goes
to quartz's postfix like every other LAN host; if quartz is down there is no alert, which is the documented norm.

Runs as the systemd unit `graphite-meter-watchdog.service` on blackpi2 (see systemd/). Read-only against Graphite.
Stdlib only. `--once` does one check and exits; `--test-mail` sends one test email and exits.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import smtplib
import socket
import sys
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import datetime, timezone
from email.message import EmailMessage

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

POLL_SECONDS = 300
WINDOW_SECONDS = 30 * 60
ALERT_AFTER_SECONDS = 30 * 60
MIN_ALERT_INTERVAL_SECONDS = 24 * 60 * 60
REMIND_EVERY_SECONDS = 24 * 60 * 60
STATE_DIR = '/var/tmp/meter-watchdog'  # noqa: S108 - persistent marker files, as the coolbox watchdog uses
POWER = 'home.electricity.meter.power_watts'
PULSES = 'home.electricity.meter.pulse_count'
SMTP_HOST = 'quartz.local'
SMTP_PORT = 25
ALERT_RCPT = 'nickc@quartz.local'
ALERT_FROM = f'meter-watchdog@{socket.gethostname()}'
GRAFANA_HINT = 'Grafana: Electricity Monitoring v2, Usage panel (meter.power_watts on the right axis).'

SUBJECTS = {
    'zero': 'no pulses: sensor knocked off the meter LED?',
    'silent': 'meter not reporting: ESP32 or Wi-Fi down?',
}

log = logging.getLogger('meter_watchdog')

Points = list[tuple[int, float]]


def parse_render(payload: object) -> Points:
    """Graphite render JSON to [(timestamp, value)], nulls dropped."""
    if not payload:
        return []
    return [(int(t), float(v)) for v, t in payload[0]['datapoints'] if v is not None]


def fetch(target: str, window_seconds: int) -> Points:
    query = urllib.parse.urlencode({'target': target, 'from': f'-{window_seconds}s', 'format': 'json'})
    url = f'{config.GRAPHITE_RENDER_URL}?{query}'
    with urllib.request.urlopen(url, timeout=config.GRAPHITE_SSH_TIMEOUT) as response:  # noqa: S310 - config URL
        return parse_render(json.load(response))


def classify(power: Points, pulses: Points) -> str:
    """'ok', 'silent' (no data at all) or 'zero' (data but no pulses)."""
    if not power and not pulses:
        return 'silent'
    if len(pulses) >= 2 and max(v for _, v in pulses) == min(v for _, v in pulses):
        return 'zero'
    if power and max(v for _, v in power) == 0 and len(pulses) < 2:
        return 'zero'
    return 'ok'


def _when(ts: float | None) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%SZ') if ts else 'unknown'


def describe(status: str, power: Points, pulses: Points, last_pulse_advance: float | None) -> str:
    """Diagnostic detail for the email body."""
    lines = [f'Window: last {WINDOW_SECONDS // 60} minutes; {len(power)} power points, {len(pulses)} pulse points.']
    if status == 'zero':
        lines += [
            f'pulse_count is flat at {pulses[-1][1]:.0f}; it last advanced at {_when(last_pulse_advance)}.'
            if pulses
            else 'No pulse_count series in the window; power_watts reads 0 W throughout.',
            'The board is reporting, so the LM393 light sensor is not seeing the meter LED flash.',
            'Fix: in the garage, re-aim the sensor at the meter pulse LED (it is very sensitive to being knocked) and,',
            'if it is still blind, re-trim the module pot. Firmware notes: ~/scripts/electricity_monitor/.',
        ]
    elif status == 'silent':
        lines += [
            'Nothing from the meter at all: the ESP32 (mpy-esp32.local) or its Wi-Fi is down.',
            'Fix: ping mpy-esp32.local; power-cycle the board in the garage if it does not answer.',
        ]
    lines.append(GRAFANA_HINT)
    return '\n'.join(lines)


def last_advance(pulses: Points) -> float | None:
    """Timestamp of the last pulse_count increase in the window, if any."""
    for (_, a), (t, b) in reversed(list(zip(pulses, pulses[1:]))):
        if b > a:
            return t
    return None


def send_alert_email(subject: str, body: str) -> bool:
    """Email Nick via quartz's postfix relay. Never raises."""
    try:
        msg = EmailMessage()
        msg['From'] = ALERT_FROM
        msg['To'] = ALERT_RCPT
        msg['Subject'] = subject
        msg.set_content(body)
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as smtp:
            smtp.send_message(msg)
        log.info('alert email sent to %s: %s', ALERT_RCPT, subject)
        return True
    except (OSError, smtplib.SMTPException) as exc:
        log.warning('could not send alert email: %s', exc)
        return False


class Episode:
    """Fault episode tracked in marker files so it survives restarts.

    since       start of the current fault (removed on recovery)
    alerted     an alert went out for this episode (removed on recovery)
    last_alert  time of the last email sent; kept across episodes for the 24 h ceiling
    """

    def __init__(
        self, state_dir: str, now: Callable[[], float] = time.time, send: Callable[[str, str], bool] = send_alert_email
    ):
        self.state_dir, self.now, self.send = state_dir, now, send

    def _path(self, name: str) -> str:
        return os.path.join(self.state_dir, name)

    def _read(self, name: str) -> float | None:
        try:
            with open(self._path(name)) as f:
                return float(f.read().strip())
        except (OSError, ValueError):
            return None

    def _write(self, name: str, value: float) -> None:
        os.makedirs(self.state_dir, exist_ok=True)
        with open(self._path(name), 'w') as f:
            f.write(str(value))

    def _remove(self, name: str) -> None:
        try:
            os.remove(self._path(name))
        except OSError:
            pass

    def observe(self, status: str, detail: str) -> None:
        if status == 'ok':
            self._clear()
        else:
            self._record(status, detail)

    def _clear(self) -> None:
        since = self._read('since')
        if self._read('alerted') is not None:
            minutes = int((self.now() - since) / 60) if since else 0
            self.send(
                '[meter] RECOVERED - whole-house meter reporting pulses again',
                f'The meter outage is over after {minutes} minutes.\n\nVerified at {_when(self.now())} on {socket.gethostname()}.\n',
            )
        for name in ('since', 'alerted'):
            self._remove(name)

    def _record(self, status: str, detail: str) -> None:
        now = self.now()
        since = self._read('since')
        if since is None:
            since = now
            self._write('since', since)
        minutes = int((now - since) / 60)
        log.info("fault '%s' has persisted for %d min (alert at %d min)", status, minutes, ALERT_AFTER_SECONDS // 60)
        if now - since < ALERT_AFTER_SECONDS:
            return
        last = self._read('last_alert')
        alerted = self._read('alerted') is not None
        if alerted:
            if last is not None and now - last < REMIND_EVERY_SECONDS:
                return
            subject = f'[meter] still faulty after {minutes // 60} h - {SUBJECTS[status]}'
        else:
            if last is not None and now - last < MIN_ALERT_INTERVAL_SECONDS:
                log.info('suppressing alert: last one was %.1f h ago, ceiling is one per 24 h', (now - last) / 3600)
                return
            subject = f'[meter] {SUBJECTS[status]}'
        body = f"Whole-house meter fault '{status}' for {minutes} minutes (since {_when(since)}).\n\n{detail}\n\nChecked at {_when(now)} on {socket.gethostname()}.\n"
        if self.send(subject, body):
            self._write('alerted', now)
            self._write('last_alert', now)


def check(episode: Episode) -> str:
    power, pulses = fetch(POWER, WINDOW_SECONDS), fetch(PULSES, WINDOW_SECONDS)
    status = classify(power, pulses)
    advance = last_advance(pulses)
    log.info(
        'status %s: %d power points, %d pulse points, last pulse advance %s',
        status,
        len(power),
        len(pulses),
        _when(advance),
    )
    episode.observe(status, describe(status, power, pulses, advance))
    return status


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--once', action='store_true', help='one check, then exit')
    parser.add_argument('--test-mail', action='store_true', help='send one test email via quartz and exit')
    parser.add_argument('--state-dir', default=STATE_DIR)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    if args.test_mail:
        ok = send_alert_email(
            '[meter] test email from meter_watchdog', f'Sent at {_when(time.time())} from {socket.gethostname()}.\n'
        )
        return 0 if ok else 1
    episode = Episode(args.state_dir)
    while True:
        try:
            check(episode)
        except (OSError, ValueError, KeyError, IndexError) as exc:
            # Graphite unreachable is not a meter fault: log and try again next round.
            log.warning('check failed: %s', exc)
        if args.once:
            return 0
        time.sleep(POLL_SECONDS)


if __name__ == '__main__':
    raise SystemExit(main())
