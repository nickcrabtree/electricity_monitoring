# How to deploy a single‑homed Raspberry Pi on an OpenWrt network for Kasa → Graphite

This guide sets up a Raspberry Pi connected only to the OpenWrt Wi‑Fi to discover and poll Kasa devices locally, then push metrics over TCP to your Graphite/Carbon server upstream (e.g., 192.168.86.123:2003) through NAT. No routing between networks is required.

## 1) Hardware checklist
- Raspberry Pi with Wi‑Fi (Pi Zero 2 W, 3B+, 4, 5; prefer 3B+/4/5 for stability)
- 5V 2.5A+ PSU (official recommended)
- micro‑SD card (8GB+), SD adapter/writer
- Optional: case, heatsinks

## 2) Network prerequisites on OpenWrt
- Know the OpenWrt Wi‑Fi SSID and passphrase
- Ensure wireless client isolation is DISABLED for this SSID (AP isolation prevents device‑to‑device communication and will break discovery and polling)
  - LuCI: Network → Wireless → Edit SSID → Advanced → uncheck “Isolate Clients”
- LAN firewall zone should allow intra‑LAN traffic (default OpenWrt does)
- Outbound TCP from Pi to Graphite (e.g., 192.168.86.123:2003) must be allowed (default OpenWrt does via NAT)

## 3) Flash Raspberry Pi OS Lite
1. Use Raspberry Pi Imager
   - OS: Raspberry Pi OS Lite (64‑bit) or current minimal
   - Set hostname: kasa‑agent
   - Enable SSH and set password or add your SSH key
   - Configure Wi‑Fi: SSID = your OpenWrt SSID, WPA2 passphrase, country
   - Set locale/timezone
2. Eject card, insert into Pi, power on

## 4) First boot and basic hardening
SSH to the Pi (find IP in OpenWrt DHCP leases or use mDNS if available):
```bash
ssh pi@<pi_openwrt_ip>
```
Recommended:
```bash
sudo apt-get update && sudo apt-get -y upgrade
sudo timedatectl set-timezone Europe/London  # adjust
# Optional: create a non-default user and disable pi or set SSH keys only
```

## 5) Verify network path
- Kasa device reachability (replace example IP):
```bash
ping -c 1 192.168.50.42
```
- Graphite reachability (replace with your server):
```bash
nc -zv 192.168.86.123 2003
```
If Graphite is not reachable, confirm OpenWrt WAN is upstream of that LAN and that upstream IP is routable from OpenWrt clients.

## 6) Install dependencies (system Python)
```bash
# Upgrade pip and install deps for this repo
pip3 install --user --upgrade pip
cd /home/pi
mkdir -p /home/pi/code && cd /home/pi/code
# Clone the repository (adjust SSH setup or use HTTPS)
# Using HTTPS example:
git clone https://github.com/nickcrabtree/electricity_monitoring.git
cd electricity_monitoring
/home/pi/.local/bin/pip3 install --user -r requirements.txt
```
Notes:
- The python‑kasa CLI is useful for quick tests:
```bash
~/.local/bin/kasa discover
```

## 7) Quick manual test
Discover and single‑poll from the Pi on the OpenWrt network:
```bash
python3 kasa_to_graphite.py --discover
python3 kasa_to_graphite.py --once
```
You should see devices listed and a batch of metrics sent to Graphite.

## 8) Run continuously as a systemd service
Create a unit file so the agent survives reboots and restarts on failure.
```ini
# /etc/systemd/system/kasa-monitoring.service
[Unit]
Description=Kasa Smart Plug Monitoring (OpenWrt LAN → Graphite)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/code/electricity_monitoring
ExecStart=/usr/bin/python3 /home/pi/code/electricity_monitoring/kasa_to_graphite.py
Restart=always
RestartSec=10
StandardOutput=append:/home/pi/electricity_kasa.log
StandardError=append:/home/pi/electricity_kasa.log

[Install]
WantedBy=multi-user.target
```
Enable and start:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now kasa-monitoring
sudo systemctl status kasa-monitoring
```
Tail logs:
```bash
tail -f /home/pi/electricity_kasa.log
```

## 9) Troubleshooting
- No devices discovered:
  - Ensure Pi and Kasa plugs are on the same OpenWrt LAN/SSID/VLAN
  - Disable SSID client isolation; allow LAN→LAN in OpenWrt firewall
  - Test with CLI: `~/.local/bin/kasa discover`
  - Verify multicast/broadcast isn’t filtered on SSID (default is allowed)
- Metrics not in Graphite:
  - Test Carbon: `echo "test.metric 1 $(date +%s)" | nc 192.168.86.123 2003`
  - Check Graphite server IP/port in `config.py` (default 192.168.86.123:2003)
  - Confirm Pi can reach Graphite IP (see step 5)
- Python errors: ensure `requirements.txt` installed with user pip path; re‑login or export PATH:
```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc
```

## 10) Operational tips
- Reserve DHCP leases in OpenWrt for the Pi and plugs to avoid IP churn
- Keep Pi time in sync (NTP on by default)
- Monitor service health:
```bash
systemctl --no-pager --full status kasa-monitoring
journalctl -u kasa-monitoring -n 200 --no-pager
```
- Upgrades:
```bash
cd /home/pi/code/electricity_monitoring
git pull
/home/pi/.local/bin/pip3 install --user -r requirements.txt
sudo systemctl restart kasa-monitoring
```

## 11) Optional enhancements (later)
- Add a second script on this Pi for Tuya (cloud or LAN) if any Tuya devices live on OpenWrt
- If discovery is inconsistent on your SSID, we can add CIDR scanning to `kasa_to_graphite.py` in the repo and re‑deploy the unit with flags
- Centralize logs with `rsyslog`/`journald` remote forwarding if desired

## 12) Security considerations
- This Pi does not need routing/NAT; it’s a simple client on OpenWrt
- Limit SSH exposure: use keys, disable password auth if possible
- Keep OS and Python packages updated

With this setup, the Pi on the OpenWrt network discovers and polls Kasa devices locally and reliably, and pushes metrics to Graphite over TCP without any cross‑subnet routing.
