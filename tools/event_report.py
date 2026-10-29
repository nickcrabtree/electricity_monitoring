#!/usr/bin/env python3
"""Cumulative house consumption since the start of an Equiwatt event, from the Graphite render API (read-only).

Prints one line per report: kWh since the start (meter `total_kwh` delta), the mean kW so far and over the last
interval, the monitored-plug kWh (aggregate) and the unmonitored remainder. Conda env: electricity (stdlib only).

  event_report.py --start 2026-10-29T17:00 --end 2026-10-29T19:00            one report, now
  event_report.py --start ... --end ... --every 900                          report at each 15 min mark until the end

Times are UTC (the clocks changed on 2026-10-25, so UTC is local time until the spring change).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import GRAPHITE_RENDER_URL  # noqa: E402

TARGETS = {
    'kwh': 'home.electricity.meter.total_kwh',
    'meter': 'home.electricity.meter.power_watts',
    'agg': 'home.electricity.aggregate.power_watts',
}


def fetch(target: str, start: datetime, until: datetime) -> list[tuple[int, float]]:
    query = urllib.parse.urlencode(
        {'target': target, 'from': int(start.timestamp()), 'until': int(until.timestamp()), 'format': 'json'}
    )
    with urllib.request.urlopen(f'{GRAPHITE_RENDER_URL}?{query}', timeout=30) as response:  # noqa: S310
        payload = json.load(response)
    return [(t, v) for v, t in payload[0]['datapoints'] if v is not None] if payload else []


def integral_kwh(points: list[tuple[int, float]], step: int) -> float:
    return sum(v for _, v in points) * step / 3600 / 1000


def report(start: datetime, end: datetime, interval: int, now: datetime, target_kwh: float | None = None) -> str:
    stop = min(now, end)
    kwh = fetch(TARGETS['kwh'], start, stop)
    if len(kwh) < 2:
        return f'{now:%H:%M}Z no meter data yet'
    cumulative = kwh[-1][1] - kwh[0][1]
    window_start = kwh[-1][0] - interval
    recent = [p for p in kwh if p[0] >= window_start]
    recent_kw = (recent[-1][1] - recent[0][1]) / ((recent[-1][0] - recent[0][0]) / 3600) if len(recent) > 1 else 0.0
    agg = fetch(TARGETS['agg'], start, stop)
    step = (agg[1][0] - agg[0][0]) if len(agg) > 1 else 10
    plugs = integral_kwh(agg, step)
    elapsed = (stop - start).total_seconds() / 60
    total_h = (end - start).total_seconds() / 3600
    remaining_h = max(0.0, (end - stop).total_seconds() / 3600)
    span_min = min(interval, int(kwh[-1][0] - kwh[0][0])) // 60
    head = f'{now:%H:%M}Z ({elapsed:.0f}/{total_h * 60:.0f} min)'
    used = f'ENERGY kWh: used {cumulative:.2f}'
    rate = f'last {span_min} min {recent_kw:.2f} kW'
    detail = f'(plugs {plugs:.2f}, unmonitored {cumulative - plugs:.2f})'
    if remaining_h > 0:
        projected = cumulative + recent_kw * remaining_h
        used += f', expected total at 19:00 {projected:.2f}'
        if target_kwh is not None:
            left = target_kwh - cumulative
            used += f' vs target <{target_kwh:.2f}'
            rate += f', rest of window must average <{max(left, 0) / remaining_h:.2f} kW ({left:.2f} kWh left)'
        return f'{head} | {used} | {rate} {detail}'
    verdict = ''
    if target_kwh is not None:
        verdict = f'FINAL {"MET" if cumulative < target_kwh else "MISSED"}: {cumulative:.2f} kWh vs target <{target_kwh:.2f} kWh | '
    return f'{head} | {verdict}{used} | {rate} {detail}'


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start', required=True, help='UTC, e.g. 2026-10-29T17:00')
    parser.add_argument('--end', required=True, help='UTC, e.g. 2026-10-29T19:00')
    parser.add_argument('--target-kwh', type=float, default=None, help='target total kWh for the window')
    parser.add_argument('--every', type=int, default=0, help='seconds between reports (0 = one report)')
    args = parser.parse_args()
    start = datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc)
    end = datetime.fromisoformat(args.end).replace(tzinfo=timezone.utc)
    interval = args.every or 900
    if not args.every:
        print(report(start, end, interval, datetime.now(timezone.utc), args.target_kwh), flush=True)
        return 0
    mark = start + timedelta(seconds=args.every)
    while True:
        wait = (mark - datetime.now(timezone.utc)).total_seconds()
        if wait > 0:
            time.sleep(wait)
        try:
            print(report(start, end, interval, mark, args.target_kwh), flush=True)
        except (OSError, ValueError, IndexError, KeyError) as exc:
            print(f'{mark:%H:%M}Z report failed: {exc}', flush=True)
        if mark >= end:
            return 0
        mark += timedelta(seconds=args.every)


if __name__ == '__main__':
    raise SystemExit(main())
