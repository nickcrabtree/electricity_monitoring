#!/usr/bin/env python3
"""Estimate the power an unmetered device draws from its on/off state, read from the unexplained house load.

The unexplained load is the whole-house meter minus the aggregate of the metered plugs (the "unexplained" line on the
Grafana Usage panel). A device with a known on/off state but no power meter lives inside it. At every on and off
transition the unexplained load steps by about the device's draw; unrelated devices switch at random times, so the
median of those steps over many transitions isolates the device. Read-only: Graphite render API only.

  estimate_unmetered.py --device towel_rail                      state from home.electricity.tuya.towel_rail.is_on
  estimate_unmetered.py --state-metric home.tado.hot_water.heating --days 60

Conda env: electricity (stdlib only). Times are UTC; inside the Claude sandbox the host `ubuntu64.local:81` must be
allowed. See docs/CALIBRATION.md ("Estimating unmetered devices").

Limits: needs a state signal accurate to about a minute and a draw above about 100 W (the meter steps in about 90 W
quanta); the result is a median over many cycles, not a live reading; cyclic appliances (washing machine, dishwasher)
give a start step, not a profile.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import GRAPHITE_RENDER_URL  # noqa: E402

MINUTE = 60
PRE = (-6, -1)  # minutes before a transition (relative to the first minute in the new state)
POST = (2, 6)  # minutes after it, leaving a guard for plug lag
MIN_SAMPLES = 3
MIN_EVENTS = 6
GOOD_EVENTS = 10
METER = 'home.electricity.meter.power_watts'
AGGREGATE = 'home.electricity.aggregate.power_watts'


@dataclass
class Estimate:
    n_on: int
    n_off: int
    median_on_watts: float
    median_off_watts: float
    median_watts: float
    iqr_watts: float
    on_fraction: float
    mean_watts: float
    confidence: str


def build_unexplained(meter: dict[int, float], aggregate: dict[int, float]) -> dict[int, float]:
    """Meter minus aggregate per minute. A 0 W meter reading is an outage (it happened for whole days in October)."""
    return {t: meter[t] - aggregate[t] for t in meter if t in aggregate and meter[t] > 0}


def _window(series: dict[int, float], t: int, lo: int, hi: int) -> list[float]:
    return [series[x] for x in range(t + lo * MINUTE, t + hi * MINUTE + 1, MINUTE) if x in series]


def _constant(state: dict[int, bool], t: int, lo: int, hi: int, value: bool) -> bool:
    """True when every state sample in the window equals `value` (missing samples are tolerated)."""
    return all(state[x] == value for x in range(t + lo * MINUTE, t + hi * MINUTE + 1, MINUTE) if x in state)


def _step_at(state: dict[int, bool], unexplained: dict[int, float], t: int, new: bool) -> float | None:
    """Unexplained step across the transition at minute `t`, or None if the windows are not clean."""
    if not (_constant(state, t, PRE[0], PRE[1], not new) and _constant(state, t, 0, POST[1], new)):
        return None
    before = _window(unexplained, t, *PRE)
    after = _window(unexplained, t, *POST)
    if len(before) < MIN_SAMPLES or len(after) < MIN_SAMPLES:
        return None
    return statistics.median(after) - statistics.median(before)


def _confidence(steps: list[float]) -> tuple[float, float, str]:
    if len(steps) < MIN_EVENTS:
        return (statistics.median(steps) if steps else 0.0), 0.0, 'insufficient'
    median = statistics.median(steps)
    q1, _, q3 = statistics.quantiles(steps, n=4)
    iqr = q3 - q1
    if abs(median) <= iqr / 2:
        return median, iqr, 'not detectable'
    if len(steps) >= GOOD_EVENTS and iqr <= abs(median):
        return median, iqr, 'good'
    return median, iqr, 'weak'


def estimate_draw(state: dict[int, bool], unexplained: dict[int, float]) -> Estimate:
    """Median unexplained step across the device's clean on and off transitions."""
    ons: list[float] = []
    offs: list[float] = []
    previous = None
    for t in sorted(state):
        if previous is not None and t - previous[0] == MINUTE and state[t] != previous[1]:
            step = _step_at(state, unexplained, t, state[t])
            if step is not None:
                (ons if state[t] else offs).append(step if state[t] else -step)
        previous = (t, state[t])
    median, iqr, confidence = _confidence(ons + offs)
    on_fraction = sum(state.values()) / len(state) if state else 0.0
    return Estimate(
        n_on=len(ons),
        n_off=len(offs),
        median_on_watts=statistics.median(ons) if ons else 0.0,
        median_off_watts=statistics.median(offs) if offs else 0.0,
        median_watts=median,
        iqr_watts=iqr,
        on_fraction=on_fraction,
        mean_watts=median * on_fraction,
        confidence=confidence,
    )


def sample_interval(state: dict[int, bool]) -> int:
    """Median seconds between state samples (0 with fewer than two)."""
    times = sorted(state)
    return int(statistics.median(b - a for a, b in zip(times, times[1:]))) if len(times) > 1 else 0


def format_result(name: str, e: Estimate) -> str:
    return (
        f'{name}: draws {e.median_watts:.0f} W when on (on-steps {e.median_on_watts:.0f} W, off-drops '
        f'{e.median_off_watts:.0f} W, spread {e.iqr_watts:.0f} W) from {e.n_on} on + {e.n_off} off events; '
        f'on {e.on_fraction * 100:.0f} % of the time = about {e.mean_watts:.0f} W average | confidence: {e.confidence}'
    )


def fetch_minutes(target: str, days: int) -> dict[int, float]:
    query = urllib.parse.urlencode(
        {'target': f'summarize({target},"1min","avg")', 'from': f'-{days}d', 'until': 'now', 'format': 'json'}
    )
    with urllib.request.urlopen(f'{GRAPHITE_RENDER_URL}?{query}', timeout=120) as response:  # noqa: S310
        payload = json.load(response)
    return {t // MINUTE * MINUTE: v for v, t in payload[0]['datapoints'] if v is not None} if payload else {}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--device', action='append', default=[], help='Tuya device name, e.g. towel_rail')
    parser.add_argument('--state-metric', action='append', default=[], help='full Graphite path of a 0/1 state series')
    parser.add_argument('--days', type=int, default=28, help='history to use (default 28)')
    args = parser.parse_args()
    metrics = {d: f'home.electricity.tuya.{d}.is_on' for d in args.device}
    metrics.update({m: m for m in args.state_metric})
    if not metrics:
        parser.error('give at least one --device or --state-metric')
    unexplained = build_unexplained(fetch_minutes(METER, args.days), fetch_minutes(AGGREGATE, args.days))
    for name, metric in metrics.items():
        state = {t: v >= 0.5 for t, v in fetch_minutes(metric, args.days).items()}
        interval = sample_interval(state)
        if interval > MINUTE:
            print(f'{name}: state is sampled every {interval // MINUTE} min; needs 1 min or finer, result unusable')
        print(format_result(name, estimate_draw(state, unexplained)), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
