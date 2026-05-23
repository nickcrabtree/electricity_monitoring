#!/usr/bin/env python3
"""
WiFi presence scanner using ARP to detect devices on local network
"""

import logging
import socket
import ipaddress
from typing import Any, Set, List, Dict, Optional
import time

try:
    from scapy.all import ARP, Ether, srp, conf, get_if_list, get_if_addr
    SCAPY_AVAILABLE = True
except ImportError:
    SCAPY_AVAILABLE = False

logger = logging.getLogger(__name__)


def normalize_mac(mac: str) -> str:
    """Normalize MAC address to uppercase colon-separated format"""
    if not mac:
        return ""
    # Remove any separators and convert to uppercase
    cleaned = ''.join(mac.upper().split(':'))
    cleaned = ''.join(cleaned.split('-'))
    cleaned = ''.join(cleaned.split('.'))
    # Add colons every 2 characters
    if len(cleaned) == 12:
        return ':'.join(cleaned[i:i+2] for i in range(0, 12, 2))
    return mac.upper()


def get_hostname(ip: str) -> Optional[str]:
    """Try to get hostname for IP address"""
    try:
        hostname = socket.gethostbyaddr(ip)[0]
        return hostname
    except (socket.herror, socket.gaierror):
        return None


def fingerprint_device(ip: str) -> Dict[str, Any]:
    """Use nmap to fingerprint a device for identification"""
    import subprocess
    import re
    
    fingerprint = {
        'os_guess': None,
        'open_ports': [],
        'device_type': None,
        'vendor': None
    }
    
    try:
        # Quick OS detection scan (timeout after 10 seconds)
        logger.debug(f"Fingerprinting {ip}...")
        result = subprocess.run([
            'nmap', '-O', '-sS', '--top-ports', '100', 
            '--max-rtt-timeout', '1000ms',
            '--max-retries', '1',
            '--host-timeout', '10s',
            ip
        ], capture_output=True, text=True, timeout=15)
        
        if result.returncode == 0:
            output = result.stdout
            
            # Extract open ports
            port_pattern = re.compile(r'(\d+)/(tcp|udp)\s+open\s+(\S+)')
            ports = port_pattern.findall(output)
            fingerprint['open_ports'] = [{'port': int(p[0]), 'protocol': p[1], 'service': p[2]} for p in ports]
            
            # Extract OS guess
            os_pattern = re.compile(r'Running: (.+?)\n', re.IGNORECASE)
            os_match = os_pattern.search(output)
            if os_match:
                fingerprint['os_guess'] = os_match.group(1).strip()
            
            # Look for device type indicators
            output_lower = output.lower()
            if 'iphone' in output_lower or 'ios' in output_lower:
                fingerprint['device_type'] = 'iPhone'
            elif 'android' in output_lower:
                fingerprint['device_type'] = 'Android'
            elif 'mac os' in output_lower or 'macos' in output_lower:
                fingerprint['device_type'] = 'macOS'
            elif 'windows' in output_lower:
                fingerprint['device_type'] = 'Windows'
            elif 'linux' in output_lower:
                fingerprint['device_type'] = 'Linux'
            
            # Detect common iPhone/iOS patterns
            iphone_ports = [62078, 49152, 49153, 49154]  # Common iOS ports
            open_port_nums = [p['port'] for p in fingerprint['open_ports']]
            if any(port in open_port_nums for port in iphone_ports):
                fingerprint['device_type'] = 'iPhone'
            
            logger.debug(f"Fingerprinted {ip}: {fingerprint}")
            
    except subprocess.TimeoutExpired:
        logger.debug(f"Fingerprinting timeout for {ip}")
    except Exception as e:
        logger.debug(f"Fingerprinting failed for {ip}: {e}")
    
    return fingerprint


def add_fingerprints(devices: List[Dict]) -> None:
    """Add fingerprints to devices that look like phones"""
    for device in devices:
        hostname = device.get('hostname', '') or ''
        # Only fingerprint devices that might be phones
        if 'iphone' in hostname.lower() or 'android' in hostname.lower():
            device['fingerprint'] = fingerprint_device(device['ip'])


def pre_scan_ping(ips: list) -> None:
    """Ping known device IPs to wake sleeping WiFi radios before an ARP scan."""
    import subprocess
    for ip in ips:
        try:
            subprocess.run(['ping', '-c', '1', '-W', '1', ip],
                          capture_output=True, timeout=1.5)
        except Exception:
            pass


def scan_network_scapy(cidr: str, timeout: int = 2) -> Dict[str, Any]:
    """
    Scan network using scapy ARP requests
    
    Returns:
        dict with 'devices' list and 'present_macs' set
    """
    if not SCAPY_AVAILABLE:
        raise ImportError("scapy is required for ARP scanning")
    
    logger.debug(f"Scanning network {cidr} with scapy")
    
    try:
        # Create ARP request packet
        arp = ARP(pdst=cidr)
        ether = Ether(dst="ff:ff:ff:ff:ff:ff")
        packet = ether / arp
        
        # Send packets and receive responses
        answered_list = srp(packet, timeout=timeout, verbose=False)[0]
        
        devices = []
        present_macs = set()
        
        for element in answered_list:
            ip = element[1].psrc
            mac = normalize_mac(element[1].hwsrc)
            hostname = get_hostname(ip)
            
            device = {
                'ip': ip,
                'mac': mac,
                'hostname': hostname
            }
            devices.append(device)
            present_macs.add(mac)
            
        logger.debug(f"Found {len(devices)} devices via ARP scan")
        return {
            'devices': devices,
            'present_macs': present_macs
        }
        
    except Exception as e:
        logger.error(f"Scapy ARP scan failed: {e}")
        return {'devices': [], 'present_macs': set()}


def get_ipv6_neighbors() -> Dict[str, str]:
    """Get IPv6 neighbor table (equivalent to ARP for IPv6)"""
    import subprocess
    
    ipv6_neighbors = {}
    try:
        result = subprocess.run(['ip', '-6', 'neigh', 'show'], 
                              capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            for line in result.stdout.split('\n'):
                if line.strip() and 'REACHABLE' in line:
                    parts = line.split()
                    if len(parts) >= 5:
                        ipv6 = parts[0]
                        mac = parts[4]
                        if ':' in mac:  # Valid MAC
                            ipv6_neighbors[normalize_mac(mac)] = ipv6
    except Exception as e:
        logger.debug(f"IPv6 neighbor discovery failed: {e}")
    
    return ipv6_neighbors


def _discover_active_ips(cidr: str) -> Set[str]:
    """Phase 1: collect active IPs via nmap + ARP table."""
    import subprocess
    import re

    ip_set: Set[str] = set()

    result = subprocess.run(['nmap', '-sn', cidr], capture_output=True, text=True, timeout=15)
    if result.returncode == 0:
        nmap_ips = re.findall(r'Nmap scan report for (\d+\.\d+\.\d+\.\d+)', result.stdout)
        ip_set.update(nmap_ips)
        logger.debug(f"nmap found {len(nmap_ips)} hosts")

    arp_result = subprocess.run(['ip', 'neigh', 'show'], capture_output=True, text=True, timeout=5)
    if arp_result.returncode == 0:
        for line in arp_result.stdout.split('\n'):
            if line.strip():
                parts = line.split()
                if len(parts) >= 5 and 'REACHABLE' in line:
                    ip = parts[0]
                    if '.' in ip:
                        ip_set.add(ip)

    return ip_set


def _resolve_ips_to_devices(ip_set: Set[str], cidr: str) -> Dict[str, Any]:
    """Phase 2: map IPs to MAC addresses and build device records."""
    import subprocess

    ipv6_neighbors = get_ipv6_neighbors()

    subnet_base = '.'.join(cidr.split('.')[:-1])
    for ip in [f"{subnet_base}.1", f"{subnet_base}.254"]:
        try:
            subprocess.run(['ping', '-c', '1', '-W', '1', ip], capture_output=True, timeout=2)
        except Exception:
            pass

    devices = []
    present_macs: Set[str] = set()

    arp_result = subprocess.run(['ip', 'neigh', 'show'], capture_output=True, text=True, timeout=5)
    if arp_result.returncode == 0:
        for line in arp_result.stdout.split('\n'):
            if line.strip():
                parts = line.split()
                if len(parts) >= 5:
                    ip = parts[0]
                    if ip in ip_set and ':' in parts[4]:
                        mac_normalized = normalize_mac(parts[4])
                        devices.append({
                            'ip': ip,
                            'mac': mac_normalized,
                            'hostname': get_hostname(ip),
                            'ipv6': ipv6_neighbors.get(mac_normalized),
                        })
                        present_macs.add(mac_normalized)

    return {'devices': devices, 'present_macs': present_macs}


def scan_network_fallback(cidr: str) -> Dict[str, Any]:
    """
    Fallback network scan using system tools when scapy unavailable or fails.
    Uses nmap, ARP table, ping, and IPv6 discovery in a two-phase pipeline.

    Returns:
        dict with 'devices' list and 'present_macs' set
    """
    logger.debug(f"Using fallback scan for network {cidr}")
    try:
        ip_set = _discover_active_ips(cidr)
        logger.debug(f"Phase 1 complete: {len(ip_set)} candidate IPs")
        result = _resolve_ips_to_devices(ip_set, cidr)
        logger.debug(f"Fallback scan found {len(result['devices'])} devices with MAC addresses")
        return result
    except Exception as e:
        logger.warning(f"Fallback scan failed: {e}")
        return {'devices': [], 'present_macs': set()}


def scan_network(cidr: str, fingerprint_iphones: bool = False, wake_ips: list = None) -> Dict[str, Any]:
    """
    Scan network for active devices

    Args:
        cidr: Network CIDR (e.g., "192.168.86.0/24")
        fingerprint_iphones: If True, fingerprint devices that look like iPhones
        wake_ips: Optional list of IPs to ping before scanning to wake sleeping radios

    Returns:
        dict with:
            - devices: List of {ip, mac, hostname, fingerprint?} dicts
            - present_macs: Set of MAC addresses found
    """
    start_time = time.time()

    # Ping known IPs to wake sleeping WiFi radios (helps Android power-saving devices)
    if wake_ips:
        logger.debug(f"Pre-scan ping to {len(wake_ips)} known IPs to wake sleeping radios")
        pre_scan_ping(wake_ips)

    # Try scapy first
    if SCAPY_AVAILABLE:
        try:
            result = scan_network_scapy(cidr)
            if result['devices']:  # If we got results, use them
                scan_time = time.time() - start_time
                logger.debug(f"Network scan completed in {scan_time:.2f}s using scapy")
                # Add fingerprinting if requested
                if fingerprint_iphones:
                    add_fingerprints(result['devices'])
                return result
        except Exception as e:
            logger.warning(f"Scapy scan failed, trying fallback: {e}")
    
    # Fall back to system tools
    result = scan_network_fallback(cidr)
    scan_time = time.time() - start_time
    logger.debug(f"Network scan completed in {scan_time:.2f}s using fallback")
    
    # Add fingerprinting if requested
    if fingerprint_iphones:
        add_fingerprints(result['devices'])
    
    return result


if __name__ == '__main__':
    # Test the scanner
    logging.basicConfig(level=logging.DEBUG)
    
    cidr = "192.168.86.0/24"
    print(f"Scanning {cidr}...")
    
    result = scan_network(cidr)
    
    print(f"\nFound {len(result['devices'])} devices:")
    for device in result['devices']:
        print(f"  {device['ip']:<15} {device['mac']:<18} {device['hostname'] or 'N/A'}")
    
    print(f"\nPresent MACs: {len(result['present_macs'])}")