#!/usr/bin/env python3
"""
Tuya Cloud to Graphite Integration
Polls Tuya/SmartLife devices via Tuya IoT Cloud and sends metrics to Graphite/Carbon.

Usage:
    python tuya_cloud_to_graphite.py [--discover] [--once]

Requires tinytuya configured (tinytuya.json with apiKey/apiSecret/apiRegion/apiDeviceID).
"""

import asyncio
import time
import logging
import argparse
import json
import os
import datetime
import threading
from typing import Dict, List, Tuple, Any, Optional
import urllib.parse
import urllib.request

import tinytuya

import config
from graphite_helper import send_metrics, format_device_name

# Logging
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def _pick(d: Dict[str, Any], keys: List[str]):
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return None


# Default scales for metrics (when not found in devices.json)
DEFAULT_SCALES = {
    "cur_power": 1,      # power in deciwatts (divide by 10)
    "cur_voltage": 1,    # voltage in decivolts (divide by 10)
    "cur_current": 3,    # current in milliamps (divide by 1000)
}

# Map cloud API metric codes to DPS IDs for scale lookup
METRIC_CODE_TO_DPS = {
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

# Global variable to cache device scales
_device_scales = {}
_devices_json_mtime = 0


def load_device_scales() -> Dict[str, Dict[str, Dict[str, int]]]:
    """
    Load device scale information from devices.json
    
    Returns:
        Dict mapping device_id -> dps_id -> metric_code -> scale
        Example: {"device123": {"19": {"code": "cur_power", "scale": 1}}}
    """
    devices_json_path = os.path.join(os.path.dirname(__file__), "devices.json")
    
    if not os.path.exists(devices_json_path):
        logger.warning(f"devices.json not found at {devices_json_path}, using default scales")
        return {}
    
    try:
        with open(devices_json_path, 'r') as f:
            devices = json.load(f)
        
        scales_by_device = {}
        for device in devices:
            device_id = device.get('id')
            if not device_id:
                continue
            
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
                    scale = values['scale']
                    scales_by_device[device_id][dps_id] = {
                        'code': code,
                        'scale': int(scale)
                    }
        
        logger.info(f"Loaded scale information for {len(scales_by_device)} devices")
        return scales_by_device
        
    except Exception as e:
        logger.error(f"Error loading devices.json: {e}")
        return {}


def reload_device_scales_if_changed():
    """Check if devices.json has been modified and reload if necessary"""
    global _device_scales, _devices_json_mtime
    
    devices_json_path = os.path.join(os.path.dirname(__file__), "devices.json")
    if not os.path.exists(devices_json_path):
        return
    
    try:
        current_mtime = os.path.getmtime(devices_json_path)
        if current_mtime != _devices_json_mtime:
            logger.info("devices.json modified, reloading scale information")
            _device_scales = load_device_scales()
            _devices_json_mtime = current_mtime
    except Exception as e:
        logger.warning(f"Error checking devices.json modification time: {e}")


def normalize_value(device_id: str, metric_code: str, raw_value: Any) -> Optional[float]:
    """
    Normalize a raw value using the scale from devices.json
    
    Args:
        device_id: The device ID
        metric_code: The metric code (e.g., "cur_power", "cur_voltage")
        raw_value: The raw value from the device
        
    Returns:
        Normalized value or None if invalid
    """
    if raw_value is None:
        return None
    
    try:
        val = float(raw_value)
    except (TypeError, ValueError):
        logger.warning(f"Non-numeric raw value for {device_id} {metric_code}: {raw_value}")
        return None
    
    # Map metric code to DPS ID for scale lookup
    dps_id = METRIC_CODE_TO_DPS.get(metric_code)
    
    # Look up scale from device scales
    scale = None
    
    if dps_id and device_id in _device_scales and dps_id in _device_scales[device_id]:
        scale = _device_scales[device_id][dps_id].get('scale')
    
    # Fall back to default scale based on metric code
    if scale is None:
        # Try to find default by checking if metric_code matches a default key
        for default_key in DEFAULT_SCALES:
            if default_key in metric_code:
                scale = DEFAULT_SCALES[default_key]
                logger.debug(f"Using default scale {scale} for {device_id} {metric_code}")
                break
    
    if scale is None:
        # No scale found, return value as-is
        logger.debug(f"No scale found for {device_id} {metric_code}, returning raw value")
        return val
        # Apply scale: actual_value = raw_value / (10 ** scale)
    return val / (10 ** scale)


# --- Tuya Cloud quota management (free tier safeguards) ---
# Free tier limits (per calendar month) as of configuration time:
#   - ~26,000 API calls
#   - ~68,000 messages
# We enforce a conservative cap on API calls so we stay below the limit
# even if this process runs continuously. Messages are usually derived
# from API calls, so keeping calls under control keeps messages in check.

TUYA_CLOUD_API_CALLS_PER_MONTH = 13000 # Actually 26000 but leave headroom for homeassistant
TUYA_CLOUD_MESSAGES_PER_MONTH = 68000

# We derive an average per-minute allowance from the monthly call limit.
# To be safe for all calendar months, assume 31 days (the shortest average
# rate), so we never exceed the 26k/month cap even in a long month if we
# sustain that rate continuously.
TUYA_CLOUD_MINUTES_PER_MONTH = 31 * 24 * 60
TUYA_CLOUD_CALLS_PER_MINUTE = TUYA_CLOUD_API_CALLS_PER_MONTH / TUYA_CLOUD_MINUTES_PER_MONTH
TUYA_CLOUD_CALLS_PER_SECOND = TUYA_CLOUD_CALLS_PER_MINUTE / 60.0

# Allow at most one minute worth of calls as burst. This keeps the short-term
# rate close to the per-minute allowance while still allowing small bursts.
# Allow a larger burst so we can accumulate enough tokens to poll all devices in one sweep.
# This keeps the long-term average rate the same (TUYA_CLOUD_CALLS_PER_SECOND)
# but permits up to roughly 6 hours worth of calls to be spent in a single run.
TUYA_CLOUD_MAX_BURST = max(5.0, TUYA_CLOUD_CALLS_PER_SECOND * 3600.0 * 6.0)



# Quota state is persisted on disk so restarts do not reset counters
_TUYA_CLOUD_QUOTA_STATE_FILE = os.path.join(os.path.dirname(__file__), 'tuya_cloud_quota_state.json')
_TUYA_CLOUD_QUOTA_LOCK = threading.Lock()


def _tuya_cloud_current_month_key() -> str:
    """Return the current calendar month key as YYYY-MM in UTC."""
    now = datetime.datetime.utcnow()
    return f"{now.year:04d}-{now.month:02d}"


def _tuya_cloud_load_quota_state() -> dict:
    """Load persisted quota state from disk, or return defaults."""
    state = {
        'month': _tuya_cloud_current_month_key(),
        'api_calls': 0,
    }
    try:
        if os.path.exists(_TUYA_CLOUD_QUOTA_STATE_FILE):
            with open(_TUYA_CLOUD_QUOTA_STATE_FILE, 'r') as f:
                data = json.load(f)
            if isinstance(data, dict):
                state.update({k: v for k, v in data.items() if k in state})
    except Exception as e:  # pragma: no cover - defensive
        logger.warning(f"Failed to load Tuya cloud quota state: {e}")
    return state


def _tuya_cloud_save_quota_state(state: dict) -> None:
    """Persist quota state to disk in a best-effort manner."""
    try:
        tmp_path = _TUYA_CLOUD_QUOTA_STATE_FILE + '.tmp'
        with open(tmp_path, 'w') as f:
            json.dump(state, f)
        os.replace(tmp_path, _TUYA_CLOUD_QUOTA_STATE_FILE)
    except Exception as e:  # pragma: no cover - defensive
        logger.warning(f"Failed to save Tuya cloud quota state: {e}")


def _tuya_cloud_can_spend(api_calls: int) -> bool:
    """Return True if we are allowed to make additional cloud API calls.

    This enforces a rolling rate limit based on the derived per-minute
    allowance from the monthly Tuya Cloud free-tier limits. Internally it
    uses a simple token bucket so the *average* rate never exceeds
    TUYA_CLOUD_CALLS_PER_SECOND when the process runs continuously.
    """
    if api_calls <= 0:
        return True

    # Token bucket state (float tokens so we can work with sub-1 rates)
    global _TUYA_CLOUD_TOKENS, _TUYA_CLOUD_LAST_REFILL
    try:
        _ = _TUYA_CLOUD_TOKENS
        _ = _TUYA_CLOUD_LAST_REFILL
    except NameError:
        _TUYA_CLOUD_TOKENS = float(TUYA_CLOUD_MAX_BURST)
        _TUYA_CLOUD_LAST_REFILL = time.monotonic()

    now = time.monotonic()
    elapsed = max(0.0, now - _TUYA_CLOUD_LAST_REFILL)
    _TUYA_CLOUD_LAST_REFILL = now

    # Refill tokens at the configured per-second rate
    refill = elapsed * TUYA_CLOUD_CALLS_PER_SECOND
    _TUYA_CLOUD_TOKENS = min(float(TUYA_CLOUD_MAX_BURST), _TUYA_CLOUD_TOKENS + refill)

    # Check if we have enough tokens for this operation
    required = float(api_calls)
    if _TUYA_CLOUD_TOKENS >= required:
        _TUYA_CLOUD_TOKENS -= required
        return True

    # Not enough budget right now; skip this call.
    logger.info(
        "Tuya cloud rate limit reached: need %.2f tokens, have %.2f; "
        "skipping API call",
        required,
        _TUYA_CLOUD_TOKENS,
    )
    return False




_TUYA_LOCAL_STATE_FILE = os.path.join(os.path.dirname(__file__), 'tuya_local_state.json')
# Consider a device "covered" by local polling if we saw a success recently.
_LOCAL_SUCCESS_TTL_SECONDS = 10 * getattr(config, 'SMART_PLUG_POLL_INTERVAL', 30)
# When checking Graphite for cross-host local coverage we can use a
# similar time window; allow override via config if needed.
_LOCAL_GRAPHITE_TTL_SECONDS = getattr(
    config,
    'TUYA_LOCAL_GRAPHITE_TTL_SECONDS',
    _LOCAL_SUCCESS_TTL_SECONDS,
)


def _tuya_cloud_available_tokens() -> float:
    """Return current token bucket balance after refilling.

    This mirrors the refill logic in _tuya_cloud_can_spend but does not
    consume tokens, so callers can decide whether to run a full sweep or
    skip this iteration to let tokens accumulate.
    """
    global _TUYA_CLOUD_TOKENS, _TUYA_CLOUD_LAST_REFILL
    try:
        _ = _TUYA_CLOUD_TOKENS
        _ = _TUYA_CLOUD_LAST_REFILL
    except NameError:
        _TUYA_CLOUD_TOKENS = float(TUYA_CLOUD_MAX_BURST)
        _TUYA_CLOUD_LAST_REFILL = time.monotonic()

    now = time.monotonic()
    elapsed = max(0.0, now - _TUYA_CLOUD_LAST_REFILL)
    _TUYA_CLOUD_LAST_REFILL = now

    refill = elapsed * TUYA_CLOUD_CALLS_PER_SECOND
    _TUYA_CLOUD_TOKENS = min(float(TUYA_CLOUD_MAX_BURST), _TUYA_CLOUD_TOKENS + refill)
    return float(_TUYA_CLOUD_TOKENS)


def _load_recent_local_successes(now: Optional[float] = None) -> dict[str, float]:
    """Load device IDs that have recent successful local (LAN) polling.

    The companion script tuya_local_to_graphite.py records per-device
    last_success timestamps into TUYA_LOCAL_STATE_FILE. We treat entries
    older than _LOCAL_SUCCESS_TTL_SECONDS as stale.
    """
    if now is None:
        now = time.time()
    try:
        if not os.path.exists(_TUYA_LOCAL_STATE_FILE):
            return {}
        with open(_TUYA_LOCAL_STATE_FILE, 'r') as f:
            data = json.load(f)
    except Exception:
        return {}

    devices = {}
    try:
        devs = data.get('devices', {}) if isinstance(data, dict) else {}
    except AttributeError:
        devs = {}

    for dev_id, info in devs.items():
        if not isinstance(info, dict):
            continue
        ts = info.get('last_success_ts')
        if isinstance(ts, (int, float)) and ts >= now - _LOCAL_SUCCESS_TTL_SECONDS:
            devices[str(dev_id)] = float(ts)
    return devices


def _graphite_has_recent_local_metrics(dev: dict[str, Any], now: Optional[float] = None) -> bool:
    """Return True if Graphite has recent local Tuya metrics for this device.

    This lets a Tuya Cloud poller running on one host honour local‑LAN
    coverage from *any* host, because all local scripts ultimately send
    metrics to the same Graphite instance.
    """
    if now is None:
        now = time.time()
    if not isinstance(dev, dict):
        return False

    name = dev.get('name') or dev.get('dev_name') or dev.get('id') or dev.get('uuid')
    if not name:
        return False

    metric_name = format_device_name(name)
    target = f"{config.METRIC_PREFIX}.tuya.{metric_name}.power_watts"

    params = urllib.parse.urlencode(
        {
            'target': target,
            'from': f'-{int(_LOCAL_GRAPHITE_TTL_SECONDS)}s',
            'format': 'json',
            'maxDataPoints': '1',
        }
    )
    url = f"http://{config.CARBON_SERVER}/render?{params}"

    try:
        with urllib.request.urlopen(url, timeout=3) as resp:
            payload = resp.read()
        data = json.loads(payload.decode('utf-8'))
    except Exception as e:  # Graphite down or HTTP error – fall back to file-based hints only.
        logger.debug(f"Graphite local-coverage check failed for {target}: {e}")
        return False

    if not isinstance(data, list):
        return False

    for series in data:
        if not isinstance(series, dict):
            continue
        points = series.get('datapoints') or []
        for value, ts in points:
            if value is not None and isinstance(ts, (int, float)) and ts >= now - _LOCAL_GRAPHITE_TTL_SECONDS:
                return True
    return False


def _filter_devices_needing_cloud(devices: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return only devices that are not recently covered by local polling.

    A device is considered "covered" if either:
    - tuya_local_to_graphite on this host has a recent success recorded in
      tuya_local_state.json, or
    - any host has recently emitted Tuya power metrics for this device to
      Graphite (checked via HTTP render API).
    """
    if not devices:
        return []

    now = time.time()
    recent_local = _load_recent_local_successes(now)

    filtered: list[dict[str, Any]] = []
    skipped = 0
    for dev in devices:
        if not isinstance(dev, dict):
            filtered.append(dev)
            continue

        dev_id = dev.get('id') or dev.get('uuid')
        locally_ok = False

        # 1) Same-host hint from tuya_local_state.json
        if dev_id and str(dev_id) in recent_local:
            locally_ok = True
        else:
            # 2) Cross-host hint via Graphite metrics
            if _graphite_has_recent_local_metrics(dev, now):
                locally_ok = True

        if locally_ok:
            skipped += 1
            continue

        filtered.append(dev)

    if skipped:
        logger.info(
            'Skipping %d Tuya devices in cloud poll because they are recently '
            'reachable via local Tuya polling on at least one host',
            skipped,
        )
    return filtered


async def _cloud():

    # tinytuya.Cloud() reads tinytuya.json by default
    return tinytuya.Cloud()


async def cloud_list_devices(cloud, enforce_quota: bool = True) -> List[Dict[str, Any]]:
    """
    Get list of devices from Tuya cloud with defensive parsing
    Handles both list-of-dicts and error responses
    """
    def _list():
        try:
            # Enforce Tuya Cloud monthly API quota (getdevices = 1 call)
            if enforce_quota and not _tuya_cloud_can_spend(1):
                logger.info("Skipping Tuya cloud device list due to quota cap")
                return []

            result = cloud.getdevices()
            
            # Handle string response (error or JSON)
            if isinstance(result, str):
                try:
                    result = json.loads(result)
                except json.JSONDecodeError:
                    logger.error(f"Device list is non-JSON string: {repr(result)[:200]}")
                    return []
            
            # Handle dict response (might have 'result' field with device list)
            if isinstance(result, dict):
                # Check for Tuya Cloud error responses
                if 'Error' in result or 'Err' in result:
                    err_msg = result.get('Payload') or result.get('Error') or str(result)
                    logger.error(f"Tuya Cloud API error: {err_msg}")
                    return []
                # Check if it's an error response
                if 'success' in result and not result['success']:
                    logger.error(f"Device list error: {result.get('msg', 'unknown')}")
                    return []
                # Try to extract device list from 'result' field
                if 'result' in result:
                    result = result['result']
                else:
                    # Entire dict might be a single device
                    logger.warning(f"Device list is dict, not list. Treating as single device.")
                    return [result]
            
            # Now expect a list
            if not isinstance(result, list):
                logger.error(f"Device list unexpected type after parsing: {type(result)}")
                return []
            
            # Filter out non-dict items
            devices = []
            for item in result:
                if isinstance(item, dict):
                    devices.append(item)
                elif isinstance(item, str):
                    # Try to parse as JSON
                    try:
                        parsed = json.loads(item)
                        if isinstance(parsed, dict):
                            devices.append(parsed)
                        else:
                            logger.warning(f"Device item parsed but not a dict: {type(parsed)}")
                    except json.JSONDecodeError:
                        logger.warning(f"Device item is unparseable string: {repr(item)[:100]}")
                else:
                    logger.warning(f"Device item unexpected type: {type(item)}")
            
            return devices
            
        except Exception as e:
            logger.error(f"Error getting device list: {e}", exc_info=True)
            return []
    
    return await asyncio.to_thread(_list)


async def cloud_get_status(cloud, device_id: str) -> Dict[str, Any]:
    """Get device status from Tuya cloud with defensive parsing"""
    def _status():
        try:
            # Each status call consumes one Tuya Cloud API call
            if not _tuya_cloud_can_spend(1):
                logger.info(f"Skipping Tuya cloud status for {device_id} due to quota cap")
                return {}

            resp = cloud.getstatus(device_id)
            return normalize_tuya_response(resp, device_id)
        except Exception as e:
            logger.error(f"Cloud API error for {device_id}: {e}")
            return {}
    return await asyncio.to_thread(_status)


def normalize_tuya_response(resp: Any, device_id: str) -> Dict[str, Any]:
    """
    Normalize Tuya cloud API response which can be dict, list, or stringified JSON
    
    Args:
        resp: Raw response from tinytuya
        device_id: Device ID for logging
        
    Returns:
        Dictionary with normalized status keys
    """
    if not resp:
        return {}
    
    # Handle stringified JSON responses
    if isinstance(resp, str):
        try:
            resp = json.loads(resp)
        except json.JSONDecodeError:
            logger.error(f"{device_id}: Response is non-JSON string: {repr(resp)[:200]}")
            return {}
    
    # Extract result field if present
    if isinstance(resp, dict):
        # Check for error response
        if 'success' in resp and not resp['success']:
            logger.warning(f"{device_id}: API returned error: {resp.get('msg', 'unknown')}")
            return {}
        
        result = resp.get('result')
        if result is None:
            # Response might already be the status dict
            result = resp
    else:
        result = resp
    
    # Handle stringified result
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except json.JSONDecodeError:
            logger.error(f"{device_id}: Result is non-JSON string: {repr(result)[:200]}")
            return {}
    
    # Parse result based on type
    status: Dict[str, Any] = {}
    
    if isinstance(result, list):
        # List of {'code': ..., 'value': ...} dicts
        for item in result:
            if isinstance(item, dict) and 'code' in item:
                status[item['code']] = item.get('value')
            else:
                logger.debug(f"{device_id}: Unexpected list item type: {type(item)}")
    
    elif isinstance(result, dict):
        # Already a status dictionary
        status = result
    
    else:
        logger.error(f"{device_id}: Unexpected result type {type(result)}: {repr(result)[:500]}")
        return {}
    
    return status


async def get_device_metrics(cloud, dev: Any) -> List[Tuple[str, float]]:
    """
    Extract metrics from a Tuya cloud device with defensive error handling
    
    Args:
        cloud: Tuya cloud instance
        dev: Device info (should be dict, but handle gracefully if not)
        
    Returns:
        List of (metric_name, value) tuples
    """
    metrics: List[Tuple[str, float]] = []
    
    # Defensive check: ensure dev is a dict
    if not isinstance(dev, dict):
        logger.error(f"Device is not a dict: {type(dev)} - {repr(dev)[:200]}")
        return metrics
    
    name = dev.get('name') or dev.get('dev_name') or dev.get('id', 'unknown')
    devid = dev.get('id') or dev.get('uuid')
    
    try:
        if not devid:
            logger.warning(f"Device {name} has no ID, skipping")
            return metrics
        
        status = await cloud_get_status(cloud, devid)
        
        if not status:
            logger.debug(f"No status data for {name} ({devid})")
            return metrics
        
        if not isinstance(status, dict):
            logger.error(f"{name}: Status is not a dict: {type(status)}")
            return metrics

        device_name = format_device_name(name)
        base = f"{config.METRIC_PREFIX}.tuya.{device_name}"

        # On/off state
        is_on = _pick(status, ['switch', 'switch_1', 'switch_0', 'power_switch'])
        if isinstance(is_on, bool):
            metrics.append((f"{base}.is_on", 1 if is_on else 0))

        # Power (watts)
        power_keys = ['cur_power', 'power', 'power_w', 'add_ele']
        p = _pick(status, power_keys)
        if p is not None:
            # Find which key was matched
            metric_code = None
            for key in power_keys:
                if key in status and status[key] == p:
                    metric_code = key
                    break
            if metric_code:
                pw = normalize_value(devid, metric_code, p)
                if pw is not None:
                    metrics.append((f"{base}.power_watts", pw))

        # Voltage (volts)
        voltage_keys = ['cur_voltage', 'voltage', 'va_voltage']
        v = _pick(status, voltage_keys)
        if v is not None:
            # Find which key was matched
            metric_code = None
            for key in voltage_keys:
                if key in status and status[key] == v:
                    metric_code = key
                    break
            if metric_code:
                vv = normalize_value(devid, metric_code, v)
                if vv is not None:
                    metrics.append((f"{base}.voltage_volts", vv))

        # Current (amps)
        current_keys = ['cur_current', 'electric_current', 'i_current']
        a = _pick(status, current_keys)
        if a is not None:
            # Find which key was matched
            metric_code = None
            for key in current_keys:
                if key in status and status[key] == a:
                    metric_code = key
                    break
            if metric_code:
                aa = normalize_value(devid, metric_code, a)
                if aa is not None:
                    metrics.append((f"{base}.current_amps", aa))

        logger.debug(f"Collected {len(metrics)} metrics from {name} ({devid})")
        
    except Exception as e:
        logger.error(f"Error collecting metrics for {name} ({devid}): {e}", exc_info=True)
    
    return metrics


async def poll_devices_once(cloud, devices: List[Dict[str, Any]]) -> int:
    all_metrics: List[Tuple[str, float]] = []
    tasks = [get_device_metrics(cloud, d) for d in devices]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for res in results:
        if isinstance(res, list):
            all_metrics.extend(res)
        else:
            logger.error(f"Device polling error: {res}")

    if not all_metrics:
        logger.warning("No Tuya cloud metrics collected")
        return 0

    count = send_metrics(config.CARBON_SERVER, config.CARBON_PORT, all_metrics)
    logger.info(f"Sent {count} Tuya cloud metrics to Graphite")
    return count


async def discover_and_print():
    cloud = await _cloud()
    devices = await cloud_list_devices(cloud)
    if not devices:
        print("No Tuya devices found in cloud project. Ensure app account is linked and APIs authorized.")
        return
    print(f"\nDiscovered {len(devices)} Tuya device(s) via Cloud:\n")
    for d in devices:
        name = d.get('name') or d.get('dev_name') or d.get('id')
        print(f"Name: {name}")
        print(f"  ID: {d.get('id') or d.get('uuid')}")
        print(f"  Category: {d.get('category')}")
        print(f"  Product: {d.get('product_name') or d.get('product_id')}")
        print(f"  Online: {d.get('online')}")
        print(f"  Metric name: {format_device_name(name)}")
        # Fetch a short status snapshot to help mapping
        try:
            status = await cloud_get_status(cloud, d.get('id') or d.get('uuid'))
            if status:
                print(f"  Status keys: {', '.join(list(status.keys())[:10])}{' ...' if len(status)>10 else ''}")
        except Exception:
            pass
        print()


async def poll_once():
    # Load device scales
    global _device_scales, _devices_json_mtime
    _device_scales = load_device_scales()
    devices_json_path = os.path.join(os.path.dirname(__file__), "devices.json")
    if os.path.exists(devices_json_path):
        _devices_json_mtime = os.path.getmtime(devices_json_path)
    
    cloud = await _cloud()
    devices = await cloud_list_devices(cloud)
    if not devices:
        print("No Tuya devices found in cloud project.")
        return
    print("\nPolling Tuya cloud devices...")
    count = await poll_devices_once(cloud, devices)
    print(f"\nSent {count} metrics to Graphite at {config.CARBON_SERVER}:{config.CARBON_PORT}")


async def main_loop():
    """
    Main monitoring loop - poll Tuya cloud devices continuously
    Robust: continues running even if API calls fail
    """
    logger.info("Starting Tuya Cloud to Graphite monitoring")
    logger.info(f"Graphite server: {config.CARBON_SERVER}:{config.CARBON_PORT}")
    logger.info(f"Poll interval: {config.SMART_PLUG_POLL_INTERVAL} seconds")
    logger.info(
        "Tuya Cloud rate limit: %.3f calls/min (%.5f calls/sec, burst %.2f calls)",
        TUYA_CLOUD_CALLS_PER_MINUTE,
        TUYA_CLOUD_CALLS_PER_SECOND,
        TUYA_CLOUD_MAX_BURST,
    )

    # Load device scales from devices.json
    global _device_scales, _devices_json_mtime
    _device_scales = load_device_scales()
    devices_json_path = os.path.join(os.path.dirname(__file__), "devices.json")
    if os.path.exists(devices_json_path):
        _devices_json_mtime = os.path.getmtime(devices_json_path)

    cloud = await _cloud()
    devices = await cloud_list_devices(cloud)
    
    if not devices:
        logger.warning("No Tuya devices found in cloud project initially. Will retry...")
    
    last_discovery = time.time()
    discovery_interval = 21600  # Refresh device list every 6 hours

    try:
        while True:
            try:
                # Reload device scales if devices.json has changed
                reload_device_scales_if_changed()
                
                # Poll devices if we have any, but avoid wasting cloud calls
                # on devices that are healthy via local LAN polling.
                if devices:
                    devices_to_poll = _filter_devices_needing_cloud(devices)
                    if not devices_to_poll:
                        logger.info("All Tuya devices recently reachable via local polling; skipping cloud poll")
                    else:
                        required_calls = float(len(devices_to_poll))
                        available = _tuya_cloud_available_tokens()
                        if available < required_calls:
                            logger.info(
                                "Not enough Tuya cloud tokens for full poll (need %.1f, have %.2f); skipping this iteration",
                                required_calls,
                                available,
                            )
                        else:
                            await poll_devices_once(cloud, devices_to_poll)
                else:
                    logger.warning("No Tuya devices to poll")
                
                # Refresh device list periodically
                if time.time() - last_discovery >= discovery_interval:
                    try:
                        logger.info("Refreshing Tuya cloud device list (scheduled 6h refresh)...")
                        new_devices = await cloud_list_devices(cloud, enforce_quota=False)
                        if new_devices:
                            devices = new_devices
                            logger.info(f"Refreshed device list: {len(devices)} devices")
                        last_discovery = time.time()
                    except Exception as e:
                        logger.error(f"Error refreshing cloud device list: {e}")
                
            except Exception as e:
                logger.error(f"Error in main loop iteration: {e}", exc_info=True)
            
            await asyncio.sleep(config.SMART_PLUG_POLL_INTERVAL)
            
    except KeyboardInterrupt:
        logger.info("Shutting down...")


def main():
    parser = argparse.ArgumentParser(description='Tuya Cloud to Graphite Integration')
    parser.add_argument('--discover', action='store_true', help='Discover cloud devices and exit')
    parser.add_argument('--once', action='store_true', help='Poll once and exit (for testing)')
    args = parser.parse_args()

    if args.discover:
        asyncio.run(discover_and_print())
    elif args.once:
        asyncio.run(poll_once())
    else:
        asyncio.run(main_loop())


if __name__ == '__main__':
    main()
