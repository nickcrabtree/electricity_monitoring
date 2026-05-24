# Desloppify: Code Quality Workflow

## Conda environment

```
conda run -n aro_desloppify_20260228T061951Z desloppify <command>
```

Never use bare `desloppify` — always prefix with `conda run -n aro_desloppify_20260228T061951Z`.

---

## Scores as of 2026-05-24

| Metric | Score |
|---|---|
| Overall (lenient) | 71.5 / 100 |
| Objective (mechanical) | 82.5 / 100 |
| Strict (penalises wontfix) | 63.6 / 100 |
| Verified (scan-confirmed) | 60.9 / 100 |

Session started at overall 54.7 / strict 54.7. Target is strict 85.0.

**Objective ≥ 80 is the threshold the owner cares about for a "good enough" pass.** Strict lags because some pure-IO modules were wontfix-skipped in the test_coverage dimension — this is intentional and documented below.

---

## Improvements made (May 2026)

### Code structure
- Removed duplicate boundary functions in `aggregate_energy.py` (2 copies of 8 functions)
- Replaced 4 repeated reset blocks with `_PERIODS` table + `apply_resets()` loop
- Extracted `_integrate_energy` / `_build_metrics` helpers; `compute_and_send` is now 6 lines
- Merged `_scan_wifi` / `_scan_wifi_with_fingerprinting` into one function with `fingerprint=` param
- Removed `normalize_tuya_value` module-level wrapper in `metric_scaling.py` (all callers use `get_scaler()`)
- Extracted `_build_devices()` helper in `tuya_local_to_graphite.py`; removed 3 verbatim copy-paste loops
- `_pick()` in `tuya_cloud_to_graphite.py` now returns `(key, value)` pair, eliminating re-scan loops
- Moved `patch_kasa_timezone.py` → `tools/` subdirectory; updated `watchdog_kasa_patch.sh`

### API / type safety
- Fixed `Dict[str, any]` → `Dict[str, Any]` in `presence/wifi_scan.py`
- Updated `_send_metrics` signature to `Dict[str, PersonPresence]`
- Added `VALID_ROLES` assertion in `config.py`; removed dead `KASA_SSH_TUNNEL_ENABLED` / `UDP_TUNNEL_*` vars
- `HomeAssistantAPI.get_presence_data()` now accepts `ha_device_tracker` or `ha_person_entity` config key,
  uses a single `get_states()` call; `_get_homeassistant_presence()` delegates to it (was 35 lines of manual iteration)

### Token bucket / rate limiting
- Extracted `_refill_tokens()` helper; added module-level float vars to drop the NameError-guard pattern

### Tests (93 tests across 7 files)
| File | Count | What it covers |
|---|---|---|
| `tests/test_aggregate_energy.py` | 13 | boundary helpers, apply_resets |
| `tests/test_metric_scaling.py` | 14 | canonical code mapping, normalize_by_code/dps |
| `tests/test_device_names.py` | 16 | load/save/get/set, cache, error fallbacks |
| `tests/test_graphite_helper.py` | 9 | format_device_name normalization |
| `tests/test_mac_learning.py` | 15 | extract_ipv6_suffix, fingerprint_similarity |
| `tests/test_kasa_to_graphite.py` | 9 | resolve_device_ip dispatch, ARP parsing |
| `tests/test_patch_kasa_timezone.py` | 7 | is_patched, apply_patch, backup creation |
| `tests/test_homeassistant_api.py` | 10 | get_presence_data with both config key styles |

Test health went from 0% → 79.3% after rescan.

### Infrastructure
- Added `pyproject.toml` with `pythonpath = ["."]` and `testpaths = ["tests"]` (removes `sys.path.insert` hacks)
- Added `requirements-lock.txt`
- Added `AGENTS.md` with test run instructions

### Intentional wontfix skips (test_coverage dimension)
Pure I/O scripts with no testable pure logic were permanently skipped:
- `presence/wifi_scan.py` — wraps nmap/ARP subprocess calls
- `presence_to_graphite.py` — integration orchestrator
- `tuya_local_to_graphite.py` — asyncio polling loop
- `tuya_remote_scan.py` — single SSH-shelling function

These are the reason strict < objective. The tradeoff is correct.

---

## Next steps to reach strict 85

1. **Subjective re-review** — 20 batch review was in-flight when work was paused (batches 1–5 running).
   After completion, import with:
   ```
   desloppify review --import-run .desloppify/subagents/runs/20260524_074000 --scan-after-import
   ```
   Then run the triage workflow (strategize → observe → reflect → organize → enrich → sense-check → write strategy).
   Lowest-scoring subjective dimensions: Test strategy 52%, Logic clarity 55%, Type safety 55%.

2. **Security dimension** — 67 issues flagged (strict 67.8%). Run `desloppify next --cluster` on security items.
   Most are expected patterns in a single-user hobby repo; many will be wontfix with attestation.

3. **Code quality autofix** — strict 67.3%. Run `desloppify autofix` to see what can be machine-fixed.

4. **Rescan after subjective triage** — after completing the 20-batch import and triage, rescan:
   ```
   desloppify scan
   ```

---

## How to run desloppify

### Check current score
```bash
conda run -n aro_desloppify_20260228T061951Z desloppify status
```

### Get the next task
```bash
conda run -n aro_desloppify_20260228T061951Z desloppify next
```

### Resolve a completed cluster
```bash
conda run -n aro_desloppify_20260228T061951Z desloppify plan resolve "<cluster-name>" \
  --note "what you did" --confirm
```

### Skip a false positive or wontfix item
```bash
conda run -n aro_desloppify_20260228T061951Z desloppify plan skip "<issue-id>" --permanent \
  --note "reason" \
  --attest "I have reviewed this skip against the code and I am not gaming the score. <detail>."
```
The attestation must contain "not gaming" and either "reviewed" or "i have actually".

### Rescan (only when queue is clear)
```bash
conda run -n aro_desloppify_20260228T061951Z desloppify scan
```

### Subjective review workflow

1. Generate batch prompts (dry run — no subagents launched):
   ```bash
   conda run -n aro_desloppify_20260228T061951Z desloppify review --run-batches --dry-run
   ```
   This creates 20 prompt files under `.desloppify/subagents/runs/<timestamp>/prompts/`.

2. Launch subagents in groups of **4–5** (never all 20 at once — hits session limits):
   - Each subagent reads `prompts/batch-N.md` and writes JSON to `results/batch-N.raw.txt`
   - Prompt: *"Read the full prompt from `<path>/batch-N.md`, follow ALL instructions, write JSON output to the results file."*

3. Import results and rescan:
   ```bash
   conda run -n aro_desloppify_20260228T061951Z desloppify review \
     --import-run .desloppify/subagents/runs/<timestamp> --scan-after-import
   ```

4. Run the triage workflow:
   ```bash
   conda run -n aro_desloppify_20260228T061951Z desloppify plan triage
   ```
   Stages: strategize → observe → reflect → organize → enrich → sense-check → write strategy.
   Each stage requires citing specific issue IDs with `review::` prefix.

5. Record commits:
   ```bash
   conda run -n aro_desloppify_20260228T061951Z desloppify plan commit-log record
   ```
