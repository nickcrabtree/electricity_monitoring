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
import socket
import subprocess
import re
from typing import Dict, List, Tuple, Optional

try:
    from kasa import Discover, Device, DeviceConfig
except ImportError:
    print("Error: python-kasa not installed. Run: pip install python-kasa")
    sys.exit(1)

import config
from graphite_helper import send_metrics, format_device_name
from device_names import get_device_name

# Optional SSH tunnel support
try:
    from ssh_tunnel_manager import SSHTunnelManager
    SSH_TUNNEL_AVAILABLE = True
except ImportError:
    SSH_TUNNEL_AVAILABLE = False

# Optional UDP tunnel support for Kasa discovery
try:
    from udp_tunnel import SimpleUDPTunnel
    UDP_TUNNEL_AVAILABLE = True
except ImportError:
    UDP_TUNNEL_AVAILABLE = False

# Set up logging
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def resolve_device_ip(identifier: str) -> Optional[str]:
    """
    Resolve device identifier to IP address.
    Supports IP addresses, hostnames (mDNS), and MAC addresses.
    
    Args:
        identifier: IP address, hostname, or MAC address
        
    Returns:
        IP address if resolved, None otherwise
    """
    # Check if it's already an IP address
    try:
        socket.inet_aton(identifier)
        return identifier  # Already an IP
    except socket.error:
        pass
    
    # Check if it's a MAC address (format: XX:XX:XX:XX:XX:XX)
    if re.match(r'^([0-9A-Fa-f]{2}[:-]){5}([0-9A-Fa-f]{2})$', identifier):
        return resolve_mac_to_ip(identifier)
    
    # Try hostname resolution
    return resolve_hostname_to_ip(identifier)


def resolve_hostname_to_ip(hostname: str) -> Optional[str]:
    """
    Resolve hostname to IP address using DNS/mDNS.
    """
    try:
        ip = socket.gethostbyname(hostname)
        logger.info(f"Resolved hostname {hostname} to {ip}")
        return ip
    except socket.gaierror as e:
        logger.debug(f"Failed to resolve hostname {hostname}: {e}")
        return None


def resolve_mac_to_ip(mac_address: str) -> Optional[str]:
    """
    Resolve MAC address to IP using ARP table lookup.
    """
    try:
        # Normalize MAC address format
        mac = mac_address.upper().replace('-', ':')
        
        # Use arp command to find IP by MAC
        arp_output = subprocess.check_output(
            f"/usr/sbin/arp -a | grep -i {mac}",
            shell=True, stderr=subprocess.DEVNULL
        ).decode('utf-8')
        
        # Parse output: hostname (192.168.86.x) at aa:bb:cc:dd:ee:ff [ether]
        ip_match = re.search(r'\((\d+\.\d+\.\d+\.\d+)\)', arp_output)
        if ip_match:
            ip = ip_match.group(1)
            logger.info(f"Resolved MAC {mac} to {ip} via ARP")
            return ip
        else:
            logger.debug(f"MAC {mac} found in ARP but could not parse IP")
            return None
            
    except subprocess.CalledProcessError:
        logger.debug(f"MAC {mac_address} not found in ARP table")
        return None
    except Exception as e:
        logger.debug(f"Error resolving MAC {mac_address}: {e}")
        return None


# Global SSH tunnel manager (created once, reused)
global_tunnel_manager = None

# Global UDP tunnel (created once, reused)
global_udp_tunnel = None


def _is_cross_subnet_mode() -> bool:
    """Check if running in legacy single-host cross-subnet mode."""
    return getattr(config, 'LOCAL_ROLE', 'main_lan') == 'single_host_cross_subnet'


def get_tunnel_manager() -> Optional[SSHTunnelManager]:
    """
    Get or create the global SSH tunnel manager.
    Only used in single_host_cross_subnet mode.
    """
    global global_tunnel_manager
    
    # Skip tunnel manager unless in legacy cross-subnet mode
    if not _is_cross_subnet_mode():
        return None
    
    if not SSH_TUNNEL_AVAILABLE or not config.SSH_TUNNEL_ENABLED:
        return None
    
    if global_tunnel_manager is None:
        try:
            ssh_host = getattr(config, 'SSH_REMOTE_HOST', 'root@192.168.86.1')
            identity_file = getattr(config, 'SSH_IDENTITY_FILE', None)
            
            global_tunnel_manager = SSHTunnelManager(ssh_host, identity_file)
            
            if not global_tunnel_manager.test_connection():
                logger.error("Failed to establish SSH connection")
                global_tunnel_manager = None
                return None
            
            logger.info("SSH tunnel manager initialized")
        except Exception as e:
            logger.error(f"Error initializing SSH tunnel manager: {e}")
            global_tunnel_manager = None
    
    return global_tunnel_manager


def get_udp_tunnel() -> Optional[SimpleUDPTunnel]:
    """
    Get or create the global UDP tunnel for cross-subnet Kasa discovery.
    Only used in single_host_cross_subnet mode.
    """
    global global_udp_tunnel
    
    # Skip UDP tunnel unless in legacy cross-subnet mode
    if not _is_cross_subnet_mode():
        return None
    
    if not UDP_TUNNEL_AVAILABLE or not getattr(config, 'UDP_TUNNEL_ENABLED', False):
        return None
    
    if global_udp_tunnel is None:
        try:
            ssh_host = getattr(config, 'SSH_REMOTE_HOST', 'openwrt')
            remote_broadcast = getattr(config, 'UDP_TUNNEL_REMOTE_BROADCAST', '192.168.1.255')
            local_port = getattr(config, 'UDP_TUNNEL_LOCAL_PORT', 9999)
            remote_port = getattr(config, 'UDP_TUNNEL_REMOTE_PORT', 9999)
            identity_file = getattr(config, 'SSH_IDENTITY_FILE', None)
            
            global_udp_tunnel = SimpleUDPTunnel(
                ssh_host=ssh_host,
                remote_ip=remote_broadcast,
                local_port=local_port,
                remote_port=remote_port,
                ssh_identity=identity_file
            )
            
            if global_udp_tunnel.start():
                logger.info(f"UDP tunnel started: localhost:{local_port} <-> {remote_broadcast}:{remote_port}")
            else:
                logger.warning("Failed to start UDP tunnel")
                global_udp_tunnel = None
                return None
        except Exception as e:
            logger.error(f"Error initializing UDP tunnel: {e}")
            global_udp_tunnel = None
    
    return global_udp_tunnel


async def discover_devices(prev_devices: Dict[str, Device] = None) -> Dict[str, Device]:
    """
    Discover Kasa devices on the network with error resilience.
    Supports cross-subnet discovery when KASA_DISCOVERY_NETWORKS is configured.
    All devices are discovered automatically.
    
    Args:
        prev_devices: Previously known devices to fall back on if discovery fails
    
    Returns:
        Dictionary mapping device IP to Device object
    """
    if prev_devices is None:
        prev_devices = {}
    
    all_devices = {}
    
    try:
        logger.info("Discovering Kasa devices on network...")
        
        # 1. SSH TUNNEL REMOTE DISCOVERY (if enabled)
        tunnel_manager = get_tunnel_manager()
        if tunnel_manager:
            subnet = getattr(config, 'SSH_TUNNEL_SUBNET', '192.168.1.0/24')
            try:
                remote_devices = tunnel_manager.discover_remote_devices(subnet)
                for ip, device_info in remote_devices.items():
                    hostname = device_info.get('hostname', '')
                    mac = device_info.get('mac', '')
                    
                    # Create tunnel for the device
                    local_port = tunnel_manager.create_tunnel(ip)
                    if local_port:
                        # Connect to device through the SSH tunnel using Device.connect
                        try:
                            device_config = DeviceConfig(host="127.0.0.1", port_override=local_port, timeout=10)
                            dev = await Device.connect(config=device_config)
                            await dev.update()
                            all_devices[ip] = dev
                            logger.info(f"Added tunneled device {dev.alias} at {ip} -> localhost:{local_port}")
                        except Exception as e:
                            logger.error(f"Failed to connect to tunneled device {ip}: {e}")
                    else:
                        logger.warning(f"Could not create tunnel for {ip}")
            except Exception as e:
                logger.error(f"SSH remote discovery failed: {e}")
        
        # 2. LOCAL NETWORK DISCOVERY
        # Determine which networks to scan
        discovery_networks = getattr(config, 'KASA_DISCOVERY_NETWORKS', [None])
        
        # Discover on each configured network
        for network in discovery_networks:
            try:
                if network is None:
                    # Default discovery (local subnet)
                    logger.debug("Scanning local subnet...")
                    discovered = await asyncio.wait_for(Discover.discover(), timeout=10)
                else:
                    # Cross-subnet discovery
                    logger.debug(f"Scanning network: {network}...")
                    discovered = await asyncio.wait_for(Discover.discover(target=network), timeout=10)
                
                if discovered:
                    # Merge discovered devices
                    for ip, dev in discovered.items():
                        all_devices[ip] = dev
            except Exception as e:
                logger.warning(f"Failed to discover on network {network}: {e}")
        
        devices = all_devices
        
        if not devices:
            logger.warning("No Kasa devices found on any configured network")
            if prev_devices:
                logger.info(f"Reusing {len(prev_devices)} previously known devices")
                return prev_devices
            return {}
        
        logger.info(f"Found {len(devices)} Kasa device(s):")
        
        # Try to update each device, but don't fail on errors
        available_devices = {}
        for ip, dev in devices.items():
            try:
                await asyncio.wait_for(dev.update(), timeout=5)
                logger.info(f"  {ip}: {dev.alias} ({dev.model})")
                available_devices[ip] = dev
            except Exception as e:
                logger.warning(f"  {ip}: Failed to update: {e} (will retry on next poll)")
                # Still add the device, we'll try again on next poll
                available_devices[ip] = dev
        
        return available_devices if available_devices else devices
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
            
            # Use MAC address as stable identifier, with device alias as friendly name
            device_mac = device.mac if hasattr(device, 'mac') else device.host
            friendly_name = get_device_name(device_mac, fallback_name=device.alias)
            device_name = format_device_name(friendly_name)
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
    
    # Start UDP tunnel for cross-subnet discovery if enabled
    udp_tunnel = get_udp_tunnel()
    
    # Discover devices initially
    devices = await discover_devices()
    
    if not devices:
        logger.warning("No devices found initially. Will retry discovery in main loop...")
    
    # Main loop - never exit except on KeyboardInterrupt
    last_discovery = time.time()
    discovery_interval = getattr(config, "KASA_REDISCOVERY_INTERVAL", 180)  # Re-discover every N minutes
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
        if udp_tunnel:
            udp_tunnel.stop()


async def discover_and_print():
    """
    Discover devices and print information
    """
    # Start UDP tunnel for cross-subnet discovery if enabled
    udp_tunnel = get_udp_tunnel()
    
    try:
        devices = await discover_devices()
        
        if not devices:
            print("\nNo Kasa devices found on network.")
            print("Make sure devices are on the same network and powered on.")
            return
        
        print(f"\nFound {len(devices)} device(s):\n")
        
        for ip, device in devices.items():
            try:
                await asyncio.wait_for(device.update(), timeout=5)
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
            except Exception as e:
                print(f"IP: {ip}")
                print(f"  ERROR: Failed to communicate with device: {e}")
                print(f"  Note: Device may be offline or on different subnet")
            
            print()
    finally:
        if udp_tunnel:
            udp_tunnel.stop()


async def poll_once():
    """
    Poll devices once and print results (for testing)
    """
    # Start UDP tunnel for cross-subnet discovery if enabled
    udp_tunnel = get_udp_tunnel()
    
    try:
        devices = await discover_devices()
        
        if not devices:
            print("No devices found.")
            return
        
        print("\nPolling devices...")
        count = await poll_devices_once(devices)
        print(f"\nSent {count} metrics to Graphite at {config.CARBON_SERVER}:{config.CARBON_PORT}")
    finally:
        if udp_tunnel:
            udp_tunnel.stop()


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
