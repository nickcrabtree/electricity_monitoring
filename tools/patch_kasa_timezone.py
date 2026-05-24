#!/usr/bin/env python3
"""
Patch python-kasa to handle legacy timezone names like 'GB'.

This works around https://github.com/python-kasa/python-kasa/issues/1508
where devices reporting 'GB' timezone cause ZoneInfoNotFoundError.

Usage:
    python patch_kasa_timezone.py          # Apply patch if needed
    python patch_kasa_timezone.py --check  # Check if patch is needed (exit 0 if patched, 1 if not)
    python patch_kasa_timezone.py --force  # Force re-apply patch
"""

import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path

# The marker we use to detect if our patch is applied
PATCH_MARKER = "_TZ_ALIASES"

# The patched file content
PATCHED_CONTENT = '''\
"""Module for caching ZoneInfos."""

from __future__ import annotations

import asyncio
from zoneinfo import ZoneInfo

# Map legacy timezone names to IANA names
# See: https://github.com/python-kasa/python-kasa/issues/1508
_TZ_ALIASES = {
    "GB": "Europe/London",
    "Zulu": "UTC",
}


class CachedZoneInfo(ZoneInfo):
    """Cache ZoneInfo objects."""

    _cache: dict[str, ZoneInfo] = {}

    @classmethod
    async def get_cached_zone_info(cls, time_zone_str: str) -> ZoneInfo:
        """Get a cached zone info object."""
        if cached := cls._cache.get(time_zone_str):
            return cached
        loop = asyncio.get_running_loop()
        zinfo = await loop.run_in_executor(None, _get_zone_info, time_zone_str)
        cls._cache[time_zone_str] = zinfo
        return zinfo


def _get_zone_info(time_zone_str: str) -> ZoneInfo:
    """Get a time zone object for the given time zone string."""
    tz_key = _TZ_ALIASES.get(time_zone_str, time_zone_str)
    return ZoneInfo(tz_key)
'''


def find_kasa_cachedzoneinfo() -> Path | None:
    """Find the cachedzoneinfo.py file in the kasa package."""
    try:
        import kasa
        kasa_path = Path(kasa.__file__).parent
        target = kasa_path / "cachedzoneinfo.py"
        if target.exists():
            return target
    except ImportError:
        pass
    return None


def is_patched(filepath: Path) -> bool:
    """Check if the file already has our patch applied."""
    content = filepath.read_text()
    return PATCH_MARKER in content


def apply_patch(filepath: Path, force: bool = False) -> bool:
    """Apply the timezone patch. Returns True if patch was applied."""
    if not force and is_patched(filepath):
        print(f"Patch already applied to {filepath}")
        return False

    # Create backup
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    backup_path = filepath.with_suffix(f".py.{timestamp}.bak")
    shutil.copy2(filepath, backup_path)
    print(f"Created backup: {backup_path}")

    # Write patched content
    filepath.write_text(PATCHED_CONTENT)
    print(f"Applied timezone patch to {filepath}")
    return True


def main():
    parser = argparse.ArgumentParser(description="Patch python-kasa timezone handling")
    parser.add_argument("--check", action="store_true", help="Check if patch is needed (exit 1 if not patched)")
    parser.add_argument("--force", action="store_true", help="Force re-apply patch even if already applied")
    args = parser.parse_args()

    filepath = find_kasa_cachedzoneinfo()
    if filepath is None:
        print("ERROR: Could not find kasa/cachedzoneinfo.py - is python-kasa installed?", file=sys.stderr)
        sys.exit(2)

    print(f"Found: {filepath}")

    if args.check:
        if is_patched(filepath):
            print("Patch is applied")
            sys.exit(0)
        else:
            print("Patch is NOT applied")
            sys.exit(1)

    apply_patch(filepath, force=args.force)


if __name__ == "__main__":
    main()
