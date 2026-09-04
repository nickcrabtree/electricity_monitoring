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
from typing import Any, Dict, List, Tuple

import config
from graphite_helper import format_device_name, send_metrics
from presence.homeassistant_api import HomeAssistantAPI

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

DEVICES_FILE = os.path.join(os.path.dirname(__file__), 'ha_bridge_devices.json')


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


def poll_once(client: HomeAssistantAPI, bridge_devices: List[Dict[str, Any]]) -> int:
    states = client.get_states()
    if not states:
        logger.warning("No states returned from Home Assistant")
        return 0
    metrics = build_metrics(states, bridge_devices)
    if not metrics:
        logger.warning("No metrics extracted from Home Assistant states")
        return 0
    count = send_metrics(config.CARBON_SERVER, config.CARBON_PORT, metrics)
    logger.info(f"Sent {count} HA-bridge Tuya metrics to Graphite")
    return count


def discover_and_print(client: HomeAssistantAPI, bridge_devices: List[Dict[str, Any]]) -> None:
    states = client.get_states()
    if not states:
        print("Could not fetch states from Home Assistant.")
        return
    metrics = build_metrics(states, bridge_devices)
    print(f"\n{len(bridge_devices)} configured bridge device(s), {len(metrics)} metric(s):\n")
    for metric_name, value in metrics:
        print(f"  {metric_name} = {value}")


def main_loop(client: HomeAssistantAPI, bridge_devices: List[Dict[str, Any]]) -> None:
    logger.info("Starting Tuya-via-Home-Assistant bridge to Graphite")
    logger.info(f"Graphite server: {config.CARBON_SERVER}:{config.CARBON_PORT}")
    logger.info(f"Poll interval: {config.SMART_PLUG_POLL_INTERVAL} seconds")
    try:
        while True:
            try:
                poll_once(client, bridge_devices)
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
        poll_once(client, bridge_devices)
    else:
        main_loop(client, bridge_devices)


if __name__ == '__main__':
    main()
