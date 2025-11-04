#!/usr/bin/env python3
"""
Device name persistence helper
Maps device IDs (MAC addresses, Tuya IDs) to friendly names that persist across IP changes
"""

import json
import os
import logging
from typing import Optional, Dict

logger = logging.getLogger(__name__)

DEVICE_NAMES_FILE = 'device_names.json'


def load_device_names() -> Dict[str, str]:
    """Load device name mappings from JSON file"""
    if not os.path.exists(DEVICE_NAMES_FILE):
        return {}
    
    try:
        with open(DEVICE_NAMES_FILE, 'r') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load device names: {e}")
        return {}


def save_device_names(names: Dict[str, str]):
    """Save device name mappings to JSON file"""
    try:
        with open(DEVICE_NAMES_FILE, 'w') as f:
            json.dump(names, f, indent=2, sort_keys=True)
    except Exception as e:
        logger.error(f"Failed to save device names: {e}")


def get_device_name(device_id: str, fallback_name: Optional[str] = None) -> str:
    """
    Get friendly name for a device, using fallback if not mapped
    
    Args:
        device_id: Unique device identifier (MAC, Tuya ID, etc)
        fallback_name: Name to use if not found in mapping (e.g., device's reported alias)
    
    Returns:
        Friendly name for the device
    """
    names = load_device_names()
    
    if device_id in names:
        return names[device_id]
    
    # If we have a fallback name from the device itself, use and save it
    if fallback_name:
        logger.info(f"New device discovered: {device_id} -> {fallback_name}")
        names[device_id] = fallback_name
        save_device_names(names)
        return fallback_name
    
    # Last resort: use device ID
    return device_id


def set_device_name(device_id: str, friendly_name: str):
    """
    Set/update friendly name for a device
    
    Args:
        device_id: Unique device identifier
        friendly_name: Human-readable name
    """
    names = load_device_names()
    names[device_id] = friendly_name
    save_device_names(names)
    logger.info(f"Updated device name: {device_id} -> {friendly_name}")
