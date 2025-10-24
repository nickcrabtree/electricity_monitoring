#!/usr/bin/env python3
"""
Tuya Cloud to Graphite Integration
Polls Tuya/SmartLife devices via Tuya IoT Cloud and sends metrics to Graphite/Carbon.

Usage:
    python tuya_cloud_to_graphite.py [--discover] [--once]

Requires tinytuya configured (tinytuya.json with apiKey/apiSecret/apiRegion/apiDeviceID).
"""

import asyncio
import time
import logging
import argparse
from typing import Dict, List, Tuple, Any, Optional

import tinytuya

import config
from graphite_helper import send_metrics, format_device_name

# Logging
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def _pick(d: Dict[str, Any], keys: List[str]):
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return None


def _normalize_voltage(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        vv = float(v)
    except Exception:
        return None
    # Many Tuya cloud voltages are in decivolts (e.g., 2350)
    if vv > 1000:
        return vv / 10.0
    return vv


def _normalize_current(a: Any) -> Optional[float]:
    if a is None:
        return None
    try:
        aa = float(a)
    except Exception:
        return None
    # Often milliamps
    if aa > 10.0:
        return aa / 1000.0
    return aa


def _normalize_power(w: Any) -> Optional[float]:
    if w is None:
        return None
    try:
        ww = float(w)
    except Exception:
        return None
    # Tuya cloud reports power in deciwatts (watts * 10)
    return ww / 10.0


async def _cloud():
    # tinytuya.Cloud() reads tinytuya.json by default
    return tinytuya.Cloud()


async def cloud_list_devices(cloud) -> List[Dict[str, Any]]:
    def _list():
        return cloud.getdevices()
    return await asyncio.to_thread(_list)


async def cloud_get_status(cloud, device_id: str) -> Dict[str, Any]:
    def _status():
        resp = cloud.getstatus(device_id)
        # tinytuya returns { 'result': [ {'code': 'switch_1', 'value': True}, ...] }
        if not resp:
            return {}
        result = resp.get('result') if isinstance(resp, dict) else resp
        status: Dict[str, Any] = {}
        if isinstance(result, list):
            for item in result:
                code = item.get('code')
                status[code] = item.get('value')
        elif isinstance(result, dict):
            # Some older responses may be dict-like
            status = result
        return status
    return await asyncio.to_thread(_status)


async def get_device_metrics(cloud, dev: Dict[str, Any]) -> List[Tuple[str, float]]:
    metrics: List[Tuple[str, float]] = []
    try:
        name = dev.get('name') or dev.get('dev_name') or dev.get('id')
        devid = dev.get('id') or dev.get('uuid')
        if not devid:
            return metrics
        status = await cloud_get_status(cloud, devid)
        if not status:
            return metrics

        device_name = format_device_name(name)
        base = f"{config.METRIC_PREFIX}.tuya.{device_name}"

        # On/off
        is_on = _pick(status, ['switch', 'switch_1', 'switch_0', 'power_switch'])
        if isinstance(is_on, bool):
            metrics.append((f"{base}.is_on", 1 if is_on else 0))

        # Power (watts)
        p = _pick(status, ['cur_power', 'power', 'power_w', 'add_ele'])
        pw = _normalize_power(p)
        if pw is not None:
            metrics.append((f"{base}.power_watts", pw))

        # Voltage (volts)
        v = _pick(status, ['cur_voltage', 'voltage', 'va_voltage'])
        vv = _normalize_voltage(v)
        if vv is not None:
            metrics.append((f"{base}.voltage_volts", vv))

        # Current (amps)
        a = _pick(status, ['cur_current', 'electric_current', 'i_current'])
        aa = _normalize_current(a)
        if aa is not None:
            metrics.append((f"{base}.current_amps", aa))

        logger.debug(f"Collected {len(metrics)} metrics from {name} ({devid})")
    except Exception as e:
        logger.error(f"Error collecting Tuya cloud metrics for {dev.get('name')} ({dev.get('id')}): {e}")
    return metrics


async def poll_devices_once(cloud, devices: List[Dict[str, Any]]) -> int:
    all_metrics: List[Tuple[str, float]] = []
    tasks = [get_device_metrics(cloud, d) for d in devices]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for res in results:
        if isinstance(res, list):
            all_metrics.extend(res)
        else:
            logger.error(f"Device polling error: {res}")

    if not all_metrics:
        logger.warning("No Tuya cloud metrics collected")
        return 0

    count = send_metrics(config.CARBON_SERVER, config.CARBON_PORT, all_metrics)
    logger.info(f"Sent {count} Tuya cloud metrics to Graphite")
    return count


async def discover_and_print():
    cloud = await _cloud()
    devices = await cloud_list_devices(cloud)
    if not devices:
        print("No Tuya devices found in cloud project. Ensure app account is linked and APIs authorized.")
        return
    print(f"\nDiscovered {len(devices)} Tuya device(s) via Cloud:\n")
    for d in devices:
        name = d.get('name') or d.get('dev_name') or d.get('id')
        print(f"Name: {name}")
        print(f"  ID: {d.get('id') or d.get('uuid')}")
        print(f"  Category: {d.get('category')}")
        print(f"  Product: {d.get('product_name') or d.get('product_id')}")
        print(f"  Online: {d.get('online')}")
        print(f"  Metric name: {format_device_name(name)}")
        # Fetch a short status snapshot to help mapping
        try:
            status = await cloud_get_status(cloud, d.get('id') or d.get('uuid'))
            if status:
                print(f"  Status keys: {', '.join(list(status.keys())[:10])}{' ...' if len(status)>10 else ''}")
        except Exception:
            pass
        print()


async def poll_once():
    cloud = await _cloud()
    devices = await cloud_list_devices(cloud)
    if not devices:
        print("No Tuya devices found in cloud project.")
        return
    print("\nPolling Tuya cloud devices...")
    count = await poll_devices_once(cloud, devices)
    print(f"\nSent {count} metrics to Graphite at {config.CARBON_SERVER}:{config.CARBON_PORT}")


async def main_loop():
    logger.info("Starting Tuya Cloud to Graphite monitoring")
    logger.info(f"Graphite server: {config.CARBON_SERVER}:{config.CARBON_PORT}")
    logger.info(f"Poll interval: {config.SMART_PLUG_POLL_INTERVAL} seconds")

    cloud = await _cloud()
    devices = await cloud_list_devices(cloud)
    if not devices:
        logger.error("No Tuya devices found in cloud project. Exiting.")
        return

    try:
        while True:
            await poll_devices_once(cloud, devices)
            # Refresh device list every 10 minutes
            if int(time.time()) % 600 < config.SMART_PLUG_POLL_INTERVAL:
                try:
                    devices = await cloud_list_devices(cloud)
                except Exception as e:
                    logger.error(f"Error refreshing cloud device list: {e}")
            await asyncio.sleep(config.SMART_PLUG_POLL_INTERVAL)
    except KeyboardInterrupt:
        logger.info("Shutting down...")


def main():
    parser = argparse.ArgumentParser(description='Tuya Cloud to Graphite Integration')
    parser.add_argument('--discover', action='store_true', help='Discover cloud devices and exit')
    parser.add_argument('--once', action='store_true', help='Poll once and exit (for testing)')
    args = parser.parse_args()

    if args.discover:
        asyncio.run(discover_and_print())
    elif args.once:
        asyncio.run(poll_once())
    else:
        asyncio.run(main_loop())


if __name__ == '__main__':
    main()
