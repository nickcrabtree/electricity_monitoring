# Documentation index

Start with the [project README](../README.md) for setup. Paths in command examples
are relative to the repository root unless stated otherwise. Dated measurements
and deployments are evidence from that date, not live status checks.

## Operating the system

| Document | Purpose |
| --- | --- |
| [Architecture](ARCHITECTURE.md) | Dual-Pi deployment and host responsibilities |
| [Collectors](COLLECTORS.md) | Kasa, Tuya, HA bridge, aggregation and presence commands |
| [Operations and device history](OPERATIONS.md) | Watchdogs, dashboard identities, house topology and dated device evidence |
| [Device discovery](DEVICE_DISCOVERY.md) | Naming, local and remote scans, missing-device diagnosis |
| [Flint SSH setup](FLINT_SSH_SETUP.md) | Reverse tunnel setup and troubleshooting |
| [Presence operations](PRESENCE_OPERATIONS.md) | Service health, credentials, MAC updates and incident evidence |
| [MAC learning](MAC_LEARNING.md) | Mapping suggestions, confidence and state |
| [Calibration](CALIBRATION.md) | Meter checks, load estimates, Equiwatt reporting and RAPL |
| [Reminders](REMINDERS.md) | Open physical tasks for Nick; consumed by the weekly reminder tool |

## Development and future work

| Document | Purpose |
| --- | --- |
| [Development](DEVELOPMENT.md) | Environment, tests, staged-file hooks and configuration |
| [Pipelines](PIPELINES.md) | Collector internals, scaling, fallback and aggregation |
| [Desloppify](DESLOPPIFY.md) | Quality workflow and dated May 2026 review evidence |
| [Tuya quota design](TUYA_CLOUD_QUOTA.md) | Unimplemented adaptive quota proposal; not a deployment runbook |
| [Future work](ROADMAP.md) | Ideas retained from superseded plans; not an active task list |

[AGENTS.md](../AGENTS.md) contains repository safeguards and selective reading
instructions. CLAUDE.md and WARP.md are compatibility entry points to it.

## Historical documents

The original implementation plan and the 2024 presence status checklist were
removed during the November 2026 tidy because their setup steps and status claims
were superseded. Useful future ideas are in [Future work](ROADMAP.md); operational
evidence remains in the runbooks. Retrieve earlier versions with
`git log --all -- <path>` and `git show <commit>:<path>`. The old paths were
`docs/IMPLEMENTATION_PLAN.md` and `docs/PRESENCE_STATUS.md`.
