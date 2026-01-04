#!/usr/bin/env python3
"""
Centralized metric scaling for Tuya devices.

This module provides unified scaling logic for power, voltage, and current
metrics from Tuya devices. It supports:
1. Device-specific scales from devices.json mapping
2. Product-type defaults (by product_id) for automatic scaling of new devices
3. DPS-based fallback defaults

Usage:
    from metric_scaling import MetricScaler
    
    scaler = MetricScaler()
    
    # For local Tuya (using DPS IDs)
    power_watts = scaler.normalize_by_dps(device_id, "19", raw_value, product_id=product_id)
    
    # For cloud Tuya (using metric codes)
    power_watts = scaler.normalize_by_code(device_id, "cur_power", raw_value, product_id=product_id)
"""

import json
import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


# Default scales by product_id
# Scale N means: actual_value = raw_value / (10 ** N)
# Scale 0 = no change, Scale 1 = divide by 10, Scale 3 = divide by 1000
PRODUCT_SCALES: Dict[str, Dict[str, int]] = {
    # ANTELA Smart Plug UK (mkdejkrgvhsfwfrd) - verified working
    "mkdejkrgvhsfwfrd": {
        "cur_power": 1,      # deciwatts -> watts
        "cur_voltage": 1,    # decivolts -> volts
        "cur_current": 0,    # milliamps (leave as mA, convert separately)
    },
    # ANTELA SMRAT PLUG (0mowk2uxxz6kokyk) - same hardware, same scales
    "0mowk2uxxz6kokyk": {
        "cur_power": 1,
        "cur_voltage": 1,
        "cur_current": 0,
    },
    # Circuit breaker / metering device (odhgp5hewa1o7mdn)
    "odhgp5hewa1o7mdn": {
        "cur_power": 1,
        "cur_voltage": 1,
        "cur_current": 0,
    },
    # Gosund Smart Plug (QBgRvx34KBPPmEea)
    "QBgRvx34KBPPmEea": {
        "cur_power": 1,
        "cur_voltage": 1,
        "cur_current": 0,
    },
}

# DPS ID to metric code mapping
DPS_TO_CODE: Dict[str, str] = {
    "4":  "cur_power",
    "6":  "cur_power",
    "18": "cur_current",
    "19": "cur_power",
    "20": "cur_voltage",
}

# Metric code to DPS ID mapping (reverse)
CODE_TO_DPS: Dict[str, str] = {
    "cur_power": "19",
    "power": "19",
    "power_w": "19",
    "add_ele": "19",
    "cur_voltage": "20",
    "voltage": "20",
    "va_voltage": "20",
    "cur_current": "18",
    "electric_current": "18",
    "i_current": "18",
}

# Fallback defaults when no product-specific scale exists
DEFAULT_SCALES: Dict[str, int] = {
    "cur_power": 1,      # deciwatts -> watts
    "cur_voltage": 1,    # decivolts -> volts
    "cur_current": 0,    # milliamps (handle conversion separately)
}

# Current is special: raw value is in mA, we want amps
CURRENT_MA_TO_AMPS_DIVISOR = 1000.0


class MetricScaler:
    """
    Handles metric value normalization for Tuya devices.
    
    Loads device-specific scales from devices.json and falls back to
    product-type defaults for devices without explicit mapping.
    """
    
    def __init__(self, devices_json_path: Optional[str] = None):
        if devices_json_path is None:
            devices_json_path = os.path.join(os.path.dirname(__file__), "devices.json")
        self._devices_json_path = devices_json_path
        self._devices_json_mtime: float = 0
        self._device_scales: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self._product_by_device: Dict[str, str] = {}
        self._reload_if_changed()
    
    def _reload_if_changed(self) -> None:
        """Reload scales from devices.json if the file has been modified."""
        if not os.path.exists(self._devices_json_path):
            return
        
        try:
            current_mtime = os.path.getmtime(self._devices_json_path)
            if current_mtime == self._devices_json_mtime:
                return
            
            with open(self._devices_json_path, 'r') as f:
                devices = json.load(f)
            
            scales_by_device: Dict[str, Dict[str, Dict[str, Any]]] = {}
            product_by_device: Dict[str, str] = {}
            
            for device in devices:
                device_id = device.get('id')
                if not device_id:
                    continue
                
                # Track product_id for each device
                product_id = device.get('product_id')
                if product_id:
                    product_by_device[device_id] = product_id
                
                # Load explicit mapping if present
                mapping = device.get('mapping', {})
                if not isinstance(mapping, dict):
                    continue
                
                scales_by_device[device_id] = {}
                for dps_id, dps_info in mapping.items():
                    if not isinstance(dps_info, dict):
                        continue
                    
                    code = dps_info.get('code')
                    values = dps_info.get('values', {})
                    if isinstance(values, dict) and 'scale' in values:
                        scales_by_device[device_id][dps_id] = {
                            'code': code,
                            'scale': int(values['scale'])
                        }
            
            self._device_scales = scales_by_device
            self._product_by_device = product_by_device
            self._devices_json_mtime = current_mtime
            logger.info(f"Loaded scaling info for {len(scales_by_device)} devices, "
                       f"{len(product_by_device)} with product_id")
            
        except Exception as e:
            logger.error(f"Error loading devices.json: {e}")
    
    def get_scale(self, device_id: str, metric_code: str, 
                  dps_id: Optional[str] = None,
                  product_id: Optional[str] = None) -> Optional[int]:
        """
        Get the scale factor for a metric.
        
        Lookup order:
        1. Device-specific mapping from devices.json
        2. Product-type default from PRODUCT_SCALES
        3. Generic default from DEFAULT_SCALES
        
        Args:
            device_id: The device ID
            metric_code: The metric code (e.g., "cur_power")
            dps_id: Optional DPS ID for direct lookup
            product_id: Optional product_id override (otherwise looked up)
            
        Returns:
            Scale factor (0, 1, 2, 3, etc.) or None if unknown
        """
        self._reload_if_changed()
        
        # Normalize metric_code
        canonical_code = self._canonical_code(metric_code)
        if dps_id is None:
            dps_id = CODE_TO_DPS.get(canonical_code)
        
        # 1. Try device-specific scale from devices.json
        if device_id in self._device_scales and dps_id:
            dps_info = self._device_scales[device_id].get(dps_id)
            if dps_info and 'scale' in dps_info:
                return dps_info['scale']
        
        # 2. Try product-type default
        if product_id is None:
            product_id = self._product_by_device.get(device_id)
        
        if product_id and product_id in PRODUCT_SCALES:
            product_scale = PRODUCT_SCALES[product_id].get(canonical_code)
            if product_scale is not None:
                logger.debug(f"Using product default scale {product_scale} for "
                           f"{device_id} ({product_id}) {canonical_code}")
                return product_scale
        
        # 3. Fall back to generic defaults
        if canonical_code in DEFAULT_SCALES:
            logger.debug(f"Using generic default scale for {device_id} {canonical_code}")
            return DEFAULT_SCALES[canonical_code]
        
        return None
    
    def _canonical_code(self, metric_code: str) -> str:
        """Map various metric code variants to canonical form."""
        # Power variants -> cur_power
        if metric_code in ('power', 'power_w', 'add_ele'):
            return 'cur_power'
        # Voltage variants -> cur_voltage
        if metric_code in ('voltage', 'va_voltage'):
            return 'cur_voltage'
        # Current variants -> cur_current
        if metric_code in ('electric_current', 'i_current'):
            return 'cur_current'
        return metric_code
    
    def normalize_by_dps(self, device_id: str, dps_id: str, raw_value: Any,
                         product_id: Optional[str] = None) -> Optional[float]:
        """
        Normalize a raw value using DPS ID lookup.
        
        For local Tuya polling where we have DPS IDs directly.
        
        Args:
            device_id: The device ID
            dps_id: The DPS ID (e.g., "19" for power)
            raw_value: Raw value from device
            product_id: Optional product_id for fallback lookup
            
        Returns:
            Normalized value or None if invalid
        """
        if raw_value is None:
            return None
        
        try:
            val = float(raw_value)
        except (TypeError, ValueError):
            logger.warning(f"Non-numeric value for {device_id} DPS {dps_id}: {raw_value}")
            return None
        
        metric_code = DPS_TO_CODE.get(dps_id)
        scale = self.get_scale(device_id, metric_code or "", dps_id=dps_id, 
                               product_id=product_id)
        
        if scale is None:
            logger.debug(f"No scale for {device_id} DPS {dps_id}, returning raw")
            return val
        
        scaled = val / (10 ** scale)
        
        # Special handling: current is in mA, convert to amps
        if metric_code == 'cur_current':
            scaled = scaled / CURRENT_MA_TO_AMPS_DIVISOR
        
        return scaled
    
    def normalize_by_code(self, device_id: str, metric_code: str, raw_value: Any,
                          product_id: Optional[str] = None) -> Optional[float]:
        """
        Normalize a raw value using metric code lookup.
        
        For cloud Tuya polling where we have metric codes.
        
        Args:
            device_id: The device ID
            metric_code: The metric code (e.g., "cur_power")
            raw_value: Raw value from device
            product_id: Optional product_id for fallback lookup
            
        Returns:
            Normalized value or None if invalid
        """
        if raw_value is None:
            return None
        
        try:
            val = float(raw_value)
        except (TypeError, ValueError):
            logger.warning(f"Non-numeric value for {device_id} {metric_code}: {raw_value}")
            return None
        
        dps_id = CODE_TO_DPS.get(metric_code)
        scale = self.get_scale(device_id, metric_code, dps_id=dps_id,
                               product_id=product_id)
        
        if scale is None:
            logger.debug(f"No scale for {device_id} {metric_code}, returning raw")
            return val
        
        scaled = val / (10 ** scale)
        
        # Special handling: current is in mA, convert to amps
        canonical = self._canonical_code(metric_code)
        if canonical == 'cur_current':
            scaled = scaled / CURRENT_MA_TO_AMPS_DIVISOR
        
        return scaled


# Module-level singleton for convenience
_scaler: Optional[MetricScaler] = None


def get_scaler() -> MetricScaler:
    """Get the module-level MetricScaler singleton."""
    global _scaler
    if _scaler is None:
        _scaler = MetricScaler()
    return _scaler


def normalize_tuya_value(device_id: str, metric_code: str, raw_value: Any,
                         product_id: Optional[str] = None,
                         dps_id: Optional[str] = None) -> Optional[float]:
    """
    Convenience function to normalize a Tuya metric value.
    
    Args:
        device_id: The device ID
        metric_code: The metric code (e.g., "cur_power") 
        raw_value: Raw value from device
        product_id: Optional product_id for fallback lookup
        dps_id: Optional DPS ID for direct lookup
        
    Returns:
        Normalized value or None if invalid
    """
    scaler = get_scaler()
    if dps_id:
        return scaler.normalize_by_dps(device_id, dps_id, raw_value, product_id)
    return scaler.normalize_by_code(device_id, metric_code, raw_value, product_id)
