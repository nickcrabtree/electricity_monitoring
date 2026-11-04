#!/usr/bin/env python3
"""Email Nick when Smart Life / Home Assistant lose the towel rail while it still works on the LAN.

From 2026-11-02 16:23 UTC to 2026-11-04 the towel rail dropped off the Tuya cloud: HA (which reaches it through the
cloud) kept a plausible "off" with last_changed frozen, while blackpi2's direct LAN poll saw it switch several times.
Toggling it in the Smart Life app brought it back.

Each run compares the direct poll (Graphite `home.electricity.tuya.towel_rail.is_on`) with HA's
`switch.towel_rail_switch_1`. It is a fault if HA says unavailable, disagrees with the direct state, or last changed
well before the latest direct transition (it missed an on/off pair). No recent direct data, or HA unreachable, is
"unknown": no alert, no recovery. Email after 30 min of fault, again every 24 h while it lasts, and RECOVERED when it
clears. Marker files in /var/tmp/towel-rail-check. Run every 15 min from quartz's crontab. Stdlib only.

  towel_rail_cloud_check.py              check once
  towel_rail_cloud_check.py --dry-run    print the classification, no email or markers
  towel_rail_cloud_check.py --test-mail  send one test email
"""

from __future__ import annotations

import argparse
import json
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
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SECRETS = REPO / 'secrets_local.env'
RENDER_URL = 'http://ubuntu64.local:81/render'
LOCAL_METRIC = 'home.electricity.tuya.towel_rail.is_on'
HA_URL = 'http://homeassistant.lan:8123'  # .local does not resolve on quartz
HA_ENTITY = 'switch.towel_rail_switch_1'
STATE_DIR = '/var/tmp/towel-rail-check'  # noqa: S108 - persistent marker files, as the meter watchdog uses
SMTP_HOST = 'quartz.local'
SMTP_PORT = 25
ALERT_RCPT = 'nickc@quartz.local'
ALERT_FROM = f'towel-rail-check@{socket.gethostname()}'

LOCAL_STALE_SECONDS = 10 * 60  # no direct point this recent: cannot compare
MISSED_SLACK_SECONDS = 5 * 60  # HA may record a change up to a poll before the direct series does
ALERT_AFTER_SECONDS = 30 * 60
REMIND_EVERY_SECONDS = 24 * 60 * 60
FIX = 'Fix that worked on 2026-11-04: toggle the towel rail in the Smart Life app.'


def _when(t: float | None) -> str:
    return 'never' if t is None else datetime.fromtimestamp(t, timezone.utc).strftime('%Y-%m-%d %H:%M UTC')


def parse_render(payload: list) -> list[tuple[float, float]]:
    if not payload:
        return []
    return [(float(t), float(v)) for v, t in payload[0]['datapoints'] if v is not None]


def parse_ha(payload: dict) -> dict:
    changed = datetime.fromisoformat(payload['last_changed']).timestamp()
    return {'state': payload['state'], 'last_changed': changed}


def classify(
    points: list[tuple[float, float]], ha: dict | None, now: float, fault_since: float | None = None
) -> tuple[str, str]:
    """Return ('ok' | 'fault' | 'unknown', detail) for direct poll points and HA's state.

    During a fault episode (fault_since set), recovery also needs HA to have registered a change since the fault
    began: missed transitions may have left the 6 h lookback while HA's state happens to match again.
    """
    if not points or now - points[-1][0] > LOCAL_STALE_SECONDS:
        return 'unknown', f'no direct reading since {_when(points[-1][0] if points else None)}'
    if ha is None:
        return 'unknown', 'Home Assistant unreachable'
    states = [(t, 'on' if v >= 0.5 else 'off') for t, v in points]
    local_state = states[-1][1]
    last_transition = None
    for i in range(len(states) - 1, 0, -1):
        if states[i][1] != states[i - 1][1]:
            last_transition = states[i][0]
            break
    detail = (
        f'Direct LAN poll (blackpi2): {local_state}, last switched {_when(last_transition)}.\n'
        f'Smart Life / HA ({HA_ENTITY}): {ha["state"]}, last changed {_when(ha["last_changed"])}.'
    )
    if ha['state'] == 'unavailable':
        return 'fault', detail
    if ha['state'] != local_state:
        return 'fault', detail
    if last_transition is not None and ha['last_changed'] < last_transition - MISSED_SLACK_SECONDS:
        return 'fault', detail
    if fault_since is not None and ha['last_changed'] < fault_since - MISSED_SLACK_SECONDS:
        return 'fault', detail
    return 'ok', detail


def send_alert_email(subject: str, body: str) -> bool:
    """Email Nick via quartz's postfix. Never raises."""
    try:
        msg = EmailMessage()
        msg['From'], msg['To'], msg['Subject'] = ALERT_FROM, ALERT_RCPT, subject
        msg.set_content(body)
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as smtp:
            smtp.send_message(msg)
        return True
    except (OSError, smtplib.SMTPException) as exc:
        print(f'could not send alert email: {exc}', file=sys.stderr)
        return False


class Episode:
    """Fault episode in marker files: since (fault start), alerted (an email went out), last_alert (last email)."""

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

    def since(self) -> float | None:
        return self._read('since')

    def observe(self, status: str, detail: str) -> None:
        if status == 'ok':
            self._clear(detail)
        elif status == 'fault':
            self._record(detail)

    def _clear(self, detail: str) -> None:
        since = self._read('since')
        if self._read('alerted') is not None:
            hours = (self.now() - since) / 3600 if since else 0
            self.send(
                '[towel rail] RECOVERED - Smart Life / HA see it again',
                f'The towel rail is back in Smart Life / Home Assistant after {hours:.1f} h.\n\n{detail}\n',
            )
        for name in ('since', 'alerted'):
            self._remove(name)

    def _record(self, detail: str) -> None:
        now = self.now()
        since = self._read('since')
        if since is None:
            since = now
            self._write('since', since)
        if now - since < ALERT_AFTER_SECONDS:
            return
        last = self._read('last_alert')
        if last is not None and now - last < REMIND_EVERY_SECONDS:
            return
        still = 'still ' if self._read('alerted') is not None else ''
        body = (
            f'Smart Life / Home Assistant have {still}lost the towel rail since {_when(since)}, although it still '
            f'works on the home network.\n\n{detail}\n\n{FIX}\n\n'
            f'Checked at {_when(now)} on {socket.gethostname()}; at most one of these emails a day. '
            'See docs/OPERATIONS.md in electricity_monitoring.\n'
        )
        if self.send(f'[towel rail] {still}not reachable in Smart Life / HA', body):
            self._write('alerted', now)
            self._write('last_alert', now)


def _ha_token() -> str:
    for line in SECRETS.read_text().splitlines():
        key, _, value = line.partition('=')
        if key.strip() == 'HA_TOKEN':
            return value.strip().strip('"\'')
    raise KeyError(f'HA_TOKEN not in {SECRETS}')


def fetch_local() -> list[tuple[float, float]]:
    query = urllib.parse.urlencode({'target': LOCAL_METRIC, 'from': '-6h', 'format': 'json'})
    with urllib.request.urlopen(f'{RENDER_URL}?{query}', timeout=20) as resp:  # noqa: S310 - fixed http URL
        return parse_render(json.load(resp))


def fetch_ha() -> dict | None:
    req = urllib.request.Request(  # noqa: S310 - fixed http URL
        f'{HA_URL}/api/states/{HA_ENTITY}', headers={'Authorization': f'Bearer {_ha_token()}'}
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310 - fixed http URL
            return parse_ha(json.load(resp))
    except OSError:
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--test-mail', action='store_true')
    parser.add_argument('--state-dir', default=STATE_DIR)
    args = parser.parse_args()
    if args.test_mail:
        return 0 if send_alert_email('[towel rail] test email', f'Sent at {_when(time.time())}.\n') else 1
    episode = Episode(args.state_dir)
    status, detail = classify(fetch_local(), fetch_ha(), time.time(), fault_since=episode.since())
    print(f'{_when(time.time())} {status}: {detail}'.replace('\n', ' '))
    if not args.dry_run:
        episode.observe(status, detail)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
