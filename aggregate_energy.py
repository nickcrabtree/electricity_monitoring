#!/usr/bin/env python3
"""
Aggregate electricity usage across Kasa and Tuya Cloud devices.
Sends whole-home aggregate power (watts) and cumulative energy (kWh) for
- day (resets at local midnight)
- week (resets at 01:00 Monday)
- month (resets at 01:00 on the 1st)
- year (resets at 01:00 on Jan 1)

Usage:
  python aggregate_energy.py [--once]

Notes:
- Uses python-kasa to read Kasa device power (current_consumption in watts)
- Uses TinyTuya Cloud to read Tuya/SmartLife device status over cloud
- Persists counters in energy_state.json in the current directory
"""

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
import argparse
from typing import Optional, Tuple

import config
from graphite_helper import send_metrics, format_device_name

# Kasa
from kasa import Discover, Device

# Tuya Cloud
import tinytuya

STATE_FILE = os.path.join(os.path.dirname(__file__), 'energy_state.json')

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@dataclass
class EnergyState:
    last_ts: Optional[float] = None
    day_kwh: float = 0.0
    week_kwh: float = 0.0
    month_kwh: float = 0.0
    year_kwh: float = 0.0
    last_day_reset: Optional[float] = None
    last_week_reset: Optional[float] = None
    last_month_reset: Optional[float] = None
    last_year_reset: Optional[float] = None

    @staticmethod
    def load(path: str) -> 'EnergyState':
        try:
            with open(path, 'r') as f:
                data = json.load(f)
            return EnergyState(**data)
        except Exception:
            return EnergyState()

    def save(self, path: str) -> None:
        tmp = path + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(asdict(self), f)
        os.replace(tmp, path)


def local_now() -> datetime:
    # Use local time
    return datetime.now().astimezone()


def next_day_boundary(now: datetime) -> datetime:
    # next midnight
    d = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return d


def current_day_boundary(now: datetime) -> datetime:
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def current_week_boundary(now: datetime) -> datetime:
    # Monday 01:00 local of current week (or most recent past one)
    # weekday(): Monday=0
    days_back = (now.weekday() - 0) % 7
    boundary = (now - timedelta(days=days_back)).replace(hour=1, minute=0, second=0, microsecond=0)
    if boundary > now:
        boundary -= timedelta(days=7)
    return boundary


def next_week_boundary(now: datetime) -> datetime:
    return current_week_boundary(now) + timedelta(days=7)


def current_month_boundary(now: datetime) -> datetime:
    # 1st of month at 01:00
    return now.replace(day=1, hour=1, minute=0, second=0, microsecond=0)


def next_month_boundary(now: datetime) -> datetime:
    # move to first of next month at 01:00
    year = now.year + (1 if now.month == 12 else 0)
    month = 1 if now.month == 12 else now.month + 1
    return now.replace(year=year, month=month, day=1, hour=1, minute=0, second=0, microsecond=0)


def current_year_boundary(now: datetime) -> datetime:
    # Jan 1 at 01:00
    return now.replace(month=1, day=1, hour=1, minute=0, second=0, microsecond=0)


def next_year_boundary(now: datetime) -> datetime:
    return now.replace(year=now.year + 1, month=1, day=1, hour=1, minute=0, second=0, microsecond=0)


async def get_total_kasa_power_watts() -> float:
    total = 0.0
    try:
        devices = await Discover.discover()
        for _, dev in devices.items():
            try:
                await dev.update()
                if dev.has_emeter:
                    energy = dev.modules.get("Energy")
                    if energy and hasattr(energy, 'current_consumption') and energy.current_consumption is not None:
                        total += float(energy.current_consumption)
            except Exception as e:
                logger.error(f"Kasa device error: {e}")
    except Exception as e:
        logger.error(f"Kasa discovery error: {e}")
    return total


async def _tuya_cloud() -> tinytuya.Cloud:
    return tinytuya.Cloud()


def _pick(d: dict, keys) -> Optional[float]:
    for k in keys:
        if k in d and d[k] is not None:
            try:
                return float(d[k])
            except Exception:
                continue
    return None


def _normalize_voltage(v: Optional[float]) -> Optional[float]:
    if v is None:
        return None
    if v > 1000:
        return v / 10.0
    return v


def _normalize_current(a: Optional[float]) -> Optional[float]:
    if a is None:
        return None
    if a > 10.0:
        return a / 1000.0
    return a


def _normalize_power(w: Optional[float]) -> Optional[float]:
    if w is None:
        return None
    if w > 10000.0:
        return w / 10.0
    return w


async def get_total_tuya_cloud_power_watts() -> float:
    total = 0.0
    try:
        cloud = await _tuya_cloud()
        devices = await asyncio.to_thread(cloud.getdevices)
        for d in devices:
            try:
                devid = d.get('id') or d.get('uuid')
                if not devid:
                    continue
                status_resp = await asyncio.to_thread(cloud.getstatus, devid)
                result = status_resp.get('result') if isinstance(status_resp, dict) else status_resp
                status = {}
                if isinstance(result, list):
                    for item in result:
                        status[item.get('code')] = item.get('value')
                elif isinstance(result, dict):
                    status = result
                p = _pick(status, ['cur_power', 'power', 'power_w', 'add_ele'])
                pw = _normalize_power(p)
                if pw is not None:
                    total += pw
            except Exception as e:
                logger.error(f"Tuya cloud device error: {e}")
    except Exception as e:
        logger.error(f"Tuya cloud error: {e}")
    return total


def apply_resets(state: EnergyState, now: datetime) -> None:
    # initialize last reset times if missing
    if state.last_day_reset is None:
        state.last_day_reset = current_day_boundary(now).timestamp()
    if state.last_week_reset is None:
        state.last_week_reset = current_week_boundary(now).timestamp()
    if state.last_month_reset is None:
        state.last_month_reset = current_month_boundary(now).timestamp()
    if state.last_year_reset is None:
        state.last_year_reset = current_year_boundary(now).timestamp()

    # compute upcoming boundaries
    day_boundary = current_day_boundary(now)
    if now.timestamp() >= day_boundary.timestamp() and state.last_day_reset < day_boundary.timestamp():
        state.day_kwh = 0.0
        state.last_day_reset = day_boundary.timestamp()

    week_boundary = current_week_boundary(now)
    if now.timestamp() >= week_boundary.timestamp() and state.last_week_reset < week_boundary.timestamp():
        state.week_kwh = 0.0
        state.last_week_reset = week_boundary.timestamp()

    month_boundary = current_month_boundary(now)
    if now.timestamp() >= month_boundary.timestamp() and state.last_month_reset < month_boundary.timestamp():
        state.month_kwh = 0.0
        state.last_month_reset = month_boundary.timestamp()

    year_boundary = current_year_boundary(now)
    if now.timestamp() >= year_boundary.timestamp() and state.last_year_reset < year_boundary.timestamp():
        state.year_kwh = 0.0
        state.last_year_reset = year_boundary.timestamp()


async def compute_and_send(state: EnergyState) -> Tuple[EnergyState, int]:
    now = local_now()
    apply_resets(state, now)

    total_power_w = 0.0
    kasa_w = await get_total_kasa_power_watts()
    tuya_w = await get_total_tuya_cloud_power_watts()
    total_power_w = kasa_w + tuya_w

    sent = 0

    # Prepare metrics list
    metrics = []
    base = f"{config.METRIC_PREFIX}.aggregate"
    metrics.append((f"{base}.power_watts", total_power_w))

    # Integrate energy from last timestamp
    ts_now = time.time()
    if state.last_ts is not None:
        dt = max(0.0, ts_now - state.last_ts)
        kwh_inc = (total_power_w * dt) / 3600000.0
        state.day_kwh += kwh_inc
        state.week_kwh += kwh_inc
        state.month_kwh += kwh_inc
        state.year_kwh += kwh_inc

    state.last_ts = ts_now

    # Add energy metrics
    metrics.append((f"{base}.energy_kwh_daily", state.day_kwh))
    metrics.append((f"{base}.energy_kwh_weekly", state.week_kwh))
    metrics.append((f"{base}.energy_kwh_monthly", state.month_kwh))
    metrics.append((f"{base}.energy_kwh_yearly", state.year_kwh))

    sent = send_metrics(config.CARBON_SERVER, config.CARBON_PORT, metrics)
    logger.info(f"Aggregate sent: power={total_power_w:.3f}W, day={state.day_kwh:.3f}kWh, week={state.week_kwh:.3f}kWh, month={state.month_kwh:.3f}kWh, year={state.year_kwh:.3f}kWh")

    state.save(STATE_FILE)
    return state, sent


async def main_loop(once: bool = False):
    state = EnergyState.load(STATE_FILE)
    try:
        while True:
            state, _ = await compute_and_send(state)
            if once:
                break
            await asyncio.sleep(config.SMART_PLUG_POLL_INTERVAL)
    except KeyboardInterrupt:
        logger.info("Shutting down...")


def main():
    parser = argparse.ArgumentParser(description='Aggregate electricity usage across Kasa and Tuya Cloud')
    parser.add_argument('--once', action='store_true', help='Run one cycle and exit')
    args = parser.parse_args()
    asyncio.run(main_loop(once=args.once))


if __name__ == '__main__':
    main()
