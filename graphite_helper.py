#!/usr/bin/env python3
"""
Shared helper functions for sending metrics to Graphite/Carbon server
Based on patterns from ~/scripts/graphite_temperatures.py
"""

import socket
import time
import logging
from typing import List, Tuple, Optional

logger = logging.getLogger(__name__)


def send_metric(server: str, port: int, metric_name: str, value: float, timestamp: Optional[int] = None) -> bool:
    """Send a single metric to Carbon/Graphite. Returns True on success."""
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
    """Send multiple metrics in a single TCP connection. Returns count sent."""
    if timestamp is None:
        timestamp = int(time.time())
    
    if not metrics:
        return 0
    
    # Build message with all metrics
    lines = [f"{name} {value} {timestamp}" for name, value in metrics]
    message = '\n'.join(lines) + '\n'
    
    try:
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
    """Normalize device name to a lowercase_underscored metric path segment."""
    name = name.lower().replace(' ', '_').replace('-', '_')
    name = ''.join(c for c in name if c.isalnum() or c == '_')
    while '__' in name:
        name = name.replace('__', '_')
    return name.strip('_')
