# Agent guide: safety1

Read when selected by AGENTS.md, not at every startup. Paths and commands are
relative to the repository root. Detailed source sections are retained below.
Current AGENTS.md and shared policy supersede dated history: old quiet-window
times, expired governor lifts, amend/reset advice and older host assumptions do
not grant permission. For current quiet-window status consult
`~/scripts/cross_repo_notes/AGENT_POLICY.md`.

## Detailed reference

## Repository constraints and state handling

State files not tracked in git (runtime state):

- `tinytuya.json` — Tuya API credentials
- `energy_state.json` — aggregation state
- `presence/state.json` — presence state
- `presence/mac_learning_state.json` — MAC learning state
