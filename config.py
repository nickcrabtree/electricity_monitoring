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

# Network subnets to scan for Kasa devices.
# By default we only scan the *local* subnet on each host. This lets you
# run `kasa_to_graphite.py` on multiple Pis (e.g. blackpi2 on 192.168.86.x
# and flint on 192.168.1.x) without duplicating metrics for the same plug.
#
# If you still need cross-subnet discovery from a single host, you can
# temporarily add extra CIDRs here (e.g. '192.168.1.0/24') on that host
# only.
KASA_DISCOVERY_NETWORKS = [
    None,  # None means "use the local subnet only" on this host
]


# ESP32 receiver settings
ESP32_RECEIVER_HOST = '0.0.0.0'
ESP32_RECEIVER_PORT = 5000

# SSH Tunnel Configuration (for cross-subnet device discovery)
# These were used when a single host (e.g. quartz) needed to see Kasa
# devices on the OpenWrt subnet as well. Now that `flint` can live on
# the 192.168.1.x side and run `kasa_to_graphite.py` locally, we keep
# the tunnel settings for future use but leave them disabled by default
# to avoid duplicate metrics across hosts.
SSH_TUNNEL_ENABLED = False  # Set True only on a host that should probe a remote subnet
SSH_REMOTE_HOST = 'root@openwrt.lan'  # SSH connection string
SSH_IDENTITY_FILE = None  # Use default from SSH config
SSH_TUNNEL_SUBNET = '192.168.1.0/24'  # Remote subnet to scan (if SSH_TUNNEL_ENABLED)
SSH_USE_SSHPASS = False  # Use sshpass for password auth (set OPENWRT_PASSWORD env var)
SSH_PASSWORD_ENV_VAR = 'OPENWRT_PASSWORD'  # Environment variable containing SSH password

# UDP Tunnel for Kasa discovery across subnets
# When enabled, creates SSH tunnel to forward Kasa discovery UDP packets
UDP_TUNNEL_ENABLED = False
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

# Re-discovery intervals (seconds)
KASA_REDISCOVERY_INTERVAL = 180  # 3 minutes - detect new devices/IP changes
TUYA_REDISCOVERY_INTERVAL = 180  # 3 minutes
