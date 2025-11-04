#!/usr/bin/env python3
"""
SSH Tunnel Manager for Cross-Subnet Device Discovery

Manages SSH connections to OpenWrt router for:
1. Remote device discovery (DHCP leases, ARP table)
2. Port forwarding tunnels for device communication
"""

import subprocess
import logging
import socket
import time
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class SSHTunnelManager:
    """
    Manages SSH tunnel to remote network for device discovery and communication.
    
    Enables communication with devices on remote subnet (e.g., 192.168.1.0/24)
    through an SSH tunnel to OpenWrt router.
    """
    
    def __init__(self, ssh_host: str, identity_file: Optional[str] = None):
        """
        Initialize SSH tunnel manager.
        
        Args:
            ssh_host: SSH connection string (user@host)
            identity_file: Path to SSH private key (None = use default)
        """
        self.ssh_host = ssh_host
        self.identity_file = identity_file
        self.tunnel_mappings: Dict[str, int] = {}  # Maps remote_ip to local_port
        self.local_port_base = 9900
        
    def test_connection(self) -> bool:
        """Test SSH connection to the remote host."""
        try:
            cmd = ['ssh', '-o', 'ConnectTimeout=5', self.ssh_host, 'echo OK']
            if self.identity_file:
                cmd.extend(['-i', self.identity_file])
            
            result = subprocess.run(cmd, capture_output=True, timeout=10, text=True)
            
            if result.returncode == 0 and 'OK' in result.stdout:
                logger.info(f"SSH connection to {self.ssh_host} successful")
                return True
            else:
                logger.error(f"SSH connection failed: {result.stderr}")
                return False
                
        except subprocess.TimeoutExpired:
            logger.error(f"SSH connection timeout to {self.ssh_host}")
            return False
        except Exception as e:
            logger.error(f"SSH connection error: {e}")
            return False
    
    def discover_remote_devices(self, subnet: str = '192.168.1.0/24') -> Dict[str, Dict]:
        """
        Discover Kasa devices on remote subnet via SSH.
        
        Queries OpenWrt router's DHCP leases to find devices.
        
        Args:
            subnet: Remote subnet to scan (e.g., '192.168.1.0/24')
            
        Returns:
            Dict mapping IP to device info: {IP: {hostname, mac, subnet}}
        """
        devices = {}
        
        try:
            logger.info(f"Discovering devices on remote subnet {subnet} via SSH...")
            
            # Get DHCP leases from OpenWrt
            dhcp_info = self._query_remote_dhcp_leases()
            
            # Filter for subnet and Kasa devices
            for ip, info in dhcp_info.items():
                if self._ip_in_subnet(ip, subnet):
                    hostname = info.get('hostname', '')
                    mac = info.get('mac', '')
                    
                    # Look for Kasa-like hostnames
                    if any(x in hostname.lower() for x in ['kasa', 'tp-link', 'smart-plug', 'tapo', 'kp115', 'kp303', 'kp125', 'hs110']):
                        devices[ip] = {
                            'hostname': hostname,
                            'mac': mac,
                            'subnet': subnet,
                            'source': 'dhcp'
                        }
                        logger.info(f"Found device: {hostname} at {ip} (MAC: {mac})")
            
            if not devices:
                logger.warning(f"No Kasa devices found on {subnet}")
            else:
                logger.info(f"Discovered {len(devices)} device(s) on {subnet}")
            
            return devices
            
        except Exception as e:
            logger.error(f"Error discovering remote devices: {e}")
            return {}
    
    def _query_remote_dhcp_leases(self) -> Dict[str, Dict]:
        """Query DHCP leases on remote OpenWrt router."""
        try:
            cmd = ['ssh', self.ssh_host, 'cat /var/dhcp.leases']
            if self.identity_file:
                cmd.insert(1, '-i')
                cmd.insert(2, self.identity_file)
            
            result = subprocess.run(cmd, capture_output=True, timeout=10, text=True)
            
            if result.returncode != 0:
                logger.warning(f"Failed to query DHCP leases: {result.stderr}")
                return {}
            
            # Parse DHCP leases format: timestamp mac ip hostname remaining
            devices = {}
            for line in result.stdout.strip().split('\n'):
                if not line.strip():
                    continue
                parts = line.split()
                if len(parts) >= 4:
                    ip = parts[2]  # IP is at index 2
                    mac = parts[1]  # MAC is at index 1
                    hostname = parts[3] if len(parts) > 3 else 'unknown'  # Hostname at index 3
                    devices[ip] = {
                        'mac': mac,
                        'hostname': hostname
                    }
            
            return devices
            
        except Exception as e:
            logger.error(f"Error querying DHCP leases: {e}")
            return {}
    
    @staticmethod
    def _ip_in_subnet(ip: str, subnet: str) -> bool:
        """Check if IP address is in the given subnet."""
        try:
            import ipaddress
            ip_obj = ipaddress.ip_address(ip)
            subnet_obj = ipaddress.ip_network(subnet, strict=False)
            return ip_obj in subnet_obj
        except Exception as e:
            logger.debug(f"Error checking subnet: {e}")
            return False
    
    def create_tunnel(self, remote_ip: str, remote_port: int = 9999) -> Optional[int]:
        """
        Create SSH port forwarding tunnel to a remote device.
        
        Args:
            remote_ip: IP address of device on remote subnet
            remote_port: Port on remote device (9999 for Kasa)
            
        Returns:
            Local port number if successful, None otherwise
        """
        try:
            # Check if tunnel already exists
            if remote_ip in self.tunnel_mappings:
                return self.tunnel_mappings[remote_ip]
            
            # Find available local port
            local_port = self._find_available_port()
            if not local_port:
                logger.error("Could not find available local port")
                return None
            
            logger.info(f"Creating SSH tunnel to {remote_ip}:{remote_port} -> localhost:{local_port}")
            
            # Create tunnel: ssh -L local_port:remote_ip:remote_port
            cmd = [
                'ssh',
                '-L', f'{local_port}:{remote_ip}:{remote_port}',
                '-N',  # No remote command
                '-f',  # Background
                self.ssh_host
            ]
            if self.identity_file:
                cmd.insert(1, '-i')
                cmd.insert(2, self.identity_file)
            
            result = subprocess.run(cmd, capture_output=True, timeout=5, text=True)
            
            if result.returncode == 0:
                time.sleep(0.5)  # Give tunnel time to establish
                self.tunnel_mappings[remote_ip] = local_port
                logger.info(f"Tunnel established: localhost:{local_port} -> {remote_ip}:{remote_port}")
                return local_port
            else:
                logger.error(f"Failed to create tunnel: {result.stderr}")
                return None
                
        except Exception as e:
            logger.error(f"Error creating tunnel: {e}")
            return None
    
    def _find_available_port(self, start: int = 9900, end: int = 10000) -> Optional[int]:
        """Find an available local port for tunneling."""
        for port in range(start, end):
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(0.1)
                result = sock.connect_ex(('127.0.0.1', port))
                sock.close()
                if result != 0:  # Port is not in use
                    return port
            except Exception:
                pass
        return None
    
    def close_tunnel(self, remote_ip: str) -> bool:
        """Close a tunnel connection."""
        try:
            if remote_ip not in self.tunnel_mappings:
                return True
            
            local_port = self.tunnel_mappings[remote_ip]
            
            # Kill SSH process using this port
            cmd = f"ps aux | grep 'ssh.*-L.*{local_port}' | grep -v grep | awk '{{print $2}}' | xargs kill 2>/dev/null || true"
            subprocess.run(cmd, shell=True, timeout=5)
            
            del self.tunnel_mappings[remote_ip]
            logger.info(f"Tunnel closed: localhost:{local_port}")
            return True
            
        except Exception as e:
            logger.error(f"Error closing tunnel: {e}")
            return False
    
    def cleanup(self):
        """Close all open tunnels and cleanup."""
        remote_ips = list(self.tunnel_mappings.keys())
        for remote_ip in remote_ips:
            self.close_tunnel(remote_ip)
        logger.info("SSH tunnel manager cleanup complete")
    
    def __del__(self):
        """Ensure cleanup on deletion."""
        try:
            self.cleanup()
        except Exception:
            pass
