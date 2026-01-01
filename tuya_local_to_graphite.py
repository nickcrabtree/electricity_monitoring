#!/usr/bin/env python3
"""
Tuya Local LAN to Graphite Integration
Polls Tuya/SmartLife devices via local network and sends metrics to Graphite/Carbon.

Usage:
    python tuya_local_to_graphite.py [--discover] [--once]

Requires tinytuya configured (run 'python -m tinytuya wizard' first).
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
from device_names import get_device_name
from tuya_remote_scan import scan_remote_subnet

# Logging
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


_TUYA_LOCAL_STATE_FILE = os.path.join(os.path.dirname(__file__), 'tuya_local_state.json')
_TUYA_LOCAL_STATE: dict = {}
_TUYA_LOCAL_STATE_LAST_FLUSH: float = 0.0
_TUYA_LOCAL_STATE_FLUSH_INTERVAL: float = 30.0  # seconds


def _tuya_local_load_state() -> dict:
    """Best-effort load of local Tuya success state from disk."""
    try:
        if not os.path.exists(_TUYA_LOCAL_STATE_FILE):
            return {'version': 1, 'devices': {}}
        with open(_TUYA_LOCAL_STATE_FILE, 'r') as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {'version': 1, 'devices': {}}
        data.setdefault('version', 1)
        data.setdefault('devices', {})
        if not isinstance(data['devices'], dict):
            data['devices'] = {}
        return data
    except Exception:
        return {'version': 1, 'devices': {}}


def _tuya_local_save_state(state: dict) -> None:
    """Persist local Tuya success state in a small JSON file."""
    state = dict(state) if isinstance(state, dict) else {'version': 1, 'devices': {}}
    state.setdefault('version', 1)
    state.setdefault('devices', {})
    state['updated_at_ts'] = time.time()
    try:
        tmp_path = _TUYA_LOCAL_STATE_FILE + '.tmp'
        with open(tmp_path, 'w') as f:
            json.dump(state, f)
        os.replace(tmp_path, _TUYA_LOCAL_STATE_FILE)
    except Exception:
        # Best-effort only; failures here should not break polling.
        return


def _mark_local_success(device_id: str) -> None:
    """Record a successful local poll for a device.

    This state is consumed by tuya_cloud_to_graphite.py so we avoid
    wasting Tuya Cloud tokens on devices that are healthy via LAN.
    """
    global _TUYA_LOCAL_STATE, _TUYA_LOCAL_STATE_LAST_FLUSH
    now = time.time()
    if not _TUYA_LOCAL_STATE:
        _TUYA_LOCAL_STATE = _tuya_local_load_state()
    devices = _TUYA_LOCAL_STATE.setdefault('devices', {})
    if not isinstance(devices, dict):
        devices = _TUYA_LOCAL_STATE['devices'] = {}
    devices[device_id] = {'last_success_ts': now}

    # Throttle disk writes to avoid excessive wear on the Pi's storage.
    if now - _TUYA_LOCAL_STATE_LAST_FLUSH >= _TUYA_LOCAL_STATE_FLUSH_INTERVAL:
        _tuya_local_save_state(_TUYA_LOCAL_STATE)
        _TUYA_LOCAL_STATE_LAST_FLUSH = now



# Default scales for metrics (when not found in devices.json)
DEFAULT_SCALES = {
    "cur_power": 1,      # power in deciwatts (divide by 10)
    "cur_voltage": 1,    # voltage in decivolts (divide by 10)
    "cur_current": 3,    # current in milliamps (divide by 1000)
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


def normalize_value(device_id: str, dps_id: str, raw_value: Any) -> Optional[float]:
    """
    Normalize a raw value using the scale from devices.json
    
    Args:
        device_id: The device ID
        dps_id: The DPS ID (e.g., "19" for power)
        raw_value: The raw value from the device
        
    Returns:
        Normalized value or None if invalid
    """
    if raw_value is None:
        return None
    
    try:
        val = float(raw_value)
    except (TypeError, ValueError):
        logger.warning(f"Non-numeric raw value for {device_id} DPS {dps_id}: {raw_value}")
        return None
    
    # Look up scale from device scales
    scale = None
    metric_code = None
    
    if device_id in _device_scales and dps_id in _device_scales[device_id]:
        scale = _device_scales[device_id][dps_id].get('scale')
        metric_code = _device_scales[device_id][dps_id].get('code')
    
    # Fall back to default scale based on metric code
    if scale is None and metric_code in DEFAULT_SCALES:
        scale = DEFAULT_SCALES[metric_code]
        logger.debug(f"Using default scale {scale} for {device_id} DPS {dps_id} ({metric_code})")
    elif scale is None:
        # DPS-based defaults
        if dps_id in ("19",):        # power W*10
            scale = 1
        elif dps_id in ("20",):      # voltage V*10
        scale = 1
        elif dps_id in ("18",):      # current in mA
        scale = 3
        logger.debug(f"No scale found for {device_id} DPS {dps_id}, returning DPS default scale {scale}")
    
    # Apply scale: actual_value = raw_value / (10 ** scale)
    return val / (10 ** scale)


async def scan_for_devices() -> Dict[str, Dict[str, Any]]:
    """
    Scan local network and remote subnets for Tuya devices
    
    Returns:
        Dictionary mapping device ID to device info dict with 'ip', 'name', 'key', 'version'
    """
    def _scan():
        try:
            logger.info("Scanning local network for Tuya devices...")
            # deviceScan parameters vary by tinytuya version
            try:
                devices_raw = tinytuya.deviceScan(verbose=False, maxDevices=50)
            except TypeError:
                # Older version without maxDevices parameter
                devices_raw = tinytuya.deviceScan(verbose=False)
            
            # Convert to proper format: device_id -> device_info
            devices = {}
            if devices_raw:
                for ip_or_id, info in devices_raw.items():
                    # Get actual device ID (not IP)
                    device_id = info.get('id') or info.get('gwId')
                    if not device_id:
                        logger.warning(f"Device at {ip_or_id} has no ID, skipping")
                        continue
                    
                    # Store device info with friendly name
                    device_name = info.get('name', device_id)
                    devices[device_id] = {
                        'ip': info.get('ip'),
                        'name': device_name,
                        'key': info.get('key', ''),
                        'version': info.get('version', '3.3'),
                        'mac': info.get('mac', '')
                    }
                    
                    # Register friendly name for persistence
                    get_device_name(device_id, fallback_name=device_name)
                    
            return devices
        except Exception as e:
            logger.error(f"Tuya scan failed: {e}")
            return {}
    
    # Scan local network
    devices = await asyncio.to_thread(_scan)
    
    # LEGACY: Scan remote subnet only in single_host_cross_subnet mode
    local_role = getattr(config, 'LOCAL_ROLE', 'main_lan')
    if local_role == 'single_host_cross_subnet' and getattr(config, 'SSH_TUNNEL_ENABLED', False):
        try:
            ssh_host = getattr(config, 'SSH_REMOTE_HOST', 'openwrt')
            remote_subnet = getattr(config, 'SSH_TUNNEL_SUBNET', '192.168.1.0/24')
            ssh_identity = getattr(config, 'SSH_IDENTITY_FILE', None)
            use_sshpass = getattr(config, 'SSH_USE_SSHPASS', False)
            password_env_var = getattr(config, 'SSH_PASSWORD_ENV_VAR', 'OPENWRT_PASSWORD')
            
            logger.info(f"Scanning remote subnet {remote_subnet} via {ssh_host}...")
            remote_ips = await asyncio.to_thread(
                scan_remote_subnet, ssh_host, remote_subnet, ssh_identity, use_sshpass, password_env_var
            )
            
            # Remote devices found - would need proper device info to add them
            if remote_ips:
                logger.info(f"Found {len(remote_ips)} potential Tuya device(s) on {remote_subnet}")
                # Note: Without running tinytuya scan on remote network, we can't get device IDs
        except Exception as e:
            logger.warning(f"Remote subnet scan failed: {e}")
    
    logger.info(f"Discovered {len(devices)} Tuya device(s)")
    return devices


async def get_device_metrics(device: tinytuya.Device, device_id: str, retries: int = 3) -> List[Tuple[str, float]]:
    """
    Get power metrics from a Tuya device with retry logic
    
    Args:
        device: Tuya Device object
        device_id: Device ID for logging
        retries: Number of retry attempts
        
    Returns:
        List of (metric_name, value) tuples
    """
    for attempt in range(1, retries + 1):
        try:
            def _get_status():
                return device.status()
            
            # Get device status with timeout
            status = await asyncio.wait_for(
                asyncio.to_thread(_get_status),
                timeout=5
            )
            
            if not status or not isinstance(status, dict):
                logger.warning(f"{device_id}: Invalid status response")
                continue
            
            # Extract DPS values
            dps = status.get('dps', {})
            if not dps:
                logger.debug(f"{device_id}: No DPS data")
                return []
            
            metrics = []
            # Use device ID as stable identifier, get friendly name from persistence
            friendly_name = get_device_name(device_id)
            device_name = format_device_name(friendly_name)
            base = f"{config.METRIC_PREFIX}.tuya.{device_name}"
            
            # Common DPS mappings (may vary by device):
            # 1: switch (on/off)
            # 18: current (mA)
            # 19: power (W * 10)
            # 20: voltage (V * 10)
            
            # On/off state (DPS 1)
            if '1' in dps:
                is_on = 1 if dps['1'] else 0
                metrics.append((f"{base}.is_on", is_on))
            
            # Power (DPS 19 or 4 or 6)
            for dps_id in ['19', '4', '6']:
                power_raw = dps.get(dps_id)
                if power_raw is not None:
                    power = normalize_value(device_id, dps_id, power_raw)
                    if power is not None:
                        metrics.append((f"{base}.power_watts", power))
                    break
            
            # Voltage (DPS 20)
            voltage_raw = dps.get('20')
            if voltage_raw is not None:
                voltage = normalize_value(device_id, '20', voltage_raw)
                if voltage is not None:
                    metrics.append((f"{base}.voltage_volts", voltage))
            
            # Current (DPS 18)
            current_raw = dps.get('18')
            if current_raw is not None:
                current = normalize_value(device_id, '18', current_raw)
                if current is not None:
                    metrics.append((f"{base}.current_amps", current))
            
            logger.debug(f"Collected {len(metrics)} metrics from {device_id}")
            if metrics:
                _mark_local_success(device_id)
            return metrics
            
        except asyncio.TimeoutError:
            if attempt < retries:
                wait_time = min(2 ** attempt, 10)
                logger.warning(f"{device_id} timeout ({attempt}/{retries}). Retrying in {wait_time}s...")
                await asyncio.sleep(wait_time)
            else:
                logger.error(f"{device_id} failed after {retries} timeout attempts")
        except Exception as e:
            if attempt < retries:
                wait_time = min(2 ** attempt, 10)
                logger.warning(f"{device_id} error ({attempt}/{retries}): {e}. Retrying in {wait_time}s...")
                await asyncio.sleep(wait_time)
            else:
                logger.error(f"{device_id} failed after {retries} attempts: {e}")
    
    return []


async def poll_devices_once(devices: Dict[str, tinytuya.Device]) -> int:
    """
    Poll all devices once and send metrics to Graphite
    Uses asyncio.gather with return_exceptions to isolate device failures
    
    Args:
        devices: Dictionary of device_id -> Device
        
    Returns:
        Number of metrics sent
    """
    if not devices:
        logger.warning("No Tuya devices to poll")
        return 0
    
    # Poll all devices concurrently with isolated error handling
    tasks = [get_device_metrics(dev, dev_id) for dev_id, dev in devices.items()]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    all_metrics = []
    for result in results:
        if isinstance(result, Exception):
            logger.error(f"Device polling task error: {result}")
        elif isinstance(result, list):
            all_metrics.extend(result)
    
    if not all_metrics:
        logger.warning("No Tuya metrics collected")
        return 0
    
    # Send all metrics to Graphite
    try:
        count = send_metrics(config.CARBON_SERVER, config.CARBON_PORT, all_metrics)
        logger.info(f"Sent {count} Tuya metrics to Graphite")
        return count
    except Exception as e:
        logger.error(f"Failed to send metrics to Graphite: {e}")
        return 0


async def discover_and_print():
    """Discover devices and print information"""
    devices = await scan_for_devices()
    
    if not devices:
        print("\nNo Tuya devices found on local network.")
        print("Make sure devices are on the same network and powered on.")
        print("You may need to run 'python -m tinytuya wizard' first.")
        return
    
    print(f"\nFound {len(devices)} Tuya device(s):\n")
    
    for dev_id, dev_info in devices.items():
        print(f"Device ID: {dev_id}")
        print(f"  Name: {dev_info.get('name', 'unknown')}")
        print(f"  IP: {dev_info.get('ip', 'unknown')}")
        print(f"  Version: {dev_info.get('version', 'unknown')}")
        print(f"  Metric name: {format_device_name(dev_info.get('name', dev_id))}")
        print()


async def poll_once():
    """Poll devices once and print results (for testing)"""
    # Load device scales
    global _device_scales, _devices_json_mtime
    _device_scales = load_device_scales()
    devices_json_path = os.path.join(os.path.dirname(__file__), "devices.json")
    if os.path.exists(devices_json_path):
        _devices_json_mtime = os.path.getmtime(devices_json_path)
    
    devices_info = await scan_for_devices()
    
    if not devices_info:
        print("No Tuya devices found.")
        return
    
    # Create Device objects from scan results
    devices = {}
    for dev_id, dev_info in devices_info.items():
        try:
            dev = tinytuya.Device(
                dev_id=dev_id,
                address=dev_info.get('ip'),
                local_key=dev_info.get('key', ''),
                version=dev_info.get('version', '3.3')
            )
            devices[dev_id] = dev
        except Exception as e:
            logger.warning(f"Could not create device {dev_id}: {e}")
    
    print("\nPolling Tuya devices...")
    count = await poll_devices_once(devices)
    print(f"\nSent {count} metrics to Graphite at {config.CARBON_SERVER}:{config.CARBON_PORT}")


async def main_loop():
    """
    Main monitoring loop - scan for devices and poll continuously
    Robust: continues running even if scan or polling fails
    """
    logger.info("Starting Tuya Local LAN to Graphite monitoring")
    logger.info(f"Graphite server: {config.CARBON_SERVER}:{config.CARBON_PORT}")
    logger.info(f"Poll interval: {config.SMART_PLUG_POLL_INTERVAL} seconds")
    
    # Load device scales from devices.json
    global _device_scales, _devices_json_mtime
    _device_scales = load_device_scales()
    devices_json_path = os.path.join(os.path.dirname(__file__), "devices.json")
    if os.path.exists(devices_json_path):
        _devices_json_mtime = os.path.getmtime(devices_json_path)
    
    # Initial scan
    devices_info = await scan_for_devices()
    devices = {}
    
    for dev_id, dev_info in devices_info.items():
        try:
            dev = tinytuya.Device(
                dev_id=dev_id,
                address=dev_info.get('ip'),
                local_key=dev_info.get('key', ''),
                version=dev_info.get('version', '3.3')
            )
            devices[dev_id] = dev
        except Exception as e:
            logger.warning(f"Could not create device {dev_id}: {e}")
    
    if not devices:
        logger.warning("No Tuya devices found initially. Will retry scan in main loop...")
    
    # Main loop - never exit except on KeyboardInterrupt
    last_scan = time.time()
    scan_interval = getattr(config, "TUYA_REDISCOVERY_INTERVAL", 180)  # Re-scan every N minutes
    failed_polls = 0  # Track consecutive failed polls
    
    try:
        while True:
            try:
                # Reload device scales if devices.json has changed
                reload_device_scales_if_changed()
                
                # Poll devices if we have any
                if devices:
                    metrics_sent = await poll_devices_once(devices)
                    if metrics_sent == 0:
                        failed_polls += 1
                        # If we haven't sent metrics in 3 polls, try re-scanning
                        if failed_polls >= 3:
                            logger.warning(f"No metrics sent for {failed_polls} polls - triggering re-scan")
                            devices_info = await scan_for_devices()
                            
                            # Update device list
                            new_devices = {}
                            for dev_id, dev_info in devices_info.items():
                                try:
                                    dev = tinytuya.Device(
                                        dev_id=dev_id,
                                        address=dev_info.get('ip'),
                                        local_key=dev_info.get('key', ''),
                                        version=dev_info.get('version', '3.3')
                                    )
                                    new_devices[dev_id] = dev
                                except Exception as e:
                                    logger.warning(f"Could not create device {dev_id}: {e}")
                            
                            if new_devices:
                                devices = new_devices
                                logger.info(f"Updated device list after failed polls: {len(devices)} devices")
                            
                            failed_polls = 0
                            last_scan = time.time()
                    else:
                        failed_polls = 0  # Reset counter on successful poll
                else:
                    logger.warning("No Tuya devices available to poll")
                
                # Re-scan periodically
                if time.time() - last_scan >= scan_interval:
                    logger.info("Re-scanning for Tuya devices (periodic scan)...")
                    devices_info = await scan_for_devices()
                    
                    # Update device list
                    new_devices = {}
                    for dev_id, dev_info in devices_info.items():
                        try:
                            dev = tinytuya.Device(
                                dev_id=dev_id,
                                address=dev_info.get('ip'),
                                local_key=dev_info.get('key', ''),
                                version=dev_info.get('version', '3.3')
                            )
                            new_devices[dev_id] = dev
                        except Exception as e:
                            logger.warning(f"Could not create device {dev_id}: {e}")
                    
                    if new_devices:
                        devices = new_devices
                        logger.info(f"Updated device list: {len(devices)} devices")
                    
                    failed_polls = 0
                    last_scan = time.time()
                
            except Exception as e:
                logger.error(f"Error in main loop iteration: {e}", exc_info=True)
            
            # Sleep until next poll
            await asyncio.sleep(config.SMART_PLUG_POLL_INTERVAL)
            
    except KeyboardInterrupt:
        logger.info("Shutting down...")


def main():
    parser = argparse.ArgumentParser(description='Tuya Local LAN to Graphite Integration')
    parser.add_argument('--discover', action='store_true', help='Discover devices and exit')
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
