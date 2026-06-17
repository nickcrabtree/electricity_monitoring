#!/usr/bin/env bash
#
# Install/refresh the Graphite collector systemd units in this directory.
# Run ON THE TARGET PI after `git pull`. Idempotent; requires passwordless sudo.
#
# This copies the units into /etc/systemd/system, reloads systemd, and enables
# them at boot. It does NOT start them (start/restart is a deliberate separate
# step so a migration can stop any old instance first).
#
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
shopt -s nullglob
units=("$DIR"/*.service)
[ ${#units[@]} -gt 0 ] || { echo "no .service files in $DIR"; exit 1; }

for u in "${units[@]}"; do
  name="$(basename "$u")"
  echo "installing $name"
  sudo -n cp "$u" "/etc/systemd/system/$name"
done

sudo -n systemctl daemon-reload
for u in "${units[@]}"; do
  sudo -n systemctl enable "$(basename "$u")"
done

echo "Installed + enabled: ${units[*]##*/}"
echo "Start with: sudo systemctl restart <unit>"
