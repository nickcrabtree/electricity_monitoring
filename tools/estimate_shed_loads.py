#!/usr/bin/env python3
"""Estimate the power of loads with no meter behind the Shed plug, from the plug's own readings (read-only).

The Shed plug (`tuya.shed`) is the feed for the whole shed. It reports in sparse snapshots: a step when the load changes
by tens of watts, plus a refresh about once an hour, so a series of its readings is not a power trace. Its children
(Quartz server plug, shed freezer plug, ...) are metered, but their plugs are just as sparse, so subtracting them by eye
mixes readings taken at different instants. This tool instead:

1. takes the instants at which the Shed plug updated (value changed);
2. models the Quartz server's wall power at each instant from the 1 s GPU and CPU collectors, calibrated on the Quartz
   plug's own updates over the previous day (`QuartzModel`; R^2 about 0.9, residual about 27 W);
3. subtracts that and the other metered children (`aggregate_hierarchy.json`) held at that instant;
4. fits the remaining residual by least squares against the on/off intervals you give with --load, with a baseline for
   everything else, and prints each load with its standard error.

  estimate_shed_loads.py --from 2026-11-09T16:00 --until 2026-11-09T22:00 \\
      --load 'shed lights=17:25-18:40' --load 'gym lights=18:20-19:00'

Times are UTC; the spans in --load and --exclude are clock times on the date of --from. A reading taken while a load's
state is uncertain belongs in --exclude. Conda env: electricity (stdlib only). Inside the Claude sandbox the host
`ubuntu64.local:81` must be allowed. The 1 s GPU and 5 s CPU series only reach back about a day, so --until must be
within that. See docs/CALIBRATION.md ("Gym lights in the shed").
"""

from __future__ import annotations

import argparse
import bisect
import json
import math
import sys
import urllib.parse
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import GRAPHITE_RENDER_URL  # noqa: E402

Points = list[tuple[float, float]]
Spans = list[tuple[float, float]]

QUARTZ_LAG = -1  # s: the Quartz plug's reading is best matched by the collectors one second later (measured 2026-11-09)
GPU_WINDOW = 5  # s of 1 s GPU power averaged for one instant
CPU_WINDOW = 5  # s of CPU load (5 s collector) averaged for one instant
HOLD_MAX_AGE = 40  # s: a plug reading older than this is not a reading of that instant (plugs report every 10-30 s)
SHED = 'tuya.shed'
QUARTZ = 'tuya.quartz_server_shed'
HIERARCHY_FILE = Path(__file__).resolve().parent.parent / 'aggregate_hierarchy.json'
GPU_TARGET = 'computer.quartz.gpu.power_draw'
CPU_TARGET = 'computer.quartz.cpu.load'


def metric(device_key: str) -> str:
    return f'home.electricity.{device_key}.power_watts'


def update_instants(points: Points) -> Points:
    """The points whose value differs from the previous point (a plug updated at that instant)."""
    return [(t, v) for (_, v0), (t, v) in zip(points, points[1:]) if v != v0]


def window_mean(points: Points, t: float, width: float, lag: float = 0) -> float | None:
    """Mean of the values with timestamps in (t - lag - width, t - lag]; None if there are none. points must be sorted."""
    end = t - lag
    times = [p[0] for p in points]
    lo, hi = bisect.bisect_right(times, end - width), bisect.bisect_right(times, end)
    if hi <= lo:
        return None
    return sum(v for _, v in points[lo:hi]) / (hi - lo)


def hold(points: Points, t: float, max_age: float | None = None) -> float | None:
    """The latest value at or before t, or None if there is none or it is older than max_age."""
    times = [p[0] for p in points]
    i = bisect.bisect_right(times, t) - 1
    if i < 0 or (max_age is not None and t - times[i] > max_age):
        return None
    return points[i][1]


def _invert(matrix: list[list[float]]) -> list[list[float]]:
    """Gauss-Jordan inverse with partial pivoting; ValueError if singular."""
    n = len(matrix)
    a = [list(row) + [1.0 if i == j else 0.0 for j in range(n)] for i, row in enumerate(matrix)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(a[r][col]))
        if abs(a[pivot][col]) < 1e-9:
            raise ValueError('singular matrix: the loads cannot be told apart from these readings')
        a[col], a[pivot] = a[pivot], a[col]
        scale = a[col][col]
        a[col] = [x / scale for x in a[col]]
        for r in range(n):
            if r != col:
                factor = a[r][col]
                a[r] = [x - factor * y for x, y in zip(a[r], a[col])]
    return [row[n:] for row in a]


def _normal_inverse(rows: Sequence[Sequence[float]]) -> list[list[float]]:
    k = len(rows[0])
    xtx = [[sum(r[i] * r[j] for r in rows) for j in range(k)] for i in range(k)]
    return _invert(xtx)


def fit_least_squares(rows: Sequence[Sequence[float]], y: Sequence[float]) -> tuple[list[float], float]:
    """Ordinary least squares: (coefficients, residual standard deviation with n - k degrees of freedom)."""
    n, k = len(rows), len(rows[0]) if rows else 0
    if n <= k:
        raise ValueError(f'need more than {k} rows to fit {k} coefficients, got {n}')
    inv = _normal_inverse(rows)
    xty = [sum(r[i] * v for r, v in zip(rows, y)) for i in range(k)]
    beta = [sum(inv[i][j] * xty[j] for j in range(k)) for i in range(k)]
    ssr = sum((v - sum(b * x for b, x in zip(beta, r))) ** 2 for r, v in zip(rows, y))
    return beta, math.sqrt(ssr / (n - k))


@dataclass(frozen=True)
class QuartzModel:
    """Quartz wall power (W) = beta[0] + beta[1] * GPU power (W) + beta[2] * CPU load (%); sd is the fit residual."""

    beta: tuple[float, float, float]
    sd: float

    def predict(self, gpu_w: float, cpu_load: float) -> float:
        return self.beta[0] + self.beta[1] * gpu_w + self.beta[2] * cpu_load


def _collector_values(t: float, gpu: Points, cpu: Points) -> tuple[float, float] | None:
    g = window_mean(gpu, t, GPU_WINDOW, QUARTZ_LAG)
    c = window_mean(cpu, t, CPU_WINDOW, QUARTZ_LAG)
    return None if g is None or c is None else (g, c)


def fit_quartz_model(plug_updates: Points, gpu: Points, cpu: Points) -> QuartzModel:
    """Calibrate on the Quartz plug's own updates against the collectors at the same instants."""
    rows, y = [], []
    for t, v in plug_updates:
        pair = _collector_values(t, gpu, cpu)
        if pair:
            rows.append([1.0, *pair])
            y.append(v)
    beta, sd = fit_least_squares(rows, y)
    return QuartzModel(beta=(beta[0], beta[1], beta[2]), sd=sd)


@dataclass(frozen=True)
class Residual:
    t: float
    shed: float
    quartz: float
    children: float
    resid: float


def residuals(shed_updates: Points, model: QuartzModel, gpu: Points, cpu: Points, children: dict[str, Points]):
    """At each Shed update: Shed reading less the modelled Quartz power less the held readings of the other children."""
    rows = []
    for t, shed in shed_updates:
        pair = _collector_values(t, gpu, cpu)
        if pair is None:
            continue
        quartz = model.predict(*pair)
        other = sum(hold(series, t, HOLD_MAX_AGE) or 0.0 for series in children.values())
        rows.append(Residual(t=t, shed=shed, quartz=quartz, children=other, resid=shed - quartz - other))
    return rows


@dataclass(frozen=True)
class Load:
    watts: float
    se: float


@dataclass(frozen=True)
class Fit:
    baseline: Load
    loads: dict[str, Load]
    n: int
    residual_sd: float | None


def _inside(t: float, spans: Spans) -> bool:
    return any(a <= t < b for a, b in spans)


def estimate_loads(obs: Points, intervals: dict[str, Spans], sigma: float, exclude: Spans | None = None) -> Fit:
    """Least squares of residuals against 'load is on' indicators; sigma is the noise of one reading (W).

    Readings outside every interval fix the baseline (everything else behind the plug). Standard errors are
    sigma * sqrt(diag((X'X)^-1)); the loads may overlap. ValueError if a load has no reading or none is a baseline.
    """
    kept = [(t, r) for t, r in obs if not _inside(t, exclude or [])]
    names = list(intervals)
    rows = [[1.0] + [1.0 if _inside(t, intervals[n]) else 0.0 for n in names] for t, _ in kept]
    if not any(sum(row[1:]) == 0 for row in rows):
        raise ValueError('no reading outside every interval to fix the baseline')
    for j, name in enumerate(names, start=1):
        if not any(row[j] for row in rows):
            raise ValueError(f'no reading inside the interval of {name!r}')
    y = [r for _, r in kept]
    inv = _normal_inverse(rows)
    k = len(names) + 1
    xty = [sum(row[i] * v for row, v in zip(rows, y)) for i in range(k)]
    beta = [sum(inv[i][j] * xty[j] for j in range(k)) for i in range(k)]
    se = [sigma * math.sqrt(inv[i][i]) for i in range(k)]
    ssr = sum((v - sum(b * x for b, x in zip(beta, row))) ** 2 for row, v in zip(rows, y))
    sd = math.sqrt(ssr / (len(rows) - k)) if len(rows) > k else None
    return Fit(
        baseline=Load(beta[0], se[0]),
        loads={n: Load(beta[i], se[i]) for i, n in enumerate(names, start=1)},
        n=len(rows),
        residual_sd=sd,
    )


def parse_clock(text: str) -> float:
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc).timestamp()


def parse_spans(text: str, day_start: float) -> Spans:
    """'18:20-18:40,19:00-19:10' as epoch spans on the UTC date of day_start."""
    midnight = day_start - day_start % 86400
    spans = []
    for part in text.split(','):
        start, _, end = part.strip().partition('-')
        a, b = (midnight + int(x.split(':')[0]) * 3600 + int(x.split(':')[1]) * 60 for x in (start, end))
        if b <= a:
            raise ValueError(f'span {part!r} ends before it starts')
        spans.append((a, b))
    return spans


def parse_load(text: str, day_start: float) -> tuple[str, Spans]:
    name, _, spans = text.partition('=')
    if not spans:
        raise ValueError(f'--load needs NAME=SPANS, got {text!r}')
    return name.strip(), parse_spans(spans, day_start)


def children_of_shed() -> list[str]:
    hierarchy = json.loads(HIERARCHY_FILE.read_text())
    return [c for c in hierarchy.get(SHED, []) if c != QUARTZ]


def fetch(targets: list[str], start: float, until: float) -> dict[str, Points]:
    query = urllib.parse.urlencode(
        [('target', t) for t in targets]
        + [('from', int(start)), ('until', int(until)), ('format', 'json'), ('noCache', 1)]
    )
    with urllib.request.urlopen(f'{GRAPHITE_RENDER_URL}?{query}', timeout=60) as response:  # noqa: S310
        payload = json.load(response)
    return {s['target']: [(float(t), float(v)) for v, t in s['datapoints'] if v is not None] for s in payload}


def _stamp(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime('%H:%M:%S')


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--from', dest='start', required=True, help='UTC, e.g. 2026-11-09T16:00')
    parser.add_argument('--until', required=True)
    parser.add_argument('--load', action='append', default=[], help='NAME=HH:MM-HH:MM[,...] when the load was on')
    parser.add_argument('--exclude', default='', help='HH:MM-HH:MM[,...] readings to ignore')
    parser.add_argument('--fit-hours', type=float, default=23.0, help='hours of Quartz history for the model')
    parser.add_argument('--sigma', type=float, help='noise of one reading in W (default: the Quartz model residual)')
    args = parser.parse_args(argv)
    start, until = parse_clock(args.start), parse_clock(args.until)
    loads = dict(parse_load(item, start) for item in args.load)
    exclude = parse_spans(args.exclude, start) if args.exclude else []
    fit_from = until - args.fit_hours * 3600
    kids = children_of_shed()
    data = fetch([metric(SHED), metric(QUARTZ), GPU_TARGET, CPU_TARGET] + [metric(k) for k in kids], fit_from, until)
    shed, quartz = data.get(metric(SHED), []), data.get(metric(QUARTZ), [])
    gpu, cpu = data.get(GPU_TARGET, []), data.get(CPU_TARGET, [])
    model = fit_quartz_model(update_instants(quartz), gpu, cpu)
    print(
        f'Quartz model: {model.beta[0]:.1f} + {model.beta[1]:.3f} x GPU W + {model.beta[2]:.2f} x CPU load %, sd {model.sd:.1f} W'
    )
    shed_updates = [(t, v) for t, v in update_instants(shed) if start <= t <= until]
    rows = residuals(shed_updates, model, gpu, cpu, {k: data.get(metric(k), []) for k in kids})
    print('time (UTC)  shed W  Quartz model W  other children W  residual W')
    for r in rows:
        print(f'{_stamp(r.t)}  {r.shed:6.1f}  {r.quartz:14.1f}  {r.children:16.1f}  {r.resid:10.1f}')
    if loads:
        sigma = args.sigma if args.sigma is not None else model.sd
        fit = estimate_loads([(r.t, r.resid) for r in rows], loads, sigma, exclude)
        print(
            f'\nFit to {fit.n} readings (noise per reading {sigma:.1f} W; observed spread {fit.residual_sd or float("nan"):.1f} W):'
        )
        print(
            f'  baseline (everything else behind the Shed plug): {fit.baseline.watts:6.1f} +/- {fit.baseline.se:.1f} W'
        )
        for name, load in fit.loads.items():
            print(f'  {name}: {load.watts:6.1f} +/- {load.se:.1f} W')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
