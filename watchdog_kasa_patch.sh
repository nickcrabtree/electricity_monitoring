#!/bin/bash
#
# Watchdog to ensure python-kasa timezone patch is applied.
# Run via cron, e.g.: */5 * * * * /home/nickc/code/electricity_monitoring/watchdog_kasa_patch.sh
#
# This detects if python-kasa was upgraded (removing our patch) and re-applies it.
# It also restarts kasa_to_graphite.py if the patch was applied.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PATCH_SCRIPT="$SCRIPT_DIR/patch_kasa_timezone.py"
LOG_FILE="$SCRIPT_DIR/kasa_patch_watchdog.log"

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $1" >> "$LOG_FILE"
}

# Check if patch script exists
if [ ! -f "$PATCH_SCRIPT" ]; then
    log "ERROR: Patch script not found: $PATCH_SCRIPT"
    exit 1
fi

# Check if patch is applied
if python3 "$PATCH_SCRIPT" --check > /dev/null 2>&1; then
    # Patch is applied, nothing to do
    exit 0
fi

# Patch is not applied - apply it
log "Patch not applied, applying now..."
OUTPUT=$(python3 "$PATCH_SCRIPT" 2>&1)
RESULT=$?

if [ $RESULT -eq 0 ]; then
    log "Patch applied successfully: $OUTPUT"
    
    # Restart kasa_to_graphite.py if running
    if pgrep -f "kasa_to_graphite.py" > /dev/null; then
        log "Restarting kasa_to_graphite.py..."
        pkill -f "kasa_to_graphite.py"
        sleep 2
        cd "$SCRIPT_DIR"
        nohup python3 kasa_to_graphite.py >> "$SCRIPT_DIR/kasa_to_graphite.log" 2>&1 &
        log "kasa_to_graphite.py restarted with PID $!"
    fi
else
    log "ERROR: Failed to apply patch (exit $RESULT): $OUTPUT"
    exit 1
fi
