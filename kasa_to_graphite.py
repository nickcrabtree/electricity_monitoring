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
    from kasa import Discover, Device
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


async def discover_devices(prev_devices: Dict[str, Device] = None) -> Dict[str, Device]:
    """
    Discover Kasa devices on the network with error resilience
    
    Args:
        prev_devices: Previously known devices to fall back on if discovery fails
    
    Returns:
        Dictionary mapping device IP to Device object
    """
    if prev_devices is None:
        prev_devices = {}
    
    try:
        logger.info("Discovering Kasa devices on network...")
        devices = await asyncio.wait_for(Discover.discover(), timeout=10)
        
        if not devices:
            logger.warning("No Kasa devices found on network")
            if prev_devices:
                logger.info(f"Reusing {len(prev_devices)} previously known devices")
                return prev_devices
            return {}
        
        logger.info(f"Found {len(devices)} Kasa device(s):")
        for ip, dev in devices.items():
            try:
                await asyncio.wait_for(dev.update(), timeout=5)
                logger.info(f"  {ip}: {dev.alias} ({dev.model})")
            except Exception as e:
                logger.warning(f"  {ip}: Failed to update during discovery: {e}")
        
        return devices
    except Exception as e:
        logger.error(f"Kasa discovery failed: {e}")
        if prev_devices:
            logger.info(f"Reusing {len(prev_devices)} previously known devices")
            return prev_devices
        return {}


async def get_device_metrics(device: Device, retries: int = 3) -> List[Tuple[str, float]]:
    """
    Get power metrics from a Kasa device with retry logic
    
    Args:
        device: Device object
        retries: Number of retry attempts
    
    Returns:
        List of (metric_name, value) tuples
    """
    device_id = getattr(device, 'host', 'unknown')
    
    for attempt in range(1, retries + 1):
        try:
            # Update device with timeout
            await asyncio.wait_for(device.update(), timeout=5)
            
            # Format device name for metric path
            device_name = format_device_name(device.alias)
            base_metric = f"{config.METRIC_PREFIX}.kasa.{device_name}"
            
            metrics = []
            
            # Check if device has energy module (power monitoring)
            if not device.has_emeter:
                logger.debug(f"Device {device.alias} does not have power monitoring")
                return metrics
            
            # Get energy module
            energy = device.modules.get("Energy")
            if not energy:
                logger.debug(f"Device {device.alias} has emeter but no Energy module")
                return metrics
            
            # Extract metrics using the new API
            # current_consumption is power in watts
            if hasattr(energy, 'current_consumption') and energy.current_consumption is not None:
                metrics.append((f"{base_metric}.power_watts", energy.current_consumption))
            
            if hasattr(energy, 'voltage') and energy.voltage is not None:
                metrics.append((f"{base_metric}.voltage_volts", energy.voltage))
            
            if hasattr(energy, 'current') and energy.current is not None:
                metrics.append((f"{base_metric}.current_amps", energy.current))
            
            # Device state (on/off as 1/0)
            is_on = 1 if device.is_on else 0
            metrics.append((f"{base_metric}.is_on", is_on))
            
            logger.debug(f"Collected {len(metrics)} metrics from {device.alias}")
            return metrics
            
        except (asyncio.TimeoutError, ConnectionResetError, OSError) as e:
            if attempt < retries:
                wait_time = min(2 ** attempt, 10)  # Exponential backoff, max 10s
                logger.warning(f"{device_id} update failed ({attempt}/{retries}): {e}. Retrying in {wait_time}s...")
                await asyncio.sleep(wait_time)
            else:
                logger.error(f"{device_id} failed after {retries} attempts: {e}")
        except Exception as e:
            logger.error(f"Unexpected error getting metrics from {device_id}: {e}")
            break
    
    return []


async def poll_devices_once(devices: Dict[str, Device]) -> int:
    """
    Poll all devices once and send metrics to Graphite
    Uses asyncio.gather with return_exceptions to isolate device failures
    
    Args:
        devices: Dictionary of IP -> Device
    
    Returns:
        Number of metrics sent
    """
    if not devices:
        logger.warning("No devices to poll")
        return 0
    
    # Poll all devices concurrently with isolated error handling
    tasks = [get_device_metrics(device) for device in devices.values()]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    all_metrics = []
    for result in results:
        if isinstance(result, Exception):
            logger.error(f"Device polling task error: {result}")
        elif isinstance(result, list):
            all_metrics.extend(result)
    
    if not all_metrics:
        logger.warning("No metrics collected from any device")
        return 0
    
    # Send all metrics to Graphite
    try:
        count = send_metrics(config.CARBON_SERVER, config.CARBON_PORT, all_metrics)
        logger.info(f"Sent {count} metrics to Graphite")
        return count
    except Exception as e:
        logger.error(f"Failed to send metrics to Graphite: {e}")
        return 0


async def main_loop():
    """
    Main monitoring loop - discover devices and poll continuously
    Robust: continues running even if discovery or polling fails
    """
    logger.info("Starting Kasa to Graphite monitoring")
    logger.info(f"Graphite server: {config.CARBON_SERVER}:{config.CARBON_PORT}")
    logger.info(f"Poll interval: {config.SMART_PLUG_POLL_INTERVAL} seconds")
    
    # Discover devices initially
    devices = await discover_devices()
    
    if not devices:
        logger.warning("No devices found initially. Will retry discovery in main loop...")
    
    # Main loop - never exit except on KeyboardInterrupt
    last_discovery = time.time()
    discovery_interval = 600  # Re-discover every 10 minutes
    failed_polls = 0  # Track consecutive failed polls
    
    try:
        while True:
            try:
                # Poll devices if we have any
                if devices:
                    metrics_sent = await poll_devices_once(devices)
                    if metrics_sent == 0:
                        failed_polls += 1
                        # If we haven't sent metrics in 3 polls, try rediscovering
                        if failed_polls >= 3:
                            logger.warning(f"No metrics sent for {failed_polls} polls - triggering rediscovery")
                            devices = await discover_devices(devices)
                            failed_polls = 0
                            last_discovery = time.time()
                    else:
                        failed_polls = 0  # Reset counter on successful poll
                else:
                    logger.warning("No devices available to poll")
                
                # Re-discover devices periodically
                if time.time() - last_discovery >= discovery_interval:
                    logger.info("Re-discovering devices (periodic scan)...")
                    devices = await discover_devices(devices)  # Pass prev_devices as fallback
                    failed_polls = 0
                    last_discovery = time.time()
                
            except Exception as e:
                logger.error(f"Error in main loop iteration: {e}", exc_info=True)
            
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
                energy = device.modules.get("Energy")
                if energy:
                    power = energy.current_consumption if hasattr(energy, 'current_consumption') else 'N/A'
                    print(f"  Current Power: {power} W")
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
