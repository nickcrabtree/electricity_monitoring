#!/usr/bin/env python3
"""One-off Quartz wall-power calibration run: 60 s idle, 180 s GPU load, 60 s idle, all inside ONE GPU lease.

Approved by the quartz gpu queue session on 2026-10-29 (one run only; repeats need a new ask).
Launcher mode (default) claims the lease with run_in_gpu_lease and runs this same file as the DIRECT child
(`--child`). The child is the only process that touches CUDA. See README.md for how to run it.
"""

from __future__ import annotations

import argparse
import json
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

QGPU_SRC = Path.home() / 'code/quartz_gpu_queue_service/src'
sys.path.insert(0, str(QGPU_SRC))

from quartz_gpu_queue_service import (  # noqa: E402
    DEFAULT_QUEUE_ROOT,
    ollama_aware_previous_holder_present,
)
from quartz_gpu_queue_service.member import note_progress  # noqa: E402
from quartz_gpu_queue_service.supervised import run_in_gpu_lease  # noqa: E402

PROJECT = 'power_calibration'
REQUEST_KIND = 'torch_training'
EXPECTED_SECONDS = 420  # short tier (limit 600 s); never declare above 600, the lease must end within 660 s
WAIT_TIMEOUT_SECONDS = 300
MIN_FREE_VRAM_MIB = 1500
PHASES = (('idle_before', 60), ('load', 180), ('idle_after', 60))
STEP_SECONDS = 5.0  # progress ping, power-hold read and sample, well inside the 10 s required
POWER_HOLD = Path.home() / 'power_hold.json'
HOLD_STALE_SECONDS = 600
MATRIX = 4096  # three fp32 4096x4096 matrices, about 200 MB of VRAM, far under 6 GiB

EXIT_HOLD = 2
EXIT_TERM = 3


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def hold_active() -> bool:
    """True when ~/power_hold.json says a hold is active and the record is fresh (stale > 600 s means no hold)."""
    try:
        record = json.loads(POWER_HOLD.read_text())
        refreshed = datetime.fromisoformat(record['refreshed_utc'].replace('Z', '+00:00'))
    except (OSError, ValueError, KeyError, TypeError):
        return False
    if (utc_now() - refreshed).total_seconds() > HOLD_STALE_SECONDS:
        return False
    return bool(record.get('active'))


def gpu_sample() -> tuple[str, str, str]:
    """nvidia-smi power.draw (W), utilization.gpu (%), memory.used (MiB); blanks on failure."""
    try:
        out = subprocess.run(  # noqa: S603
            [
                '/usr/bin/nvidia-smi',
                '--query-gpu=power.draw,utilization.gpu,memory.used',
                '--format=csv,noheader,nounits',
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        ).stdout.strip()
        power, util, mem = (x.strip() for x in out.split(','))
        return power, util, mem
    except (OSError, ValueError, subprocess.SubprocessError):
        return '', '', ''


class Stop(Exception):
    def __init__(self, code: int, why: str) -> None:
        super().__init__(why)
        self.code = code


def child(out_dir: Path) -> int:
    terminated = {'flag': False}

    def on_term(_signum: int, _frame: object) -> None:
        terminated['flag'] = True

    signal.signal(signal.SIGTERM, on_term)
    note_progress()  # first ping at child start, before anything slow

    csv_path = out_dir / f'samples_{utc_now():%Y%m%dT%H%M%SZ}.csv'
    marks = {}
    torch = None
    a = b = c = None
    burn_event = None

    def check() -> None:
        if terminated['flag']:
            raise Stop(EXIT_TERM, 'SIGTERM')
        if hold_active():
            raise Stop(EXIT_HOLD, 'power hold active')

    try:
        with csv_path.open('w') as fh:
            fh.write('utc,phase,gpu_power_w,gpu_util_pct,gpu_mem_mib\n')
            for phase, seconds in PHASES:
                check()
                marks[phase + '_start'] = utc_now().isoformat()
                if phase == 'load':
                    import torch as torch_mod  # CUDA only inside the child, imported late

                    torch = torch_mod
                    a = torch.randn(MATRIX, MATRIX, device='cuda')
                    b = torch.randn(MATRIX, MATRIX, device='cuda')
                    c = torch.empty(MATRIX, MATRIX, device='cuda')
                    torch.cuda.synchronize()
                end = time.monotonic() + seconds
                next_step = time.monotonic()
                while time.monotonic() < end:
                    now = time.monotonic()
                    if now >= next_step:
                        note_progress()
                        check()
                        power, util, mem = gpu_sample()
                        fh.write(f'{utc_now().isoformat()},{phase},{power},{util},{mem}\n')
                        fh.flush()
                        next_step = now + STEP_SECONDS
                    if phase == 'load':
                        for _ in range(8):  # about 0.1 s of work per batch on a GTX 1080
                            torch.matmul(a, b, out=c)
                        burn_event = torch.cuda.Event()
                        burn_event.record()
                        while not burn_event.query():  # poll, do not spin in synchronize (it would load a CPU core)
                            time.sleep(0.002)
                    else:
                        time.sleep(0.5)
                marks[phase + '_end'] = utc_now().isoformat()
                if phase == 'load':
                    del a, b, c
                    torch.cuda.empty_cache()
    except Stop as stop:
        marks['stopped'] = f'{stop.code}: {stop}'
        print(f'power_calibration child: stopping early ({stop})', flush=True)
        (out_dir / 'marks.json').write_text(json.dumps(marks, indent=2))
        return stop.code
    (out_dir / 'marks.json').write_text(json.dumps(marks, indent=2))
    print(f'power_calibration child: done, samples in {csv_path}, marks {json.dumps(marks)}', flush=True)
    return 0


def launcher(out_dir: Path) -> int:
    if hold_active():
        print('power hold active: not enrolling', flush=True)
        return EXIT_HOLD
    command = [sys.executable, str(Path(__file__).resolve()), '--child', '--out', str(out_dir)]
    print(f'{utc_now().isoformat()} enrolling {PROJECT}, expected_seconds={EXPECTED_SECONDS}', flush=True)
    status = run_in_gpu_lease(
        command,
        project=PROJECT,
        expected_seconds=EXPECTED_SECONDS,
        wait_timeout_seconds=WAIT_TIMEOUT_SECONDS,
        root=DEFAULT_QUEUE_ROOT,
        request_kind=REQUEST_KIND,
        min_free_vram_mib=MIN_FREE_VRAM_MIB,
        wait_for_previous_holder_clear=True,
        previous_holder_still_present_fn=ollama_aware_previous_holder_present,
        on_child_started=lambda pid: print(f'{utc_now().isoformat()} lease claimed, child pid {pid}', flush=True),
    )
    print(f'{utc_now().isoformat()} lease released, child status {status}', flush=True)
    return status


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--child', action='store_true', help='internal: run the three phases (inside the lease)')
    parser.add_argument('--out', type=Path, default=Path(__file__).resolve().parent, help='output directory')
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    return child(args.out) if args.child else launcher(args.out)


if __name__ == '__main__':
    raise SystemExit(main())
