#!/usr/bin/env python3
"""
Shared configuration for electricity monitoring scripts
"""

# Graphite/Carbon server settings
CARBON_SERVER = '192.168.86.123'
CARBON_PORT = 2003

# Polling intervals (seconds)
SMART_PLUG_POLL_INTERVAL = 30
METER_POLL_INTERVAL = 5

# Metric naming prefix
METRIC_PREFIX = 'home.electricity'

# Device configurations are now fully automatic via discovery
# Device names are persisted in device_names.json

# Network subnets to scan for Kasa devices (for cross-subnet routing)
# Add subnets where you have Kasa devices
KASA_DISCOVERY_NETWORKS = [
    # Default local subnet (auto-discovered)
    None,  # None means use the local subnet
    # Add additional subnets after cross-subnet routing is configured:
    '192.168.1.0/24',  # OpenWRT subnet with smart plugs
]


# ESP32 receiver settings
ESP32_RECEIVER_HOST = '0.0.0.0'
ESP32_RECEIVER_PORT = 5000

# SSH Tunnel Configuration (for cross-subnet device discovery)
# Set up SSH tunnel to OpenWrt router to discover devices on 192.168.1.0 network
SSH_TUNNEL_ENABLED = True  # Enable UDP tunnel for cross-subnet Kasa discovery
SSH_REMOTE_HOST = 'openwrt'  # SSH connection string
SSH_IDENTITY_FILE = None  # Use default from SSH config
SSH_TUNNEL_SUBNET = '192.168.1.0/24'  # Remote subnet to scan
SSH_USE_SSHPASS = False  # Use sshpass for password auth (set OPENWRT_PASSWORD env var)
SSH_PASSWORD_ENV_VAR = 'OPENWRT_PASSWORD'  # Environment variable containing SSH password

# UDP Tunnel for Kasa discovery across subnets
# When enabled, creates SSH tunnel to forward Kasa discovery UDP packets
UDP_TUNNEL_ENABLED = True
UDP_TUNNEL_LOCAL_PORT = 9999  # Local port to listen on
UDP_TUNNEL_REMOTE_PORT = 9999  # Port on remote subnet
UDP_TUNNEL_REMOTE_BROADCAST = '192.168.1.255'  # Broadcast address on remote subnet

# If SSH_TUNNEL_ENABLED, the script will:
# 1. SSH to OpenWrt router
# 2. Query DHCP leases and ARP for Kasa devices on 192.168.1.0
# 3. Create port forwarding tunnels for device communication
# 4. Poll devices through the tunnel

# Logging
LOG_LEVEL = 'INFO'  # DEBUG, INFO, WARNING, ERROR

# Graphite query settings (for aggregate script to read existing metrics)
GRAPHITE_SSH_HOST = 'nickc@192.168.86.123'
GRAPHITE_WHISPER_PATH = '/var/lib/graphite/whisper/home/electricity'
GRAPHITE_SSH_TIMEOUT = 8
GRAPHITE_FETCH_TAIL_LINES = 720  # Last hour at 5s resolution
