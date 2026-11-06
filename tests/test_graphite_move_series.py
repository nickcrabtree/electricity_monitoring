"""Tests for tools/graphite_move_series.py on temporary whisper files (no Graphite, no carbon)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
import whisper

# tools/ is not on the default path
sys.path.insert(0, str(Path(__file__).parent.parent / 'tools'))
import graphite_move_series as gms  # noqa: E402

# The live tuya schema on ubuntu64: 5s:24h, 60s:60d, 15m:5y, 1h:20y, average, xFilesFactor 0.
ARCHIVES = [(5, 17280), (60, 86400), (900, 175200), (3600, 175200)]
NOW = 1793970000  # 2026-11-06 13:40:00 UTC
CUT = NOW - 4000  # 12:33:20: on a 5 s boundary, inside a 60 s, 15 min and 1 h bucket
UNTIL = NOW - 1800  # the collector's last point under the old name
SRC = 'home.electricity.tuya.old_plug'
DST = 'home.electricity.tuya.new_plug'
METRICS = ('power_watts', 'energy_kwh_daily')


def value_at(ts: int) -> float:
    return float(ts % 97) + 0.25


def make_file(root: Path, metric_path: str, points: list[tuple[int, float]]) -> Path:
    path = gms.metric_file(root, metric_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    whisper.create(str(path), ARCHIVES, xFilesFactor=0.0, aggregationMethod='average')
    if points:
        whisper.update_many(str(path), points, now=NOW)
    return path


def series(start: int, end: int, step: int = 5) -> list[tuple[int, float]]:
    return [(t, value_at(t)) for t in range(start, end + 1, step)]


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """Old plug: 5 s data from NOW-6h to UNTIL, then aggregator-style energy points after UNTIL.

    New plug: unrelated old data around NOW-20h, which must survive.
    """
    root = tmp_path / 'whisper'
    for name in METRICS:
        pts = series(NOW - 6 * 3600, UNTIL)
        if name.startswith('energy'):
            pts += series(UNTIL + 5, NOW - 600)  # stale integration after the collector stopped
        make_file(root, f'{SRC}.{name}', pts)
        make_file(root, f'{DST}.{name}', series(NOW - 20 * 3600, NOW - 19 * 3600))
    return root


class FakeCarbon:
    """Stands in for carbon-cache: applies plaintext lines with whisper.update_many, as carbon does."""

    def __init__(self, root: Path):
        self.root = root
        self.lines: list[str] = []

    def __call__(self, lines: list[str]) -> None:
        self.lines.extend(lines)
        grouped: dict[str, list[tuple[int, float]]] = {}
        for line in lines:
            metric, value, ts = line.split()
            grouped.setdefault(metric, []).append((int(ts), float(value)))
        for metric, pts in grouped.items():
            whisper.update_many(str(gms.metric_file(self.root, metric)), pts, now=NOW)


def archive(path: Path, step: int) -> dict[int, float]:
    return gms.read_archive(path, step, NOW)


# --- parsing and planning ----------------------------------------------------------------------


def test_parse_time_accepts_epoch_and_iso_with_zone():
    assert gms.parse_time('1793967520') == 1793967520
    assert gms.parse_time('2026-11-06T12:18:40Z') == 1793967520
    assert gms.parse_time('2026-11-06 12:18:40+00:00') == 1793967520


def test_metric_file_maps_dots_to_directories(tmp_path):
    assert gms.metric_file(tmp_path, 'a.b.c') == tmp_path / 'a' / 'b' / 'c.wsp'


def test_list_metrics_finds_every_wsp(tree):
    assert gms.list_metrics(tree, SRC) == sorted(METRICS)


def test_default_until_is_last_power_point(tree):
    assert gms.default_until(tree, SRC, CUT, NOW) == UNTIL


def test_plan_counts_boundary_point_at_cutover_as_moving(tree):
    plan = gms.plan_metric(tree, SRC, DST, 'power_watts', CUT, UNTIL, NOW)
    times = [t for t, _ in plan.to_send]
    assert times[0] == CUT  # a point exactly at T moves
    assert times[-1] == UNTIL
    assert len(times) == (UNTIL - CUT) // 5 + 1
    assert plan.step == 5
    assert plan.identical == 0
    assert plan.conflicts == []
    assert plan.schema_matches


def test_plan_does_not_copy_points_after_until(tree):
    plan = gms.plan_metric(tree, SRC, DST, 'energy_kwh_daily', CUT, UNTIL, NOW)
    assert max(t for t, _ in plan.to_send) == UNTIL
    assert plan.after_until == len(range(UNTIL + 5, NOW - 600 + 1, 5))


def test_plan_reports_prune_counts_per_archive(tree):
    plan = gms.plan_metric(tree, SRC, DST, 'power_watts', CUT, UNTIL, NOW)
    assert plan.prune[5] == (UNTIL - CUT) // 5 + 1
    # 60 s buckets starting at or after T; the straddling bucket is recomputed, not dropped
    first_full_minute = CUT - CUT % 60 + 60
    assert plan.prune[60] == len(range(first_full_minute, UNTIL - UNTIL % 60 + 1, 60))


def test_plan_flags_conflicting_destination_points(tree):
    dst = gms.metric_file(tree, f'{DST}.power_watts')
    whisper.update_many(str(dst), [(CUT + 50, 999.0), (CUT + 100, value_at(CUT + 100))], now=NOW)
    plan = gms.plan_metric(tree, SRC, DST, 'power_watts', CUT, UNTIL, NOW)
    assert plan.conflicts == [(CUT + 50, value_at(CUT + 50), 999.0)]
    assert plan.identical == 1
    assert CUT + 50 not in {t for t, _ in plan.to_send}
    assert CUT + 100 not in {t for t, _ in plan.to_send}


def test_plan_refuses_mismatched_schema(tree):
    other = gms.metric_file(tree, f'{DST}.power_watts')
    other.unlink()
    whisper.create(str(other), [(60, 1440)], xFilesFactor=0.0, aggregationMethod='average')
    plan = gms.plan_metric(tree, SRC, DST, 'power_watts', CUT, UNTIL, NOW)
    assert not plan.schema_matches


# --- copy --------------------------------------------------------------------------------------


def test_carbon_lines_format():
    assert gms.carbon_lines('a.b', [(100, 1.5)]) == ['a.b 1.5 100\n']


def test_copy_writes_points_and_verifies(tree):
    carbon = FakeCarbon(tree)
    result = gms.copy_series(tree, SRC, DST, list(METRICS), CUT, UNTIL, NOW, carbon, overwrite=False)
    assert result.ok
    for name in METRICS:
        dst = archive(gms.metric_file(tree, f'{DST}.{name}'), 5)
        for t in range(CUT, UNTIL + 1, 5):
            assert dst[t] == value_at(t)
        assert dst.get(CUT - 5) is None
        assert dst.get(UNTIL + 5) is None
        assert gms.verify_copy(tree, SRC, DST, name, CUT, UNTIL, NOW) == []
    # the unrelated old destination data survives
    old = archive(gms.metric_file(tree, f'{DST}.power_watts'), 5)
    assert old[NOW - 20 * 3600] == value_at(NOW - 20 * 3600)


def test_copy_is_idempotent(tree):
    carbon = FakeCarbon(tree)
    gms.copy_series(tree, SRC, DST, list(METRICS), CUT, UNTIL, NOW, carbon, overwrite=False)
    sent_first = len(carbon.lines)
    result = gms.copy_series(tree, SRC, DST, list(METRICS), CUT, UNTIL, NOW, carbon, overwrite=False)
    assert result.ok
    assert len(carbon.lines) == sent_first  # nothing left to send


def test_copy_refuses_conflicts_unless_overwrite(tree):
    dst = gms.metric_file(tree, f'{DST}.power_watts')
    whisper.update_many(str(dst), [(CUT + 50, 999.0)], now=NOW)
    carbon = FakeCarbon(tree)
    result = gms.copy_series(tree, SRC, DST, list(METRICS), CUT, UNTIL, NOW, carbon, overwrite=False)
    assert not result.ok
    assert carbon.lines == []
    result = gms.copy_series(tree, SRC, DST, list(METRICS), CUT, UNTIL, NOW, carbon, overwrite=True)
    assert result.ok
    assert archive(dst, 5)[CUT + 50] == value_at(CUT + 50)


def test_copy_rolls_coarser_destination_archives(tree):
    gms.copy_series(tree, SRC, DST, ['power_watts'], CUT, UNTIL, NOW, FakeCarbon(tree), overwrite=False)
    dst = gms.metric_file(tree, f'{DST}.power_watts')
    minutes = archive(dst, 60)
    full = CUT - CUT % 60 + 60
    assert minutes[full] == pytest.approx(sum(value_at(t) for t in range(full, full + 60, 5)) / 12)
    straddle = CUT - CUT % 60  # only the post-T points of that minute
    assert minutes[straddle] == pytest.approx(
        sum(value_at(t) for t in range(CUT, straddle + 60, 5)) / len(range(CUT, straddle + 60, 5))
    )


# --- backup ------------------------------------------------------------------------------------


def test_backup_copies_and_verifies(tree, tmp_path):
    bdir = tmp_path / 'backup'
    manifest = gms.backup(tree, [SRC, DST], bdir, CUT)
    assert len(manifest['files']) == 2 * len(METRICS)
    for entry in manifest['files']:
        assert (bdir / entry['backup']).read_bytes() == Path(entry['original']).read_bytes()
    assert json.loads((bdir / gms.MANIFEST).read_text())['cutover'] == CUT
    assert gms.verify_backup(tree, bdir, SRC, CUT, NOW) == []


def test_backup_rerun_keeps_existing_backup(tree, tmp_path):
    bdir = tmp_path / 'backup'
    gms.backup(tree, [SRC, DST], bdir, CUT)
    first = (bdir / gms.MANIFEST).read_text()
    gms.backup(tree, [SRC, DST], bdir, CUT)
    assert (bdir / gms.MANIFEST).read_text() == first


# --- prune -------------------------------------------------------------------------------------


def prepared(tree: Path, tmp_path: Path) -> Path:
    bdir = tmp_path / 'backup'
    gms.backup(tree, [SRC, DST], bdir, CUT)
    gms.copy_series(tree, SRC, DST, list(METRICS), CUT, UNTIL, NOW, FakeCarbon(tree), overwrite=False)
    return bdir


def test_prune_refuses_without_backup(tree, tmp_path):
    gms.copy_series(tree, SRC, DST, list(METRICS), CUT, UNTIL, NOW, FakeCarbon(tree), overwrite=False)
    before = gms.metric_file(tree, f'{SRC}.power_watts').read_bytes()
    with pytest.raises(gms.Refused, match='backup'):
        gms.prune(tree, SRC, DST, CUT, UNTIL, tmp_path / 'missing', NOW, quiet_seconds=0)
    assert gms.metric_file(tree, f'{SRC}.power_watts').read_bytes() == before


def test_prune_refuses_tampered_backup(tree, tmp_path):
    bdir = prepared(tree, tmp_path)
    victim = bdir / json.loads((bdir / gms.MANIFEST).read_text())['files'][0]['backup']
    victim.write_bytes(victim.read_bytes()[:-12] + b'\0' * 12)
    with pytest.raises(gms.Refused, match='backup'):
        gms.prune(tree, SRC, DST, CUT, UNTIL, bdir, NOW, quiet_seconds=0)


def test_prune_refuses_before_copy(tree, tmp_path):
    bdir = tmp_path / 'backup'
    gms.backup(tree, [SRC, DST], bdir, CUT)
    with pytest.raises(gms.Refused, match='copy'):
        gms.prune(tree, SRC, DST, CUT, UNTIL, bdir, NOW, quiet_seconds=0)


def test_prune_refuses_while_source_still_written(tree, tmp_path):
    bdir = prepared(tree, tmp_path)
    with pytest.raises(gms.Refused, match='still'):
        gms.prune(tree, SRC, DST, CUT, UNTIL, bdir, NOW, quiet_seconds=900)  # energy point 600 s old


def test_prune_removes_points_from_cutover_and_keeps_the_rest(tree, tmp_path):
    bdir = prepared(tree, tmp_path)
    src = gms.metric_file(tree, f'{SRC}.power_watts')
    info_before = whisper.info(str(src))
    stat_before = src.stat()
    gms.prune(tree, SRC, DST, CUT, UNTIL, bdir, NOW, quiet_seconds=0)
    assert whisper.info(str(src)) == info_before  # schema, xff and aggregation preserved
    assert (src.stat().st_mode, src.stat().st_uid) == (stat_before.st_mode, stat_before.st_uid)
    backup_file = bdir / gms.backup_name(f'{SRC}.power_watts')
    for step, _ in ARCHIVES:
        old, new = archive(backup_file, step), archive(src, step)
        assert all(t < CUT for t in new)
        straddle = CUT - CUT % step
        for t, v in old.items():
            if t < CUT and not (step > 5 and t == straddle):
                assert new[t] == v
    assert archive(src, 5)[CUT - 5] == value_at(CUT - 5)
    assert not list(src.parent.glob('*.prune-tmp'))


def test_prune_recomputes_straddling_coarse_buckets(tree, tmp_path):
    bdir = prepared(tree, tmp_path)
    gms.prune(tree, SRC, DST, CUT, UNTIL, bdir, NOW, quiet_seconds=0)
    src = gms.metric_file(tree, f'{SRC}.power_watts')
    s60 = CUT - CUT % 60
    expected60 = [value_at(t) for t in range(s60, CUT, 5)]
    minutes = archive(src, 60)
    assert minutes[s60] == pytest.approx(sum(expected60) / len(expected60))
    s900 = CUT - CUT % 900
    quarter = archive(src, 900)
    expected900 = [minutes[t] for t in range(s900, CUT, 60) if t in minutes]
    assert quarter[s900] == pytest.approx(sum(expected900) / len(expected900))
    s3600 = CUT - CUT % 3600
    hours = archive(src, 3600)
    expected3600 = [quarter[t] for t in range(s3600, CUT, 900) if t in quarter]
    assert hours[s3600] == pytest.approx(sum(expected3600) / len(expected3600))


def test_prune_is_safe_to_rerun(tree, tmp_path):
    bdir = prepared(tree, tmp_path)
    gms.prune(tree, SRC, DST, CUT, UNTIL, bdir, NOW, quiet_seconds=0)
    src = gms.metric_file(tree, f'{SRC}.power_watts')
    once = src.read_bytes()
    report = gms.prune(tree, SRC, DST, CUT, UNTIL, bdir, NOW, quiet_seconds=0)
    assert src.read_bytes() == once
    assert all(r.already_pruned for r in report)


def test_late_run_skips_coarse_bucket_holding_until(tree, tmp_path):
    """After 24 h only 60 s data covers T; the minute holding `until` also holds new-name points in B."""
    bdir = prepared(tree, tmp_path)
    dst = gms.metric_file(tree, f'{DST}.power_watts')
    whisper.update_many(str(dst), [(t, 5000.0) for t in range(UNTIL + 5, UNTIL + 60, 5)], now=NOW)
    later = NOW + 2 * 86400
    plan = gms.plan_metric(tree, SRC, DST, 'power_watts', CUT, UNTIL, later)
    assert plan.step == 60
    assert plan.conflicts == []
    assert plan.to_send == []
    report = gms.prune(tree, SRC, DST, CUT, UNTIL, bdir, later, quiet_seconds=900)
    assert not any(r.already_pruned for r in report)


def test_prune_honours_metric_selection(tree, tmp_path):
    bdir = tmp_path / 'backup'
    gms.backup(tree, [SRC, DST], bdir, CUT)
    gms.copy_series(tree, SRC, DST, ['power_watts'], CUT, UNTIL, NOW, FakeCarbon(tree), overwrite=False)
    energy = gms.metric_file(tree, f'{SRC}.energy_kwh_daily')
    before = energy.read_bytes()
    report = gms.prune(tree, SRC, DST, CUT, UNTIL, bdir, NOW, quiet_seconds=0, metrics=['power_watts'])
    assert [r.metric for r in report] == ['power_watts']
    assert energy.read_bytes() == before


def test_prune_handles_ring_base_slot_after_cutover(tmp_path):
    """The first slot of a ring is whisper's anchor; pruning it must not blank the whole archive."""
    root = tmp_path / 'whisper'
    first = CUT - CUT % 3600 + 3600  # written first, so it anchors slot 0 of every archive and is >= T
    for name in METRICS:
        path = make_file(root, f'{SRC}.{name}', series(first, UNTIL))
        whisper.update_many(str(path), series(CUT - 3600, CUT - 5), now=NOW)
        make_file(root, f'{DST}.{name}', [])
    anchors = [gms.POINT.unpack_from(path.read_bytes(), a['offset'])[0] for a in whisper.info(str(path))['archives']]
    assert all(a >= CUT for a in anchors)
    bdir = tmp_path / 'backup'
    gms.backup(root, [SRC, DST], bdir, CUT)
    gms.copy_series(root, SRC, DST, list(METRICS), CUT, UNTIL, NOW, FakeCarbon(root), overwrite=False)
    gms.prune(root, SRC, DST, CUT, UNTIL, bdir, NOW, quiet_seconds=0)
    kept = archive(gms.metric_file(root, f'{SRC}.power_watts'), 5)
    assert kept[CUT - 5] == value_at(CUT - 5)
    assert kept[CUT - 3600] == value_at(CUT - 3600)
    assert all(t < CUT for t in kept)


def test_cli_dry_run_prints_plan(tree, capsys):
    rc = gms.main(
        [
            'dry-run',
            '--whisper-root',
            str(tree),
            '--source',
            SRC,
            '--dest',
            DST,
            '--cutover',
            str(CUT),
            '--now',
            str(NOW),
        ]
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert 'power_watts' in out and 'energy_kwh_daily' in out
    assert str((UNTIL - CUT) // 5 + 1) in out
    assert os.path.exists(gms.metric_file(tree, f'{SRC}.power_watts'))
