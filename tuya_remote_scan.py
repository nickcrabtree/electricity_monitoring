#!/usr/bin/env python3
"""
Helper to scan for Tuya devices on a remote subnet via SSH
"""

import json
import subprocess
import logging
import os
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


def scan_remote_subnet(ssh_host: str, subnet: str = '192.168.1.0/24', ssh_identity: Optional[str] = None, use_sshpass: bool = False, password_env_var: str = 'OPENWRT_PASSWORD') -> List[str]:
    """
    Scan for Tuya devices on a remote subnet by SSHing to a router/gateway
    
    Args:
        ssh_host: SSH connection string (e.g., 'root@192.168.1.1' or 'openwrt')
        subnet: Subnet to scan (CIDR notation)
        ssh_identity: Path to SSH identity file (optional)
        use_sshpass: Use sshpass for password authentication
        password_env_var: Environment variable containing SSH password
    
    Returns:
        List of IP addresses where Tuya devices were found
    """
    try:
        # Build SSH command
        ssh_cmd = []
        
        # Use sshpass if enabled and password is available
        if use_sshpass and password_env_var in os.environ:
            ssh_cmd = ['sshpass', '-e']
            # Set SSH_ASKPASS environment variable name for sshpass
            os.environ['SSHPASS'] = os.environ[password_env_var]
        
        ssh_cmd.extend(['ssh'])
        if ssh_identity:
            ssh_cmd.extend(['-i', ssh_identity])
        ssh_cmd.extend(['-o', 'StrictHostKeyChecking=no', '-o', 'ConnectTimeout=5'])
        ssh_cmd.append(ssh_host)
        
        # Scan for devices on port 6668 (Tuya default)
        # Use nmap if available, otherwise try netcat scan
        remote_cmd = f"""
        # Try nmap first
        if command -v nmap >/dev/null 2>&1; then
            nmap -p 6668 --open {subnet} 2>/dev/null | grep 'Nmap scan report for' | awk '{{print $NF}}' | tr -d '()'
        else
            # Fallback: scan common IPs with nc
            for i in $(seq 1 254); do
                ip="{subnet.rsplit('.', 1)[0]}.$i"
                timeout 0.2 nc -z -w 1 "$ip" 6668 2>/dev/null && echo "$ip" &
            done
            wait
        fi
        """
        
        ssh_cmd.append(remote_cmd)
        
        logger.debug(f"Scanning {subnet} via {ssh_host}...")
        result = subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=30)
        
        if result.returncode != 0:
            logger.warning(f"Remote scan command failed: {result.stderr}")
            return []
        
        # Parse IPs from output
        ips = [line.strip() for line in result.stdout.strip().split('\n') if line.strip()]
        if ips:
            logger.info(f"Found {len(ips)} potential Tuya device(s) on {subnet}: {ips}")
        else:
            logger.debug(f"No Tuya devices found on {subnet}")
        
        return ips
        
    except subprocess.TimeoutExpired:
        logger.error(f"Remote scan timed out for {subnet}")
        return []
    except Exception as e:
        logger.error(f"Error scanning remote subnet {subnet}: {e}")
        return []
