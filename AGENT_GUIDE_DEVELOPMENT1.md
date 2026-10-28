# Agent guide: development1

Read when selected by AGENTS.md, not at every startup. Paths and commands are
relative to the repository root. Detailed source sections are retained below.
Current AGENTS.md and shared policy supersede dated history: old quiet-window
times, expired governor lifts, amend/reset advice and older host assumptions do
not grant permission. For current quiet-window status consult
`~/scripts/cross_repo_notes/AGENT_POLICY.md`.

## Detailed reference

## Environment setup

**Conda (preferred on dev machines)**

```bash
conda create -n electricity python=3.11 -y
conda activate electricity
pip install -r requirements.txt
```

**On Raspberry Pi without conda**

```bash
pip3 install --user -r requirements.txt
```

## Running tests

Install development tools into the project's conda environment, then install
this clone's Git hook (hooks themselves are not tracked by Git):

```bash
conda run -n electricity python -m pip install -r requirements-dev.txt
conda run -n electricity python -m pre_commit install
```

The tracked `.pre-commit-config.yaml` checks staged files for syntax, merge
conflicts, file hygiene, private keys, Ruff lint, and Ruff formatting. Tool
versions are pinned; keep the Ruff version in `requirements-dev.txt` aligned
with the hook revision. Ruff rules and formatting live in `pyproject.toml`.

Before committing:

```bash
conda run -n electricity python -m pytest tests/ -v
# Stage only the intended files, then:
conda run -n electricity python -m pre_commit run
```

If hygiene hooks modify files, review and re-stage them, then repeat the gate.
For Python formatting, run `conda run -n electricity python -m ruff format`
with explicit touched-file paths. Never run formatters or hooks with
`--all-files`; fix every lint error in each touched file. Do not bypass hooks
or allow a missing configuration. Commit approval follows the shared policy.

All Python on development machines uses conda environments. Development tools
are separate from production dependencies and are not needed just to run the
collectors on a Pi. Tests use temporary state and mocks; a live `--once` run
writes metrics and runtime state and is not a read-only smoke test.

### Core configuration (`config.py`)

- Defines the **Graphite/Carbon target** (`CARBON_SERVER`, `CARBON_PORT`), **poll intervals** (e.g. `SMART_PLUG_POLL_INTERVAL`), and the **metric prefix** (`METRIC_PREFIX`, typically `home.electricity`).
- Settings for network scanning (`KASA_DISCOVERY_NETWORKS`), rediscovery cadence (`KASA_REDISCOVERY_INTERVAL`, `TUYA_REDISCOVERY_INTERVAL`), and the Graphite render API (`GRAPHITE_RENDER_URL`, `GRAPHITE_RENDER_WINDOW`) used by the aggregation script, which makes one HTTP request per cycle and no SSH.
- All higher-level scripts import `config.py` rather than hard-coding these values.

## Code quality (desloppify)

See [docs/DESLOPPIFY.md](docs/DESLOPPIFY.md) for scores, what was improved, next steps, and the full workflow for running subjective review batches.
