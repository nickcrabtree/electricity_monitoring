# Architecture Review: Dual-Subnet Deployment with `blackpi2` and `flint`

## 1. Context and Goals

The original design assumed **one monitoring host on 192.168.86.0/24** that needed to see Kasa/Tuya devices on **192.168.1.0/24** behind an OpenWrt router. Cross-subnet access was implemented via:

- Static routes and OpenWrt configuration.
- SSH-based remote discovery and tunnelling (`ssh_tunnel_manager.py`, `udp_tunnel.py`).
- Docs and runbooks focused on cross-subnet discovery from the main LAN side.

The current reality is different:

- There is now **one Pi per subnet**:
  - `blackpi2` (or similar) on **192.168.86.x**.
  - `flint` on **192.168.1.x**.
- Outbound traffic from `flint` to the main LAN and the internet works via **NAT on OpenWrt**.
- Inbound traffic to `flint` from 192.168.86.x is not routable, but:
  - `flint` maintains a **reverse SSH tunnel** to `quartz` (see `docs/HOWTO_flint_remote_ssh.md`).
  - We can SSH into `flint` via `ssh -p 2222 nickc@localhost` on `quartz` / dev machine.

**Goal of this review:**

- Identify **code and documentation** that still assume a *single* monitoring host bridging across OpenWrt.
- Recommend changes so that:
  - Each Pi only discovers and polls **its own local subnet**.
  - OpenWrt-based SSH tunnelling and UDP tunnelling paths are clearly treated as **legacy/optional**.
  - Operational docs and configuration reflect the new dual-subnet, dual-Pi design.

---

## 2. High-Level Target Model

### 2.1 Desired responsibilities per host

- **`blackpi2` (192.168.86.0/24)**
  - Discovers and polls Kasa/Tuya devices on 192.168.86.x.
  - Sends metrics to Graphite (192.168.86.123:2003).
  - Does **not** attempt to reach 192.168.1.x devices directly.
  - Does **not** use SSH tunnels to OpenWrt for device access.

- **`flint` (192.168.1.0/24)**
  - Discovers and polls Kasa/Tuya devices on 192.168.1.x.
  - Sends metrics to the same Graphite instance.
  - Exposes an admin SSH path back to `quartz` via **reverse SSH** but otherwise behaves as a normal local collector for its subnet.

Consequences:

- **No SSH tunnel / UDP tunnel through OpenWrt** is required for normal monitoring.
- Cross-subnet logic should be **disabled by default** and clearly documented as legacy/advanced.
- Discovery configuration should be **local-only** on both Pis (e.g. `KASA_DISCOVERY_NETWORKS = [None]`).

---

## 3. Code Paths Tied to OpenWrt / Cross-Subnet Tunnelling

### 3.1 `config.py`

Relevant section:

- `KASA_DISCOVERY_NETWORKS = [ None ]` – already set to **local only**.
- SSH tunnel configuration:
  - `SSH_TUNNEL_ENABLED`
  - `SSH_REMOTE_HOST = 'root@openwrt.lan'`
  - `SSH_TUNNEL_SUBNET = '192.168.1.0/24'`
  - `SSH_USE_SSHPASS`, `SSH_PASSWORD_ENV_VAR`.
- UDP tunnel flags:
  - `UDP_TUNNEL_ENABLED`
  - `UDP_TUNNEL_*` ports and broadcast.

**Issues / observations:**

- These settings are still present and referenced by other modules, even though they are conceptually **obsolete in the flint+blackpi2 architecture**.
- On `flint`, we observed `ssh_tunnel_manager` trying to SSH to `root@openwrt.lan` and failing with host-key errors, despite `SSH_TUNNEL_ENABLED = False` in the repo version – implying at least some hosts still run older configs.

**Recommendations:**

1. **Keep the fields but harden their semantics:**
   - Default all tunnel flags to **False**.
   - Add strong inline comments labelling them as **legacy / single-host-cross-subnet mode only**, not for the dual-Pi deployment.
2. Consider a **per-host override pattern**, e.g.:
   - `LOCAL_ROLE = 'main_lan' | 'remote_lan' | 'single_host_cross_subnet'`.
   - Assert in startup code that tunnel options are disabled for `main_lan`/`remote_lan` roles.

### 3.2 `kasa_to_graphite.py`

Key OpenWrt-related pieces:

- Optional SSH tunnel support:
  - `from ssh_tunnel_manager import SSHTunnelManager`.
  - Global `get_tunnel_manager()` that:
    - Reads `SSH_TUNNEL_ENABLED`, `SSH_REMOTE_HOST`, `SSH_IDENTITY_FILE` from `config`.
    - Calls `SSHTunnelManager.test_connection()`.
- Optional UDP tunnel support:
  - `from udp_tunnel import SimpleUDPTunnel`.
  - `get_udp_tunnel()` using `UDP_TUNNEL_ENABLED` and `SSH_REMOTE_HOST`.
- In `discover_devices(...)`:
  - **Step 1: SSH tunnel remote discovery** via `tunnel_manager.discover_remote_devices(subnet)`.
  - Per-remote-device tunnel creation via `tunnel_manager.create_tunnel(ip)` and `Device.connect(config=DeviceConfig(host='127.0.0.1', port_override=local_port))`.
  - **Step 2: Local network discovery**, iterating `KASA_DISCOVERY_NETWORKS` with `Discover.discover()`.

**Issues / risks in dual-Pi world:**

- On `flint`, this code tries to treat 192.168.1.0/24 as a *remote* subnet discovered via OpenWrt, even though `flint` is **already on 192.168.1.0/24**.
- If configs drift (e.g. `SSH_TUNNEL_ENABLED` re-enabled on `flint`), it may:
  - Do redundant or broken SSH connections to OpenWrt.
  - Create unnecessary local tunnels to the *same* 192.168.1.x devices it can see directly.
- On `blackpi2`, this code allows continuing to treat 192.168.1.x as a **remote subnet** *instead* of delegating that responsibility to `flint`.

**Recommendations:**

1. **Introduce an explicit “local-only” mode for Kasa collectors.**
   - When running on `blackpi2` or `flint` in their normal roles, `kasa_to_graphite.py` should:
     - Skip `get_tunnel_manager()` entirely.
     - Skip any UDP tunnel setup.
     - Only run discovery over `KASA_DISCOVERY_NETWORKS`, which for both Pis should be `[None]`.
   - Implementation options:
     - Add a small guard near the top, e.g. `if not config.SSH_TUNNEL_ENABLED: tunnel_manager = None;` and **short-circuit** the whole “SSH tunnel remote discovery” block.
     - Or make `discover_devices()` only call into `get_tunnel_manager()` when a new `config.LOCAL_ROLE == 'single_host_cross_subnet'`.
2. **Deprecate UDP tunnel in code comments and logs.**
   - Mark `udp_tunnel`-based discovery as **experimental/legacy** and not used in the current architecture.
3. **For now, do not delete the SSH/UDP paths**, but:
   - Make it very clear (logging + comments + docs) that they are **off** in the flint+blackpi2 deployment.
   - Ensure that if tunnel init fails, it logs a one-line summary and then continues with purely local discovery.

### 3.3 `ssh_tunnel_manager.py`

This module is **entirely OpenWrt-centric**:

- It assumes an SSH endpoint that can:
  - Provide `/var/dhcp.leases` in OpenWrt format.
  - Accept `ssh -L local:remote_ip:9999` forwards.
- It is used only via `kasa_to_graphite.py`’s `get_tunnel_manager()` and remote discovery block.

**Current behaviour on flint:**

- When (mis-)configured, `SSHTunnelManager`:
  - Fails `test_connection()` with `Host key verification failed` against `root@openwrt.lan`.
  - Causes `kasa_to_graphite.py` to log SSH tunnel errors before falling back to local discovery.

**Recommendations:**

1. **Leave `ssh_tunnel_manager.py` in the tree** for historical and potentially future use, but:
   - It should be clearly labelled in its docstring and comments as **legacy for single-host-cross-subnet**.
2. In `kasa_to_graphite.py`, ensure any call path into `SSHTunnelManager` is guarded by a strong configuration flag (see `LOCAL_ROLE` suggestion).

### 3.4 `udp_tunnel.py`

This file implements both `UDPTunnel` and `SimpleUDPTunnel` for forwarding Kasa discovery traffic across SSH to OpenWrt.

**In dual-Pi architecture:**

- It is **not needed** in normal operation:
  - `blackpi2` discovers Kasa plugs on 192.168.86.x directly.
  - `flint` discovers Kasa plugs on 192.168.1.x directly.

**Recommendations:**

- As with `ssh_tunnel_manager.py`, keep the module but make it clear in comments and, optionally, in a header note that it is unused in the flint+blackpi2 deployment.

### 3.5 `tuya_local_to_graphite.py`

Relevant snippet:

- After local tinytuya scan, there is a **remote subnet scan** block:
  - Triggered when `config.SSH_TUNNEL_ENABLED` is True.
  - Uses `scan_remote_subnet(ssh_host, remote_subnet, ...)`.
  - Currently only logs that some IPs were found; it **does not** build full Tuya device objects over SSH.

**In dual-Pi architecture:**

- Tuya devices on 192.168.86.x should be handled by the main-LAN Pi.
- Tuya devices on 192.168.1.x should be handled by `flint` locally.
- Cross-subnet Tuya scanning is **no longer needed**.

**Recommendations:**

1. Similar to Kasa:
   - Gate the remote-subnet scan behind a stronger role-based condition or leave it effectively disabled by default.
   - Update comments to call it **legacy** / “only for single-host-cross-subnet deployments”.
2. Consider simplifying logs so that when `SSH_TUNNEL_ENABLED` is False, there is **no mention** of remote scanning to avoid confusion.

---

## 4. Documentation Tied to Old Single-Host Model

### 4.1 `SSH_TUNNEL_AUTO_DISCOVERY.md` and `SSH_TUNNEL_IMPLEMENTATION_SUMMARY.md`

These two files are explicitly about:

- Using SSH to `root@192.168.86.1` (OpenWrt) from a single host on 192.168.86.x.
- Discovering and tunnelling to Kasa devices on 192.168.1.0/24.

In the dual-Pi world:

- This *can* still work, but is **not the preferred architecture**.
- It is easy to get into a **confusing hybrid state** where both `blackpi2` and `flint` attempt to own 192.168.1.x.

**Recommendations:**

1. Add a short **“Status” section at the top** of both docs:
   - Clearly call them **legacy / optional**.
   - Explain that with `flint` now resident on 192.168.1.x, the recommended approach is *per-subnet collectors* without SSH tunnelling.
2. Cross-link to `docs/HOWTO_flint_remote_ssh.md` and a new high-level architecture doc (this one or a shorter user-facing summary) that explains the dual-Pi model.

### 4.2 `CROSS_SUBNET_SETUP.md` and `NETWORK_SETUP_STATUS.md`

These describe:

- Static routes from 192.168.86.0/24 to 192.168.1.0/24.
- nmap scans from the main LAN to 192.168.1.x for Kasa devices.

In the new architecture:

- Some of this is still useful as **background** (how the networks are wired), but:
  - The *operational* advice (“scan 192.168.1.0/24 directly from the main LAN”) is no longer how we run production collectors.

**Recommendations:**

1. Add a **short note near the top** of each doc:
   - “For current deployments with `flint` on 192.168.1.x and `blackpi2` on 192.168.86.x, per-subnet collectors are preferred. This document primarily applies to the legacy single-host cross-subnet setup.”
2. Optionally, add a section at the end describing how the dual-Pi setup obviates the need for cross-subnet routing from the main LAN to 192.168.1.0/24.

### 4.3 `README_SSH_SETUP.md` and `PASSWORDLESS_SSH_SETUP.md`

These docs mostly concern:

- SSH key-based access from the monitoring host to OpenWrt.
- Using SSH to read DHCP leases and scan Tuya/Kasa devices.

They remain **technically accurate**, but their relevance shifts:

- They are now **supporting material** for the legacy cross-subnet mode, not for the recommended dual-Pi deployment.

**Recommendations:**

- Add a brief **“Usage context”** paragraph stating that in the current setup:
  - These docs are only needed if you deliberately run a **single-host-cross-subnet** mode (for future experiments or fallback).

### 4.4 `docs/HOWTO_openwrt_kasa_pi.md`

This guide covers running the monitoring code on a **single Pi behind OpenWrt**, which is conceptually similar to `flint`’s role but predates the dual-Pi architecture and reverse SSH.

**Recommendations:**

- Clarify in the introduction that `flint` is the canonical example of this pattern, but that **remote access** is now provided via the reverse SSH pattern documented in `HOWTO_flint_remote_ssh.md`, not by trying to reach `flint` directly from 192.168.86.x.

---

## 5. Recommended Concrete Changes

### 5.1 Code-level changes

1. **Role / mode configuration** (minimalistic version):
   - Add something like `LOCAL_ROLE` to `config.py`:
     - `'main_lan'` on `blackpi2`.
     - `'remote_lan'` on `flint`.
     - `'single_host_cross_subnet'` only if explicitly testing legacy tunnelling.
   - In `kasa_to_graphite.py` and `tuya_local_to_graphite.py`, gate any use of:
     - `get_tunnel_manager()` / `SSHTunnelManager`.
     - `get_udp_tunnel()` / `SimpleUDPTunnel`.
     - Remote Tuya scanning (`SSH_TUNNEL_ENABLED`-driven block).

2. **Harden discovery behaviour:**
   - In `discover_devices()` for Kasa:
     - When `LOCAL_ROLE` is not `'single_host_cross_subnet'`, **skip** the entire “SSH tunnel remote discovery” section.
   - In `scan_for_devices()` for Tuya:
     - Treat remote-subnet scanning as disabled unless explicitly in cross-subnet mode.

3. **Logging clean‑up:**
   - When tunnel features are disabled by role/config, avoid log spam mentioning SSH/tunnelling so operator logs reflect the simplified architecture.

### 5.2 Documentation changes

1. **New summary doc (this file):**
   - Keep it as the internal architecture analysis.
2. **Add a short, user-facing “Current Deployment Architecture” doc** (could be a new `docs/CURRENT_ARCHITECTURE_OVERVIEW.md` or an added section to `README.md`), describing:
   - One Pi per subnet.
   - No cross-subnet device polling.
   - Reverse SSH from `flint` to `quartz` for admin access.
3. **Mark older SSH-tunnel / cross-subnet docs as legacy or optional**:
   - `SSH_TUNNEL_AUTO_DISCOVERY.md`.
   - `SSH_TUNNEL_IMPLEMENTATION_SUMMARY.md`.
   - `CROSS_SUBNET_SETUP.md`.
   - `NETWORK_SETUP_STATUS.md`.
   - `README_SSH_SETUP.md` and `PASSWORDLESS_SSH_SETUP.md`.

### 5.3 Operational guidance

1. **On `blackpi2`:**
   - Ensure `SSH_TUNNEL_ENABLED = False`, `UDP_TUNNEL_ENABLED = False`, `KASA_DISCOVERY_NETWORKS = [None]`.
   - Run `kasa_to_graphite.py` and `tuya_local_to_graphite.py` only for 192.168.86.x devices.
2. **On `flint`:**
   - Same discovery config: `KASA_DISCOVERY_NETWORKS = [None]`.
   - No OpenWrt SSH usage; all Kasa/Tuya devices are local.
   - Use only outbound connections (Graphite, Git, reverse SSH).

---

## 6. Summary

- The codebase and documentation still contain a substantial amount of logic oriented around a **single host on 192.168.86.x** reaching 192.168.1.x devices via OpenWrt SSH tunnels and UDP forwarding.
- With the introduction of **`flint` on 192.168.1.x**, this complexity is no longer required for normal operation and, if accidentally re-enabled, can cause confusing failures (e.g. host-key errors to `openwrt.lan`, duplicate ownership of the remote subnet).
- The safest path forward is to:
  - **Consolidate on per-subnet collectors** (blackpi2 + flint),
  - **Disable and clearly label** all OpenWrt SSH/UDP tunnelling paths as **legacy/optional**, and
  - Add a **small amount of role-aware configuration** and documentation to make this architecture obvious to future readers and operators.
