#!/usr/bin/env bash
# Enable the Home Assistant presence source for presence-monitoring.service
# and restart it so the current working tree (Tado/HA result caching) is live.
#
# Run as root on the host that runs the service (quartz):
#     sudo systemd/presence_enable_ha_and_restart.sh
#
# What it does, in order:
#   1. backs up /etc/presence-monitoring.env to /etc/presence-monitoring.env.bak.<timestamp>
#   2. writes HA_TOKEN and HA_URL into that file, taken from the repo's
#      secrets_local.env (replacing any existing values, keeping every other line)
#   3. restarts presence-monitoring.service
#   4. prints the startup lines and ~2 minutes of presence lines so you can see
#      "Initialized Home Assistant API client" and tado=1 / ha=1 holding steady.
#
# Idempotent: re-running only rewrites the two HA_ lines and restarts again.
set -euo pipefail

ENV_FILE=/etc/presence-monitoring.env
SERVICE=presence-monitoring.service
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SECRETS="$REPO_DIR/secrets_local.env"
WATCH_SECONDS="${WATCH_SECONDS:-120}"

if [ "$(id -u)" -ne 0 ]; then
  echo "ERROR: run as root (sudo $0)" >&2
  exit 1
fi
if [ ! -r "$SECRETS" ]; then
  echo "ERROR: cannot read $SECRETS" >&2
  exit 1
fi

# Pull the two values out of secrets_local.env without sourcing the whole file.
ha_token="$(sed -n 's/^HA_TOKEN=//p' "$SECRETS" | head -n1 | tr -d '"'"'")"
ha_url="$(sed -n 's/^HA_URL=//p' "$SECRETS" | head -n1 | tr -d '"'"'")"
ha_url="${ha_url:-http://homeassistant.lan:8123}"
if [ -z "$ha_token" ]; then
  echo "ERROR: HA_TOKEN is empty in $SECRETS" >&2
  exit 1
fi

# 1. Backup (only if the file exists; the unit uses EnvironmentFile=- so it may not).
if [ -f "$ENV_FILE" ]; then
  backup="$ENV_FILE.bak.$(date +%Y%m%d-%H%M%S)"
  cp -p "$ENV_FILE" "$backup"
  echo "Backed up $ENV_FILE -> $backup"
else
  echo "$ENV_FILE does not exist yet; creating it"
  : > "$ENV_FILE"
fi

# 2. Rewrite into a temp file, then move into place (never edit in place).
tmp="$(mktemp "$ENV_FILE.tmp.XXXXXX")"
trap 'rm -f "$tmp"' EXIT
grep -v -E '^(HA_TOKEN|HA_URL)=' "$ENV_FILE" > "$tmp" || true
if [ -s "$tmp" ] && [ "$(tail -c1 "$tmp" | od -An -c | tr -d ' ')" != '\n' ]; then
  echo >> "$tmp"
fi
{
  echo "HA_TOKEN=$ha_token"
  echo "HA_URL=$ha_url"
} >> "$tmp"
chmod 600 "$tmp"
chown root:root "$tmp"
mv "$tmp" "$ENV_FILE"
trap - EXIT
echo "Wrote HA_TOKEN and HA_URL ($ha_url) to $ENV_FILE; keys now present:"
sed -n 's/=.*//p' "$ENV_FILE" | sort | tr '\n' ' '; echo

# 3. Restart.
restart_at="$(date '+%Y-%m-%d %H:%M:%S')"
systemctl restart "$SERVICE"
systemctl --no-pager --lines=0 status "$SERVICE" | head -n 5

# 4. Verify.
echo
echo "=== startup lines ==="
sleep 5
journalctl -u "$SERVICE" --since "$restart_at" --no-pager \
  | grep -E 'Initialized|enabled but|Tado enabled|Home Assistant enabled' || true
echo
echo "=== watching presence for ${WATCH_SECONDS}s (tado=/ha= should hold between polls) ==="
sleep "$WATCH_SECONDS"
journalctl -u "$SERVICE" --since "$restart_at" --no-pager \
  | grep -E 'Presence sources|ERROR|WARNING' \
  | sed -E 's/.*INFO - Presence sources: /  /; s/; (charlie|archie|mo|tom):.*//' \
  | uniq -c
echo
echo "Done. Expect nick/susan tado=1 and ha=1 on every line while they are home."
