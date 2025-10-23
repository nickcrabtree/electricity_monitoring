#!/usr/bin/env python3
"""
Shared helper functions for sending metrics to Graphite/Carbon server
Based on patterns from ~/scripts/graphite_temperatures.py
"""

import socket
import time
import datetime
import logging
from typing import List, Tuple, Optional

logger = logging.getLogger(__name__)


def send_metric(server: str, port: int, metric_name: str, value: float, timestamp: Optional[int] = None) -> bool:
    """
    Send a single metric to Carbon/Graphite server
    
    Args:
        server: Carbon server IP or hostname
        port: Carbon port (usually 2003)
        metric_name: Full metric path (e.g., 'home.electricity.kasa.lamp.power')
        value: Metric value
        timestamp: Unix timestamp (defaults to now)
    
    Returns:
        True if successful, False otherwise
    """
    if timestamp is None:
        timestamp = int(time.time())
    
    message = f"{metric_name} {value} {timestamp}\n"
    
    try:
        sock = socket.socket()
        sock.settimeout(5)
        sock.connect((server, port))
        sock.sendall(message.encode())
        sock.close()
        logger.debug(f"Sent: {metric_name} = {value}")
        return True
    except socket.error as exc:
        logger.error(f"Failed to send metric {metric_name}: {exc}")
        return False
    except Exception as exc:
        logger.error(f"Unexpected error sending metric {metric_name}: {exc}")
        return False


def send_metrics(server: str, port: int, metrics: List[Tuple[str, float]], timestamp: Optional[int] = None) -> int:
    """
    Send multiple metrics to Carbon/Graphite server in a single connection
    
    Args:
        server: Carbon server IP or hostname
        port: Carbon port (usually 2003)
        metrics: List of (metric_name, value) tuples
        timestamp: Unix timestamp (defaults to now)
    
    Returns:
        Number of metrics successfully sent
    """
    if timestamp is None:
        timestamp = int(time.time())
    
    if not metrics:
        return 0
    
    # Build message with all metrics
    lines = [f"{name} {value} {timestamp}" for name, value in metrics]
    message = '\n'.join(lines) + '\n'
    
    try:
        now = datetime.datetime.now()
        logger.info(now.strftime("%Y-%m-%d %H:%M:%S"))
        logger.debug(f"Sending {len(metrics)} metrics:\n{message}")
        
        sock = socket.socket()
        sock.settimeout(5)
        sock.connect((server, port))
        sock.sendall(message.encode())
        sock.close()
        
        logger.info(f"Successfully sent {len(metrics)} metrics")
        return len(metrics)
        
    except socket.error as exc:
        logger.error(f"Socket error sending metrics: {exc}")
        return 0
    except Exception as exc:
        logger.error(f"Unexpected error sending metrics: {exc}")
        return 0


def format_device_name(name: str) -> str:
    """
    Format device name for use in metric path
    Converts to lowercase, replaces spaces with underscores, removes special chars
    
    Args:
        name: Device name (e.g., "Living Room Lamp")
    
    Returns:
        Formatted name (e.g., "living_room_lamp")
    """
    # Convert to lowercase
    name = name.lower()
    # Replace spaces and dashes with underscores
    name = name.replace(' ', '_').replace('-', '_')
    # Remove any characters that aren't alphanumeric or underscore
    name = ''.join(c for c in name if c.isalnum() or c == '_')
    # Remove consecutive underscores
    while '__' in name:
        name = name.replace('__', '_')
    # Strip leading/trailing underscores
    name = name.strip('_')
    return name
