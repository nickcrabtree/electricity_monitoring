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


def _normalize_voltage(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        vv = float(v)
    except Exception:
        return None
    # Many Tuya devices report in decivolts
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
    # Some devices report in deciwatts
    if ww > 1000:
        return ww / 10.0
    return ww


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
    
    # Scan remote subnet if configured
    if getattr(config, 'SSH_TUNNEL_ENABLED', False):
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
            power_raw = dps.get('19') or dps.get('4') or dps.get('6')
            if power_raw is not None:
                power = _normalize_power(power_raw)
                if power is not None:
                    metrics.append((f"{base}.power_watts", power))
            
            # Voltage (DPS 20)
            voltage_raw = dps.get('20')
            if voltage_raw is not None:
                voltage = _normalize_voltage(voltage_raw)
                if voltage is not None:
                    metrics.append((f"{base}.voltage_volts", voltage))
            
            # Current (DPS 18)
            current_raw = dps.get('18')
            if current_raw is not None:
                current = _normalize_current(current_raw)
                if current is not None:
                    metrics.append((f"{base}.current_amps", current))
            
            logger.debug(f"Collected {len(metrics)} metrics from {device_id}")
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
    scan_interval = 180  # Re-scan every 3 minutes for faster new device detection
    failed_polls = 0  # Track consecutive failed polls
    
    try:
        while True:
            try:
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
