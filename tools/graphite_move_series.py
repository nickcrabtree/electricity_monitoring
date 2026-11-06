#!/usr/bin/env python3
"""Move the tail of one Graphite series tree to another name, from a cutover time on.

Used when a plug is repurposed: points recorded under the old name A from cutover T onwards really belong to the new
name B. Runs on the Graphite host (ubuntu64), where the whisper files live. Stdlib plus the `whisper` module only, so
it runs from a single file under the system python3 (`python3 - <subcommand> ... < graphite_move_series.py` works too).

Phases, each a subcommand, each safe to rerun:

  dry-run  per metric: points to copy, points already present or conflicting in B, points to prune from A per
           archive, schemas, and how recently A was last written. Reads only.
  backup   copy every A.* and B.* file to a durable directory, verify each copy reads back byte-identical, and write
           manifest.json. Reads the whisper files only; run as nickc. An existing backup is verified, never replaced.
  copy     send A's points in [T, until] to B through carbon's plaintext port (no root needed), from the
           highest-precision archive that still covers T, then poll B until every point reads back. Refuses if B
           already holds different values there (use --overwrite) or the schemas differ.
  prune    rebuild each A.* file without points >= T. Needs root (files belong to _graphite): run under sudo. Refuses
           unless the backup verifies, B holds the copied points, and A has not been written for --quiet-minutes.
           Writes a temp file beside the original, verifies it against the backup, then swaps it in atomically.

`until` defaults to A's last power_watts point at or after T: the moment the collector stopped using the old name.
Later points under A are not copied (aggregate_energy.py keeps integrating a device's last power reading for its
one-hour render window after the device stops reporting, so A's energy_kwh_* gain stale points) but are pruned.

A point's timestamp is the start of its bucket, so a point at exactly T moves. Coarse buckets that straddle T (for
example the 1 h bucket holding T) are recomputed in A from the finer points before T, with the file's aggregation
method; B's straddling buckets are rolled up by whisper from the copied points alone.

Times: epoch seconds or ISO 8601; give a zone (Z or +00:00), otherwise the host's local time is used.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import socket
import struct
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import whisper

WHISPER_ROOT = Path('/var/lib/graphite/whisper')
BACKUP_BASE = Path('/home/nickc/electricity_monitoring/backups')
CARBON_HOST = '192.168.86.123'  # the Graphite host; the one fixed address the LAN policy allows
CARBON_PORT = 2003
MANIFEST = 'manifest.json'
TMP_SUFFIX = '.prune-tmp'
BOUNDARY_MARGIN = 600  # refuse to copy when T is this close to leaving the archive the plan reads
POINT = struct.Struct(whisper.pointFormat)

Sender = Callable[[list], None]


class Refused(RuntimeError):
    """A safety check failed; nothing was changed by the refusing step."""


# --- time and paths --------------------------------------------------------------------------------------------------


def parse_time(text: str) -> int:
    text = text.strip()
    if text.isdigit():
        return int(text)
    stamp = datetime.fromisoformat(text.replace('Z', '+00:00'))
    if stamp.tzinfo is None:
        stamp = stamp.astimezone()  # host local time
    return int(stamp.timestamp())


def fmt(ts: float | None) -> str:
    if ts is None:
        return 'none'
    return datetime.fromtimestamp(ts, timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')


def metric_file(root: Path, metric: str) -> Path:
    return Path(root, *metric.split('.')).with_suffix('.wsp')


def list_metrics(root: Path, prefix: str) -> list[str]:
    folder = Path(root, *prefix.split('.'))
    return sorted(p.stem for p in folder.glob('*.wsp')) if folder.is_dir() else []


def backup_name(metric: str) -> str:
    return f'{metric}.wsp'


def default_backup_dir(source: str, dest: str, cutover: int) -> Path:
    day = datetime.fromtimestamp(cutover, timezone.utc).strftime('%Y-%m-%d')
    return BACKUP_BASE / day / f'{source.rsplit(".", 1)[-1]}-to-{dest.rsplit(".", 1)[-1]}'


# --- whisper reading -------------------------------------------------------------------------------------------------


def schema(path: Path) -> tuple:
    info = whisper.info(str(path))
    return (
        info['aggregationMethod'],
        info['xFilesFactor'],
        tuple((a['secondsPerPoint'], a['points']) for a in info['archives']),
    )


def schema_text(path: Path) -> str:
    method, xff, archives = schema(path)
    return f'{",".join(f"{s}s*{n}" for s, n in archives)} {method} xff={xff:g}'


def choose_step(path: Path, ts: int, now: int) -> int | None:
    """The archive whisper reads (and carbon writes) for a point at ts: the finest whose retention covers its age."""
    for archive in whisper.info(str(path))['archives']:
        if archive['retention'] >= now - ts:
            return archive['secondsPerPoint']
    return None


def read_archive(path: Path, step: int, now: int) -> dict[int, float]:
    """Every visible point of one archive, as whisper itself would return it."""
    archive = next(a for a in whisper.info(str(path))['archives'] if a['secondsPerPoint'] == step)
    fetched = whisper.fetch(str(path), now - archive['retention'], now, now=now, archiveToSelect=step)
    if fetched is None:
        return {}
    (start, _end, got_step), values = fetched
    return {start + i * got_step: v for i, v in enumerate(values) if v is not None}


def same(a: float | None, b: float | None) -> bool:
    return a is not None and b is not None and math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-12)


def newest_point(path: Path, now: int) -> int | None:
    newest = None
    for archive in whisper.info(str(path))['archives']:
        points = read_archive(path, archive['secondsPerPoint'], now)
        if points:
            newest = max(newest or 0, max(points))
    return newest


def last_point_from(path: Path, cutover: int, now: int) -> int | None:
    step = choose_step(path, cutover, now)
    if step is None:
        return None
    times = [t for t in read_archive(path, step, now) if t >= cutover]
    return max(times) if times else None


def default_until(root: Path, source: str, cutover: int, now: int, backup_dir: Path | None = None) -> int:
    """A's last power_watts point at or after T: in the live file, or in the backup once the live one is pruned."""
    candidates = [metric_file(root, f'{source}.power_watts')]
    if backup_dir is not None:
        candidates.append(Path(backup_dir) / backup_name(f'{source}.power_watts'))
    for path in candidates:
        if path.exists():
            last = last_point_from(path, cutover, now)
            if last is not None:
                return last
    raise Refused(f'no {source}.power_watts point at or after {fmt(cutover)}; give --until explicitly')


def moves(ts: int, step: int, finest: int, cutover: int, until: int) -> bool:
    """Whether the bucket [ts, ts + step) lies wholly in the moved span [cutover, until + finest).

    At the finest step this is cutover <= ts <= until. In a coarser archive the bucket holding `until` also holds
    whatever the new name recorded after it, so it is neither copied nor compared (A's copy is still pruned).
    """
    return ts >= cutover and ts + step <= until + finest


# --- planning --------------------------------------------------------------------------------------------------------


@dataclass
class MetricPlan:
    metric: str
    step: int | None
    schema_matches: bool
    near_boundary: bool
    to_send: list = field(default_factory=list)  # [(ts, value)]
    identical: int = 0
    conflicts: list = field(default_factory=list)  # [(ts, source value, destination value)]
    after_until: int = 0
    prune: dict = field(default_factory=dict)  # {seconds per point: points at or after T}
    newest: int | None = None
    problems: list = field(default_factory=list)


def plan_metric(root: Path, source: str, dest: str, metric: str, cutover: int, until: int, now: int) -> MetricPlan:
    src = metric_file(root, f'{source}.{metric}')
    dst = metric_file(root, f'{dest}.{metric}')
    step = choose_step(src, cutover, now)
    plan = MetricPlan(metric, step, schema_matches=False, near_boundary=False)
    plan.newest = newest_point(src, now)
    for archive in whisper.info(str(src))['archives']:
        spp = archive['secondsPerPoint']
        plan.prune[spp] = sum(1 for t in read_archive(src, spp, now) if t >= cutover)
    if step is None:
        plan.problems.append(f'cutover {fmt(cutover)} is older than every archive of {src}')
        return plan
    if not dst.exists():
        plan.problems.append(f'{dst} does not exist (start the collector under the new name first)')
        return plan
    plan.schema_matches = schema(src) == schema(dst)
    if not plan.schema_matches:
        plan.problems.append(f'schemas differ: {schema_text(src)} vs {schema_text(dst)}')
    plan.near_boundary = choose_step(src, cutover, now + BOUNDARY_MARGIN) != step
    if plan.near_boundary:
        plan.problems.append(f'cutover is about to age out of the {step}s archive; rerun the plan in a few minutes')
    source_points = read_archive(src, step, now)
    dest_points = read_archive(dst, step, now) if plan.schema_matches else {}
    finest = schema(src)[2][0][0]
    for ts in sorted(source_points):
        if ts < cutover:
            continue
        if not moves(ts, step, finest, cutover, until):
            plan.after_until += 1
            continue
        value, existing = source_points[ts], dest_points.get(ts)
        if existing is None:
            plan.to_send.append((ts, value))
        elif same(existing, value):
            plan.identical += 1
        else:
            plan.conflicts.append((ts, value, existing))
    return plan


def compare_range(src: Path, dst: Path, step: int, cutover: int, until: int, now: int) -> list[str]:
    """Points of src that moved (one archive) and that dst lacks or holds differently."""
    finest = schema(src)[2][0][0]
    source_points = read_archive(src, step, now)
    dest_points = read_archive(dst, step, now)
    return [
        f'{fmt(ts)}: source {v!r}, destination {dest_points.get(ts)!r}'
        for ts, v in sorted(source_points.items())
        if moves(ts, step, finest, cutover, until) and not same(dest_points.get(ts), v)
    ]


def verify_copy(root: Path, source: str, dest: str, metric: str, cutover: int, until: int, now: int) -> list[str]:
    src = metric_file(root, f'{source}.{metric}')
    step = choose_step(src, cutover, now)
    if step is None:
        return [f'cutover is older than every archive of {src}']
    return compare_range(src, metric_file(root, f'{dest}.{metric}'), step, cutover, until, now)


# --- copy ------------------------------------------------------------------------------------------------------------


def carbon_lines(metric: str, points: list) -> list[str]:
    return [f'{metric} {value!r} {ts}\n' for ts, value in points]


def carbon_sender(host: str, port: int) -> Sender:
    def send(lines: list) -> None:
        with socket.create_connection((host, port), timeout=30) as conn:
            conn.sendall(''.join(lines).encode())

    return send


@dataclass
class CopyResult:
    ok: bool
    sent: int
    messages: list


def copy_series(
    root: Path,
    source: str,
    dest: str,
    metrics: list,
    cutover: int,
    until: int,
    now: int,
    sender: Sender,
    overwrite: bool,
    wait: float = 0.0,
) -> CopyResult:
    plans = [plan_metric(root, source, dest, m, cutover, until, now) for m in metrics]
    messages = [f'{p.metric}: {problem}' for p in plans for problem in p.problems]
    if messages:
        return CopyResult(False, 0, messages + ['nothing sent'])
    conflicted = [p for p in plans if p.conflicts]
    if conflicted and not overwrite:
        for p in conflicted:
            ts, value, existing = p.conflicts[0]
            messages.append(
                f'{p.metric}: {len(p.conflicts)} destination points differ (first {fmt(ts)}: '
                f'source {value!r}, destination {existing!r})'
            )
        return CopyResult(False, 0, messages + ['nothing sent; rerun with --overwrite to replace them'])
    sent = 0
    for p in plans:
        points = p.to_send + ([(ts, v) for ts, v, _ in p.conflicts] if overwrite else [])
        if points:
            sender(carbon_lines(f'{dest}.{p.metric}', sorted(points)))
            sent += len(points)
        messages.append(f'{p.metric}: sent {len(points)}, already present {p.identical}')
    deadline = time.monotonic() + wait
    while True:
        missing = {m: verify_copy(root, source, dest, m, cutover, until, now) for m in metrics}
        missing = {m: problems for m, problems in missing.items() if problems}
        if not missing or time.monotonic() >= deadline:
            break
        time.sleep(5)
    for m, problems in missing.items():
        messages.append(f'{m}: NOT VERIFIED, {len(problems)} points missing or different (first {problems[0]})')
    if not missing:
        messages.append(f'verified: every point in [{fmt(cutover)}, {fmt(until)}] reads back from {dest}.*')
    return CopyResult(not missing, sent, messages)


# --- backup ----------------------------------------------------------------------------------------------------------


def _write_durably(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + '.tmp')
    with open(tmp, 'wb') as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def backup(root: Path, prefixes: list, backup_dir: Path, cutover: int) -> dict:
    backup_dir = Path(backup_dir)
    manifest_path = backup_dir / MANIFEST
    if manifest_path.exists():
        return json.loads(manifest_path.read_text())
    backup_dir.mkdir(parents=True, exist_ok=True)
    files = []
    for prefix in prefixes:
        for metric in list_metrics(root, prefix):
            original = metric_file(root, f'{prefix}.{metric}')
            data = original.read_bytes()
            target = backup_dir / backup_name(f'{prefix}.{metric}')
            _write_durably(target, data)
            if target.read_bytes() != data:
                raise Refused(f'backup of {original} does not read back identical')
            files.append(
                {
                    'metric': f'{prefix}.{metric}',
                    'original': str(original),
                    'backup': target.name,
                    'sha256': hashlib.sha256(data).hexdigest(),
                    'size': len(data),
                    'schema': schema_text(target),
                }
            )
    manifest = {
        'created': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'host': socket.gethostname(),
        'cutover': cutover,
        'cutover_utc': fmt(cutover),
        'prefixes': list(prefixes),
        'whisper_root': str(root),
        'files': files,
    }
    _write_durably(manifest_path, (json.dumps(manifest, indent=2) + '\n').encode())
    return manifest


def _straddles(step: int, finest: int, ts: int, cutover: int) -> bool:
    return step != finest and cutover % step != 0 and ts == cutover - cutover % step


def verify_backup(root: Path, backup_dir: Path, source: str, cutover: int, now: int) -> list[str]:
    """Problems that make the backup unfit to restore A from; empty when it is fit."""
    manifest_path = Path(backup_dir) / MANIFEST
    if not manifest_path.exists():
        return [f'no backup manifest at {manifest_path}']
    manifest = json.loads(manifest_path.read_text())
    if manifest.get('cutover') != cutover:
        return [f'backup was taken for cutover {manifest.get("cutover_utc")}, not {fmt(cutover)}']
    entries = {e['metric']: e for e in manifest['files']}
    problems = []
    for metric in list_metrics(root, source):
        name = f'{source}.{metric}'
        entry = entries.get(name)
        copy = Path(backup_dir) / backup_name(name)
        if entry is None or not copy.exists():
            problems.append(f'{name}: not in the backup')
            continue
        data = copy.read_bytes()
        if hashlib.sha256(data).hexdigest() != entry['sha256']:
            problems.append(f'{name}: backup file checksum does not match the manifest')
            continue
        live = metric_file(root, name)
        if schema(copy) != schema(live):
            problems.append(f'{name}: backup schema differs from the live file')
            continue
        archives = schema(live)[2]
        for step, _ in archives:
            old, cur = read_archive(copy, step, now), read_archive(live, step, now)
            differ = [
                t
                for t in set(old) | set(cur)
                if t < cutover and not _straddles(step, archives[0][0], t, cutover) and not same(old.get(t), cur.get(t))
            ]
            if differ:
                problems.append(f'{name}: {len(differ)} points before the cutover differ from the backup ({step}s)')
    return problems


# --- prune -----------------------------------------------------------------------------------------------------------


def prune_bytes(data: bytes, cutover: int, info: dict) -> tuple[bytes, list]:
    """Return the file contents with every point >= cutover removed and straddling coarse buckets recomputed.

    A removed slot keeps its value but its timestamp moves back a whole number of ring lengths, which is how whisper
    itself leaves an expired slot: it stays where it is (so the ring's first slot still anchors the layout) and no
    fetch returns it.
    """
    buf = bytearray(data)
    notes = []
    archives = info['archives']
    original_newest = {}
    for archive in archives:
        newest = 0
        for k in range(archive['points']):
            newest = max(newest, POINT.unpack_from(buf, archive['offset'] + k * POINT.size)[0])
        original_newest[archive['secondsPerPoint']] = newest

    def slots(archive):
        for k in range(archive['points']):
            pos = archive['offset'] + k * POINT.size
            yield pos, *POINT.unpack_from(buf, pos)

    def expire(pos, interval, value, span):
        shift = span * ((interval - cutover) // span + 1)
        POINT.pack_into(buf, pos, interval - shift, value)

    for idx, archive in enumerate(archives):
        step, span = archive['secondsPerPoint'], archive['retention']
        for pos, interval, value in slots(archive):
            if interval >= cutover:
                expire(pos, interval, value, span)
        if idx == 0 or cutover % step == 0:
            continue
        start = cutover - cutover % step
        bucket = next(((pos, value) for pos, interval, value in slots(archive) if interval == start), None)
        if bucket is None:
            continue
        finer = archives[idx - 1]
        fine_step = finer['secondsPerPoint']
        if start <= original_newest[fine_step] - finer['retention']:
            notes.append(f'{step}s bucket {fmt(start)} kept as is: the {fine_step}s archive no longer covers it')
            continue
        fine = {i: v for _, i, v in slots(finer) if start <= i < cutover}
        neighbours = [fine.get(t) for t in range(start, start + step, fine_step)]
        known = [v for v in neighbours if v is not None]
        if known and len(known) / len(neighbours) >= info['xFilesFactor']:
            new = whisper.aggregate(info['aggregationMethod'], known, neighbours)
            POINT.pack_into(buf, bucket[0], start, new)
            notes.append(
                f'{step}s bucket {fmt(start)} recomputed from {len(known)} earlier points: {bucket[1]!r} -> {new!r}'
            )
        else:
            expire(bucket[0], start, bucket[1], span)
            notes.append(f'{step}s bucket {fmt(start)} removed: no earlier points in it')
    return bytes(buf), notes


def verify_pruned(new: Path, backup_file: Path, cutover: int, now: int) -> list[str]:
    if schema(new) != schema(backup_file):
        return [f'{new}: schema changed']
    archives = schema(new)[2]
    problems = []
    for step, _ in archives:
        old, cur = read_archive(backup_file, step, now), read_archive(new, step, now)
        late = [t for t in cur if t >= cutover]
        if late:
            problems.append(f'{new}: {len(late)} points at or after the cutover remain ({step}s)')
        lost = [
            t
            for t, v in old.items()
            if t < cutover and not _straddles(step, archives[0][0], t, cutover) and not same(cur.get(t), v)
        ]
        if lost:
            problems.append(f'{new}: {len(lost)} points before the cutover lost or changed ({step}s)')
    return problems


@dataclass
class PruneResult:
    metric: str
    already_pruned: bool
    removed: dict
    notes: list


def prune(
    root: Path,
    source: str,
    dest: str,
    cutover: int,
    until: int,
    backup_dir: Path,
    now: int,
    quiet_seconds: int,
    metrics: list | None = None,
) -> list:
    backup_dir = Path(backup_dir)
    problems = verify_backup(root, backup_dir, source, cutover, now)
    if problems:
        raise Refused('no verified backup: ' + '; '.join(problems))
    metrics = metrics or list_metrics(root, source)
    for metric in metrics:
        saved = backup_dir / backup_name(f'{source}.{metric}')
        step = choose_step(saved, cutover, now)
        missing = compare_range(saved, metric_file(root, f'{dest}.{metric}'), step, cutover, until, now)
        if missing:
            raise Refused(f'copy not verified for {dest}.{metric}: {len(missing)} points missing ({missing[0]})')
    for metric in metrics:
        newest = newest_point(metric_file(root, f'{source}.{metric}'), now)
        if newest is not None and newest >= cutover and now - newest < quiet_seconds:
            raise Refused(
                f'{source}.{metric} is still being written (last point {fmt(newest)}, {now - newest}s ago); '
                f'wait until it has been quiet for {quiet_seconds}s'
            )
    folder = metric_file(root, f'{source}.x').parent
    if not os.access(folder, os.W_OK):
        raise Refused(f'cannot write {folder}: run with sudo')
    results = []
    for metric in metrics:
        live = metric_file(root, f'{source}.{metric}')
        saved = backup_dir / backup_name(f'{source}.{metric}')
        info = whisper.info(str(live))
        data = live.read_bytes()
        new, notes = prune_bytes(data, cutover, info)
        removed = {
            a['secondsPerPoint']: sum(1 for t in read_archive(live, a['secondsPerPoint'], now) if t >= cutover)
            for a in info['archives']
        }
        if new == data:
            results.append(PruneResult(metric, True, removed, notes))
            continue
        stat = live.stat()
        tmp = live.with_name(live.name + TMP_SUFFIX)
        with open(tmp, 'wb') as fh:
            fh.write(new)
            fh.flush()
            os.fsync(fh.fileno())
        if os.geteuid() == 0:
            os.chown(tmp, stat.st_uid, stat.st_gid)
        os.chmod(tmp, stat.st_mode & 0o7777)
        bad = verify_pruned(tmp, saved, cutover, now)
        if bad:
            tmp.unlink()
            raise Refused('pruned copy failed verification, original left untouched: ' + '; '.join(bad))
        os.replace(tmp, live)
        dir_fd = os.open(folder, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
        bad = verify_pruned(live, saved, cutover, now)
        if bad:
            raise Refused(f'{live} failed verification after the swap; restore it from {saved}: ' + '; '.join(bad))
        results.append(PruneResult(metric, False, removed, notes))
    return results


# --- command line ----------------------------------------------------------------------------------------------------


def _resolve(args) -> tuple[int, int, int, list, Path]:
    now = args.now if args.now is not None else int(time.time())
    cutover = parse_time(args.cutover)
    backup_dir = Path(args.backup_dir) if args.backup_dir else default_backup_dir(args.source, args.dest, cutover)
    until = (
        parse_time(args.until)
        if args.until
        else default_until(args.whisper_root, args.source, cutover, now, backup_dir)
    )
    metrics = args.metrics.split(',') if args.metrics else list_metrics(args.whisper_root, args.source)
    if not metrics:
        raise Refused(f'no metrics under {args.source} in {args.whisper_root}')
    return now, cutover, until, metrics, backup_dir


def _header(args, now: int, cutover: int, until: int, backup_dir: Path) -> None:
    print(f'source  {args.source}.*')
    print(f'dest    {args.dest}.*')
    print(f'cutover {fmt(cutover)} ({cutover}); points at or after it move')
    how = 'given' if args.until else "source's last power_watts point"
    print(f'until   {fmt(until)} ({until}; {how}); later source points are pruned, not copied')
    print(f'now     {fmt(now)}')
    print(f'backup  {backup_dir}')


def cmd_dry_run(args) -> int:
    now, cutover, until, metrics, backup_dir = _resolve(args)
    _header(args, now, cutover, until, backup_dir)
    root = args.whisper_root
    src0 = metric_file(root, f'{args.source}.{metrics[0]}')
    dst0 = metric_file(root, f'{args.dest}.{metrics[0]}')
    print(f'source schema {schema_text(src0)}')
    print(f'dest schema   {schema_text(dst0) if dst0.exists() else "missing"}')
    print()
    print(
        f'{"metric":<20} {"step":>4} {"copy":>6} {"present":>7} {"conflict":>8} {"after":>6}  prune per archive  newest'
    )
    ok = True
    for metric in metrics:
        p = plan_metric(root, args.source, args.dest, metric, cutover, until, now)
        prune_text = ' '.join(f'{s}s:{n}' for s, n in p.prune.items())
        age = f'{fmt(p.newest)} ({now - p.newest}s ago)' if p.newest else 'none'
        print(
            f'{metric:<20} {p.step or "-":>4} {len(p.to_send):>6} {p.identical:>7} {len(p.conflicts):>8} '
            f'{p.after_until:>6}  {prune_text}  {age}'
        )
        for problem in p.problems:
            ok = False
            print(f'    PROBLEM: {problem}')
        if p.conflicts:
            ts, value, existing = p.conflicts[0]
            print(f'    first conflict {fmt(ts)}: source {value!r}, destination {existing!r}')
    print()
    print('copy = points to send; present = already identical in dest; after = source points not copied (after')
    print('until, or a coarse bucket that also holds until); prune = points at or after the cutover per archive')
    problems = verify_backup(root, backup_dir, args.source, cutover, now)
    print('backup: ' + ('verified' if not problems else '; '.join(problems)))
    return 0 if ok else 1


def cmd_backup(args) -> int:
    now, cutover, _until, _metrics, backup_dir = _resolve(args)
    existed = (backup_dir / MANIFEST).exists()
    manifest = backup(args.whisper_root, [args.source, args.dest], backup_dir, cutover)
    print(f'{"existing backup kept" if existed else "backed up"}: {len(manifest["files"])} files in {backup_dir}')
    for entry in manifest['files']:
        print(f'  {entry["backup"]}  {entry["size"]} bytes  sha256 {entry["sha256"][:16]}')
    problems = verify_backup(args.whisper_root, backup_dir, args.source, cutover, now)
    for problem in problems:
        print(f'PROBLEM: {problem}')
    print('backup verified' if not problems else 'backup NOT verified')
    return 0 if not problems else 1


def cmd_copy(args) -> int:
    now, cutover, until, metrics, backup_dir = _resolve(args)
    _header(args, now, cutover, until, backup_dir)
    problems = verify_backup(args.whisper_root, backup_dir, args.source, cutover, now)
    if problems:
        print('refusing to copy without a verified backup: ' + '; '.join(problems))
        return 1
    sender = carbon_sender(args.carbon_host, args.carbon_port)
    result = copy_series(
        args.whisper_root, args.source, args.dest, metrics, cutover, until, now, sender, args.overwrite, args.wait
    )
    for message in result.messages:
        print(message)
    return 0 if result.ok else 1


def cmd_prune(args) -> int:
    now, cutover, until, metrics, backup_dir = _resolve(args)
    _header(args, now, cutover, until, backup_dir)
    quiet = int(args.quiet_minutes * 60)
    results = prune(args.whisper_root, args.source, args.dest, cutover, until, backup_dir, now, quiet, metrics)
    for r in results:
        state = 'already pruned' if r.already_pruned else 'pruned'
        counts = ' '.join(f'{s}s:{n}' for s, n in r.removed.items())
        print(f'{r.metric:<20} {state:<15} removed {counts}')
        for note in r.notes:
            print(f'    {note}')
    print(
        f'verified: no point at or after the cutover in the {args.source} files above; earlier points match the backup'
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    for name, func in (('dry-run', cmd_dry_run), ('backup', cmd_backup), ('copy', cmd_copy), ('prune', cmd_prune)):
        p = sub.add_parser(name)
        p.set_defaults(func=func)
        p.add_argument('--source', required=True, help='old metric prefix, e.g. home.electricity.tuya.garage_power_a')
        p.add_argument('--dest', required=True, help='new metric prefix, e.g. home.electricity.tuya.tumble_dryer')
        p.add_argument('--cutover', required=True, help='first moment that belongs to the new name')
        p.add_argument('--until', help="last point to copy (default: the source's last power_watts point)")
        p.add_argument('--metrics', help='comma-separated metric names (default: every .wsp under the source)')
        p.add_argument('--whisper-root', type=Path, default=WHISPER_ROOT)
        p.add_argument('--backup-dir', help=f'default {BACKUP_BASE}/<cutover date>/<old>-to-<new>')
        p.add_argument('--now', type=int, help=argparse.SUPPRESS)
        if name == 'copy':
            p.add_argument('--carbon-host', default=CARBON_HOST)
            p.add_argument('--carbon-port', type=int, default=CARBON_PORT)
            p.add_argument('--overwrite', action='store_true', help='replace differing destination points')
            p.add_argument('--wait', type=float, default=180, help='seconds to wait for carbon to flush')
        if name == 'prune':
            p.add_argument('--quiet-minutes', type=float, default=15, help='source must be unwritten this long')
    return parser


def main(argv: list | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except Refused as exc:
        print(f'REFUSED: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
