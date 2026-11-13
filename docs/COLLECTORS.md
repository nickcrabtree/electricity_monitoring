# Collectors and command reference

Commands run from the repository root with the electricity conda environment active.
`--once` writes metrics and runtime state; it is not a read-only smoke test.

- **One-shot commands (tests, single scripts)**: `conda run -n electricity ...` is fine, and is what `~/code/AGENT_POLICY.md` recommends for one-shot invocations — see [development](DEVELOPMENT.md).
- **Long-running/daemon scripts** (the monitoring loops below): avoid one-line `conda run -n electricity ...`; it buffers stdout/stderr and makes long-running processes hard to debug. Always `conda activate electricity` first, then run the command directly in that shell.

## Common commands

### Kasa integration (`kasa_to_graphite.py`)

```bash
python kasa_to_graphite.py --discover  # Discover devices
python kasa_to_graphite.py --once      # Single live cycle
python kasa_to_graphite.py             # Continuous monitoring
```

### Tuya integrations

#### Local LAN (`tuya_local_to_graphite.py`)

```bash
python tuya_local_to_graphite.py --discover  # Discover devices on the local network (+ configured remote subnets via SSH)
python tuya_local_to_graphite.py --once      # Single cycle
python tuya_local_to_graphite.py             # Continuous monitoring
```

Device-specific power/voltage/current scales are loaded from `devices.json` and automatically reloaded when the file changes. A device whose local key cannot be obtained through discovery can be supplied through the mode-`0600`, untracked file `~/.config/electricity-monitoring/tuya_local_devices.json`; its `id`, `name`, `ip`, `key`, and `version` override discovery without exposing the key in Git or logs.

`--discover` also scans the subnets listed for this hostname in `config.TUYA_REMOTE_DISCOVERY_HOSTS` (on quartz: flint's `192.168.1.0/24`, over the reverse SSH tunnel), so devices that only flint can reach - currently the two oven metering breakers - show up too, and it lists any `devices.json` device no subnet saw. Continuous polling is unaffected: each Pi still polls only its own subnet. See [device discovery](DEVICE_DISCOVERY.md).

#### Tuya Cloud (`tuya_cloud_to_graphite.py`)

```bash
python -m tinytuya wizard                    # First-time setup (creates tinytuya.json)
python tuya_cloud_to_graphite.py --discover  # Discover devices via the Tuya cloud
python tuya_cloud_to_graphite.py --once      # Single cycle
python tuya_cloud_to_graphite.py             # Continuous monitoring
```

`tuya_cloud_to_graphite.py` also honors per-device scaling from `devices.json`, similar to the local path.

#### HA-bridge fallback (`tuya_ha_bridge_to_graphite.py`)

```bash
python tuya_ha_bridge_to_graphite.py --discover  # Show current values, and which devices are already covered locally
python tuya_ha_bridge_to_graphite.py --once      # Single cycle
python tuya_ha_bridge_to_graphite.py             # Continuous monitoring
```

Polls Home Assistant's own (separate) Tuya cloud login for devices listed in `ha_bridge_devices.json`, for cases where `tuya_local_to_graphite.py` doesn't have a working `local_key` for a device yet (e.g. a brand-new device paired while the Tuya Cloud IoT Core subscription used by `tinytuya wizard` is lapsed). Requires `HA_TOKEN` (and optionally `HA_URL`) in the environment.

Runs continuously alongside `tuya_local_to_graphite.py` rather than needing to be manually toggled: each poll it checks `tuya_local_state.json` (the same file `tuya_cloud_to_graphite.py` already reads to avoid wasting cloud quota) and skips any device with a recent local-LAN success, so it's a no-op once local polling covers a device and picks it back up automatically if local polling for it stops working. `local_key`, once obtained, doesn't expire with the cloud subscription - only a *new* device paired during a lapse actually needs this fallback.

For devices that only expose a cumulative energy reading in HA (no instantaneous power), it derives an approximate `power_watts` from the change in `total_kwh` between polls (persisted in `ha_bridge_state.json`); it never overrides a device that has a real `power_watts` sensor.

### Aggregation (`aggregate_energy.py`)

```bash
python aggregate_energy.py --once  # Compute aggregate metrics once
python aggregate_energy.py         # Continuous aggregation loop
```

State file: `energy_state.json`.

### Presence monitoring (`presence_to_graphite.py`)

Configuration lives in `presence/people_config.yaml`. Key environment variables:

- Home Assistant: `HA_TOKEN` for API access (used by `presence/homeassistant_api.py`).
- Tado: `TADO_ACCESS_TOKEN` **or** `TADO_USERNAME`/`TADO_PASSWORD` (used by `presence/tado_api.py`, with state persisted in `presence/state.json`).

```bash
python presence_to_graphite.py --discover  # Discover WiFi devices, view mapping suggestions / MAC-learning hints
python presence_to_graphite.py --once      # Single presence update cycle
python presence_to_graphite.py             # Continuous monitoring loop
```

Operational runbooks and deeper presence details are in [presence operations](PRESENCE_OPERATIONS.md) and [MAC learning](MAC_LEARNING.md).

### MQTT switching bridge (flint)

The continuous local Tuya poller can bridge Home Assistant switches through MQTT,
using the poller's own device connections and per-device locks. It is enabled only
on `config.MQTT_BRIDGE_HOSTS` (default `['flint']`) with an untracked
`mqtt_local.json` beside the script containing `username` and `password`. Protect
this credential file with mode `0600`. The broker defaults to `homeassistant.lan:1883`.
No credentials are logged. Blackpi2 and one-shot/discovery runs do not bridge.

Retained discovery under `homeassistant/switch/flint_<tuya_id>/config` and
`homeassistant/sensor/flint_<tuya_id>_power/config` exposes switches and measured
power (using the Graphite scaling). Discovery waits for the first successful poll
that reports DP 1; power entities require a decoded power reading. The flint
connectivity entity supplies the parent device. Reconnects republish discovery and
cached state. Device removal marks its availability offline.

`flint/status` supplies retained bridge availability and the offline last will;
`flint/<tuya_id>/availability`, `/state` and `/power` carry retained device
availability, ON/OFF state and watts. Send exactly `ON` or `OFF` without retain to
`flint/<tuya_id>/set`. Retained commands, unknown device IDs and other payloads are
ignored. Commands write only DP 1, then read back the real state, including after
failed writes. Startup, reconnect and re-scan never switch devices. Socket work
runs in threads under the same locks as polling; a timed-out thread keeps its lock
until it finishes. Broker outages retry in the background without blocking Graphite
polling. No second Tuya client or HA cloud command is involved.
