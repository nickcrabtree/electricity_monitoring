# AGENTS.md - electricity_monitoring

Shared policy: `~/scripts/cross_repo_notes/AGENT_POLICY.md`; selective index:
`~/scripts/cross_repo_notes/code_AGENTS.md`. Read those compact files if not already supplied.
Current shared rules supersede dated operational notes. Read only task-relevant
references before that operation; dated observations do not prove today's live state.

## Project safeguards

Collectors feed Graphite and presence data. Use conda env `electricity` on
development machines. Tests use temporary state and mocks; a live `--once` run
writes metrics and state, so it is not a read-only smoke test. Preserve untracked
credential/runtime-state files and back them up before edits. Format only touched
files. Before device, presence or deployment changes, read the matching references
below; services may run on different hosts. Never use a derived HA power value
to override a real device power sensor. Grafana changes follow shared backup/export
policy; track the resulting dashboard model without numeric id in `grafana/`.

Untracked state includes `tinytuya.json` (Tuya credentials), `mqtt_local.json` (MQTT credentials), `energy_state.json`,
`presence/state.json` and `presence/mac_learning_state.json`. Preserve other private
and runtime files too; this list is not exhaustive.

Anything important lives in git-tracked files, never only in an agent's private
memory or session schedule. Open items for Nick are in [docs/REMINDERS.md](docs/REMINDERS.md),
emailed weekly by `tools/remind_nick.py` from quartz's crontab: read it, tick done
items only when confirmed, and do not repeat open ones in every report. The agents'
rolling plan is [tasks/todo.md](tasks/todo.md) and lessons from Nick's corrections are in
[tasks/lessons.md](tasks/lessons.md).

## Select the reference before acting

| Task | Read first |
| --- | --- |
| Environments, tests, lint and configuration | [Development](docs/DEVELOPMENT.md) |
| Collector commands, credentials and fallback behaviour | [Collectors](docs/COLLECTORS.md) |
| Collector algorithms, scaling, naming and aggregation | [Pipelines](docs/PIPELINES.md) |
| Deployment or host responsibilities | [Architecture](docs/ARCHITECTURE.md) |
| Watchdogs, meter alerts, dashboards and device history | [Operations](docs/OPERATIONS.md) |
| Discovery or device naming | [Device discovery](docs/DEVICE_DISCOVERY.md) |
| Presence service or MAC mapping | [Presence operations](docs/PRESENCE_OPERATIONS.md), [MAC learning](docs/MAC_LEARNING.md) |
| Calibration, load estimation or event reporting | [Calibration](docs/CALIBRATION.md) |
| Flint remote access | [Flint SSH setup](docs/FLINT_SSH_SETUP.md) |
| Documentation maintenance or other topics | [Documentation index](docs/README.md) |

Shared policy governs safety, approvals, the Quartz quiet window and test isolation;
read its relevant topic before changing those workflows.
