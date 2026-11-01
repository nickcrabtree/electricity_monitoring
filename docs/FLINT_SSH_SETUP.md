# Flint remote SSH access

Flint polls the OpenWrt device LAN (`192.168.1.0/24`). It can make outbound
connections through NAT to the main LAN (`192.168.86.0/24`), but the main LAN
cannot initiate a direct connection back. Flint maintains a reverse SSH tunnel
to quartz for administration. Blackpi2 is a collector, not the tunnel anchor.
See [architecture](ARCHITECTURE.md) for the deployment and
[development](DEVELOPMENT.md) for dependencies.

## Addressing and prerequisites

Use hostnames, not a fixed address or DHCP reservation. On the main LAN use
`quartz.local`; across the NAT boundary use a DNS name such as `quartz.lan`
that resolves from flint. mDNS generally does not cross NAT. Verify name resolution
and SSH from flint before configuring the service; do not assume the two naming
schemes are interchangeable.

Recorded users: `nickc` on flint and quartz. Flint's recorded WiFi MAC is
`b8:27:eb:81:e4:a8`. Initial access while flint is on the main LAN is
`ssh nickc@flint.local`. It needs Git and an OpenSSH client.

## Key and manual tunnel setup

On flint, inspect existing SSH keys before creating one. If no suitable key exists,
create a dedicated key without overwriting an existing file. Authorize its public
key for `nickc` on quartz, then verify passwordless access. Keep the private key on
flint. These examples assume the verified DNS name is `quartz.lan`:

```bash
ssh-copy-id -i ~/.ssh/id_ed25519.pub nickc@quartz.lan
ssh nickc@quartz.lan 'hostname && whoami'
ssh -N -o ExitOnForwardFailure=yes -R 2222:localhost:22 nickc@quartz.lan
```

The last command holds a foreground tunnel. From a separate shell on quartz:

```bash
ssh -p 2222 nickc@localhost
```

Port 2222 on quartz forwards to port 22 on flint. Keep it bound to loopback;
there is no need to expose it to the LAN. End the manual tunnel before starting
a service using the same port.

## Persistent tunnel

Inspect the existing `flint-reverse-ssh.service` before changing it. A unit for
this topology needs the following settings, with the verified hostname and key:

```ini
[Unit]
Description=Reverse SSH tunnel from flint to quartz
Wants=network-online.target
After=network-online.target

[Service]
User=nickc
ExecStart=/usr/bin/ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=60 -o ServerAliveCountMax=3 -R 2222:localhost:22 nickc@quartz.lan
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

`ExitOnForwardFailure=yes` matters: an SSH process can remain alive after failing
to bind the reverse port, leaving a service that looks healthy but accepts no
connections. Deploy an authorized unit change through the owning configuration
workflow; reload systemd and restart only that service. For a new installation,
enable it after verifying the configuration. Check:

```bash
systemctl --no-pager --full status flint-reverse-ssh.service
journalctl -u flint-reverse-ssh.service --since '10 minutes ago'
```

After moving flint behind OpenWrt, verify the tunnel again from quartz. No new
routing is needed for outbound Graphite or Git traffic.

## Access from another machine

Use a local SSH configuration such as:

```sshconfig
Host quartz
    HostName quartz.local
    User nickc

Host flint-via-quartz
    HostName localhost
    Port 2222
    User nickc
    ProxyJump quartz
```

Then run `ssh flint-via-quartz`. Outside the LAN, use the configured external
quartz route instead of `quartz.local`. Pull reviewed code into flint's own clone;
see [architecture](ARCHITECTURE.md). Inspect the actual remote rather than assuming
historical HTTPS or SSH settings are still in use.

## Troubleshooting

- On quartz, `ss -tlnp` should show a listener on loopback port 2222.
- On flint, inspect the service journal for DNS, authentication, host-key or
  forward-bind errors. Verify `ssh nickc@quartz.lan 'echo ok'` using the configured
  name. Investigate changed host keys before replacing known-host entries.
- If the tunnel is down, use the router jump route with flint's verified DNS name
  or get local access. Router and host-access notes live in
  `~/scripts/cross_repo_notes/topics/host_access.md`; do not hard-code an old
  DHCP address from an incident report.
- After inspecting the failure, an authorized
  `sudo systemctl restart flint-reverse-ssh` on flint restarts only this tunnel.
- If port 2222 is genuinely occupied by another service, coordinate a new port
  and update both the reverse-forward argument and every client configuration.
