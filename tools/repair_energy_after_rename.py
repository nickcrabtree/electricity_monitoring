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

Sources (the repair runs at 60 s since 2026-11-09; the 5 s archive keeps 24 h, so the 2026-11-06 5 s points aged out
on 2026-11-07):
  snapshot     the 5 s render JSON saved 2026-11-06 20:42-20:44 GMT (SNAPSHOT_DIR, sha256 checked; README beside it):
               window edges, per-cycle excess and every correction before 20:44:00 are computed from it at 5 s, then
               averaged per minute as whisper does (a minute partly inside the window is the mean of old and corrected
               points). Before anything is computed, its minute means must equal the live 60 s archive (else refuse;
               if the live minutes hold the corrected means instead: "already applied").
  60 s         the live 60 s archive from 20:44:00 on 2026-11-06 to about 23 h 55 min ago: aggregate counters lose the
               whole excess (exact: a constant per point); the new names' power enters the integral as minute means at
               the minute's middle (measured error -0.49% to +0.04% of the energy over a stretch: a 60 s value is the
               mean of the readings, not of time) and each minute's counter is the mean over its twelve 5 s slots.
  5 s          the live 5 s archive for the last 23 h 55 min, point by point, exactly as before.

What is corrected (render API reads with noCache=1; blackpi2 is not contacted):
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
               limit adds nothing; the integral at a point's time is interpolated inside its segment. A counter period
               that began after T (day from 2026-11-07, week from 2026-11-09 01:00) holds only what the aggregator
               integrated itself and is left alone, in Graphite and in the state.
  state        the top-level counters minus the excess (skipped for a counter reset since the window), the new names'
               counters whose period began before T set to the integral at the state's last_ts, the dead entries
               removed.

Subcommands:
  plan    read-only: measurements, cross-checks, before/after numbers for state (with --state COPY), sample changes.
  state   --state COPY --output NEW: write a corrected copy. Never touches its input; refuses an existing output.
  series  values to rewrite (dry run by default). With --apply: writes a JSON log of every old and new value (5 s points
          and minute values) to --log-dir first, then sends through carbon's plaintext port.
  verify  --log FILE: read-only check that Graphite holds the logged new values, and that the 900 s and 3600 s buckets
          over them are the means of their finer points.
  redo    --log FILE [--apply]: send the logged new values again (to finish a partial send).
  undo    --log FILE [--apply]: send the logged old values back.
  probe   [--name N] [--apply]: write a throwaway series and read back how carbon files old and recent points.

Sending (the refusal rule): each minute is sent in exactly one form, chosen by its age when sent. A minute at least
GUARD (300 s) inside the 5 s archive gets its 5 s points, and whisper re-averages its 60 s value and the coarser ones.
A minute older than the 5 s archive gets its 60 s value at the minute's timestamp, which whisper writes to the 60 s
archive and propagates (probe of 2026-11-09: test.electricity_monitoring.energy_repair_probe.2026_11_09_2215). A minute
in between is sent as its 60 s value once it has left the 5 s archive (series waits up to about 6 min): a 5 s write
there could re-average the minute over 5 s slots already reused, and a 60 s-aligned write would land as one 5 s point.
`series` refuses when the plan is more than GUARD/2 old at sending, when `until` (the stopped state's last_ts) is not
in the 5 s archive, when the snapshot is changed or differs from Graphite, and when the repair is already applied. The
log holds the 60 s value of every touched minute, so verify, redo and undo work at any later age. After an apply, plan
and series refuse; work from the series log with verify, redo and undo.

Run from the repository root on quartz in conda env `electricity`; the sandbox must allow ubuntu64.local:81 (reads)
and 192.168.86.123:2003 (carbon, --apply only). Times: epoch seconds or ISO 8601 with a zone.
"""

from __future__ import annotations

import argparse
import bisect
import hashlib
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
LOG_DIR = Path('/ncloud/Nick/electricity_monitoring/backups/2026-11-09/energy_repair')
SNAPSHOT_DIR = Path('/ncloud/Nick/electricity_monitoring/backups/2026-11-06/energy_repair/snapshot_5s_2026-11-06_2044')
SNAPSHOT_FILES = {  # render API JSON read 2026-11-06 20:42-20:44 GMT, 5 s, 10:20:05-20:44:35 (README.md beside them)
    'w_home_electricity_aggregate__.json': '2c109db541e8624b73fa18d7b89cc78eda7959ca389ab1d90739ef77b2513801',
    'w_home_electricity_____power_watts.json': 'f8382054ed301cf02bafc103d599b8ac303795b025b60725962135765489992a',
    'w_home_electricity_tuya__dishwasher_tumble_dryer_boot_room_freezer_garage_power_a___.json': (
        'ec83bdcb665ae6f8f971e7b52978e8426968f1d5223f7d1e876f9a28b6b687ea'
    ),
}
LOCAL_TZ = ZoneInfo('Europe/London')  # blackpi2's zone, which apply_resets uses
RENDER_WINDOW = 3600  # the aggregator's GRAPHITE_RENDER_WINDOW ('-1h') when the over-count happened
START_SLACK = 180  # the first cycle that saw the new name came this soon after the old name's last point
END_SLACK = 90  # Graphite's 60 s render cache plus one cycle
FINE_RETENTION = 86400  # 5 s archive
GUARD = 300  # a minute this close to leaving the 5 s archive is written as its 60 s value once it has left
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
    step: int = 5  # 5: one 5 s point; 60: a minute's value (the mean of its 5 s points), written 60 s aligned


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


_TIMES: dict = {}


def cum_times(cum: Points) -> list:
    """The timestamps of an integral, cached: the repair evaluates long integrals hundreds of thousands of times."""
    key = id(cum)
    hit = _TIMES.get(key)
    if hit is None or hit[0] is not cum or len(hit[1]) != len(cum):
        hit = _TIMES[key] = (cum, [t for t, _ in cum])
    return hit[1]


def energy_at(cum: Points, ts: float) -> float:
    """Integral up to ts: 0 before the first point, interpolated inside a segment, held after the last."""
    times = cum_times(cum)
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
    """The new name's counter as the integral since the cutover; a period that began after the cutover is left alone."""
    return [
        Change(metric, ts, v, counter_value(cum, ts, counter))
        for ts, v in existing
        if cutover <= ts <= until and counter.start_of_period(ts) <= cutover
    ]


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
    body = {'meta': meta, 'changes': [[c.metric, c.ts, c.old, c.new, c.step] for c in changes]}
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(body, indent=0))
    tmp.replace(path)
    return path


def read_log(path: Path) -> list:
    """Changes from a log; entries without a step (the 2026-11-06 format) are 5 s points."""
    out = []
    for entry in json.loads(path.read_text())['changes']:
        metric, ts, old, new = entry[:4]
        out.append(Change(metric, int(ts), float(old), float(new), int(entry[4]) if len(entry) > 4 else 5))
    return out


def undo_changes(changes: list) -> list:
    return [Change(c.metric, c.ts, c.new, c.old, c.step) for c in changes]


def minute_means(points: list) -> dict:
    buckets: dict = {}
    for ts, v in points:
        if v is not None:
            buckets.setdefault(ts - ts % 60, []).append(v)
    return {k: sum(v) / len(v) for k, v in buckets.items()}


def close(a: float | None, b: float | None) -> bool:
    return a is not None and b is not None and abs(a - b) <= 1e-9 * max(1.0, abs(a), abs(b))


# --- the repair at 60 s: snapshot, archives and sending -------------------------------------------------------------


def minute_of(ts: float) -> int:
    return int(ts - ts % 60)


def mean(values: list) -> float:
    return sum(values) / len(values)  # whisper's average: sum of the known points over their count


def rollup(metric: str, points: Points, changes: list) -> list:
    """Each minute holding a 5 s change, as whisper averages it: (mean of old points, mean with the changes applied).

    Points of the minute that are not changed keep their old value, so a minute partly inside a window is the mean of
    old and corrected values, as the 60 s archive would hold had the 5 s points been rewritten in time.
    """
    new_by_ts = {c.ts: c.new for c in changes if c.metric == metric and c.step == 5}
    touched = {minute_of(t) for t in new_by_ts}
    buckets: dict = {}
    for ts, v in points:
        if minute_of(ts) in touched:
            buckets.setdefault(minute_of(ts), []).append((v, new_by_ts.get(ts, v)))
    return [
        Change(metric, m, mean([o for o, _ in vals]), mean([n for _, n in vals]), 60)
        for m, vals in sorted(buckets.items())
    ]


def gate(rollups: list, live60: dict) -> None:
    """Refuse unless the live 60 s archive holds the snapshot's minute means (the repair not yet applied).

    live60: {metric: {minute: value}}.
    """
    old_ok, new_ok, bad = 0, 0, []
    for c in rollups:
        if close(c.old, c.new):
            continue
        got = live60.get(c.metric, {}).get(c.ts)
        if close(got, c.old):
            old_ok += 1
        elif close(got, c.new):
            new_ok += 1
        else:
            bad.append(f'{c.metric} {fmt(c.ts)}: live {got}, snapshot {c.old}, corrected {c.new}')
    if bad:
        raise Refused(
            f'{len(bad)} minutes of the live 60 s archive differ from the snapshot, e.g. ' + '; '.join(bad[:3])
        )
    if new_ok and not old_ok:
        raise Refused(
            f'already applied: all {new_ok} snapshot minutes hold the corrected values. Do not rerun plan/series; '
            'use verify, redo or undo with the series log.'
        )
    if new_ok:
        raise Refused(
            f'partly applied ({new_ok} of {new_ok + old_ok} snapshot minutes corrected): use verify and redo or undo '
            'with the series log.'
        )


def check_inputs(snapshot: dict, live60: dict, metrics, split: int) -> None:
    """Refuse when a snapshot input's complete minutes before split differ from the live 60 s archive."""
    for metric in metrics:
        live = dict(live60.get(metric, []))
        for m, value in minute_means([(t, v) for t, v in snapshot.get(metric, []) if t < split]).items():
            if m + 60 <= split and not close(live.get(m), value):
                raise Refused(f'{metric} {fmt(m)}: live 60 s {live.get(m)} is not the snapshot minute mean {value}')


def compose_power(snap: Points, coarse: Points, live: Points, split: int, fine_from: int) -> Points:
    """One power series: snapshot 5 s points before split, 60 s means at minute midpoints, live 5 s from fine_from.

    A 60 s value is the mean of the minute's 5 s readings, so it stands for the minute's average power at its middle.
    """
    return (
        [(t, v) for t, v in snap if t < split]
        + [(m + 30, v) for m, v in coarse if split <= m < fine_from]
        + [(t, v) for t, v in live if t >= fine_from]
    )


COUNTER_BY_METRIC = {c.metric: c for c in COUNTERS}


@dataclass
class Corrector:
    """New values after the double-count window: aggregate counters lose the whole excess until they reset; the new
    names' counters are the integral since the cutover unless their period began after it. None: leave the point."""

    excess_kwh: float
    excess_start: int
    cums: dict
    renames: tuple
    until: int

    def value(self, metric: str, ts: int, old: float, span: int = 0) -> float | None:
        if ts > self.until:
            return None
        group, _, name = metric.rpartition('.')
        counter = COUNTER_BY_METRIC.get(name)
        if counter is None:
            return None
        if group == AGGREGATE:
            if ts < self.excess_start or counter.start_of_period(ts) != counter.start_of_period(self.excess_start):
                return None
            return old - self.excess_kwh
        for r in self.renames:
            if group == f'{PREFIX}.{r.new}':
                if ts < r.cutover or counter.start_of_period(ts) > r.cutover:
                    return None
                times = [ts + k for k in range(0, span, 5)] if span else [ts]
                return mean([counter_value(self.cums[r.new], t, counter) for t in times])
        return None


def split_for_send(changes: list, now: float, guard: int = GUARD) -> tuple:
    """(send now, send once aged): exactly one representation per minute.

    A minute at least guard s inside the 5 s archive gets its 5 s points (whisper re-averages the minute). One older than
    the 5 s archive gets its 60 s value at the minute's timestamp, which whisper writes to the 60 s archive. A minute in
    between gets its 60 s value once it has left the 5 s archive: a 5 s write there could re-average the minute over
    5 s slots already reused, and a 60 s-aligned write would land as one 5 s point.
    """
    groups: dict = {}
    for c in changes:
        groups.setdefault((c.metric, minute_of(c.ts)), []).append(c)
    edge = now - FINE_RETENTION
    send, later = [], []
    for (metric, m), cs in sorted(groups.items()):
        fives = [c for c in cs if c.step == 5]
        sixty = [c for c in cs if c.step == 60]
        if fives and m >= edge + guard:
            send += fives
        elif sixty and m < edge:
            send += sixty
        elif sixty:
            later += sixty
        else:
            raise Refused(
                f'{metric} {fmt(m)}: only 5 s values logged and the minute is leaving the 5 s archive; no 60 s value'
            )
    return send, later


def send_deferred(later: list, send: Callable, now: Callable = time.time, sleep: Callable = time.sleep) -> None:
    """Wait until every minute in later has left the 5 s archive, then send their 60 s values."""
    if not later:
        return
    ready = max(c.ts for c in later) + FINE_RETENTION + 30
    while now() < ready:
        print(f'waiting {ready - now():.0f} s for {len(later)} minutes to leave the 5 s archive')
        sleep(ready - now())
    send(carbon_lines(later))


def coarser_mismatches(finer: Points, coarser: Points, step: int) -> list:
    """[(bucket, coarse value, mean of its finer points)] where they differ (average, xFilesFactor 0)."""
    buckets: dict = {}
    for t, v in finer:
        if v is not None:
            buckets.setdefault(t - t % step, []).append(v)
    return [
        (t, v, mean(buckets[t]))
        for t, v in coarser
        if v is not None and t in buckets and not close(v, mean(buckets[t]))
    ]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_snapshot(directory: Path, expected: dict) -> None:
    for name, digest in expected.items():
        path = directory / name
        if not path.is_file():
            raise Refused(f'snapshot file {path} missing')
        if sha256(path) != digest:
            raise Refused(f'snapshot file {name}: sha256 differs from the one recorded in this tool')


def load_render_files(directory: Path, names) -> dict:
    out = {}
    for name in names:
        for s in json.loads((directory / name).read_text()):
            out[s['target']] = [(int(t), v) for v, t in s['datapoints'] if v is not None]
    return out


@dataclass
class Plan:
    repair: Repair  # measured on the snapshot (5 s); its 5 s changes are not sent (those points left Graphite)
    split: int  # minutes before: from the snapshot; from here: the live 60 s archive
    fine_from: int  # minutes from here: the live 5 s archive
    changes: list  # 60 s values for every touched minute, plus 5 s points for minutes from fine_from
    cums: dict
    corrector: Corrector


REWRITTEN_SUFFIXES = tuple(c.metric for c in COUNTERS)


def rewritten_metrics(renames) -> list:
    out = [f'{AGGREGATE}.power_watts'] + [f'{AGGREGATE}.{s}' for s in REWRITTEN_SUFFIXES]
    return out + [f'{PREFIX}.{r.new}.{s}' for r in renames for s in REWRITTEN_SUFFIXES]


def build_plan(
    snapshot: dict, live60: dict, fine: dict, renames, until: int, now: float, hierarchy: dict, stale_after: float
) -> Plan:
    """Every change, from the 5 s snapshot, the live 60 s archive ({metric: [(minute, value)]}) and live 5 s points."""
    fine_from = minute_of(now - FINE_RETENTION + GUARD) + 60
    if until < fine_from:
        raise Refused(
            f'until {fmt(until)} has left (or is within {GUARD} s of leaving) the 5 s archive: the minute holding it '
            'mixes points to correct with later ones. Stop the aggregator, take a fresh state copy and run again.'
        )
    split = minute_of(max(t for t, _ in snapshot[f'{AGGREGATE}.power_watts']))
    power = {r.new: f'{PREFIX}.{r.new}.power_watts' for r in renames}
    check_inputs(snapshot, live60, list(power.values()), split)
    cums = {
        r.new: cumulative_energy(
            [
                (t, v)
                for t, v in compose_power(
                    snapshot.get(power[r.new], []),
                    live60.get(power[r.new], []),
                    fine.get(power[r.new], []),
                    split,
                    fine_from,
                )
                if t <= until
            ],
            r.cutover,
            stale_after,
        )
        for r in renames
    }
    before = {m: [(t, v) for t, v in pts if t < split] for m, pts in snapshot.items()}
    repair = build_repair(before, renames, split - 1, hierarchy, stale_after, cums)
    if any(e.end is not None and e.end >= split for e in repair.episodes):
        raise Refused('the double-count window runs past the end of the snapshot')
    starts = [e.start for e in repair.episodes if e.start is not None]
    corrector = Corrector(repair.excess_kwh, min(starts) if starts else until + 1, cums, tuple(renames), until)

    by_metric: dict = {}
    for c in repair.changes:
        by_metric.setdefault(c.metric, []).append(c)
    snap_minutes = [r for m, cs in sorted(by_metric.items()) for r in rollup(m, before[m], cs)]
    gate(snap_minutes, {m: dict(pts) for m, pts in live60.items()})

    changes = list(snap_minutes)
    for metric in rewritten_metrics(renames):
        for m, v in live60.get(metric, []):
            if split <= m < fine_from:
                new = corrector.value(metric, m, v, 60)
                if new is not None:
                    changes.append(Change(metric, m, v, new, 60))
        pts = [(t, v) for t, v in fine.get(metric, []) if t >= fine_from]
        fives = []
        for t, v in pts:
            new = corrector.value(metric, t, v)
            if new is not None:
                fives.append(Change(metric, t, v, new))
        changes += fives + rollup(metric, pts, fives)
    return Plan(repair, split, fine_from, changes, cums, corrector)


@dataclass
class Probe:
    phases: list  # [[carbon line, ...], ...] sent in order, read back between
    expected: dict  # (metric, archive step, timestamp) -> value


def probe_plan(name: str, now: float) -> Probe:
    """Carbon writes to a throwaway series (default schema 5s:24h,60s:60d,15m:5y,1h:20y, average, xFilesFactor 0).

    aligned: 60 minute values 0..59 in an hour two days old (60 s archive, then 900 s and 3600 s means).
    unaligned: one point 17 s into a minute (whisper aligns it). overwrite: minutes 0 and 1 set to 1 and 3, then
    minute 0 rewritten to 5 (900 s becomes 4). fine: a minute 23 h old as twelve 5 s points 1..12, then 13..24
    (60 s mean 18.5).
    """
    hour = int(now - 2 * FINE_RETENTION) // 3600 * 3600
    minute = minute_of(now - 23 * 3600)
    first = [f'{name}.aligned {float(i)} {hour + 60 * i}\n' for i in range(60)]
    first += [
        f'{name}.unaligned 5.0 {hour + 17}\n',
        f'{name}.overwrite 1.0 {hour}\n',
        f'{name}.overwrite 3.0 {hour + 60}\n',
    ]
    first += [f'{name}.fine {float(i + 1)} {minute + 5 * i}\n' for i in range(12)]
    second = [f'{name}.overwrite 5.0 {hour}\n'] + [f'{name}.fine {float(i + 13)} {minute + 5 * i}\n' for i in range(12)]
    expected = {(f'{name}.aligned', 60, hour + 60 * i): float(i) for i in range(60)}
    expected |= {
        (f'{name}.aligned', 900, hour + 900 * q): mean([float(i) for i in range(15 * q, 15 * q + 15)]) for q in range(4)
    }
    expected[(f'{name}.aligned', 3600, hour)] = 29.5
    expected[(f'{name}.unaligned', 60, hour)] = 5.0
    expected |= {(f'{name}.overwrite', 60, hour): 5.0, (f'{name}.overwrite', 900, hour): 4.0}
    expected[(f'{name}.fine', 60, minute)] = 18.5
    expected |= {(f'{name}.fine', 5, minute + 5 * i): float(i + 13) for i in range(12)}
    return Probe([first, second], expected)


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


def build_repair(
    series: dict, renames, until: int, hierarchy: dict, stale_after: float, cums: dict | None = None
) -> Repair:
    """Everything to rewrite, from 5 s series {metric: [(ts, value)]} (no None) up to until.

    cums: the new names' integrals to use instead of integrating their power in series (build_plan passes integrals
    that continue past until).
    """
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
        changes += correct_counter(f'{AGGREGATE}.{c.metric}', series.get(f'{AGGREGATE}.{c.metric}', []), running, c)

    device_kwh = {}
    for r in renames:
        cum = cums[r.new] if cums else cumulative_energy(devices.get(r.new, []), r.cutover, stale_after)
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


def rebuilt_state_values(cums: dict, renames, state_ts: float) -> tuple:
    """(new values, comparison values) for the new names' counters at the state's last_ts.

    A counter whose period began before the cutover becomes the integral since the cutover. One that reset after it
    holds only what the aggregator integrated itself and is left; the integral since its reset is returned for
    comparison.
    """
    rebuilt: dict = {}
    compare: dict = {}
    for r in renames:
        for c in COUNTERS:
            target = rebuilt if c.start_of_period(state_ts) <= r.cutover else compare
            target.setdefault(r.new, {})[c.attr] = counter_value(cums[r.new], state_ts, c)
    return rebuilt, compare


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


def fine_start(now: float) -> int:
    """First minute rewritten as 5 s points: whole and at least GUARD s inside the 5 s archive."""
    return minute_of(now - FINE_RETENTION + GUARD) + 60


def fetch_inputs(renames, now: float, url: str) -> tuple:
    """(60 s archive from an hour before the first cutover, 5 s archive from fine_start), nulls dropped."""
    start = min(r.cutover for r in renames) - RENDER_WINDOW
    targets = [f'{AGGREGATE}.*']
    targets += [f'{PREFIX}.{r.new}.{m}' for r in renames for m in ('power_watts', 'energy_kwh_*')]
    live60 = non_null(render(targets, start, int(now), url))
    fine = non_null(render(targets, fine_start(now), int(now), url))
    if any(t % 60 for pts in live60.values() for t, _ in pts):
        raise Refused('the render from before the 5 s archive did not return 60 s points')
    return live60, fine


# --- commands ---------------------------------------------------------------------------------------------------------


def load_state(path: Path) -> dict:
    return json.loads(path.read_text())


def resolve_until(args) -> int:
    if args.until is not None:
        return args.until
    if getattr(args, 'state', None):
        return int(load_state(args.state)['last_ts'])
    return int(time.time()) - 15


@dataclass
class Inputs:
    now: float
    snapshot: dict
    live60: dict
    fine: dict
    plan: Plan


def prepare(args) -> Inputs:
    until = resolve_until(args)
    check_snapshot(args.snapshot, SNAPSHOT_FILES)
    snapshot = load_render_files(args.snapshot, SNAPSHOT_FILES)
    now = time.time()
    live60, fine = fetch_inputs(RENAMES, now, args.render_url)
    plan = build_plan(
        snapshot,
        live60,
        fine,
        RENAMES,
        until,
        now,
        load_hierarchy(HIERARCHY_FILE),
        config.GRAPHITE_STALE_AFTER_SECONDS,
    )
    return Inputs(now, snapshot, live60, fine, plan)


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


def counter_mean(cum: Points, ts: int, counter: Counter, span: int) -> float:
    """The counter at ts, or its mean over a minute's 5 s slots for a 60 s value."""
    times = [ts + k for k in range(0, span, 5)] if span else [ts]
    return mean([counter_value(cum, t, counter) for t in times])


def own_counter_agreement(inputs: Inputs, renames) -> list:
    """The aggregator's own counters for periods that began after the cutover, against the integral (left unrepaired)."""
    plan = inputs.plan
    lines = []
    for r in renames:
        for c in COUNTERS:
            metric = f'{PREFIX}.{r.new}.{c.metric}'
            pts = [(t, v, 60) for t, v in inputs.live60.get(metric, []) if t < plan.fine_from]
            pts += [(t, v, 0) for t, v in inputs.fine.get(metric, []) if plan.fine_from <= t <= plan.corrector.until]
            last: dict = {}
            for t, v, span in pts:
                period = c.start_of_period(t)
                if period > r.cutover:
                    last[period] = (t, v, span)
            for period, (t, v, span) in sorted(last.items()):
                integral = counter_mean(plan.cums[r.new], t, c, span)
                lines.append(
                    f'  {r.new} {c.period} from {fmt(period)}, at {fmt(t)}{" (60 s)" if span else ""}: aggregator {v:.4f}, '
                    f'integral {integral:.4f}, difference {v - integral:+.4f} kWh '
                    f'({(v - integral) / integral * 100 if integral else 0:+.1f}%)'
                )
    return lines


def integration_check(inputs: Inputs, renames, stale_after: float) -> list:
    """The integral from 60 s means against the exact 5 s one over the snapshot: the error of the 60 s stretch."""
    lines = []
    split = inputs.plan.split
    for r in renames:
        metric = f'{PREFIX}.{r.new}.power_watts'
        coarse = [(m + 30, v) for m, v in inputs.live60.get(metric, []) if m + 30 >= r.cutover and m < split]
        if len(coarse) < 2:
            continue
        a, b = coarse[0][0], coarse[-1][0]
        cum5 = cumulative_energy(
            [(t, v) for t, v in inputs.snapshot.get(metric, []) if t < split], r.cutover, stale_after
        )
        cum60 = cumulative_energy(coarse, a, stale_after)
        exact = energy_at(cum5, b) - energy_at(cum5, a)
        approx = energy_at(cum60, b)
        lines.append(
            f'  {r.new} {fmt(a)} to {fmt(b)}: 5 s {exact:.4f} kWh, 60 s means {approx:.4f} kWh, difference '
            f'{approx - exact:+.4f} kWh ({(approx - exact) / exact * 100 if exact else 0:+.2f}%)'
        )
    return lines


def describe_changes(changes: list) -> list:
    lines = []
    by_metric: dict = {}
    for c in changes:
        by_metric.setdefault(c.metric, []).append(c)
    for metric, cs in sorted(by_metric.items()):
        sixty = sorted((c for c in cs if c.step == 60), key=lambda c: c.ts)
        fives = sorted((c for c in cs if c.step == 5), key=lambda c: c.ts)
        lines.append(
            f'  {metric}: {len(sixty)} minutes {fmt(sixty[0].ts)} to {fmt(sixty[-1].ts)}, {len(fives)} 5 s points'
        )
        sample = [sixty[0], sixty[len(sixty) // 2], sixty[-1]] + fives[-1:]
        for c in sample:
            lines.append(f'      {fmt(c.ts)} {c.step:2d} s  {c.old:.6f} -> {c.new:.6f}  ({c.new - c.old:+.6f})')
    return lines


def state_notes(plan: Plan, state: dict) -> tuple:
    start = plan.corrector.excess_start
    rebuilt, compare = rebuilt_state_values(plan.cums, RENAMES, state['last_ts'])
    new, notes = correct_state(
        state, energy_step(running_total(plan.repair.rows), state['last_ts']), start, rebuilt, DROP_STATE_KEYS
    )
    for key, values in compare.items():
        for attr, integral in values.items():
            held = state['devices'].get(key, {}).get(attr)
            if held is None:
                continue
            notes.append(
                f'{key}.{attr}: left at {held:.6f} (reset since the cutover; the aggregator integrated it itself); '
                f'integral since the reset {integral:.6f} ({held - integral:+.6f})'
            )
    return new, notes


def print_plan(inputs: Inputs, state: dict | None) -> None:
    plan, repair, snapshot = inputs.plan, inputs.plan.repair, inputs.snapshot
    print(
        f'now {fmt(inputs.now)} UTC, until {fmt(plan.corrector.until)}; stale limit {config.GRAPHITE_STALE_AFTER_SECONDS} s'
    )
    print(
        f'sources: 5 s snapshot (2026-11-06 20:44) before {fmt(plan.split)}; live 60 s archive from then to '
        f'{fmt(plan.fine_from)}; live 5 s archive after'
    )
    print("snapshot minute means equal the live 60 s archive for the new names' power and every rewritten minute")
    for ep in repair.episodes:
        r = ep.rename
        print(
            f'{r.old} -> {r.new}: cutover {fmt(r.cutover)}, old name last point {fmt(r.last_old_point)}, stale {ep.stale_w} W'
        )
        if ep.start is None:
            print('  stale reading 0 W: nothing over-counted')
            continue
        print(
            f'  counted twice in {ep.cycles} cycles {fmt(ep.start)} to {fmt(ep.end)} (5 s snapshot); median (aggregate - '
            f'device sum) inside {ep.inside_median:.1f} W'
        )
        agg = snapshot[f'{AGGREGATE}.power_watts']
        edge = [(t, p) for t, p in agg if ep.start - 40 <= t <= ep.start + 20 or ep.end - 20 <= t <= ep.end + 40]
        print('  aggregate.power_watts at the edges: ' + ', '.join(f'{fmt(t)[11:]} {p:.1f}' for t, p in edge))
    print(
        f'median |aggregate - device sum| outside the windows since the first cutover: {repair.outside_median_abs:.2f} W'
    )
    with_dt = [r for r in repair.rows if r.dt_counter is not None]
    print(
        f'excess energy {repair.excess_kwh:.6f} kWh over {len(repair.rows)} cycles ({len(with_dt)} timed by the '
        f'counter); by bucket timestamps {repair.excess_kwh_by_stamps:.6f} kWh'
    )
    print("integration rule against the aggregator's own writes on 2026-11-06 (snapshot):")
    for line in agreement(snapshot, RENAMES, config.GRAPHITE_STALE_AFTER_SECONDS):
        print(line)
    print('integral from 60 s means against 5 s over the snapshot (the error of the 60 s stretch):')
    for line in integration_check(inputs, RENAMES, config.GRAPHITE_STALE_AFTER_SECONDS):
        print(line)
    print('counters that reset after the cutover (left as the aggregator wrote them), against the integral:')
    for line in own_counter_agreement(inputs, RENAMES):
        print(line)
    for name, cum in plan.cums.items():
        print(f'{name}: integral since cutover at until = {energy_at(cum, plan.corrector.until):.6f} kWh')
    print(f'changes: {len(plan.changes)} (60 s values for every touched minute; 5 s points from {fmt(plan.fine_from)})')
    for line in describe_changes(plan.changes):
        print(line)
    send, later = split_for_send(plan.changes, inputs.now)
    print(
        f'if sent now: {sum(c.step == 5 for c in send)} 5 s points and {sum(c.step == 60 for c in send)} minute values at '
        f'once, {len(later)} minute values once they leave the 5 s archive'
        + (f' (by {fmt(max(c.ts for c in later) + FINE_RETENTION + 30)})' if later else '')
    )
    if state is not None:
        _new, notes = state_notes(plan, state)
        print(f'state copy (last_ts {fmt(state["last_ts"])}):')
        for n in notes:
            print(f'  {n}')


def cmd_plan(args) -> int:
    inputs = prepare(args)
    print_plan(inputs, load_state(args.state) if args.state else None)
    return 0


def cmd_state(args) -> int:
    check_output_path(args.state, args.output)
    state = load_state(args.state)
    args.until = int(state['last_ts'])
    inputs = prepare(args)
    new, notes = state_notes(inputs.plan, state)
    tmp = args.output.with_suffix('.tmp')
    tmp.write_text(json.dumps(new, indent=2))
    tmp.replace(args.output)
    for n in notes:
        print(n)
    print(f'wrote {args.output} (input {args.state} untouched)')
    return 0


@dataclass
class Check:
    points: int = 0
    bad5: list = field(default_factory=list)
    bad60: list = field(default_factory=list)
    coarser: list = field(default_factory=list)
    unverifiable: int = 0
    open_buckets: int = 0  # 900 s / 3600 s buckets still being written: not checked (verify again after they close)

    @property
    def ok(self) -> bool:
        return not (self.bad5 or self.bad60 or self.coarser or self.unverifiable)


def check_once(changes: list, url: str, now: float) -> Check:
    """Graphite against the new values, by each minute's age now, and the 900 s and 3600 s means over them."""
    res = Check(points=len(changes))
    metrics = sorted({c.metric for c in changes})
    hi = max(c.ts for c in changes)
    groups: dict = {}
    for c in changes:
        groups.setdefault((c.metric, minute_of(c.ts)), []).append(c)
    readable = minute_of(now - FINE_RETENTION) + 120  # minutes wholly readable at 5 s
    c_from = min(min(c.ts for c in changes), int(now) - FINE_RETENTION) - 7200
    c_from -= c_from % 3600
    coarse = render(metrics, c_from, hi + 7200, url)
    fine = render(metrics, readable, hi + 60, url) if hi >= readable else {}
    co = {m: dict(pts) for m, pts in coarse.items()}
    fi = {m: dict(pts) for m, pts in fine.items()}
    for (metric, m), cs in sorted(groups.items()):
        fives = [c for c in cs if c.step == 5]
        sixty = [c for c in cs if c.step == 60]
        got60 = co.get(metric, {}).get(m)
        if m >= readable and fives:
            res.bad5 += [
                (metric, c.ts, fi.get(metric, {}).get(c.ts), c.new)
                for c in fives
                if not close(fi.get(metric, {}).get(c.ts), c.new)
            ]
            known = [v for t, v in fi.get(metric, {}).items() if minute_of(t) == m and v is not None]
            if not known or not close(got60, mean(known)):
                res.bad60.append((metric, m, got60, mean(known) if known else None))
        elif sixty:
            if not close(got60, sixty[0].new):
                res.bad60.append((metric, m, got60, sixty[0].new))
        else:
            res.unverifiable += 1
    r900 = render(metrics, int(now) - 61 * 86400, hi + 7200, url)
    r3600 = render(metrics, int(now) - (5 * 365 + 3) * 86400, hi + 7200, url)
    for metric in metrics:
        minutes = {m for (mm, m) in groups if mm == metric}
        p900 = r900.get(metric, [])
        step900 = p900[1][0] - p900[0][0] if len(p900) > 1 else 0
        finer_for_hour = coarse.get(metric, [])
        closed = int(now) - 60  # a bucket ending later is still taking the aggregator's new points
        if step900 == 900:
            touched = {m - m % 900 for m in minutes}
            res.open_buckets += sum(1 for t in touched if t + 900 > closed)
            touched = {t for t in touched if t + 900 <= closed}
            for t, got, want in coarser_mismatches(coarse.get(metric, []), [p for p in p900 if p[0] in touched], 900):
                res.coarser.append((metric, 900, t, got, want))
            finer_for_hour = p900
        touched = {m - m % 3600 for m in minutes}
        res.open_buckets += sum(1 for t in touched if t + 3600 > closed)
        touched = {t for t in touched if t + 3600 <= closed}
        p3600 = [p for p in r3600.get(metric, []) if p[0] in touched]
        for t, got, want in coarser_mismatches(finer_for_hour, p3600, 3600):
            res.coarser.append((metric, 3600, t, got, want))
    return res


def readback(changes: list, url: str, wait: float) -> Check:
    """check_once until it passes or wait runs out: carbon flushes to disk, and so rolls up, a little later."""
    deadline = time.monotonic() + wait
    while True:
        res = check_once(changes, url, time.time())
        if res.ok or time.monotonic() >= deadline:
            return res
        time.sleep(15)


def print_check(res: Check) -> None:
    n5 = res.bad5
    print(f'{res.points} logged values checked by their age now')
    print(f'  5 s points not holding the new value: {len(n5)}')
    for k in n5[:10]:
        print(f'    {k[0]} {fmt(k[1])}: {k[2]} (want {k[3]})')
    print(f'  minutes whose 60 s value is not the new value (or not the mean of its 5 s points): {len(res.bad60)}')
    for k in res.bad60[:10]:
        print(f'    {k[0]} {fmt(k[1])}: {k[2]} (want {k[3]})')
    print(f'  900 s / 3600 s buckets not the mean of their finer points: {len(res.coarser)}')
    for k in res.coarser[:10]:
        print(f'    {k[0]} {k[1]} s {fmt(k[2])}: {k[3]} (mean {k[4]})')
    if res.unverifiable:
        print(f'  minutes with only 5 s values logged and no longer readable at 5 s: {res.unverifiable}')
    if res.open_buckets:
        print(f'  900 s / 3600 s buckets still open, not checked: {res.open_buckets} (verify again after the hour)')


def send_changes(changes: list, args, kind: str, meta: dict, fetched: float | None = None) -> int:
    if not changes:
        print('nothing to send')
        return 0
    now = time.time()
    if fetched is not None and now - fetched > GUARD / 2:
        raise Refused(f'the plan is {now - fetched:.0f} s old; rerun so the 5 s/60 s split is current. Nothing sent.')
    send, later = split_for_send(changes, now)
    log = write_log(args.log_dir, kind, changes, meta)
    print(f'log of {len(changes)} old/new values: {log}')
    carbon_send(carbon_lines(send), args.carbon_host, args.carbon_port)
    print(f'sent {len(send)} values to {args.carbon_host}:{args.carbon_port}')
    send_deferred(later, lambda lines: carbon_send(lines, args.carbon_host, args.carbon_port))
    if later:
        print(f'sent {len(later)} deferred minute values')
    res = readback(changes, args.render_url, READBACK_WAIT)
    print_check(res)
    return 0 if res.ok else 1


def print_send_split(changes: list) -> None:
    send, later = split_for_send(changes, time.time())
    print(
        f'would send {sum(c.step == 5 for c in send)} 5 s points and {sum(c.step == 60 for c in send)} minute values now, '
        f'{len(later)} minute values once they leave the 5 s archive'
    )
    print('dry run: nothing sent (add --apply)')


def cmd_series(args) -> int:
    inputs = prepare(args)
    plan = inputs.plan
    changes = plan.changes
    print(f'{len(changes)} values in {len({c.metric for c in changes})} series up to {fmt(plan.corrector.until)}')
    print(
        f'excess {plan.repair.excess_kwh:.6f} kWh; '
        + ', '.join(f'{k} {energy_at(v, plan.corrector.until):.6f} kWh' for k, v in plan.cums.items())
    )
    if not args.apply:
        print_send_split(changes)
        return 0
    meta = {
        'until': plan.corrector.until,
        'excess_kwh': plan.repair.excess_kwh,
        'device_kwh': {k: energy_at(v, plan.corrector.until) for k, v in plan.cums.items()},
        'split': plan.split,
        'fine_from': plan.fine_from,
    }
    return send_changes(changes, args, 'series', meta, inputs.now)


def cmd_verify(args) -> int:
    res = check_once(read_log(args.log), args.render_url, time.time())
    print_check(res)
    return 0 if res.ok else 1


def redo_changes(changes: list) -> list:
    """The logged new values again, to finish or repeat an apply without recomputing (the plateau is gone by then)."""
    return list(changes)


def cmd_redo(args) -> int:
    changes = redo_changes(read_log(args.log))
    print(f'{len(changes)} values from {args.log} would get their logged new values (again)')
    if not args.apply:
        print_send_split(changes)
        return 0
    return send_changes(changes, args, 'redo', {'redoes': str(args.log)})


def cmd_undo(args) -> int:
    changes = undo_changes(read_log(args.log))
    print(f'{len(changes)} values from {args.log} would get their old values back')
    if not args.apply:
        print_send_split(changes)
        return 0
    return send_changes(changes, args, 'undo', {'undoes': str(args.log)})


def probe_read(name: str, now: float, url: str) -> dict:
    """{(metric, step, ts): value} from each archive of the probe series."""
    out = {}
    reads = [
        (5, int(now) - 23 * 3600 - 600),
        (60, int(now) - 3 * 86400),
        (900, int(now) - 61 * 86400),
        (3600, int(now) - (5 * 365 + 3) * 86400),
    ]
    for step, start in reads:
        for metric, pts in render([f'{name}.*'], start, int(now), url).items():
            out |= {(metric, step, t): v for t, v in pts if v is not None}
    return out


def cmd_probe(args) -> int:
    now = time.time()
    probe = probe_plan(args.name, now)
    print(f'probe {args.name}: {sum(len(p) for p in probe.phases)} carbon lines in {len(probe.phases)} phases')
    if not args.apply:
        print('dry run: nothing sent (add --apply)')
        return 0
    for i, lines in enumerate(probe.phases):
        carbon_send(lines, args.carbon_host, args.carbon_port)
        print(f'phase {i + 1}: sent {len(lines)} lines')
        time.sleep(30)
    deadline = time.monotonic() + READBACK_WAIT
    while True:
        got = probe_read(args.name, now, args.render_url)
        wrong = {k: got.get(k) for k, v in probe.expected.items() if not close(got.get(k), v)}
        if not wrong or time.monotonic() >= deadline:
            break
        time.sleep(15)
    for key, want in sorted(probe.expected.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2])):
        if key[1] == 60 and key[0].endswith('.aligned') and (key[2] // 60) % 15:
            continue  # one aligned minute per 900 s bucket is enough to show
        print(f'  {key[0]} {key[1]:4d} s {fmt(key[2])}: want {want}, got {got.get(key)}')
    print(f'{len(probe.expected) - len(wrong)} of {len(probe.expected)} expected values present; series left in place')
    return 0 if not wrong else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--render-url', default=config.GRAPHITE_RENDER_URL)
    parser.add_argument('--carbon-host', default=CARBON_HOST)
    parser.add_argument('--carbon-port', type=int, default=CARBON_PORT)
    parser.add_argument('--log-dir', type=Path, default=LOG_DIR)
    parser.add_argument('--snapshot', type=Path, default=SNAPSHOT_DIR, help='the 5 s snapshot (checksums checked)')
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
    probe = sub.add_parser('probe', help='write and read back a throwaway series to check how carbon files old points')
    probe.add_argument(
        '--name', default='test.electricity_monitoring.energy_repair_probe.' + time.strftime('%Y_%m_%d_%H%M')
    )
    probe.add_argument('--apply', action='store_true')
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
        'probe': cmd_probe,
    }
    try:
        return handler[args.command](args)
    except Refused as e:
        print(f'REFUSED: {e}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
