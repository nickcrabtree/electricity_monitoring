#!/usr/bin/env bash
#
# Install/refresh the Graphite collector systemd units in this directory.
# Run ON THE TARGET PI after `git pull`. Idempotent; requires passwordless sudo.
#
# This copies every unit into /etc/systemd/system and reloads systemd. It
# enables at boot only units that were NOT already installed: a unit someone
# has disabled on this host stays disabled when the script is re-run after a
# pull (2026-10-31 it re-enabled a crashing unit). It does NOT start anything
# (start/restart is a deliberate separate step so a migration can stop any old
# instance first). SYSTEMD_DIR overrides the target directory for tests.
#
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET="${SYSTEMD_DIR:-/etc/systemd/system}"
shopt -s nullglob
# Only our graphite-* collector units. A bare *.service glob would also pick up
# host-specific units kept in this repo for other hosts (e.g. flint's
# tuya-monitoring.service, User=nickc), which must not be installed here.
units=("$DIR"/graphite-*.service)
[ ${#units[@]} -gt 0 ] || { echo "no graphite-*.service files in $DIR"; exit 1; }

new=()
existing=()
for u in "${units[@]}"; do
  name="$(basename "$u")"
  if [ -e "$TARGET/$name" ]; then existing+=("$name"); else new+=("$name"); fi
  echo "installing $name"
  sudo -n cp "$u" "$TARGET/$name"
done

sudo -n systemctl daemon-reload
for name in ${new[@]+"${new[@]}"}; do
  sudo -n systemctl enable "$name"
done

echo "Newly installed + enabled: ${new[*]:-none}"
echo "Refreshed, enable state left as it was: ${existing[*]:-none}"
echo "Start with: sudo systemctl restart <unit>"
