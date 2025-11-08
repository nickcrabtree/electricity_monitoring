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
import json
import os
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


# Default scales for metrics (when not found in devices.json)
DEFAULT_SCALES = {
    "cur_power": 1,      # power in deciwatts (divide by 10)
    "cur_voltage": 1,    # voltage in decivolts (divide by 10)
    "cur_current": 3,    # current in milliamps (divide by 1000)
}

# Map cloud API metric codes to DPS IDs for scale lookup
METRIC_CODE_TO_DPS = {
    "cur_power": "19",
    "power": "19",
    "power_w": "19",
    "add_ele": "19",
    "cur_voltage": "20",
    "voltage": "20",
    "va_voltage": "20",
    "cur_current": "18",
    "electric_current": "18",
    "i_current": "18",
}

# Global variable to cache device scales
_device_scales = {}
_devices_json_mtime = 0


def load_device_scales() -> Dict[str, Dict[str, Dict[str, int]]]:
    """
    Load device scale information from devices.json
    
    Returns:
        Dict mapping device_id -> dps_id -> metric_code -> scale
        Example: {"device123": {"19": {"code": "cur_power", "scale": 1}}}
    """
    devices_json_path = os.path.join(os.path.dirname(__file__), "devices.json")
    
    if not os.path.exists(devices_json_path):
        logger.warning(f"devices.json not found at {devices_json_path}, using default scales")
        return {}
    
    try:
        with open(devices_json_path, 'r') as f:
            devices = json.load(f)
        
        scales_by_device = {}
        for device in devices:
            device_id = device.get('id')
            if not device_id:
                continue
            
            mapping = device.get('mapping', {})
            if not isinstance(mapping, dict):
                continue
            
            scales_by_device[device_id] = {}
            for dps_id, dps_info in mapping.items():
                if not isinstance(dps_info, dict):
                    continue
                
                code = dps_info.get('code')
                values = dps_info.get('values', {})
                if isinstance(values, dict) and 'scale' in values:
                    scale = values['scale']
                    scales_by_device[device_id][dps_id] = {
                        'code': code,
                        'scale': int(scale)
                    }
        
        logger.info(f"Loaded scale information for {len(scales_by_device)} devices")
        return scales_by_device
        
    except Exception as e:
        logger.error(f"Error loading devices.json: {e}")
        return {}


def reload_device_scales_if_changed():
    """Check if devices.json has been modified and reload if necessary"""
    global _device_scales, _devices_json_mtime
    
    devices_json_path = os.path.join(os.path.dirname(__file__), "devices.json")
    if not os.path.exists(devices_json_path):
        return
    
    try:
        current_mtime = os.path.getmtime(devices_json_path)
        if current_mtime != _devices_json_mtime:
            logger.info("devices.json modified, reloading scale information")
            _device_scales = load_device_scales()
            _devices_json_mtime = current_mtime
    except Exception as e:
        logger.warning(f"Error checking devices.json modification time: {e}")


def normalize_value(device_id: str, metric_code: str, raw_value: Any) -> Optional[float]:
    """
    Normalize a raw value using the scale from devices.json
    
    Args:
        device_id: The device ID
        metric_code: The metric code (e.g., "cur_power", "cur_voltage")
        raw_value: The raw value from the device
        
    Returns:
        Normalized value or None if invalid
    """
    if raw_value is None:
        return None
    
    try:
        val = float(raw_value)
    except (TypeError, ValueError):
        logger.warning(f"Non-numeric raw value for {device_id} {metric_code}: {raw_value}")
        return None
    
    # Map metric code to DPS ID for scale lookup
    dps_id = METRIC_CODE_TO_DPS.get(metric_code)
    
    # Look up scale from device scales
    scale = None
    
    if dps_id and device_id in _device_scales and dps_id in _device_scales[device_id]:
        scale = _device_scales[device_id][dps_id].get('scale')
    
    # Fall back to default scale based on metric code
    if scale is None:
        # Try to find default by checking if metric_code matches a default key
        for default_key in DEFAULT_SCALES:
            if default_key in metric_code:
                scale = DEFAULT_SCALES[default_key]
                logger.debug(f"Using default scale {scale} for {device_id} {metric_code}")
                break
    
    if scale is None:
        # No scale found, return value as-is
        logger.debug(f"No scale found for {device_id} {metric_code}, returning raw value")
        return val
    
    # Apply scale: actual_value = raw_value / (10 ** scale)
    return val / (10 ** scale)


async def _cloud():
    # tinytuya.Cloud() reads tinytuya.json by default
    return tinytuya.Cloud()


async def cloud_list_devices(cloud) -> List[Dict[str, Any]]:
    """
    Get list of devices from Tuya cloud with defensive parsing
    Handles both list-of-dicts and error responses
    """
    def _list():
        try:
            result = cloud.getdevices()
            
            # Handle string response (error or JSON)
            if isinstance(result, str):
                try:
                    result = json.loads(result)
                except json.JSONDecodeError:
                    logger.error(f"Device list is non-JSON string: {repr(result)[:200]}")
                    return []
            
            # Handle dict response (might have 'result' field with device list)
            if isinstance(result, dict):
                # Check for Tuya Cloud error responses
                if 'Error' in result or 'Err' in result:
                    err_msg = result.get('Payload') or result.get('Error') or str(result)
                    logger.error(f"Tuya Cloud API error: {err_msg}")
                    return []
                # Check if it's an error response
                if 'success' in result and not result['success']:
                    logger.error(f"Device list error: {result.get('msg', 'unknown')}")
                    return []
                # Try to extract device list from 'result' field
                if 'result' in result:
                    result = result['result']
                else:
                    # Entire dict might be a single device
                    logger.warning(f"Device list is dict, not list. Treating as single device.")
                    return [result]
            
            # Now expect a list
            if not isinstance(result, list):
                logger.error(f"Device list unexpected type after parsing: {type(result)}")
                return []
            
            # Filter out non-dict items
            devices = []
            for item in result:
                if isinstance(item, dict):
                    devices.append(item)
                elif isinstance(item, str):
                    # Try to parse as JSON
                    try:
                        parsed = json.loads(item)
                        if isinstance(parsed, dict):
                            devices.append(parsed)
                        else:
                            logger.warning(f"Device item parsed but not a dict: {type(parsed)}")
                    except json.JSONDecodeError:
                        logger.warning(f"Device item is unparseable string: {repr(item)[:100]}")
                else:
                    logger.warning(f"Device item unexpected type: {type(item)}")
            
            return devices
            
        except Exception as e:
            logger.error(f"Error getting device list: {e}", exc_info=True)
            return []
    
    return await asyncio.to_thread(_list)


async def cloud_get_status(cloud, device_id: str) -> Dict[str, Any]:
    """Get device status from Tuya cloud with defensive parsing"""
    def _status():
        try:
            resp = cloud.getstatus(device_id)
            return normalize_tuya_response(resp, device_id)
        except Exception as e:
            logger.error(f"Cloud API error for {device_id}: {e}")
            return {}
    return await asyncio.to_thread(_status)


def normalize_tuya_response(resp: Any, device_id: str) -> Dict[str, Any]:
    """
    Normalize Tuya cloud API response which can be dict, list, or stringified JSON
    
    Args:
        resp: Raw response from tinytuya
        device_id: Device ID for logging
        
    Returns:
        Dictionary with normalized status keys
    """
    if not resp:
        return {}
    
    # Handle stringified JSON responses
    if isinstance(resp, str):
        try:
            resp = json.loads(resp)
        except json.JSONDecodeError:
            logger.error(f"{device_id}: Response is non-JSON string: {repr(resp)[:200]}")
            return {}
    
    # Extract result field if present
    if isinstance(resp, dict):
        # Check for error response
        if 'success' in resp and not resp['success']:
            logger.warning(f"{device_id}: API returned error: {resp.get('msg', 'unknown')}")
            return {}
        
        result = resp.get('result')
        if result is None:
            # Response might already be the status dict
            result = resp
    else:
        result = resp
    
    # Handle stringified result
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except json.JSONDecodeError:
            logger.error(f"{device_id}: Result is non-JSON string: {repr(result)[:200]}")
            return {}
    
    # Parse result based on type
    status: Dict[str, Any] = {}
    
    if isinstance(result, list):
        # List of {'code': ..., 'value': ...} dicts
        for item in result:
            if isinstance(item, dict) and 'code' in item:
                status[item['code']] = item.get('value')
            else:
                logger.debug(f"{device_id}: Unexpected list item type: {type(item)}")
    
    elif isinstance(result, dict):
        # Already a status dictionary
        status = result
    
    else:
        logger.error(f"{device_id}: Unexpected result type {type(result)}: {repr(result)[:500]}")
        return {}
    
    return status


async def get_device_metrics(cloud, dev: Any) -> List[Tuple[str, float]]:
    """
    Extract metrics from a Tuya cloud device with defensive error handling
    
    Args:
        cloud: Tuya cloud instance
        dev: Device info (should be dict, but handle gracefully if not)
        
    Returns:
        List of (metric_name, value) tuples
    """
    metrics: List[Tuple[str, float]] = []
    
    # Defensive check: ensure dev is a dict
    if not isinstance(dev, dict):
        logger.error(f"Device is not a dict: {type(dev)} - {repr(dev)[:200]}")
        return metrics
    
    name = dev.get('name') or dev.get('dev_name') or dev.get('id', 'unknown')
    devid = dev.get('id') or dev.get('uuid')
    
    try:
        if not devid:
            logger.warning(f"Device {name} has no ID, skipping")
            return metrics
        
        status = await cloud_get_status(cloud, devid)
        
        if not status:
            logger.debug(f"No status data for {name} ({devid})")
            return metrics
        
        if not isinstance(status, dict):
            logger.error(f"{name}: Status is not a dict: {type(status)}")
            return metrics

        device_name = format_device_name(name)
        base = f"{config.METRIC_PREFIX}.tuya.{device_name}"

        # On/off state
        is_on = _pick(status, ['switch', 'switch_1', 'switch_0', 'power_switch'])
        if isinstance(is_on, bool):
            metrics.append((f"{base}.is_on", 1 if is_on else 0))

        # Power (watts)
        power_keys = ['cur_power', 'power', 'power_w', 'add_ele']
        p = _pick(status, power_keys)
        if p is not None:
            # Find which key was matched
            metric_code = None
            for key in power_keys:
                if key in status and status[key] == p:
                    metric_code = key
                    break
            if metric_code:
                pw = normalize_value(devid, metric_code, p)
                if pw is not None:
                    metrics.append((f"{base}.power_watts", pw))

        # Voltage (volts)
        voltage_keys = ['cur_voltage', 'voltage', 'va_voltage']
        v = _pick(status, voltage_keys)
        if v is not None:
            # Find which key was matched
            metric_code = None
            for key in voltage_keys:
                if key in status and status[key] == v:
                    metric_code = key
                    break
            if metric_code:
                vv = normalize_value(devid, metric_code, v)
                if vv is not None:
                    metrics.append((f"{base}.voltage_volts", vv))

        # Current (amps)
        current_keys = ['cur_current', 'electric_current', 'i_current']
        a = _pick(status, current_keys)
        if a is not None:
            # Find which key was matched
            metric_code = None
            for key in current_keys:
                if key in status and status[key] == a:
                    metric_code = key
                    break
            if metric_code:
                aa = normalize_value(devid, metric_code, a)
                if aa is not None:
                    metrics.append((f"{base}.current_amps", aa))

        logger.debug(f"Collected {len(metrics)} metrics from {name} ({devid})")
        
    except Exception as e:
        logger.error(f"Error collecting metrics for {name} ({devid}): {e}", exc_info=True)
    
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
    # Load device scales
    global _device_scales, _devices_json_mtime
    _device_scales = load_device_scales()
    devices_json_path = os.path.join(os.path.dirname(__file__), "devices.json")
    if os.path.exists(devices_json_path):
        _devices_json_mtime = os.path.getmtime(devices_json_path)
    
    cloud = await _cloud()
    devices = await cloud_list_devices(cloud)
    if not devices:
        print("No Tuya devices found in cloud project.")
        return
    print("\nPolling Tuya cloud devices...")
    count = await poll_devices_once(cloud, devices)
    print(f"\nSent {count} metrics to Graphite at {config.CARBON_SERVER}:{config.CARBON_PORT}")


async def main_loop():
    """
    Main monitoring loop - poll Tuya cloud devices continuously
    Robust: continues running even if API calls fail
    """
    logger.info("Starting Tuya Cloud to Graphite monitoring")
    logger.info(f"Graphite server: {config.CARBON_SERVER}:{config.CARBON_PORT}")
    logger.info(f"Poll interval: {config.SMART_PLUG_POLL_INTERVAL} seconds")

    # Load device scales from devices.json
    global _device_scales, _devices_json_mtime
    _device_scales = load_device_scales()
    devices_json_path = os.path.join(os.path.dirname(__file__), "devices.json")
    if os.path.exists(devices_json_path):
        _devices_json_mtime = os.path.getmtime(devices_json_path)

    cloud = await _cloud()
    devices = await cloud_list_devices(cloud)
    
    if not devices:
        logger.warning("No Tuya devices found in cloud project initially. Will retry...")
    
    last_discovery = time.time()
    discovery_interval = 600  # Refresh device list every 10 minutes

    try:
        while True:
            try:
                # Reload device scales if devices.json has changed
                reload_device_scales_if_changed()
                
                # Poll devices if we have any
                if devices:
                    await poll_devices_once(cloud, devices)
                else:
                    logger.warning("No Tuya devices to poll")
                
                # Refresh device list periodically
                if time.time() - last_discovery >= discovery_interval:
                    try:
                        logger.info("Refreshing Tuya cloud device list...")
                        new_devices = await cloud_list_devices(cloud)
                        if new_devices:
                            devices = new_devices
                            logger.info(f"Refreshed device list: {len(devices)} devices")
                        last_discovery = time.time()
                    except Exception as e:
                        logger.error(f"Error refreshing cloud device list: {e}")
                
            except Exception as e:
                logger.error(f"Error in main loop iteration: {e}", exc_info=True)
            
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
