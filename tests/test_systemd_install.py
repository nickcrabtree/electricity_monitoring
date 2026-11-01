"""Tests for systemd/install.sh: refresh every graphite-* unit, but enable only units that are new to the host."""

import os
import shutil
import subprocess
from pathlib import Path

REPO = Path(__file__).parent.parent
STUB_SUDO = """#!/bin/bash
# records every privileged call; performs the copies so the target directory fills in
echo "$*" >> "$SUDO_LOG"
[ "$1" = "-n" ] && shift
if [ "$1" = "cp" ]; then exec "$@"; fi
exit 0
"""


def run_install(tmp_path, units, already_installed):
    systemd = tmp_path / 'repo' / 'systemd'
    systemd.mkdir(parents=True)
    shutil.copy(REPO / 'systemd' / 'install.sh', systemd / 'install.sh')
    for name in units:
        (systemd / name).write_text('[Unit]\n')
    target = tmp_path / 'etc-systemd-system'
    target.mkdir()
    for name in already_installed:
        (target / name).write_text('[Unit]\nold\n')
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    (bin_dir / 'sudo').write_text(STUB_SUDO)
    (bin_dir / 'sudo').chmod(0o755)
    log = tmp_path / 'sudo.log'
    env = {**os.environ, 'PATH': f'{bin_dir}:{os.environ["PATH"]}', 'SUDO_LOG': str(log), 'SYSTEMD_DIR': str(target)}
    result = subprocess.run(['/bin/bash', str(systemd / 'install.sh')], env=env, capture_output=True, text=True)  # noqa: S603
    return result, log.read_text().splitlines(), target


def test_enables_only_units_new_to_the_host(tmp_path):
    result, calls, _ = run_install(
        tmp_path, ['graphite-a.service', 'graphite-b.service'], already_installed=['graphite-a.service']
    )
    assert result.returncode == 0, result.stderr
    enables = [c for c in calls if 'systemctl enable' in c]
    assert enables == ['-n systemctl enable graphite-b.service']


def test_refreshes_every_unit_file(tmp_path):
    result, _, target = run_install(
        tmp_path, ['graphite-a.service', 'graphite-b.service'], already_installed=['graphite-a.service']
    )
    assert result.returncode == 0, result.stderr
    assert (target / 'graphite-a.service').read_text() == '[Unit]\n'
    assert (target / 'graphite-b.service').exists()


def test_reports_units_whose_enable_state_was_left_alone(tmp_path):
    result, _, _ = run_install(tmp_path, ['graphite-a.service'], already_installed=['graphite-a.service'])
    assert result.returncode == 0, result.stderr
    assert 'graphite-a.service' in result.stdout
    assert 'left as it was' in result.stdout


def test_nothing_new_enables_nothing(tmp_path):
    result, calls, _ = run_install(tmp_path, ['graphite-a.service'], already_installed=['graphite-a.service'])
    assert result.returncode == 0, result.stderr
    assert not [c for c in calls if 'systemctl enable' in c]
    assert '-n systemctl daemon-reload' in calls


def test_ignores_units_that_are_not_graphite(tmp_path):
    result, calls, target = run_install(
        tmp_path, ['graphite-a.service', 'tuya-monitoring.service'], already_installed=[]
    )
    assert result.returncode == 0, result.stderr
    assert not (target / 'tuya-monitoring.service').exists()
    assert [c for c in calls if 'systemctl enable' in c] == ['-n systemctl enable graphite-a.service']
