#!/usr/bin/env python3
"""
Shared configuration for electricity monitoring scripts
"""

# Graphite/Carbon server settings
CARBON_SERVER = '192.168.86.123'
CARBON_PORT = 2003

# Polling intervals (seconds)
SMART_PLUG_POLL_INTERVAL = 10
METER_POLL_INTERVAL = 5

# Metric naming prefix
METRIC_PREFIX = 'home.electricity'

# Device configurations are now fully automatic via discovery
# Device names are persisted in device_names.json

# ------------------------------------------------------------------
# Deployment role
# ------------------------------------------------------------------
# Defines how this host participates in the monitoring architecture:
#   'main_lan'                - Pi on the main LAN (e.g. blackpi2 on 192.168.86.x)
#   'remote_lan'              - Pi on the remote/device LAN (e.g. flint on 192.168.1.x)
#   'single_host_cross_subnet' - Legacy: single host bridging subnets via SSH tunnels
#
# In the current dual-Pi deployment, both blackpi2 and flint should use
# 'main_lan' or 'remote_lan' (functionally equivalent for local-only discovery).
# SSH/UDP tunnelling is only used when LOCAL_ROLE == 'single_host_cross_subnet'.
VALID_ROLES = ('main_lan', 'remote_lan', 'single_host_cross_subnet')
LOCAL_ROLE = 'main_lan'
assert LOCAL_ROLE in VALID_ROLES, f"LOCAL_ROLE must be one of {VALID_ROLES}, got {LOCAL_ROLE!r}"

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

# ------------------------------------------------------------------
# LEGACY: SSH Tunnel Configuration (for cross-subnet device discovery)
# ------------------------------------------------------------------
# These settings are ONLY used when LOCAL_ROLE == 'single_host_cross_subnet'.
# In the current dual-Pi deployment (blackpi2 + flint), SSH tunnelling is
# NOT required because each Pi polls its own local subnet directly.
#
# Historical context: these were used when a single host (e.g. quartz)
# needed to see Kasa devices on the OpenWrt subnet as well.
# See docs/ARCHITECTURE_REVIEW_flint_dual_subnet.md for details.
SSH_TUNNEL_ENABLED = False  # Only set True if LOCAL_ROLE == 'single_host_cross_subnet'

SSH_REMOTE_HOST = 'root@openwrt.local'  # SSH connection string (mDNS; was openwrt.lan)
SSH_IDENTITY_FILE = None  # Use default from SSH config
SSH_TUNNEL_SUBNET = '192.168.1.0/24'  # Remote subnet to scan (if SSH_TUNNEL_ENABLED)
SSH_USE_SSHPASS = False  # Use sshpass for password auth (set OPENWRT_PASSWORD env var)
SSH_PASSWORD_ENV_VAR = 'OPENWRT_PASSWORD'  # Environment variable containing SSH password

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

# --------------------------------------------------------------
# Optional per-host overrides
# --------------------------------------------------------------
# If you need to override settings on one machine (e.g. enable legacy tunnel
# features on a single host), create a config_local.py alongside this file.
try:
    from config_local import *  # noqa: F401,F403
except ImportError:
    pass
