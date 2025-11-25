# Current Deployment Architecture: Dual-Pi Model

This document describes the **recommended** architecture for the electricity monitoring system as of November 2025.

## Overview

The system uses **one Raspberry Pi per subnet**, with each Pi polling only devices on its local network:

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         Main LAN (192.168.86.0/24)                      │
│                                                                         │
│   ┌─────────────┐      ┌─────────────┐      ┌─────────────┐            │
│   │  blackpi2   │      │   Graphite  │      │   quartz    │            │
│   │ 192.168.86.x│      │192.168.86.123│     │192.168.86.x │            │
│   │             │──────▶│   :2003     │◀────│             │            │
│   │ Kasa/Tuya   │      └─────────────┘      │ SSH anchor  │            │
│   │ collector   │                           │ for flint   │            │
│   └─────────────┘                           └──────▲──────┘            │
│         │                                          │                   │
│   polls local                              reverse │ SSH               │
│   devices                                  tunnel  │ (port 2222)       │
│                                                    │                   │
└────────────────────────────────────────────────────┼───────────────────┘
                                                     │
                          ┌──────────────────────────┼──────────────────┐
                          │     OpenWrt Router       │                  │
                          │   NAT 192.168.1.x ──────▶│──▶ main LAN      │
                          │                                             │
                          └─────────────────────────────────────────────┘
                                                     │
┌────────────────────────────────────────────────────┼───────────────────┐
│                    Device LAN (192.168.1.0/24)     │                   │
│                                                    │                   │
│   ┌─────────────┐                                  │                   │
│   │    flint    │──────────────────────────────────┘                   │
│   │ 192.168.1.x │                                                      │
│   │             │──────▶ Graphite (via NAT)                            │
│   │ Kasa/Tuya   │                                                      │
│   │ collector   │                                                      │
│   └─────────────┘                                                      │
│         │                                                              │
│   polls local                                                          │
│   devices                                                              │
│                                                                        │
│   ┌──────────┐  ┌──────────┐  ┌──────────┐                            │
│   │ Kasa #1  │  │ Kasa #2  │  │ Tuya #1  │  ...                       │
│   └──────────┘  └──────────┘  └──────────┘                            │
│                                                                        │
└────────────────────────────────────────────────────────────────────────┘
```

## Host Responsibilities

### `blackpi2` (192.168.86.x)

- Runs `kasa_to_graphite.py` and `tuya_local_to_graphite.py`
- Discovers and polls Kasa/Tuya devices on **192.168.86.0/24 only**
- Sends metrics to Graphite at `192.168.86.123:2003`
- Does **not** use SSH tunnels or cross-subnet discovery

### `flint` (192.168.1.x)

- Runs `kasa_to_graphite.py` and `tuya_local_to_graphite.py`
- Discovers and polls Kasa/Tuya devices on **192.168.1.0/24 only**
- Sends metrics to Graphite via NAT (outbound traffic works)
- Maintains a **reverse SSH tunnel** to `quartz` for admin access (see `docs/HOWTO_flint_remote_ssh.md`)

### `quartz` (192.168.86.x)

- SSH anchor host for reaching `flint`
- Connect to `flint` via: `ssh -p 2222 nickc@localhost`

## Configuration

Both Pis should use the default configuration:

```python
# config.py
LOCAL_ROLE = 'main_lan'  # or 'remote_lan' (functionally equivalent)
KASA_DISCOVERY_NETWORKS = [None]  # local subnet only
SSH_TUNNEL_ENABLED = False
UDP_TUNNEL_ENABLED = False
```

This ensures each Pi only polls its own local devices.

## Accessing `flint` Remotely

Since there is no routing from 192.168.86.x to 192.168.1.x:

1. `flint` maintains a reverse SSH tunnel to `quartz`
2. From `quartz`, connect via: `ssh -p 2222 nickc@localhost`
3. From other machines, use `ProxyJump`:
   ```
   Host flint-via-quartz
       HostName localhost
       Port 2222
       User nickc
       ProxyJump quartz
   ```

See `docs/HOWTO_flint_remote_ssh.md` for full setup details.

## Deploying Code Updates

```bash
# On quartz or dev machine:
cd /path/to/electricity_monitoring
git push

# Deploy to flint (via reverse tunnel):
ssh -p 2222 nickc@localhost 'cd ~/code/electricity_monitoring && git pull'

# Deploy to blackpi2:
ssh pi@blackpi2.local 'cd ~/code/electricity_monitoring && git pull'
```

## Legacy: Single-Host Cross-Subnet Mode

The codebase still supports the **legacy** pattern where a single host on 192.168.86.x uses SSH tunnels through OpenWrt to poll devices on 192.168.1.x.

To enable this mode (not recommended for normal use):

```python
# config.py
LOCAL_ROLE = 'single_host_cross_subnet'
SSH_TUNNEL_ENABLED = True
SSH_REMOTE_HOST = 'root@openwrt.lan'
```

See these legacy docs for details:
- `SSH_TUNNEL_AUTO_DISCOVERY.md`
- `SSH_TUNNEL_IMPLEMENTATION_SUMMARY.md`
- `CROSS_SUBNET_SETUP.md`

## Why Dual-Pi?

Advantages over the legacy single-host approach:

1. **Simpler** — no SSH tunnel management or UDP forwarding
2. **More reliable** — no dependency on OpenWrt SSH or tunnel stability
3. **Lower latency** — direct LAN access to devices
4. **Isolated failures** — if one Pi goes down, the other subnet keeps reporting

The main trade-off is managing two Pis instead of one, but the reverse SSH tunnel makes remote access straightforward.

## Related Documentation

- `docs/HOWTO_flint_remote_ssh.md` — reverse SSH setup for `flint`
- `docs/ARCHITECTURE_REVIEW_flint_dual_subnet.md` — detailed analysis of architecture changes
- `WARP.md` — development guide for this repository
