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

# Device configurations
# Add your device-specific configs here as you discover them

# Kasa devices - will be auto-discovered, but can hardcode for reliability
KASA_DEVICES = {
    # Example: '192.168.86.50': 'living_room_lamp',
    # Will be populated by discovery
}

# Tuya devices - need to be configured after running tinytuya wizard
TUYA_DEVICES = {
    # Example:
    # 'device_id_123': {
    #     'name': 'kitchen_kettle',
    #     'ip': '192.168.86.51',
    #     'local_key': 'your_local_key_here',
    #     'version': '3.3'
    # }
}

# ESP32 receiver settings
ESP32_RECEIVER_HOST = '0.0.0.0'
ESP32_RECEIVER_PORT = 5000

# Logging
LOG_LEVEL = 'INFO'  # DEBUG, INFO, WARNING, ERROR
