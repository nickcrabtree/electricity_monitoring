#!/usr/bin/env bash
#
# Electricity Monitoring Watchdog
# Ensures kasa_to_graphite.py, tuya_cloud_to_graphite.py, and aggregate_energy_enhanced.py 
# are running, and restarts them if they crash.
#
# Schedule with cron (every minute):
#   * * * * * /home/pi/code/electricity_monitoring/watchdog_electricity.sh
#

set -u

LOG="/home/pi/electricity_watchdog.log"
REPO="/home/pi/code/electricity_monitoring"
PY="/usr/bin/python3"

# Timestamp function
ts() { date -u '+%Y-%m-%dT%H:%M:%SZ'; }

# Ensure a script is running
ensure_running() {
  local name="$1"
  local pattern="$2"
  local start_cmd="$3"
  local log_file="$4"

  if pgrep -f "$pattern" >/dev/null 2>&1; then
    echo "$(ts) [$name] OK" >> "$LOG"
  else
    echo "$(ts) [$name] NOT RUNNING - restarting" >> "$LOG"
    # Start in background with disown to prevent zombie processes
    cd "$REPO" && nohup stdbuf -oL -eL $start_cmd >> "$log_file" 2>&1 &
    disown
    sleep 2
    
    if pgrep -f "$pattern" >/dev/null 2>&1; then
      echo "$(ts) [$name] restarted successfully" >> "$LOG"
    else
      echo "$(ts) [$name] FAILED to start - check $log_file" >> "$LOG"
    fi
  fi
}

# Ensure each monitoring script is running
ensure_running "kasa" \
  "kasa_to_graphite.py" \
  "$PY $REPO/kasa_to_graphite.py" \
  "/home/pi/electricity_kasa.log"

ensure_running "tuya_local" \
  "tuya_local_to_graphite.py" \
  "$PY $REPO/tuya_local_to_graphite.py" \
  "/home/pi/electricity_tuya_local.log"

ensure_running "aggregate" \
  "aggregate_energy_enhanced.py" \
  "$PY $REPO/aggregate_energy_enhanced.py" \
  "/home/pi/electricity_aggregate.log"
