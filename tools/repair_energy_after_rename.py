#!/usr/bin/env python3
"""Repair the energy figures damaged when two Tuya plugs were renamed on 2026-11-06.

Background (docs/OPERATIONS.md, "Moving series to a new name"): plug bf19c62a3629c3fc15br3k went from "Boot room
freezer" to the dishwasher (cutover 12:18:40 GMT) and bfb6f68a52a77dcbb2niyu from "Garage power A" to the tumble
dryer (12:08:15 GMT). The collector switched names at about 12:46 and 12:55. Until aggregate_energy.py learnt to
ignore stale devices, it kept counting a device's last reading for its whole one-hour render window, so:

  * aggregate.power_watts counted the dishwasher twice (stale boot_room_freezer 1648.9 W plus the live dishwasher) in
    every cycle from the first one that saw the new name until the old name's last point left the render window;
    garage_power_a was stale at 0 W and added nothing. The aggregate energy counters (four Graphite series and the top
    four counters in energy_state.json) gained that stale power times each cycle's interval.
  * The new names' energy counters held other totals: the Graphite points from the cutover to the aggregator's first
    own write are the old names' totals copied by graphite_move_series.py, and the state then carried on from an old
    dishwasher entry (May 2026, year 371 kWh) or started fresh at the rename (tumble dryer).

What this tool computes, from the Graphite render API only (noCache=1; blackpi2 is not contacted):

  over-count   per rename: the old name's last reading s (the new name's power at the old name's last point), and the
               aggregator cycles that counted both names, found from aggregate.power_watts minus the sum of the device
               power series (shed hierarchy applied, newest point within the stale limit): a cycle counts both when
               that difference is closer to s than to 0. The first such cycle after the old name's last point starts
               the window; the last one before last point + render window + 90 s (Graphite's 60 s render cache plus a
               cycle) ends it. Excess energy per cycle = s * dt, with dt = the cycle's aggregate counter increment
               divided by its power (exact for the aggregator's rectangle rule); bucket timestamps are the fallback.
  corrections  aggregate.power_watts minus s inside the window; every aggregate energy point from the window start
               minus the excess accumulated so far, until that counter's next reset (day at local midnight, week
               Monday 01:00, month 1st 01:00, year 1 Jan 01:00, Europe/London, as apply_resets on blackpi2).
  new names    every existing energy_kwh_* point from the cutover T to `until` becomes the integral of the device's own
               power since T (Nick: the appliance is metered from T; no inherited totals). Rule: the aggregator's
               trapezoid between successive readings, applied to successive power points; a gap longer than the stale
               limit adds nothing; the integral at a point's time is interpolated inside its segment. Each counter
               restarts at its own reset boundary if one falls after T.
  state        the top-level counters minus the excess (skipped for a counter reset since the window), the new names'
               four counters set to the integral at the state's last_ts, and the dead entries removed.

Subcommands:
  plan    read-only: measurements, before/after numbers for state (with --state COPY) and a sample of series points.
  state   --state COPY --output NEW: write a corrected copy. Never touches its input; refuses an existing output.
  series  points to rewrite (dry run by default). With --apply: writes a JSON log of every point's old and new value to
          --log-dir first, sends the new values through carbon's plaintext port at the existing timestamps, then reads
          them back at 5 s and checks the 60 s archive rolled them up.
  verify  --log FILE: read-only check that Graphite holds the logged new values at 5 s and 60 s.
  redo    --log FILE [--apply]: send the logged new values again (to finish a partial send).
  undo    --log FILE [--apply]: send the logged old values back.

After an apply the over-count plateau is gone from aggregate.power_watts, so plan and series refuse; from then on work
from the series log with verify, redo and undo.

Archive limits: every series here is 5s:24h then 60s (average, xFilesFactor 0). Whisper re-averages a 60 s bucket from
the 5 s points it holds, so rewriting every 5 s point of a minute corrects the minute and the coarser archives. A point
older than 24 h is no longer in the 5 s archive and a carbon write would replace the whole 60 s bucket with one value,
so `series` refuses points within 10 min of leaving it. The earliest rewritten point is the tumble dryer's cutover
(2026-11-06 12:08:15 GMT): run `series --apply` before about 12:00 GMT on 2026-11-07 (the dishwasher's cutover 12:18:40
and the aggregate window from 12:47 follow later). The day counters reset at 00:00 GMT; the tool handles either side.

Run from the repository root on quartz in conda env `electricity`; the sandbox must allow ubuntu64.local:81 (reads)
and 192.168.86.123:2003 (carbon, --apply only). Times: epoch seconds or ISO 8601 with a zone.
"""

from __future__ import annotations

import argparse
import bisect
import json
import socket
import statistics
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402
from aggregate_energy import (  # noqa: E402
    HIERARCHY_FILE,
    current_day_boundary,
    current_month_boundary,
    current_week_boundary,
    current_year_boundary,
    load_hierarchy,
)

PREFIX = config.METRIC_PREFIX
AGGREGATE = f'{PREFIX}.aggregate'
CARBON_HOST = '192.168.86.123'  # the Graphite host; the one fixed address the LAN policy allows
CARBON_PORT = 2003
LOG_DIR = Path('/ncloud/Nick/electricity_monitoring/backups/2026-11-06/energy_repair')
LOCAL_TZ = ZoneInfo('Europe/London')  # blackpi2's zone, which apply_resets uses
RENDER_WINDOW = 3600  # the aggregator's GRAPHITE_RENDER_WINDOW ('-1h') when the over-count happened
START_SLACK = 180  # the first cycle that saw the new name came this soon after the old name's last point
END_SLACK = 90  # Graphite's 60 s render cache plus one cycle
FINE_RETENTION = 86400  # 5 s archive
FINE_MARGIN = 600
READBACK_WAIT = 120.0
KWH = 3.6e6


class Refused(RuntimeError):
    """A safety check failed; nothing was written."""


@dataclass(frozen=True)
class Rename:
    old: str
    new: str
    cutover: int  # first point of the plug on its new appliance
    last_old_point: int  # the collector's last point under the old name


def parse_time(text: str) -> int:
    try:
        return int(float(text))
    except ValueError:
        pass
    parsed = datetime.fromisoformat(text.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError(f'{text!r}: give a zone (Z or +00:00)')
    return int(parsed.timestamp())


def fmt(ts: float | None) -> str:
    if ts is None:
        return '-'
    return datetime.fromtimestamp(ts, timezone.utc).strftime('%Y-%m-%d %H:%M:%S')


RENAMES = (
    Rename(
        'tuya.boot_room_freezer',
        'tuya.dishwasher',
        parse_time('2026-11-06T12:18:40Z'),
        parse_time('2026-11-06T12:46:05Z'),
    ),
    Rename(
        'tuya.garage_power_a',
        'tuya.tumble_dryer',
        parse_time('2026-11-06T12:08:15Z'),
        parse_time('2026-11-06T12:54:40Z'),
    ),
)
# boot_room_freezer and garage_power_a are dead names; kasa.tumble_dryer is the abandoned KP115.
DROP_STATE_KEYS = ('tuya.boot_room_freezer', 'tuya.garage_power_a', 'kasa.tumble_dryer')


@dataclass(frozen=True)
class Counter:
    period: str
    metric: str
    attr: str
    reset_attr: str
    boundary: Callable[[datetime], datetime]

    def start_of_period(self, ts: float) -> float:
        return self.boundary(datetime.fromtimestamp(ts, LOCAL_TZ)).timestamp()


COUNTERS = (
    Counter('day', 'energy_kwh_daily', 'day_kwh', 'last_day_reset', current_day_boundary),
    Counter('week', 'energy_kwh_weekly', 'week_kwh', 'last_week_reset', current_week_boundary),
    Counter('month', 'energy_kwh_monthly', 'month_kwh', 'last_month_reset', current_month_boundary),
    Counter('year', 'energy_kwh_yearly', 'year_kwh', 'last_year_reset', current_year_boundary),
)

Points = list  # [(ts, value)], ascending, no None


@dataclass(frozen=True)
class Change:
    metric: str
    ts: int
    old: float
    new: float


# --- integration --------------------------------------------------------------------------------------------------


def cumulative_energy(points: Points, start: int, stale_after: float) -> Points:
    """[(ts, kWh since start)] by the trapezoid between successive points at or after start.

    A gap longer than stale_after adds nothing: the fixed aggregator does not count a device that long silent.
    """
    pts = [(t, v) for t, v in points if t >= start]
    if not pts:
        return [(start, 0.0)]
    out = [(pts[0][0], 0.0)]
    total = 0.0
    for (t0, v0), (t1, v1) in zip(pts, pts[1:]):
        if t1 - t0 <= stale_after:
            total += (v0 + v1) / 2.0 * (t1 - t0) / KWH
        out.append((t1, total))
    return out


def energy_at(cum: Points, ts: float) -> float:
    """Integral up to ts: 0 before the first point, interpolated inside a segment, held after the last."""
    times = [t for t, _ in cum]
    i = bisect.bisect_right(times, ts) - 1
    if i < 0:
        return 0.0
    if i + 1 >= len(cum):
        return cum[-1][1]
    (t0, c0), (t1, c1) = cum[i], cum[i + 1]
    return c0 + (c1 - c0) * (ts - t0) / (t1 - t0)


def counter_value(cum: Points, ts: float, counter: Counter) -> float:
    """The counter at ts: the integral since the later of the integral's start and the counter's last reset."""
    since = max(cum[0][0], counter.start_of_period(ts))
    return energy_at(cum, ts) - energy_at(cum, since)


# --- over-count -----------------------------------------------------------------------------------------------------


def device_sums(devices: dict, times: list, hierarchy: dict, stale_after: float) -> list:
    """For each time, the house total as the fixed aggregator would compute it from the device series."""
    index = {k: [t for t, _ in pts] for k, pts in devices.items()}
    sums = []
    for ts in times:
        current = {}
        for key, pts in devices.items():
            i = bisect.bisect_right(index[key], ts) - 1
            if i >= 0 and ts - pts[i][0] <= stale_after:
                current[key] = pts[i][1]
        excluded = set()
        for parent, children in hierarchy.items():
            if parent in current:
                excluded.update(children)
        sums.append(sum(v for k, v in current.items() if k not in excluded))
    return sums


def double_count_window(excess: Points, stale_w: float, last_old: int) -> tuple | None:
    """(first, last) aggregate cycle that counted the old name's stale reading on top of the new name.

    excess is aggregate.power_watts minus the device sum per cycle. None when the stale reading was 0 W.
    """
    if stale_w == 0:
        return None

    def counted(e: float) -> bool:
        return abs(e - stale_w) < abs(e)

    starts = [t for t, e in excess if last_old < t <= last_old + START_SLACK and counted(e)]
    ends = [t for t, e in excess if last_old < t <= last_old + RENDER_WINDOW + END_SLACK and counted(e)]
    if not starts or not ends:
        raise Refused(
            f'no cycle counting a stale {stale_w} W after {fmt(last_old)}: the over-count plateau is absent. If the '
            'repair was already applied, do not rerun plan/series; use verify, redo or undo with the series log.'
        )
    return starts[0], ends[-1]


@dataclass(frozen=True)
class CycleExcess:
    ts: int
    over_w: float
    kwh: float
    dt_counter: float | None
    dt_stamps: int


def cycle_excess(power: Points, counter: Points, over_by_ts: dict) -> list:
    """Energy each over-counting cycle added: over_w * dt, dt from the counter increment over the cycle's power."""
    p = dict(power)
    c = dict(counter)
    times = sorted(set(p) & set(c))
    rows = []
    for prev, ts in zip(times, times[1:]):
        if ts not in over_by_ts:
            continue
        stamps = ts - prev
        inc = c[ts] - c[prev]
        dt = inc * KWH / p[ts] if p[ts] > 0 and inc >= 0 else None
        rows.append(
            CycleExcess(ts, over_by_ts[ts], over_by_ts[ts] * (dt if dt is not None else stamps) / KWH, dt, stamps)
        )
    return rows


def running_total(rows: list) -> Points:
    out, total = [], 0.0
    for r in rows:
        total += r.kwh
        out.append((r.ts, total))
    return out


# --- corrections ----------------------------------------------------------------------------------------------------


def correct_counter(metric: str, points: Points, excess: Points, counter: Counter) -> list:
    """Subtract the excess accumulated by each point's time, from the first excess cycle until the counter resets."""
    if not excess:
        return []
    first = excess[0][0]
    period = counter.start_of_period(first)
    changes = []
    for ts, value in points:
        if ts < first or counter.start_of_period(ts) != period:
            continue
        removed = energy_step(excess, ts)
        changes.append(Change(metric, ts, value, value - removed))
    return changes


def energy_step(running: Points, ts: float) -> float:
    """A running total read as a step function: the last value at or before ts."""
    times = [t for t, _ in running]
    i = bisect.bisect_right(times, ts) - 1
    return running[i][1] if i >= 0 else 0.0


def correct_power(metric: str, points: Points, over_by_ts: dict) -> list:
    return [Change(metric, ts, v, v - over_by_ts[ts]) for ts, v in points if ts in over_by_ts]


def rebuild_counter(metric: str, existing: Points, cum: Points, counter: Counter, cutover: int, until: int) -> list:
    return [Change(metric, ts, v, counter_value(cum, ts, counter)) for ts, v in existing if cutover <= ts <= until]


def correct_state(state: dict, excess_kwh: float, excess_start: float, rebuilt: dict, drop) -> tuple:
    """Corrected copy of an energy_state.json dict, and notes on what changed."""
    new = json.loads(json.dumps(state))
    notes = []
    for c in COUNTERS:
        reset = new.get(c.reset_attr)
        if reset is not None and reset > excess_start:
            notes.append(f'{c.attr}: reset at {fmt(reset)} after the over-count began; left at {new[c.attr]:.6f}')
            continue
        notes.append(f'{c.attr}: {new[c.attr]:.6f} - {excess_kwh:.6f} = {new[c.attr] - excess_kwh:.6f}')
        new[c.attr] -= excess_kwh
    devices = new.setdefault('devices', {})
    for key, values in rebuilt.items():
        if key not in devices:
            raise Refused(f'{key} is not in the state file; refusing to invent an entry')
        for attr, value in values.items():
            notes.append(f'{key}.{attr}: {devices[key][attr]:.6f} -> {value:.6f}')
            devices[key][attr] = value
    for key in drop:
        if devices.pop(key, None) is not None:
            notes.append(f'{key}: removed')
    return new, notes


# --- safety, carbon and logs ------------------------------------------------------------------------------------------


def check_fine_archive(earliest: int, now: float) -> None:
    if now - earliest > FINE_RETENTION - FINE_MARGIN:
        raise Refused(
            f'{fmt(earliest)} is within {FINE_MARGIN} s of leaving the 5 s archive (24 h); a carbon write would replace '
            'whole 60 s buckets. Nothing sent.'
        )


def check_output_path(source: Path, output: Path) -> None:
    if output.resolve() == source.resolve():
        raise Refused('--output must differ from the input copy')
    if output.exists():
        raise Refused(f'{output} exists; refusing to overwrite')


def carbon_lines(changes: list) -> list:
    return [f'{c.metric} {c.new!r} {c.ts}\n' for c in changes]


def carbon_send(lines: list, host: str = CARBON_HOST, port: int = CARBON_PORT, chunk: int = 2000) -> None:
    for i in range(0, len(lines), chunk):
        with socket.create_connection((host, port), timeout=30) as conn:
            conn.sendall(''.join(lines[i : i + chunk]).encode())
        time.sleep(0.5)


def write_log(directory: Path, kind: str, changes: list, meta: dict) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H-%M-%SZ')
    path = directory / f'{kind}_{stamp}.json'
    if path.exists():
        raise Refused(f'{path} exists')
    body = {'meta': meta, 'changes': [[c.metric, c.ts, c.old, c.new] for c in changes]}
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(body, indent=0))
    tmp.replace(path)
    return path


def read_log(path: Path) -> list:
    return [Change(m, int(ts), float(old), float(new)) for m, ts, old, new in json.loads(path.read_text())['changes']]


def undo_changes(changes: list) -> list:
    return [Change(c.metric, c.ts, c.new, c.old) for c in changes]


def minute_means(points: list) -> dict:
    buckets: dict = {}
    for ts, v in points:
        if v is not None:
            buckets.setdefault(ts - ts % 60, []).append(v)
    return {k: sum(v) / len(v) for k, v in buckets.items()}


def close(a: float | None, b: float | None) -> bool:
    return a is not None and b is not None and abs(a - b) <= 1e-9 * max(1.0, abs(a), abs(b))


# --- the whole repair -----------------------------------------------------------------------------------------------


@dataclass
class Episode:
    rename: Rename
    stale_w: float
    start: int | None = None
    end: int | None = None
    inside_median: float | None = None
    cycles: int = 0


@dataclass
class Repair:
    until: int
    episodes: list
    rows: list  # CycleExcess
    excess_kwh: float
    excess_kwh_by_stamps: float
    device_kwh: dict  # new name -> integral since its cutover at until
    changes: list
    outside_median_abs: float | None = None
    notes: list = field(default_factory=list)


def build_repair(series: dict, renames, until: int, hierarchy: dict, stale_after: float) -> Repair:
    """Everything to rewrite, from fetched series {metric: [(ts, value)]} (no None) up to until."""
    series = {m: [(t, v) for t, v in pts if t <= until] for m, pts in series.items()}
    agg_power = series[f'{AGGREGATE}.power_watts']
    devices = {}
    for metric, pts in series.items():
        parts = metric.split('.')
        if (
            len(parts) == 5
            and metric.startswith(PREFIX + '.')
            and parts[2] != 'aggregate'
            and parts[4] == 'power_watts'
        ):
            devices[f'{parts[2]}.{parts[3]}'] = pts
    times = [t for t, _ in agg_power]
    sums = device_sums(devices, times, hierarchy, stale_after)
    excess = [(t, p - s) for (t, p), s in zip(agg_power, sums)]

    over: dict = {}
    episodes = []
    for r in renames:
        new_power = dict(devices.get(r.new, []))
        if r.last_old_point not in new_power:
            raise Refused(f"{r.new}.power_watts has no point at {fmt(r.last_old_point)} (the old name's last point)")
        ep = Episode(r, new_power[r.last_old_point])
        window = double_count_window(excess, ep.stale_w, r.last_old_point)
        if window:
            ep.start, ep.end = window
            inside = [e for t, e in excess if ep.start <= t <= ep.end]
            ep.cycles = len(inside)
            ep.inside_median = statistics.median(inside)
            for t in times:
                if ep.start <= t <= ep.end:
                    over[t] = over.get(t, 0.0) + ep.stale_w
        episodes.append(ep)

    daily = series[f'{AGGREGATE}.energy_kwh_daily']
    rows = cycle_excess(agg_power, daily, over)
    running = running_total(rows)
    changes = correct_power(f'{AGGREGATE}.power_watts', agg_power, over)
    for c in COUNTERS:
        changes += correct_counter(f'{AGGREGATE}.{c.metric}', series[f'{AGGREGATE}.{c.metric}'], running, c)

    device_kwh = {}
    for r in renames:
        cum = cumulative_energy(devices.get(r.new, []), r.cutover, stale_after)
        device_kwh[r.new] = energy_at(cum, until)
        for c in COUNTERS:
            metric = f'{PREFIX}.{r.new}.{c.metric}'
            changes += rebuild_counter(metric, series.get(metric, []), cum, c, r.cutover, until)

    windows = [(e.start, e.end) for e in episodes if e.start is not None]
    lo = min((r.cutover for r in renames), default=until)
    outside = [abs(e) for t, e in excess if t >= lo and not any(a <= t <= b for a, b in windows)]
    return Repair(
        until=until,
        episodes=episodes,
        rows=rows,
        excess_kwh=sum(r.kwh for r in rows),
        excess_kwh_by_stamps=sum(r.over_w * r.dt_stamps / KWH for r in rows),
        device_kwh=device_kwh,
        changes=changes,
        outside_median_abs=statistics.median(outside) if outside else None,
    )


def rebuilt_state_values(repair: Repair, renames, state_ts: float, series: dict, stale_after: float) -> dict:
    """The new names' four counters at the state's last_ts."""
    out = {}
    for r in renames:
        cum = cumulative_energy(series.get(f'{PREFIX}.{r.new}.power_watts', []), r.cutover, stale_after)
        out[r.new] = {c.attr: counter_value(cum, state_ts, c) for c in COUNTERS}
    return out


# --- Graphite reads ---------------------------------------------------------------------------------------------------


def render(targets: list, start: int, end: int, url: str = config.GRAPHITE_RENDER_URL) -> dict:
    query = [('target', t) for t in targets] + [
        ('from', str(start)),
        ('until', str(end)),
        ('format', 'json'),
        ('noCache', '1'),
    ]
    with urllib.request.urlopen(f'{url}?{urllib.parse.urlencode(query)}', timeout=120) as response:  # noqa: S310
        payload = json.load(response)
    return {s['target']: [(int(t), v) for v, t in s['datapoints']] for s in payload}


def non_null(series: dict) -> dict:
    return {m: [(t, v) for t, v in pts if v is not None] for m, pts in series.items()}


def fetch_inputs(renames, until: int, url: str) -> dict:
    start = min(r.cutover for r in renames) - RENDER_WINDOW
    targets = [f'{AGGREGATE}.*', f'{PREFIX}.*.*.power_watts'] + [f'{PREFIX}.{r.new}.energy_kwh_*' for r in renames]
    return non_null(render(targets, start, until, url))


# --- commands ---------------------------------------------------------------------------------------------------------


def load_state(path: Path) -> dict:
    return json.loads(path.read_text())


def resolve_until(args) -> int:
    if args.until is not None:
        return args.until
    if getattr(args, 'state', None):
        return int(load_state(args.state)['last_ts'])
    return int(time.time()) - 15


def prepare(args) -> tuple:
    until = resolve_until(args)
    series = fetch_inputs(RENAMES, until, args.render_url)
    repair = build_repair(series, RENAMES, until, load_hierarchy(HIERARCHY_FILE), config.GRAPHITE_STALE_AFTER_SECONDS)
    return series, repair


def agreement(series: dict, renames, stale_after: float) -> list:
    """Integral rule vs the aggregator's live-written daily counter over the stretch it wrote itself."""
    lines = []
    for r in renames:
        daily = series.get(f'{PREFIX}.{r.new}.energy_kwh_daily', [])
        live = [(t, v) for t, v in daily if t > r.last_old_point]
        # the aggregator's own writes start afresh, so the first drop after the old name's last point ends the stand-in
        if len(live) < 2:
            continue
        own = next((i for i in range(1, len(live)) if live[i][1] < live[i - 1][1]), 0)
        live = live[own:]
        first, last = live[0], live[-1]
        cum = cumulative_energy(series.get(f'{PREFIX}.{r.new}.power_watts', []), r.cutover, stale_after)
        integral = energy_at(cum, last[0]) - energy_at(cum, first[0])
        counted = last[1] - first[1]
        lines.append(
            f'  {r.new}: {fmt(first[0])} to {fmt(last[0])}: integral {integral:.4f} kWh, live counter {counted:.4f} kWh, '
            f'difference {counted - integral:+.4f} kWh ({(counted - integral) / counted * 100 if counted else 0:+.1f}%)'
        )
    return lines


def print_plan(series: dict, repair: Repair, state: dict | None) -> None:
    print(f'until {fmt(repair.until)} UTC; stale limit {config.GRAPHITE_STALE_AFTER_SECONDS} s')
    for ep in repair.episodes:
        r = ep.rename
        print(
            f'{r.old} -> {r.new}: cutover {fmt(r.cutover)}, old name last point {fmt(r.last_old_point)}, stale {ep.stale_w} W'
        )
        if ep.start is None:
            print('  stale reading 0 W: nothing over-counted')
            continue
        print(
            f'  counted twice in {ep.cycles} cycles {fmt(ep.start)} to {fmt(ep.end)}; median (aggregate - device sum) '
            f'inside {ep.inside_median:.1f} W'
        )
        edge = [(t, p) for t, p in series[f'{AGGREGATE}.power_watts'] if ep.start - 40 <= t <= ep.start + 20]
        edge += [(t, p) for t, p in series[f'{AGGREGATE}.power_watts'] if ep.end - 20 <= t <= ep.end + 40]
        print('  aggregate.power_watts at the edges: ' + ', '.join(f'{fmt(t)[11:]} {p:.1f}' for t, p in edge))
    print(
        f'median |aggregate - device sum| outside the windows since the first cutover: {repair.outside_median_abs:.2f} W'
    )
    with_dt = [r for r in repair.rows if r.dt_counter is not None]
    print(
        f'excess energy {repair.excess_kwh:.6f} kWh over {len(repair.rows)} cycles '
        f'({len(with_dt)} timed by the counter); by bucket timestamps {repair.excess_kwh_by_stamps:.6f} kWh'
    )
    print("integration rule agreement with the aggregator's own writes:")
    for line in agreement(series, RENAMES, config.GRAPHITE_STALE_AFTER_SECONDS):
        print(line)
    for name, value in repair.device_kwh.items():
        print(f'{name}: integral since cutover at until = {value:.6f} kWh')
    by_metric: dict = {}
    for c in repair.changes:
        by_metric.setdefault(c.metric, []).append(c)
    print('series points to rewrite:')
    for metric, changes in sorted(by_metric.items()):
        sample = [changes[0], changes[len(changes) // 2], changes[-1]]
        print(f'  {metric}: {len(changes)} points {fmt(changes[0].ts)} to {fmt(changes[-1].ts)}')
        for c in sample:
            print(f'      {fmt(c.ts)}  {c.old:.6f} -> {c.new:.6f}')
    earliest = min((c.ts for c in repair.changes), default=None)
    if earliest is not None:
        print(f'earliest point {fmt(earliest)}: leaves the 5 s archive at {fmt(earliest + FINE_RETENTION)} UTC')
    if state is not None:
        start = min((e.start for e in repair.episodes if e.start is not None), default=repair.until)
        rebuilt = rebuilt_state_values(repair, RENAMES, state['last_ts'], series, config.GRAPHITE_STALE_AFTER_SECONDS)
        _new, notes = correct_state(
            state, energy_step(running_total(repair.rows), state['last_ts']), start, rebuilt, DROP_STATE_KEYS
        )
        print(f'state copy (last_ts {fmt(state["last_ts"])}):')
        for n in notes:
            print(f'  {n}')


def cmd_plan(args) -> int:
    series, repair = prepare(args)
    print_plan(series, repair, load_state(args.state) if args.state else None)
    return 0


def cmd_state(args) -> int:
    check_output_path(args.state, args.output)
    state = load_state(args.state)
    args.until = int(state['last_ts'])
    series, repair = prepare(args)
    start = min((e.start for e in repair.episodes if e.start is not None), default=repair.until)
    rebuilt = rebuilt_state_values(repair, RENAMES, state['last_ts'], series, config.GRAPHITE_STALE_AFTER_SECONDS)
    new, notes = correct_state(
        state, energy_step(running_total(repair.rows), state['last_ts']), start, rebuilt, DROP_STATE_KEYS
    )
    tmp = args.output.with_suffix('.tmp')
    tmp.write_text(json.dumps(new, indent=2))
    tmp.replace(args.output)
    for n in notes:
        print(n)
    print(f'wrote {args.output} (input {args.state} untouched)')
    return 0


def readback(changes: list, url: str, wait: float) -> tuple:
    """(points not matching at 5 s, minutes whose 60 s value is not the mean of their 5 s points).

    Polls until both agree or wait runs out: carbon flushes to disk, and so rolls up into 60 s, a little later.
    """
    metrics = sorted({c.metric for c in changes})
    lo, hi = min(c.ts for c in changes), max(c.ts for c in changes)
    want = {(c.metric, c.ts): c.new for c in changes}
    minutes = {m: sorted({c.ts - c.ts % 60 for c in changes if c.metric == m}) for m in metrics}
    deadline = time.monotonic() + wait
    while True:
        fine = render(metrics, lo - 60, hi + 60, url)
        bad = [k for k, v in want.items() if not close(dict(fine.get(k[0], [])).get(k[1]), v)]
        coarse_from = int(time.time()) - FINE_RETENTION - 600  # older than the 5 s archive: Graphite reads the 60 s one
        coarse = render(metrics, coarse_from, hi + 60, url)
        bad_minutes = []
        for m in metrics:
            means = minute_means(fine.get(m, []))
            got = dict(coarse.get(m, []))
            for minute in minutes[m]:
                if minute in means and not close(got.get(minute), means[minute]):
                    bad_minutes.append((m, minute, got.get(minute), means[minute]))
        if (not bad and not bad_minutes) or time.monotonic() >= deadline:
            return bad, bad_minutes
        time.sleep(15)


def send_changes(changes: list, args, kind: str, meta: dict) -> int:
    if not changes:
        print('nothing to send')
        return 0
    check_fine_archive(min(c.ts for c in changes), time.time())
    log = write_log(args.log_dir, kind, changes, meta)
    print(f'log of {len(changes)} old/new values: {log}')
    carbon_send(carbon_lines(changes), args.carbon_host, args.carbon_port)
    print(f'sent {len(changes)} points to {args.carbon_host}:{args.carbon_port}')
    bad, bad_minutes = readback(changes, args.render_url, READBACK_WAIT)
    print(f'5 s read-back: {len(changes) - len(bad)} of {len(changes)} points match')
    for k in bad[:10]:
        print(f'  NOT MATCHING {k[0]} {fmt(k[1])}')
    print(f'60 s archive: {len(bad_minutes)} minutes differ from the mean of their 5 s points')
    for m, minute, got, mean in bad_minutes[:10]:
        print(f'  {m} {fmt(minute)}: 60 s {got} vs 5 s mean {mean}')
    return 1 if bad or bad_minutes else 0


def cmd_series(args) -> int:
    series, repair = prepare(args)
    changes = repair.changes
    print(f'{len(changes)} points in {len({c.metric for c in changes})} series up to {fmt(repair.until)}')
    print(f'excess {repair.excess_kwh:.6f} kWh; ' + ', '.join(f'{k} {v:.6f} kWh' for k, v in repair.device_kwh.items()))
    if not args.apply:
        check_fine_archive(min(c.ts for c in changes), time.time())
        print('dry run: nothing sent (add --apply)')
        return 0
    meta = {'until': repair.until, 'excess_kwh': repair.excess_kwh, 'device_kwh': repair.device_kwh}
    return send_changes(changes, args, 'series', meta)


def cmd_verify(args) -> int:
    changes = read_log(args.log)
    bad, bad_minutes = readback(changes, args.render_url, 0.0)
    print(f'5 s: {len(changes) - len(bad)} of {len(changes)} logged new values present')
    print(f'60 s: {len(bad_minutes)} minutes differ from the mean of their 5 s points')
    return 1 if bad or bad_minutes else 0


def redo_changes(changes: list) -> list:
    """The logged new values again, to finish or repeat an apply without recomputing (the plateau is gone by then)."""
    return list(changes)


def cmd_redo(args) -> int:
    changes = redo_changes(read_log(args.log))
    print(f'{len(changes)} points from {args.log} would get their logged new values (again)')
    if not args.apply:
        check_fine_archive(min(c.ts for c in changes), time.time())
        print('dry run: nothing sent (add --apply)')
        return 0
    return send_changes(changes, args, 'redo', {'redoes': str(args.log)})


def cmd_undo(args) -> int:
    changes = undo_changes(read_log(args.log))
    print(f'{len(changes)} points from {args.log} would get their old values back')
    if not args.apply:
        check_fine_archive(min(c.ts for c in changes), time.time())
        print('dry run: nothing sent (add --apply)')
        return 0
    return send_changes(changes, args, 'undo', {'undoes': str(args.log)})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--render-url', default=config.GRAPHITE_RENDER_URL)
    parser.add_argument('--carbon-host', default=CARBON_HOST)
    parser.add_argument('--carbon-port', type=int, default=CARBON_PORT)
    parser.add_argument('--log-dir', type=Path, default=LOG_DIR)
    sub = parser.add_subparsers(dest='command', required=True)
    plan = sub.add_parser('plan', help='read-only measurements and before/after numbers')
    plan.add_argument('--state', type=Path, help='a COPY of energy_state.json; until defaults to its last_ts')
    plan.add_argument('--until', type=parse_time)
    state = sub.add_parser('state', help='write a corrected copy of a state file copy')
    state.add_argument('--state', type=Path, required=True)
    state.add_argument('--output', type=Path, required=True)
    state.add_argument('--until', type=parse_time, help=argparse.SUPPRESS)
    series = sub.add_parser('series', help='rewrite the Graphite points (dry run unless --apply)')
    series.add_argument('--state', type=Path, help='the state copy taken after stopping the aggregator (sets until)')
    series.add_argument('--until', type=parse_time)
    series.add_argument('--apply', action='store_true')
    verify = sub.add_parser('verify', help='read-only check of a series log against Graphite')
    verify.add_argument('--log', type=Path, required=True)
    redo = sub.add_parser('redo', help="send a series log's new values again (dry run unless --apply)")
    redo.add_argument('--log', type=Path, required=True)
    redo.add_argument('--apply', action='store_true')
    undo = sub.add_parser('undo', help="send a series log's old values back (dry run unless --apply)")
    undo.add_argument('--log', type=Path, required=True)
    undo.add_argument('--apply', action='store_true')
    return parser


def main(argv: list | None = None) -> int:
    args = build_parser().parse_args(argv)
    handler = {
        'plan': cmd_plan,
        'state': cmd_state,
        'series': cmd_series,
        'verify': cmd_verify,
        'redo': cmd_redo,
        'undo': cmd_undo,
    }
    try:
        return handler[args.command](args)
    except Refused as e:
        print(f'REFUSED: {e}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
