# Remote SSH access to `flint` via `quartz` (reverse SSH over NAT)

This guide explains how to set up **reverse SSH** so you can log in to a headless Raspberry Pi (`flint`) that lives only on the OpenWrt NAT subnet (e.g. `192.168.1.0/24`), even though there is **no routing** from your main LAN to that subnet.

`flint` will "phone home" to **`quartz`** (this host, on the main LAN with a fixed IP) and keep a reverse tunnel open. You then connect to `flint` by SSHing to `quartz` and hopping through that tunnel.

This is complementary to, not a replacement for, `docs/HOWTO_openwrt_kasa_pi.md` (which covers running the monitoring code on a single‑homed Pi behind OpenWrt).

---

## 0. Topology and goals

- **Main LAN:** `192.168.86.0/24`
  - `quartz` lives here, with a fixed IP (call it `<QUARTZ_IP>`).
  - `blackpi2` also lives here and runs the main monitoring code, but **is not** the SSH anchor in this guide.
- **OpenWrt / device LAN:** `192.168.1.0/24`
  - `flint` will live here permanently once deployed.
  - OpenWrt does **NAT** from `192.168.1.x` → `192.168.86.x`.
  - There is **no routing** from `192.168.86.x` back to `192.168.1.x`.

Implications:

- From `flint` you can reach hosts on the main LAN (SSH, Graphite, Git) via NAT.
- From `quartz` or `blackpi2` you **cannot** directly SSH to `flint`.
- Solution: `flint` maintains a **reverse SSH tunnel** to `quartz`; you connect *backwards* through that tunnel.

In examples below we assume:

- User on `quartz`: `nickc`.
- User on `flint`: `nickc`.
- `quartz` has fixed IP `<QUARTZ_IP>` on `192.168.86.0/24`.
- `flint` Wi‑Fi MAC (wlan0): `b8:27:eb:81:e4:a8`.

> **Important:** Do **not** rely on `*.local` mDNS names from `flint` once it is on `192.168.1.x` – mDNS usually does not cross that NAT boundary. Use the numeric `<QUARTZ_IP>` when configuring the tunnel.

---

## 1. One‑time prep while `flint` is on the main LAN

Before you move `flint` onto the 192.168.1.x network, plug it into the main LAN where you can reach it directly from `quartz` (or your laptop):

1. SSH in from `quartz` or your laptop:
   - `ssh nickc@flint.local`
2. Basic sanity:
   - `hostnamectl` to confirm the hostname is `flint` (or set it if needed).
   - `sudo apt-get update`
   - `sudo apt-get install -y git openssh-client`

You can also follow `docs/HOWTO_openwrt_kasa_pi.md` at this stage to clone this repo and set up Python, but that is independent of the SSH tunnel.

---

## 2. Create an SSH key on `flint` for calling home to `quartz`

On `flint` (as `nickc`):

1. Generate an ED25519 key if you don’t already have one:
   - `ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519 -N ""`
2. Confirm the public key exists:
   - `cat ~/.ssh/id_ed25519.pub`

This key will be authorized on `quartz` so that `flint` can log in non‑interactively.

---

## 3. Authorize `flint`’s key on `quartz`

Still on `flint` (while it is on the main LAN):

1. From `quartz`, determine its own LAN IP (if you don’t already know it):
   - `ip addr` or `hostname -I`
   - Pick the main‑LAN address, e.g. `192.168.86.10`, and treat it as `<QUARTZ_IP>`.
2. From `flint`, copy the key to `quartz`:
   - `ssh-copy-id -i ~/.ssh/id_ed25519.pub nickc@<QUARTZ_IP>`
3. Test passwordless SSH **from `flint` to `quartz`**:
   - `ssh nickc@<QUARTZ_IP> 'hostname && whoami'`
   - This should print something like `quartz` and `nickc` without asking for a password.

After this, once `flint` is on the 192.168.1.x network, it can still reach `quartz` via `ssh nickc@<QUARTZ_IP>` (NAT takes care of replies).

---

## 4. Configure the reverse SSH tunnel from `flint` to `quartz`

Goal: a **persistent reverse tunnel** from `flint` → `quartz` that exposes a port on `quartz` (e.g. `2222`) which forwards back to `flint`’s SSH port (`22`).

Conceptual command (run **on `flint`**, not yet as a service):

- `ssh -N -R 2222:localhost:22 nickc@<QUARTZ_IP>`

Meaning:

- `-R 2222:localhost:22`: On `quartz`, listen on TCP port 2222 and forward anything that connects there to `localhost:22` on `flint`.
- `-N`: Do not run a remote command (just keep the tunnel open).

By default, OpenSSH binds this reverse port on `127.0.0.1` on `quartz` only, which is good from a security perspective.

### 4.1. Quick manual test

With `flint` still on the main LAN so debugging is easy:

1. On `flint`, start the tunnel:
   - `ssh -N -R 2222:localhost:22 nickc@<QUARTZ_IP>`
   - Leave this running.
2. On `quartz`, in another terminal, test reaching `flint`:
   - `ssh -p 2222 nickc@localhost`

If that logs you into `flint`, the basic tunnel works.

Exit both SSH sessions when done, then move on to making it persistent.

---

## 5. Make the reverse tunnel persistent with systemd on `flint`

We want `flint` to re‑establish the tunnel automatically on boot and after network blips.

On `flint` (as `nickc` with sudo):

1. Create a systemd unit, e.g. `/etc/systemd/system/flint-reverse-ssh.service`.
2. Use a simple `ssh` command with keep‑alives to `nickc@<QUARTZ_IP>`.
3. Enable and start the service.

A minimal unit (pseudocode, for reference):

- `Description=Reverse SSH tunnel from flint to quartz`
- `After=network-online.target`
- `User=nickc`
- `ExecStart=/usr/bin/ssh -N -o ServerAliveInterval=60 -o ServerAliveCountMax=3 -R 2222:localhost:22 nickc@<QUARTZ_IP>`
- `Restart=always`

Follow the same patterns as in `docs/HOWTO_openwrt_kasa_pi.md`’s systemd example:

1. `sudo systemctl daemon-reload`
2. `sudo systemctl enable --now flint-reverse-ssh.service`
3. `systemctl --no-pager --full status flint-reverse-ssh.service`

As long as `flint` can reach `<QUARTZ_IP>`, it will maintain the tunnel.

> If you later change `quartz`’s IP, update the `ExecStart` line accordingly and run `sudo systemctl daemon-reload` + `sudo systemctl restart flint-reverse-ssh.service`.

---

## 6. Using the tunnel from `quartz` (and optionally other machines)

### 6.1. From `quartz` itself

Once the systemd service is running and `flint` is either on the main LAN or the 192.168.1.x network:

- On `quartz`, simply run:
  - `ssh -p 2222 nickc@localhost`

You should be dropped into a shell on `flint`.

### 6.2. From other machines, via `quartz`

If you want to reach `flint` from your laptop or another host that can SSH to `quartz`:

1. On your laptop, add something like this to `~/.ssh/config`:
   - `Host quartz`
   - `  HostName <QUARTZ_IP>`
   - `  User nickc`

   - `Host flint-via-quartz`
   - `  HostName localhost`
   - `  Port 2222`
   - `  User nickc`
   - `  ProxyJump quartz`

2. Then connect from the laptop via:
   - `ssh flint-via-quartz`

SSH will hop to `quartz` first, then through the reverse tunnel (localhost:2222) to `flint`.

> For security, the reverse port (2222) remains bound to `127.0.0.1` on `quartz`, so only someone who can SSH into `quartz` can reach `flint`.

---

## 7. Moving `flint` to the 192.168.1.x OpenWrt network

After everything above is working while `flint` is on the main LAN:

1. Shut down `flint` cleanly:
   - `sudo shutdown -h now`
2. Move its network connection so it is only on the OpenWrt / 192.168.1.x side.
3. Power it on.
4. Wait ~30–60 seconds for network + systemd.
5. On `quartz`, test:
   - `ssh -p 2222 nickc@localhost`

You should land on `flint`, just as before, despite there being no direct route from 192.168.86.x to 192.168.1.x.

---

## 8. Git and monitoring code on `flint`

Once you can SSH into `flint` through the reverse tunnel:

1. Clone or update this repository on `flint` as usual:
   - `mkdir -p ~/code && cd ~/code`
   - `git clone <your-remote-url> electricity_monitoring` **or** `cd electricity_monitoring && git pull`
2. Follow `docs/HOWTO_openwrt_kasa_pi.md` for installing Python dependencies and setting up `kasa_to_graphite.py` (or other scripts) to run as services.
3. As long as outbound NAT from 192.168.1.x to the main LAN/Internet allows TCP to your Git remotes and to Graphite, `flint` can:
   - `git pull`/`git push` against your existing remotes.
   - Send metrics to Graphite (`192.168.86.123:2003`) exactly as described in the existing docs.

No additional routing changes are required for these outbound flows.

---

## 9. Troubleshooting

### 9.1. Reverse tunnel service fails on `flint`

- Check status:
  - `systemctl --no-pager --full status flint-reverse-ssh.service`
- Common issues:
  - `<QUARTZ_IP>` is wrong or has changed.
  - SSH host key changed on `quartz` (fix `~/.ssh/known_hosts` on `flint`).
  - Network on the 192.168.1.x side is down.

### 9.2. `ssh -p 2222 nickc@localhost` on `quartz` hangs or fails

- On `quartz`, check whether the reverse‑SSH process exists:
  - `ps aux | grep 'ssh .* -R 2222:localhost:22' | grep -v grep`
  - If nothing is listed, the tunnel is down.
- From `flint`, test connectivity to `quartz`:
  - `ping -c 2 <QUARTZ_IP>`
  - `ssh nickc@<QUARTZ_IP> 'echo ok'`

### 9.3. Need to change the remote port

- If `2222` conflicts with something on `quartz`, pick another port (e.g. `2201`).
- Update both:
  - The `-R` argument in the systemd service on `flint`.
  - Any SSH configs or commands on `quartz` / your laptop that reference port 2222.

---

With this setup, `flint` behaves like the single‑homed OpenWrt Pi described in `docs/HOWTO_openwrt_kasa_pi.md` for monitoring work, while still being fully accessible over SSH from the main LAN via `quartz` as the fixed anchor host.