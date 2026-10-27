# Agent guide: architecture1

Read when selected by AGENTS.md, not at every startup. Paths and commands are
relative to the repository root. Detailed source sections are retained below.
Current AGENTS.md and shared policy supersede dated history: old quiet-window
times, expired governor lifts, amend/reset advice and older host assumptions do
not grant permission. For current quiet-window status consult
`~/scripts/cross_repo_notes/AGENT_POLICY.md`.

## Detailed reference

## Big-picture architecture overview

### Deployment architecture

Recommended deployment uses **one Pi per subnet**:

- `blackpi2` on `192.168.86.0/24` (main LAN)
- `flint` on `192.168.1.0/24` (device LAN behind OpenWrt)

Each Pi polls only its local devices — no SSH tunnelling or cross-subnet discovery required. Key configuration: `LOCAL_ROLE = 'main_lan'` (default, disables legacy tunnel code paths), `KASA_DISCOVERY_NETWORKS = [None]` (scan local subnet only), `SSH_TUNNEL_ENABLED = False`, `UDP_TUNNEL_ENABLED = False`.

`flint` maintains a **reverse SSH tunnel** to `quartz` for remote admin access (see `~/code/AGENTS.md` for the tunnel topology and its failure mode).

See `docs/ARCHITECTURE.md` for full details.
