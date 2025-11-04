# SSH Tunnel Issue - Root Cause Analysis

## Problem Summary

SSH tunnel creation **works**, but Device discovery/polling through the tunnel **fails**.

## Root Causes Identified

### 1. **Kasa Uses UDP Broadcast Discovery, Not TCP**

**Issue**: SSH port forwarding (`-L`) only works for TCP connections, not UDP.

**What happens**:
- Kasa devices advertise themselves via **UDP broadcast** on port 9999
- SSH tunnels only forward **TCP** traffic
- Therefore, UDP discovery packets cannot pass through the tunnel

**Proof**:
```
SSH Tunnel: ✓ TCP connection to localhost:9999 works
Kasa Discovery: ✗ UDP broadcast discovery times out (expected)
```

### 2. **Device Class is Abstract**

**Issue**: Cannot instantiate `Device` directly; it's an abstract base class.

**Error**:
```
Can't instantiate abstract class Device with abstract methods 
_get_device_info, alias, device_id, factory_reset, has_emeter, ...
```

**Why**: The `Device` class requires concrete implementations from factory methods or discovery.

---

## Current Architecture Problem

```
Normal Flow (Same Network):
  Discover.discover() 
    → UDP broadcast on local subnet
    → Device discovers itself
    → Returns concrete Device instance
    → Can poll device on its IP:9999

SSH Tunnel Flow (Fails):
  Discover.discover_single('127.0.0.1', port=9999)
    → Tries discovery on localhost (not remote subnet)
    → Cannot send UDP broadcast through TCP tunnel
    → Times out or fails
    → Never gets Device instance
```

---

## Solution Approach

### Option 1: **Direct Connection with SSH Remote Execution** (RECOMMENDED)
Instead of SSH tunneling, execute discovery remotely:

```python
# SSH to router, get device info
ssh openwrt python3 -c "from kasa import Discover; ..."

# Or use SSH to run discovery on the router's local network
ssh openwrt "kasa --host 192.168.1.230 status"
```

**Pros**:
- Discovery happens on the local network where devices are
- No tunnel needed
- Simple and reliable

**Cons**:
- Requires Python/kasa on the router
- More SSH calls

### Option 2: **Hybrid Discovery + TCP Tunneling**
1. Use SSH DHCP query for device IPs (already working!)
2. Create SSH tunnels for each device
3. Connect directly using IP:port, bypassing discovery

```python
# Step 1: Get IPs from DHCP (works via SSH) ✓
ssh openwrt 'cat /var/dhcp.leases | grep KP'
# Returns: KP115 at 192.168.1.230

# Step 2: Create tunnel for direct TCP connection
ssh -L 9999:192.168.1.230:9999 openwrt -N -f

# Step 3: Connect directly (bypass discovery)
# Use Device connection protocol directly with custom endpoint
```

**Pros**:
- Reuses existing SSH tunnel creation
- No discovery needed (we have IPs from DHCP)
- Direct connection works for polling

**Cons**:
- More complex implementation
- Need to use low-level protocol connection

### Option 3: **Broadcast Discovery Through SSH Tunnel** (NOT POSSIBLE)
- ❌ Technically infeasible - UDP cannot tunnel through SSH -L
- ❌ Would require custom broadcast forwarding (too complex)

---

## Recommended Implementation

**Use Option 2 (Hybrid)** because:

1. ✓ Device discovery from DHCP already working
2. ✓ SSH tunnels can be created successfully (tested)
3. ✓ Just need to connect via tunnel without discovery
4. ✓ Minimal code changes needed

### Implementation Steps

**Instead of**:
```python
dev = Device(f"127.0.0.1:{local_port}")  # ✗ Abstract class
```

**Do**:
```python
# Use connection parameters to connect through tunnel
from kasa import SmartDevice  # Concrete implementation
from kasa.protocols.xortransport import XORTransport

# Connect to tunneled device
transport = XORTransport(host='127.0.0.1', port=local_port)
dev = SmartDevice('127.0.0.1', protocol=transport)
await dev.update()  # Poll metrics
```

Or simpler - use existing local discovery, then override host:

```python
# Get device info from DHCP
ip = '192.168.1.230'  # From SSH DHCP query
tunnel_port = 9999    # Local tunnel port

# Create SSH tunnel: localhost:9999 -> 192.168.1.230:9999
create_tunnel(ip, tunnel_port)

# Create device config pointing to tunnel
dev = Device('127.0.0.1', 
             config=DeviceConfig(port=tunnel_port))
await dev.update()
```

---

## Why Current Approach Fails

Current code tries:
```python
dev = Device(f"127.0.0.1:{local_port}")
```

**Problems**:
1. Device class is abstract (cannot instantiate)
2. Even if it could, discovery wouldn't work through tunnel
3. Need concrete protocol implementation

---

## Testing Summary

| Method | Result | Reason |
|--------|--------|--------|
| SSH tunnel TCP | ✅ Works | SSH -L supports TCP forwarding |
| Kasa UDP discovery | ❌ Fails | Discovery uses UDP, tunnel uses TCP |
| Device instantiation | ❌ Fails | Device is abstract base class |
| Direct IP:port connection | ⏳ Untested | Need proper protocol implementation |

---

## Files Affected

- `kasa_to_graphite.py` - Lines 201-214 (remote device creation)
- `ssh_tunnel_manager.py` - May need protocol helper methods

---

## Action Items

1. **Research SmartDevice concrete implementations** - Find what subclass to use
2. **Test direct connection through tunnel** - Verify protocol works
3. **Implement hybrid discovery** - Use DHCP + tunnel approach
4. **Update documentation** - Explain SSH limitations
5. **Consider alternative**: Execute discovery on router itself via SSH remote command

---

## Related Issues

- SSH tunneling (socket forwarding) cannot forward UDP
- Device abstract base class requires factory methods
- Kasa discovery is network-local (broadcast-based)

## References

- Kasa library documentation
- SSH port forwarding limitations
- UDP vs TCP protocol differences
