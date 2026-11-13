# Deployment architecture: dual-Pi model

This document describes the dual-Pi architecture introduced in November 2025.
See [operations](OPERATIONS.md) for later watchdog and deployment records and
[presence operations](PRESENCE_OPERATIONS.md) for the quartz service. Check the
installed units before changing a host; dated details are not live status.

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
- Maintains a **reverse SSH tunnel** to `quartz` for admin access (see [Flint SSH setup](FLINT_SSH_SETUP.md))

### `quartz` (192.168.86.x)

- SSH anchor host for reaching `flint`
- Connect to `flint` via: `ssh -p 2222 nickc@localhost`
- Runs presence monitoring; see [presence operations](PRESENCE_OPERATIONS.md).

## Configuration

Both Pis should use the default configuration:

```python
# config.py
LOCAL_ROLE = 'main_lan'  # or 'remote_lan' (functionally equivalent)
KASA_DISCOVERY_NETWORKS = [None]  # local subnet only
SSH_TUNNEL_ENABLED = False
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

See [Flint SSH setup](FLINT_SSH_SETUP.md) for full setup details.

## The OpenWrt router itself

The router between the two subnets is reachable from quartz on its main-LAN (WAN) side: `ssh openwrt` (alias in
quartz's `~/.ssh/config` for `root@openwrt.local`, quartz's key; recorded in
`~/scripts/cross_repo_notes/topics/host_access.md`). "No routing to 192.168.1.x" applies to the IoT subnet behind it,
not to the router. flint has no key on the router, so do not try from flint. OpenWrt 24.10.1; WAN static, upstream DNS
the Nest router.

DNS for flint (2026-11-13, Nick's go): the router's dnsmasq treats `.lan` as its own domain, so it answered NXDOMAIN
for main-LAN names. It now forwards `homeassistant.lan` to the Nest router (`server=/homeassistant.lan/192.168.86.1`,
the upstream it already uses) with a rebind-protection exception (`rebind_domain homeassistant.lan`), so flint's MQTT
bridge reaches the broker by name. Other main-LAN names still do not resolve on the IoT subnet; add a forward per name
the same way (`uci add_list dhcp.@dnsmasq[0].server=...`, `uci add_list dhcp.@dnsmasq[0].rebind_domain=...`, `uci
commit dhcp`, `/etc/init.d/dnsmasq restart`). Backup of the config before the change: `/etc/config/dhcp.2026-11-13-before-ha-forward`
on the router and `/ncloud/Nick/electricity_monitoring/backups/2026-11-13/openwrt_dhcp.before`.

## Deploying Code Updates

```bash
# Deploy to flint (via reverse tunnel):
ssh -p 2222 nickc@localhost 'cd ~/code/electricity_monitoring && git pull'

# Deploy to blackpi2:
ssh pi@blackpi2.local 'cd ~/code/electricity_monitoring && git pull'
```

### Host details

Remote settings below are historical observations; inspect the current clone
before relying on them. Publish reviewed commits under shared Git policy before
pulling on a host, and do not start duplicate collectors.

| Host | User | Access | Git remote |
|------|------|--------|------------|
| `flint` | `nickc` | `ssh -p 2222 nickc@localhost` (via quartz) | HTTPS (no GitHub SSH keys) |
| `blackpi2` | `pi` | `ssh pi@blackpi2.local` | SSH (`git@github.com:...`) |

## Legacy: Single-Host Cross-Subnet Mode

The codebase still supports the **legacy** pattern where a single host on 192.168.86.x uses SSH tunnels through OpenWrt to poll devices on 192.168.1.x.

To enable this mode (not recommended for normal use):

```python
# config.py
LOCAL_ROLE = 'single_host_cross_subnet'
SSH_TUNNEL_ENABLED = True
SSH_REMOTE_HOST = 'root@openwrt.lan'
```


## Why Dual-Pi?

Advantages over the legacy single-host approach:

1. **Simpler** — no SSH tunnel management or UDP forwarding
2. **More reliable** — no dependency on OpenWrt SSH or tunnel stability
3. **Lower latency** — direct LAN access to devices
4. **Isolated failures** — if one Pi goes down, the other subnet keeps reporting

The main trade-off is managing two Pis instead of one, but the reverse SSH tunnel makes remote access straightforward.

## Related Documentation

- [Flint SSH setup](FLINT_SSH_SETUP.md) — reverse SSH setup for `flint`
- [Development](DEVELOPMENT.md) — environment, tests and gates
