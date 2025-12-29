#!/usr/bin/env python3
"""
Device name persistence helper
Maps device IDs (MAC addresses, Tuya IDs) to friendly names that persist across IP changes

Safety features:
- Atomic writes (write to temp file, then rename)
- Never truncate file on load errors
- Never overwrite existing entries with empty/None values
- Validates data before saving
"""

import json
import os
import logging
import tempfile
from typing import Optional, Dict

logger = logging.getLogger(__name__)

# Use absolute path relative to this script's location
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEVICE_NAMES_FILE = os.path.join(_SCRIPT_DIR, 'device_names.json')

# Cache to avoid re-reading file on every call and to preserve data on errors
_cached_names: Optional[Dict[str, str]] = None
_cache_mtime: float = 0


def load_device_names() -> Dict[str, str]:
    """
    Load device name mappings from JSON file.
    
    Returns cached data if file hasn't changed.
    On error, returns cached data (or empty dict if no cache).
    Never returns partial/corrupted data.
    """
    global _cached_names, _cache_mtime
    
    # Check if file exists
    if not os.path.exists(DEVICE_NAMES_FILE):
        if _cached_names is not None:
            return _cached_names.copy()
        return {}
    
    # Check if file has changed since last read
    try:
        current_mtime = os.path.getmtime(DEVICE_NAMES_FILE)
        if _cached_names is not None and current_mtime == _cache_mtime:
            return _cached_names.copy()
    except OSError as e:
        logger.warning(f"Could not stat device names file: {e}")
        if _cached_names is not None:
            return _cached_names.copy()
        return {}
    
    # Try to load the file
    try:
        with open(DEVICE_NAMES_FILE, 'r') as f:
            content = f.read()
        
        # Don't accept empty or whitespace-only files
        if not content.strip():
            logger.warning("Device names file is empty, using cached data")
            if _cached_names is not None:
                return _cached_names.copy()
            return {}
        
        data = json.loads(content)
        
        # Validate it's a dict
        if not isinstance(data, dict):
            logger.error(f"Device names file contains {type(data).__name__}, expected dict")
            if _cached_names is not None:
                return _cached_names.copy()
            return {}
        
        # Update cache
        _cached_names = data
        _cache_mtime = current_mtime
        return _cached_names.copy()
        
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse device names JSON: {e}")
        # Return cached data rather than empty dict
        if _cached_names is not None:
            logger.info("Using cached device names due to parse error")
            return _cached_names.copy()
        return {}
    except Exception as e:
        logger.error(f"Failed to load device names: {e}")
        if _cached_names is not None:
            return _cached_names.copy()
        return {}


def save_device_names(names: Dict[str, str]) -> bool:
    """
    Save device name mappings to JSON file using atomic write.
    
    Args:
        names: Dictionary of device_id -> friendly_name
        
    Returns:
        True if save succeeded, False otherwise
    """
    global _cached_names, _cache_mtime
    
    if not isinstance(names, dict):
        logger.error(f"Cannot save device names: expected dict, got {type(names).__name__}")
        return False
    
    # Filter out any None or empty string values
    clean_names = {k: v for k, v in names.items() if k and v}
    
    if not clean_names:
        logger.warning("Refusing to save empty device names dict")
        return False
    
    try:
        # Write to temp file first, then rename (atomic on POSIX)
        dir_name = os.path.dirname(DEVICE_NAMES_FILE)
        fd, tmp_path = tempfile.mkstemp(suffix='.tmp', prefix='device_names_', dir=dir_name)
        try:
            with os.fdopen(fd, 'w') as f:
                json.dump(clean_names, f, indent=2, sort_keys=True)
                f.write('\n')  # Trailing newline
            
            # Atomic rename
            os.replace(tmp_path, DEVICE_NAMES_FILE)
            
            # Update cache
            _cached_names = clean_names
            _cache_mtime = os.path.getmtime(DEVICE_NAMES_FILE)
            
            return True
        except Exception:
            # Clean up temp file on error
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
            
    except Exception as e:
        logger.error(f"Failed to save device names: {e}")
        return False


def get_device_name(device_id: str, fallback_name: Optional[str] = None) -> str:
    """
    Get friendly name for a device, using fallback if not mapped.
    
    Args:
        device_id: Unique device identifier (MAC, Tuya ID, etc)
        fallback_name: Name to use if not found in mapping (e.g., device's reported alias)
    
    Returns:
        Friendly name for the device
    
    Safety: Never overwrites existing entries. Only adds new entries if fallback_name
    is non-empty and the device_id doesn't already exist.
    """
    if not device_id:
        return device_id or ''
    
    names = load_device_names()
    
    # If we already have a mapping, always use it
    if device_id in names and names[device_id]:
        return names[device_id]
    
    # Only save new mapping if fallback is a meaningful non-empty string
    if fallback_name and fallback_name.strip() and fallback_name != device_id:
        logger.info(f"New device discovered: {device_id} -> {fallback_name}")
        names[device_id] = fallback_name
        save_device_names(names)
        return fallback_name
    
    # Last resort: use device ID
    return device_id


def set_device_name(device_id: str, friendly_name: str) -> bool:
    """
    Set/update friendly name for a device.
    
    Args:
        device_id: Unique device identifier
        friendly_name: Human-readable name
        
    Returns:
        True if save succeeded, False otherwise
    """
    if not device_id or not friendly_name:
        logger.warning(f"Refusing to set empty device name: {device_id!r} -> {friendly_name!r}")
        return False
    
    names = load_device_names()
    names[device_id] = friendly_name
    
    if save_device_names(names):
        logger.info(f"Updated device name: {device_id} -> {friendly_name}")
        return True
    return False
