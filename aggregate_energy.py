#!/usr/bin/env python3
"""
Aggregate electricity usage across Kasa and Tuya Cloud devices.
Sends:
1. Whole-home aggregate power (watts) and cumulative energy (kWh)
2. Per-device aggregate energy (kWh) for each device

All cumulative energy for:
- day (resets at local midnight)
- week (resets at 01:00 Monday)  
- month (resets at 01:00 on the 1st)
- year (resets at 01:00 on Jan 1)

Usage:
  python aggregate_energy.py [--once]

Notes:
- Reads power data from Graphite whisper files via SSH
- Persists counters in energy_state.json in the current directory
"""

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta, timezone
import argparse
from typing import Optional, Tuple, Dict, List
import subprocess

import config
from graphite_helper import send_metrics, format_device_name

STATE_FILE = os.path.join(os.path.dirname(__file__), 'energy_state.json')

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@dataclass
class DeviceEnergyState:
    """Energy state for a single device"""
    last_power_w: Optional[float] = None
    day_kwh: float = 0.0
    week_kwh: float = 0.0
    month_kwh: float = 0.0
    year_kwh: float = 0.0


@dataclass 
class EnergyState:
    # Whole-house totals
    last_ts: Optional[float] = None
    day_kwh: float = 0.0
    week_kwh: float = 0.0
    month_kwh: float = 0.0
    year_kwh: float = 0.0
    last_day_reset: Optional[float] = None
    last_week_reset: Optional[float] = None
    last_month_reset: Optional[float] = None
    last_year_reset: Optional[float] = None
    
    # Per-device state - dict[device_key, DeviceEnergyState]
    devices: Dict[str, DeviceEnergyState] = field(default_factory=dict)

    @staticmethod
    def load(path: str) -> 'EnergyState':
        try:
            with open(path, 'r') as f:
                data = json.load(f)
            
            # Convert device data back to DeviceEnergyState objects
            devices = {}
            if 'devices' in data:
                for dev_key, dev_data in data['devices'].items():
                    devices[dev_key] = DeviceEnergyState(**dev_data)
            data['devices'] = devices
            
            return EnergyState(**data)
        except Exception as e:
            logger.warning(f"Could not load energy state from {path}: {e}, starting fresh")
            return EnergyState()

    def save(self, path: str) -> None:
        # Convert DeviceEnergyState objects to dicts for JSON serialization
        data = asdict(self)
        data['devices'] = {k: asdict(v) for k, v in self.devices.items()}
        
        tmp = path + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(data, f, indent=2)
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


_PERIODS = [
    ('day',   current_day_boundary,   'last_day_reset',   'day_kwh'),
    ('week',  current_week_boundary,  'last_week_reset',  'week_kwh'),
    ('month', current_month_boundary, 'last_month_reset', 'month_kwh'),
    ('year',  current_year_boundary,  'last_year_reset',  'year_kwh'),
]


def apply_resets(state: EnergyState, now: datetime) -> None:
    now_ts = now.timestamp()
    for _period, boundary_fn, last_reset_attr, kwh_attr in _PERIODS:
        boundary_ts = boundary_fn(now).timestamp()
        if getattr(state, last_reset_attr) is None:
            setattr(state, last_reset_attr, boundary_ts)
        if now_ts >= boundary_ts and getattr(state, last_reset_attr) < boundary_ts:
            setattr(state, kwh_attr, 0.0)
            setattr(state, last_reset_attr, boundary_ts)
            for device_state in state.devices.values():
                setattr(device_state, kwh_attr, 0.0)



def get_device_power_from_graphite() -> Dict[str, float]:
    """
    Query Graphite whisper database via SSH for current power readings.
    Returns dict of {device_key: power_watts} for all devices with recent data.
    """
    devices = {}
    
    try:
        # Find all power_watts.wsp files
        find_cmd = [
            'ssh', '-o', 'BatchMode=yes', '-o', f'ConnectTimeout={config.GRAPHITE_SSH_TIMEOUT}',
            config.GRAPHITE_SSH_HOST,
            f'find {config.GRAPHITE_WHISPER_PATH} -type f -name "power_watts.wsp"'
        ]
        
        result = subprocess.run(
            find_cmd,
            capture_output=True,
            text=True,
            timeout=config.GRAPHITE_SSH_TIMEOUT
        )
        
        if result.returncode != 0:
            logger.error(f"SSH find command failed: {result.stderr}")
            return {}
        
        wsp_files = [line.strip() for line in result.stdout.strip().splitlines() if line.strip()]
        
        if not wsp_files:
            logger.warning("No power_watts.wsp files found in Graphite")
            return {}
        
        logger.debug(f"Found {len(wsp_files)} whisper files to query")
        
        # Query each file for most recent non-None value
        for wsp_path in wsp_files:
            try:
                # Fetch recent data and find last non-None value
                awk_cmd = "NF==2 && $2!=\"None\"{print; exit}"
                fetch_cmd = [
                    'ssh', '-o', 'BatchMode=yes', '-o', f'ConnectTimeout={config.GRAPHITE_SSH_TIMEOUT}',
                    config.GRAPHITE_SSH_HOST,
                    f'whisper-fetch "{wsp_path}" | tail -n {config.GRAPHITE_FETCH_TAIL_LINES} | tac | awk \'{awk_cmd}\''
                ]
                
                fetch_result = subprocess.run(
                    fetch_cmd,
                    capture_output=True,
                    text=True,
                    timeout=config.GRAPHITE_SSH_TIMEOUT
                )
                
                if fetch_result.returncode != 0:
                    logger.debug(f"Failed to fetch {wsp_path}: {fetch_result.stderr}")
                    continue
                
                output = fetch_result.stdout.strip()
                if not output:
                    logger.debug(f"No recent data for {wsp_path}")
                    continue
                
                # Parse "timestamp value" line
                parts = output.split()
                if len(parts) != 2:
                    logger.debug(f"Unexpected output format for {wsp_path}: {output}")
                    continue
                
                try:
                    power_watts = float(parts[1])
                except ValueError:
                    logger.debug(f"Could not parse power value from {wsp_path}: {parts[1]}")
                    continue
                
                # Derive device_key from path
                # Example: /var/lib/graphite/whisper/home/electricity/tuya/n_desk/power_watts.wsp
                # Should become: tuya.n_desk
                try:
                    # Find the part after ".../electricity/"
                    if '/electricity/' in wsp_path:
                        after_electricity = wsp_path.split('/electricity/')[1]
                        # Remove /power_watts.wsp suffix
                        path_parts = after_electricity.replace('/power_watts.wsp', '').split('/')
                        if len(path_parts) >= 2:
                            source = path_parts[0]  # 'tuya' or 'kasa'
                            device = path_parts[1]  # device name
                            device_key = f"{source}.{device}"
                            devices[device_key] = power_watts
                            logger.debug(f"Got {device_key}: {power_watts}W")
                        else:
                            logger.debug(f"Unexpected path structure: {wsp_path}")
                    else:
                        logger.debug(f"Path does not contain '/electricity/': {wsp_path}")
                except Exception as e:
                    logger.debug(f"Error parsing device key from {wsp_path}: {e}")
                    continue
                    
            except subprocess.TimeoutExpired:
                logger.warning(f"Timeout querying {wsp_path}")
                continue
            except Exception as e:
                logger.debug(f"Error querying {wsp_path}: {e}")
                continue
        
        logger.info(f"Retrieved power data for {len(devices)} devices from Graphite")
        
        if len(devices) == 0:
            logger.warning("No devices with recent power data found in Graphite")
        
        return devices
        
    except subprocess.TimeoutExpired:
        logger.error(f"SSH connection to Graphite timed out after {config.GRAPHITE_SSH_TIMEOUT}s")
        return {}
    except Exception as e:
        logger.error(f"Error querying Graphite: {e}")
        return {}

def _integrate_energy(state: EnergyState, all_devices: Dict[str, float], total_power_w: float) -> None:
    ts_now = time.time()
    if state.last_ts is not None:
        dt = max(0.0, ts_now - state.last_ts)
        kwh_inc = (total_power_w * dt) / 3600000.0
        for attr in ('day_kwh', 'week_kwh', 'month_kwh', 'year_kwh'):
            setattr(state, attr, getattr(state, attr) + kwh_inc)

        for device_key, current_power_w in all_devices.items():
            if device_key not in state.devices:
                state.devices[device_key] = DeviceEnergyState()
            device_state = state.devices[device_key]
            if device_state.last_power_w is not None:
                avg_power = (device_state.last_power_w + current_power_w) / 2.0
                device_kwh_inc = (avg_power * dt) / 3600000.0
                for attr in ('day_kwh', 'week_kwh', 'month_kwh', 'year_kwh'):
                    setattr(device_state, attr, getattr(device_state, attr) + device_kwh_inc)
            device_state.last_power_w = current_power_w
    state.last_ts = ts_now


def _build_metrics(state: EnergyState, all_devices: Dict[str, float], total_power_w: float) -> List[Tuple[str, float]]:
    base = f"{config.METRIC_PREFIX}.aggregate"
    metrics: List[Tuple[str, float]] = [
        (f"{base}.power_watts", total_power_w),
        (f"{base}.energy_kwh_daily", state.day_kwh),
        (f"{base}.energy_kwh_weekly", state.week_kwh),
        (f"{base}.energy_kwh_monthly", state.month_kwh),
        (f"{base}.energy_kwh_yearly", state.year_kwh),
    ]
    for device_key, device_state in state.devices.items():
        if device_key in all_devices:
            device_base = f"{config.METRIC_PREFIX}.{device_key}"
            metrics.extend([
                (f"{device_base}.energy_kwh_daily", device_state.day_kwh),
                (f"{device_base}.energy_kwh_weekly", device_state.week_kwh),
                (f"{device_base}.energy_kwh_monthly", device_state.month_kwh),
                (f"{device_base}.energy_kwh_yearly", device_state.year_kwh),
            ])
    return metrics


async def compute_and_send(state: EnergyState) -> Tuple[EnergyState, int]:
    apply_resets(state, local_now())

    all_devices = get_device_power_from_graphite()
    total_power_w = sum(all_devices.values())

    _integrate_energy(state, all_devices, total_power_w)
    metrics = _build_metrics(state, all_devices, total_power_w)
    sent = send_metrics(config.CARBON_SERVER, config.CARBON_PORT, metrics)

    active_devices = sum(1 for k in state.devices if k in all_devices)
    logger.info(f"Aggregate sent: power={total_power_w:.3f}W, day={state.day_kwh:.3f}kWh, week={state.week_kwh:.3f}kWh, month={state.month_kwh:.3f}kWh, year={state.year_kwh:.3f}kWh")
    logger.info(f"Per-device energy sent for {active_devices} devices")

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
    parser = argparse.ArgumentParser(description='Aggregate electricity usage across Kasa and Tuya Cloud with per-device tracking')
    parser.add_argument('--once', action='store_true', help='Run one cycle and exit')
    args = parser.parse_args()
    asyncio.run(main_loop(once=args.once))


if __name__ == '__main__':
    main()