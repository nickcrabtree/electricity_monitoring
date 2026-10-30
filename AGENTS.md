# AGENTS.md - electricity_monitoring

<!-- Selective repository startup: 2026-10-27 -->

Shared policy: `~/scripts/cross_repo_notes/AGENT_POLICY.md`; selective index:
`~/scripts/cross_repo_notes/code_AGENTS.md`. Read those compact files if not already supplied.
Current shared rules supersede older Git, test-runner, host, sandbox and quiet-window
recipes below. Read only task-relevant references before that operation. Dated runtime
states and measurements are historical evidence, not proof of today's deployed state.

## Project safeguards

Collectors feed Graphite and presence data. Use conda env `electricity` on
development machines. Tests use temporary state and mocks; a live `--once` run
writes metrics and state, so it is not a read-only smoke test. Preserve untracked
credential/runtime-state files and back them up before edits. Format only touched
files. Before device/presence/deployment changes read the matching operation and
architecture references and `docs/`; existing services may run on different hosts.
Never use a derived HA power value to override a real device power sensor.
Grafana changes follow shared backup/export policy.

## Select the reference before acting

- Before operating or changing the listed subsystem: [reference](AGENT_GUIDE_OPERATIONS1.md).
  Topics: Agent notes for electricity_monitoring; Conda usage: activate vs `conda run`; Common commands; Kasa
  integration (`kasa_to_graphite.py`); Tuya integrations; Aggregation (`aggregate_energy.py`); Presence monitoring
  (`presence_to_graphite.py`).
- Before the listed development, test, build or gate operation: [reference](AGENT_GUIDE_DEVELOPMENT1.md).
  Topics: Environment setup; Running tests; Core configuration (`config.py`); Code quality (desloppify).
- Before operating or changing the listed subsystem: [reference](AGENT_GUIDE_OPERATIONS2.md).
  Topics: Graphite/Carbon connectivity checks; SSH tips; Metric emission helpers (`graphite_helper.py`); Device naming
  and identity (`device_names.py`); Kasa pipeline (`kasa_to_graphite.py`); Tuya pipelines (local and cloud); Aggregation
  (`aggregate_energy.py`); Presence subsystem (`presence_to_graphite.py` and `presence/*`).
- Before changing the listed components or algorithms: [reference](AGENT_GUIDE_ARCHITECTURE1.md).
  Topics: Big-picture architecture overview; Deployment architecture.
- Before operating or changing the listed subsystem: [reference](AGENT_GUIDE_OPERATIONS3.md).
  Topics: System operation and watchdog (`watchdog_electricity.sh`); Device history notes; Grafana dashboards (state as
  of 2026-09-25); Calibration checks, the RAPL/GPU test tools and unmetered-device estimation (2026-10-29/30, detail in `docs/CALIBRATION.md`);
  House power topology (recorded 2026-09-25, during an Equiwatt event).
- Before policy, safety, claims or workflow changes: [reference](AGENT_GUIDE_SAFETY1.md).
  Topics: Repository constraints and state handling.
- Before operating or changing the listed subsystem: [reference](AGENT_GUIDE_OPERATIONS4.md).
  Topics: Documentation.

Detailed text remains in these ordinary references, not automatic imports.
