#!/usr/bin/env python3
"""
Kasa Smart Plug to Graphite Integration
Polls TP-Link Kasa smart plugs for power consumption and sends to Graphite/Carbon

Usage:
    python kasa_to_graphite.py [--discover] [--once]
    
Options:
    --discover: Discover Kasa devices on network and exit
    --once: Poll once and exit (for testing)
"""

import asyncio
import sys
import time
import logging
import argparse
from typing import Dict, List, Tuple

try:
    from kasa import Discover, SmartPlug, SmartDevice
except ImportError:
    print("Error: python-kasa not installed. Run: pip install python-kasa")
    sys.exit(1)

import config
from graphite_helper import send_metrics, format_device_name

# Set up logging
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def discover_devices() -> Dict[str, SmartDevice]:
    """
    Discover Kasa devices on the network
    
    Returns:
        Dictionary mapping device IP to SmartDevice object
    """
    logger.info("Discovering Kasa devices on network...")
    devices = await Discover.discover()
    
    if not devices:
        logger.warning("No Kasa devices found on network")
        return {}
    
    logger.info(f"Found {len(devices)} Kasa device(s):")
    for ip, dev in devices.items():
        await dev.update()
        logger.info(f"  {ip}: {dev.alias} ({dev.model})")
    
    return devices


async def get_device_metrics(device: SmartDevice) -> List[Tuple[str, float]]:
    """
    Get power metrics from a Kasa device
    
    Args:
        device: SmartDevice object
    
    Returns:
        List of (metric_name, value) tuples
    """
    metrics = []
    
    try:
        await device.update()
        
        # Format device name for metric path
        device_name = format_device_name(device.alias)
        base_metric = f"{config.METRIC_PREFIX}.kasa.{device_name}"
        
        # Check if device has emeter (power monitoring)
        if not device.has_emeter:
            logger.debug(f"Device {device.alias} does not have power monitoring")
            return metrics
        
        # Get realtime emeter data
        emeter_data = await device.get_emeter_realtime()
        
        # Extract metrics
        if 'power_mw' in emeter_data:
            # Power in milliwatts, convert to watts
            power_w = emeter_data['power_mw'] / 1000.0
            metrics.append((f"{base_metric}.power_watts", power_w))
        elif 'power' in emeter_data:
            # Already in watts
            power_w = emeter_data['power']
            metrics.append((f"{base_metric}.power_watts", power_w))
        
        if 'voltage_mv' in emeter_data:
            # Voltage in millivolts, convert to volts
            voltage_v = emeter_data['voltage_mv'] / 1000.0
            metrics.append((f"{base_metric}.voltage_volts", voltage_v))
        elif 'voltage' in emeter_data:
            voltage_v = emeter_data['voltage']
            metrics.append((f"{base_metric}.voltage_volts", voltage_v))
        
        if 'current_ma' in emeter_data:
            # Current in milliamps, convert to amps
            current_a = emeter_data['current_ma'] / 1000.0
            metrics.append((f"{base_metric}.current_amps", current_a))
        elif 'current' in emeter_data:
            current_a = emeter_data['current']
            metrics.append((f"{base_metric}.current_amps", current_a))
        
        # Device state (on/off as 1/0)
        is_on = 1 if device.is_on else 0
        metrics.append((f"{base_metric}.is_on", is_on))
        
        logger.debug(f"Collected {len(metrics)} metrics from {device.alias}")
        
    except Exception as e:
        logger.error(f"Error getting metrics from {device.alias}: {e}")
    
    return metrics


async def poll_devices_once(devices: Dict[str, SmartDevice]) -> int:
    """
    Poll all devices once and send metrics to Graphite
    
    Args:
        devices: Dictionary of IP -> SmartDevice
    
    Returns:
        Number of metrics sent
    """
    all_metrics = []
    
    for ip, device in devices.items():
        try:
            device_metrics = await get_device_metrics(device)
            all_metrics.extend(device_metrics)
        except Exception as e:
            logger.error(f"Error polling device at {ip}: {e}")
    
    if not all_metrics:
        logger.warning("No metrics collected from any device")
        return 0
    
    # Send all metrics to Graphite
    count = send_metrics(config.CARBON_SERVER, config.CARBON_PORT, all_metrics)
    logger.info(f"Sent {count} metrics to Graphite")
    
    return count


async def main_loop():
    """
    Main monitoring loop - discover devices and poll continuously
    """
    logger.info("Starting Kasa to Graphite monitoring")
    logger.info(f"Graphite server: {config.CARBON_SERVER}:{config.CARBON_PORT}")
    logger.info(f"Poll interval: {config.SMART_PLUG_POLL_INTERVAL} seconds")
    
    # Discover devices initially
    devices = await discover_devices()
    
    if not devices:
        logger.error("No devices found. Exiting.")
        return
    
    # Main loop
    try:
        while True:
            await poll_devices_once(devices)
            
            # Re-discover devices periodically (every 10 minutes)
            # This helps catch new devices or devices that came back online
            if int(time.time()) % 600 < config.SMART_PLUG_POLL_INTERVAL:
                logger.info("Re-discovering devices...")
                new_devices = await discover_devices()
                if new_devices:
                    devices = new_devices
            
            # Sleep until next poll
            await asyncio.sleep(config.SMART_PLUG_POLL_INTERVAL)
            
    except KeyboardInterrupt:
        logger.info("Shutting down...")


async def discover_and_print():
    """
    Discover devices and print information
    """
    devices = await discover_devices()
    
    if not devices:
        print("\nNo Kasa devices found on network.")
        print("Make sure devices are on the same network and powered on.")
        return
    
    print(f"\nFound {len(devices)} device(s):\n")
    
    for ip, device in devices.items():
        await device.update()
        print(f"IP: {ip}")
        print(f"  Name: {device.alias}")
        print(f"  Model: {device.model}")
        print(f"  MAC: {device.mac}")
        print(f"  Has Power Monitoring: {device.has_emeter}")
        
        if device.has_emeter:
            try:
                emeter = await device.get_emeter_realtime()
                print(f"  Current Power: {emeter.get('power', 'N/A')} W")
            except Exception as e:
                print(f"  Error reading power: {e}")
        
        print(f"  Formatted name for metrics: {format_device_name(device.alias)}")
        print()


async def poll_once():
    """
    Poll devices once and print results (for testing)
    """
    devices = await discover_devices()
    
    if not devices:
        print("No devices found.")
        return
    
    print("\nPolling devices...")
    count = await poll_devices_once(devices)
    print(f"\nSent {count} metrics to Graphite at {config.CARBON_SERVER}:{config.CARBON_PORT}")


def main():
    parser = argparse.ArgumentParser(description='Kasa Smart Plug to Graphite Integration')
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
