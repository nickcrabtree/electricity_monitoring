#!/usr/bin/env python3
"""
Bridge Tuya devices to Graphite via Home Assistant's own Tuya cloud integration.

Some Tuya devices aren't reachable via the local LAN protocol (no local_key
available, e.g. because the Tuya Cloud IoT Core subscription used by
tinytuya's wizard has lapsed). Home Assistant's built-in Tuya integration
uses a separate cloud login and may already expose these devices as
entities. This script reads those entity states and forwards them to
Graphite under the same home.electricity.tuya.<device>.* namespace used by
tuya_local_to_graphite.py.

Device list and entity mappings come from ha_bridge_devices.json.

Usage:
    python tuya_ha_bridge_to_graphite.py [--discover] [--once]

Requires HA_TOKEN (Home Assistant long-lived access token) in the
environment, and optionally HA_URL (default: http://homeassistant.local:8123).
"""

import argparse
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple

import config
from graphite_helper import format_device_name, send_metrics
from presence.homeassistant_api import HomeAssistantAPI

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

DEVICES_FILE = os.path.join(os.path.dirname(__file__), 'ha_bridge_devices.json')
STATE_FILE = os.path.join(os.path.dirname(__file__), 'ha_bridge_state.json')

# Written by tuya_local_to_graphite.py (and read the same way by
# tuya_cloud_to_graphite.py) to record per-device local-poll successes, so
# other pollers can avoid duplicating a device that's already healthy via
# local LAN. Same TTL formula as tuya_cloud_to_graphite.py.
LOCAL_STATE_FILE = os.path.join(os.path.dirname(__file__), 'tuya_local_state.json')
LOCAL_SUCCESS_TTL_SECONDS = 10 * getattr(config, 'SMART_PLUG_POLL_INTERVAL', 30)


def load_bridge_devices(path: str = DEVICES_FILE) -> List[Dict[str, Any]]:
    """Load the list of bridged devices and their HA entity mappings."""
    try:
        with open(path, 'r') as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.error(f"Failed to load {path}: {e}")
        return []


def build_metrics(states: List[Dict[str, Any]], bridge_devices: List[Dict[str, Any]]) -> List[Tuple[str, float]]:
    """Turn Home Assistant states + device config into (metric_name, value) pairs."""
    state_by_id = {s.get('entity_id'): s for s in states or []}
    metrics: List[Tuple[str, float]] = []

    for device in bridge_devices:
        name = device.get('name')
        if not name:
            logger.warning(f"Skipping bridge device with no 'name': {device}")
            continue
        base = f"{config.METRIC_PREFIX}.tuya.{format_device_name(name)}"

        switch_entity = device.get('switch_entity')
        if switch_entity:
            state_info = state_by_id.get(switch_entity)
            if state_info and state_info.get('state') in ('on', 'off'):
                metrics.append((f"{base}.is_on", 1 if state_info['state'] == 'on' else 0))

        for suffix, entity_id in device.get('sensors', {}).items():
            state_info = state_by_id.get(entity_id)
            if not state_info:
                continue
            try:
                value = float(state_info.get('state'))
            except (TypeError, ValueError):
                continue
            metrics.append((f"{base}.{suffix}", value))

    return metrics


def load_state(path: str = STATE_FILE) -> Dict[str, Dict[str, float]]:
    """Best-effort load of previous total_kwh readings, keyed by metric base path."""
    try:
        with open(path, 'r') as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def save_state(state: Dict[str, Dict[str, float]], path: str = STATE_FILE) -> None:
    """Best-effort persist of state; failures here should not break polling."""
    try:
        tmp_path = path + '.tmp'
        with open(tmp_path, 'w') as f:
            json.dump(state, f)
        os.replace(tmp_path, path)
    except OSError as e:
        logger.warning(f"Failed to save state to {path}: {e}")


def derive_power_from_energy(prev_kwh: float, prev_ts: float, curr_kwh: float, curr_ts: float) -> Optional[float]:
    """Estimate average power (W) from the change in cumulative energy (kWh)
    between two samples. Returns None if the samples can't yield a rate
    (no/negative elapsed time, or a counter reset where curr < prev)."""
    elapsed_hours = (curr_ts - prev_ts) / 3600.0
    if elapsed_hours <= 0:
        return None
    delta_kwh = curr_kwh - prev_kwh
    if delta_kwh < 0:
        return None
    return (delta_kwh / elapsed_hours) * 1000.0


def add_derived_power(
    metrics: List[Tuple[str, float]], state: Dict[str, Dict[str, float]], now_ts: float
) -> List[Tuple[str, float]]:
    """For devices with a total_kwh metric but no direct power_watts reading,
    derive an approximate power_watts from the change in total_kwh since the
    last poll. Updates state in place with the latest reading for next time.
    """
    by_name = dict(metrics)
    derived: List[Tuple[str, float]] = []

    for metric_name, value in metrics:
        if not metric_name.endswith('.total_kwh'):
            continue
        base = metric_name[: -len('.total_kwh')]
        if f"{base}.power_watts" in by_name:
            continue  # a real sensor reading already covers this device

        prev = state.get(base)
        if prev is not None:
            power = derive_power_from_energy(prev['total_kwh'], prev['ts'], value, now_ts)
            if power is not None:
                derived.append((f"{base}.power_watts", power))

        state[base] = {'total_kwh': value, 'ts': now_ts}

    return metrics + derived


def load_recent_local_successes(
    path: str = LOCAL_STATE_FILE, now: Optional[float] = None, ttl_seconds: float = LOCAL_SUCCESS_TTL_SECONDS
) -> Dict[str, float]:
    """Load device IDs with a recent successful local (LAN) poll.

    Devices covered by tuya_local_to_graphite.py don't need the HA-bridge
    fallback; entries older than ttl_seconds are treated as stale (local
    polling has stopped working for that device) and excluded.
    """
    if now is None:
        now = time.time()
    try:
        with open(path, 'r') as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}

    if not isinstance(data, dict):
        return {}
    devices = data.get('devices', {})
    if not isinstance(devices, dict):
        return {}

    recent = {}
    for device_id, info in devices.items():
        if not isinstance(info, dict):
            continue
        ts = info.get('last_success_ts')
        if isinstance(ts, (int, float)) and ts >= now - ttl_seconds:
            recent[str(device_id)] = float(ts)
    return recent


def filter_devices_needing_fallback(
    bridge_devices: List[Dict[str, Any]], recent_local_successes: Dict[str, float]
) -> List[Dict[str, Any]]:
    """Keep only devices NOT already covered by a recent local-LAN poll.

    A device with no 'tuya_device_id' configured can't be checked against
    local coverage, so it always falls back to HA (it has no other source).
    """
    result = []
    for device in bridge_devices:
        device_id = device.get('tuya_device_id')
        if device_id and device_id in recent_local_successes:
            continue
        result.append(device)
    return result


def poll_once(
    client: HomeAssistantAPI, bridge_devices: List[Dict[str, Any]], state: Optional[Dict[str, Dict[str, float]]] = None
) -> int:
    recent_local = load_recent_local_successes()
    devices_to_poll = filter_devices_needing_fallback(bridge_devices, recent_local)
    if not devices_to_poll:
        logger.debug("All bridge devices are currently covered by local LAN polling - nothing to do")
        return 0

    states = client.get_states()
    if not states:
        logger.warning("No states returned from Home Assistant")
        return 0
    metrics = build_metrics(states, devices_to_poll)
    if not metrics:
        logger.warning("No metrics extracted from Home Assistant states")
        return 0
    if state is not None:
        metrics = add_derived_power(metrics, state, time.time())
        save_state(state)
    count = send_metrics(config.CARBON_SERVER, config.CARBON_PORT, metrics)
    logger.info(f"Sent {count} HA-bridge Tuya metrics to Graphite")
    return count


def discover_and_print(client: HomeAssistantAPI, bridge_devices: List[Dict[str, Any]]) -> None:
    recent_local = load_recent_local_successes()
    devices_to_poll = filter_devices_needing_fallback(bridge_devices, recent_local)
    covered_locally = [d for d in bridge_devices if d not in devices_to_poll]
    if covered_locally:
        print(f"\n{len(covered_locally)} device(s) currently covered by local LAN polling (not bridged):")
        for d in covered_locally:
            print(f"  {d.get('name')}")

    states = client.get_states()
    if not states:
        print("Could not fetch states from Home Assistant.")
        return
    metrics = build_metrics(states, devices_to_poll)
    print(f"\n{len(devices_to_poll)} device(s) needing the HA fallback, {len(metrics)} metric(s):\n")
    for metric_name, value in metrics:
        print(f"  {metric_name} = {value}")


def main_loop(client: HomeAssistantAPI, bridge_devices: List[Dict[str, Any]]) -> None:
    logger.info("Starting Tuya-via-Home-Assistant bridge to Graphite")
    logger.info(f"Graphite server: {config.CARBON_SERVER}:{config.CARBON_PORT}")
    logger.info(f"Poll interval: {config.SMART_PLUG_POLL_INTERVAL} seconds")
    state = load_state()
    try:
        while True:
            try:
                poll_once(client, bridge_devices, state)
            except Exception as e:
                logger.error(f"Error in main loop iteration: {e}", exc_info=True)
            time.sleep(config.SMART_PLUG_POLL_INTERVAL)
    except KeyboardInterrupt:
        logger.info("Shutting down...")


def main():
    parser = argparse.ArgumentParser(description='Tuya-via-Home-Assistant bridge to Graphite')
    parser.add_argument('--discover', action='store_true', help='Fetch current values and print, then exit')
    parser.add_argument('--once', action='store_true', help='Poll once and exit')
    args = parser.parse_args()

    bridge_devices = load_bridge_devices()
    client = HomeAssistantAPI(base_url=os.getenv('HA_URL', 'http://homeassistant.local:8123'))

    if args.discover:
        discover_and_print(client, bridge_devices)
    elif args.once:
        poll_once(client, bridge_devices, load_state())
    else:
        main_loop(client, bridge_devices)


if __name__ == '__main__':
    main()
