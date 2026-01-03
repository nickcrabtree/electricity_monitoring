#!/usr/bin/env bash
#
# Electricity Monitoring Watchdog
# Ensures kasa_to_graphite.py, tuya_local_to_graphite.py, and aggregate_energy.py 
# are running, and restarts them if they crash.
#
# Works on both blackpi2 (user pi) and flint (user nickc).
#
# Schedule with cron (every minute):
#   * * * * * /path/to/electricity_monitoring/watchdog_electricity.sh
#

set -u

# Auto-detect home directory and repo path
HOME_DIR="${HOME:-$(eval echo ~$USER)}"
REPO="${HOME_DIR}/code/electricity_monitoring"
LOG="${HOME_DIR}/electricity_watchdog.log"
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
  "${HOME_DIR}/electricity_kasa.log"

ensure_running "tuya_local" \
  "tuya_local_to_graphite.py" \
  "$PY $REPO/tuya_local_to_graphite.py" \
  "${HOME_DIR}/electricity_tuya_local.log"

ensure_running "tuya_cloud" \
  "tuya_cloud_to_graphite.py" \
  "$PY $REPO/tuya_cloud_to_graphite.py" \
  "${HOME_DIR}/electricity_tuya_cloud.log"

ensure_running "aggregate" \
  "aggregate_energy.py" \
  "$PY $REPO/aggregate_energy.py" \
  "${HOME_DIR}/electricity_aggregate.log"
