#!/usr/bin/env python3
"""Read-only report of likely replacement phones, for the Home Assistant phone-change check.

For each person in presence/people_config.yaml: their configured Wi-Fi MACs and when those were last seen, and,
once none has been seen for MIN_GAP_HOURS, the unmapped devices that first joined the Wi-Fi around the time the
old phone vanished (presence/mac_learning_state.json `device_fingerprints`, last-seen times also from
presence/state.json). Prints JSON (`format_version` 1) to stdout and writes nothing.

A candidate is only a suggestion: phones mostly call themselves "iphone" and handsets are handed on within the
family, so nothing here ever changes the mapping. Nick confirms, then the electricity_monitoring session edits
people_config.yaml. No IP addresses or movement history are reported.

Usage: tools/phone_candidates.py [--config F] [--learning-state F] [--state F] [--now EPOCH]
Exit status 0 on success, 2 when an input cannot be read or parsed (message on stderr).
"""

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

FORMAT_VERSION = 1
MIN_GAP_HOURS = 6  # configured phones unseen this long make the person "stale" and get candidates
WINDOW_BEFORE_HOURS = 48  # a new phone may be set up while the old one is still on the Wi-Fi
WINDOW_AFTER_HOURS = 72
PRESENCE = Path(__file__).resolve().parent.parent / 'presence'


def iso(epoch):
    if epoch is None:
        return None
    return datetime.fromtimestamp(epoch, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _mac(text):
    return str(text).strip().upper()


def _last_seen(mac, fingerprints, last_seen_wifi):
    times = [t for t in (last_seen_wifi.get(mac), (fingerprints.get(mac) or {}).get('last_seen')) if t]
    return max(times) if times else None


def build_report(config, learning, state, now=None):
    """Build the report dict from the parsed config, MAC-learning state and presence state."""
    now = time.time() if now is None else now
    fingerprints = {_mac(m): v for m, v in (learning.get('device_fingerprints') or {}).items()}
    last_seen_wifi = {_mac(m): t for m, t in (state.get('last_seen_wifi') or {}).items()}
    people = config.get('people') or []
    mapped = {_mac(m) for p in people for m in (p.get('wifi_macs') or [])}

    entries = []
    for p in people:
        macs = [_mac(m) for m in (p.get('wifi_macs') or [])]
        configured = [
            {
                'mac': m,
                'hostname': (fingerprints.get(m) or {}).get('hostname_pattern'),
                'last_seen': iso(_last_seen(m, fingerprints, last_seen_wifi)),
            }
            for m in macs
        ]
        seen = [t for t in (_last_seen(m, fingerprints, last_seen_wifi) for m in macs) if t]
        anchor = max(seen) if seen else None
        if not macs:
            status = 'no_mapping'
        elif anchor is None:
            status = 'never_seen'
        elif now - anchor >= MIN_GAP_HOURS * 3600:
            status = 'stale'
        else:
            status = 'ok'
        candidates = []
        if status == 'stale':
            for mac, f in fingerprints.items():
                first, last = f.get('first_seen'), f.get('last_seen')
                if mac in mapped or not first or not last:
                    continue
                if not anchor - WINDOW_BEFORE_HOURS * 3600 <= first <= anchor + WINDOW_AFTER_HOURS * 3600:
                    continue
                if last < anchor:  # gone before the old phone vanished
                    continue
                candidates.append(
                    {
                        'mac': mac,
                        'hostname': f.get('hostname_pattern'),
                        'first_seen': iso(first),
                        'last_seen': iso(last),
                        'hours_after_old_last_seen': round((first - anchor) / 3600, 1),
                        'provenance': 'device_fingerprints',
                        'conflicts': [],
                    }
                )
            candidates.sort(key=lambda c: (abs(c['hours_after_old_last_seen']), c['mac']))
        entries.append(
            {
                'person': p.get('person'),
                'ha_device_tracker': p.get('ha_device_tracker'),
                'status': status,
                'wifi_last_seen': iso(anchor),
                'configured_macs': configured,
                'candidates': candidates,
            }
        )

    holders = {}
    for e in entries:
        for c in e['candidates']:
            holders.setdefault(c['mac'], []).append(e['person'])
    for e in entries:
        for c in e['candidates']:
            c['conflicts'] = sorted(x for x in holders[c['mac']] if x != e['person'])

    return {
        'format_version': FORMAT_VERSION,
        'generated': iso(now),
        'window_hours': {'before': WINDOW_BEFORE_HOURS, 'after': WINDOW_AFTER_HOURS, 'min_gap': MIN_GAP_HOURS},
        'people': entries,
    }


def _load(path, parse):
    try:
        with open(path, encoding='utf-8') as fh:
            return parse(fh)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise SystemExit(f'phone_candidates: cannot read {path}: {exc}') from exc


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--config', default=str(PRESENCE / 'people_config.yaml'))
    ap.add_argument('--learning-state', default=str(PRESENCE / 'mac_learning_state.json'))
    ap.add_argument('--state', default=str(PRESENCE / 'state.json'))
    ap.add_argument('--now', type=float, help='epoch seconds to report as of (default: now)')
    args = ap.parse_args(argv)
    try:
        config = _load(args.config, yaml.safe_load) or {}
        learning = _load(args.learning_state, json.load)
        state = _load(args.state, json.load)
    except SystemExit as exc:
        print(exc, file=sys.stderr)
        return 2
    print(json.dumps(build_report(config, learning, state, now=args.now), indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
