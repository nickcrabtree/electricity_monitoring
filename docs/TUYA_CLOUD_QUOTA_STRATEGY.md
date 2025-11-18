# Tuya Cloud Quota Strategy (Design Notes)

This document captures design ideas and options for managing Tuya Cloud API quota
for the electricity monitoring project, with the aim of avoiding monthly quota
exhaustion while coexisting with other consumers (notably Home Assistant).

It is **design-only**: nothing here is implemented yet.

---

## 1. Background

- Tuya Cloud has a monthly free-tier quota (currently ~26k API calls / ~68k messages).
- This electricity monitoring app is a new, continuous consumer of the Tuya Cloud API.
- Home Assistant also uses the same Tuya Cloud account but historically did **not**
  exhaust the quota on its own.
- After introducing this app, the quota started to run out → the new load needs
  to be throttled and better coordinated with unknown external usage.

Existing behaviour in `tuya_cloud_to_graphite.py` (high-level):

- A local token-bucket rate limiter is derived from a fixed monthly cap:
  - `TUYA_CLOUD_API_CALLS_PER_MONTH = 26000`.
  - Rate is spread uniformly over a worst-case 31-day month to get
    `TUYA_CLOUD_CALLS_PER_SECOND` and a small burst (`TUYA_CLOUD_MAX_BURST`).
- This keeps **this process**, running 24/7, safely under the presumed monthly
  limit *by itself*.
- There is a quota state file placeholder (`tuya_cloud_quota_state.json`) with
  helpers to load/save, but currently it is not used to adapt the rate.
- The logic does **not**:
  - Look at actual remaining quota from Tuya.
  - React to usage by other clients (Home Assistant, etc.).
  - Intentionally end the month with a buffer (e.g. ≥ 3% quota remaining).


## 2. Goals

1. Avoid exhausting the monthly Tuya Cloud quota, even in the presence of
   other users (notably Home Assistant).
2. End each month with a small, intentional safety margin, e.g. **≥ 3% of
   monthly calls remaining** (roughly a spare day of quota).
3. Prefer a **JSON-based** shared state file, not extra services (Redis, HTTP).
4. Update the strategy infrequently (e.g. every ~6 hours is fine).
5. Handle loss of Tuya portal access gracefully by falling back to conservative
   behaviour, not by blindly running at a risky rate.


## 3. High‑level architecture: "Quota Monitor" + JSON state

Introduce a small, separate script (e.g. `quota_monitor.py`) which runs from
cron every few hours and maintains a **single JSON state file** that the
Tuya cloud script reads to decide how aggressively to poll.

Roles:

- **Quota Monitor (new)**
  - Periodically obtains a view of how much Tuya Cloud quota remains for the
    month.
  - Combines that with local knowledge of this app's usage to compute a safe
    per-second rate and burst.
  - Writes a JSON file (e.g. `tuya_cloud_quota_state.json`) atomically.

- **Tuya cloud script (existing)**
  - On startup, and periodically (e.g. every 60s), reads the JSON state.
  - Uses the computed target rate + burst for its token bucket.
  - Maintains a simple "our calls this month" counter for the monitor to read.

This keeps the existing architecture simple (no daemon, no network service),
while still enabling some cross-application awareness through shared state.


## 4. JSON state file sketch

A possible shape for `tuya_cloud_quota_state.json` (values are examples):

```json
{
  "version": 1,
  "month": "2025-11",
  "updated_at_ts": 1763445600,
  "stale_after_ts": 1763490000,
  "source": "portal",         

  "monthly_cap": 26000,
  "remaining_calls": 12345,
  "used_calls": 13655,

  "our_calls_this_month": 8000,

  "safety_calls": 800,          
  "seconds_left": 2000000,

  "global_target_rps": 0.006,
  "our_target_rps": 0.004,
  "burst": 15.0
}
```

Notes:

- `month`: YYYY-MM (UTC) used for rollover. When the month changes, both
  the monitor and consumer reset their counters.
- `updated_at_ts`: when the monitor last produced this state.
- `stale_after_ts`: beyond this, consumers should treat the state as stale
  and fall back to conservative defaults.
- `source` indicates where the main quota numbers came from:
  - `"portal"`: scraped or fetched from iot.tuya.com.
  - `"manual"`: manually entered by the operator.
  - `"estimate"`: estimated based on historical usage only.
- `monthly_cap`: configurable in case Tuya changes the free tier.
- `remaining_calls` and `used_calls` reflect Tuya's view for the month.
- `our_calls_this_month` is a counter maintained by this app and read by
  the monitor.
- `safety_calls` is a buffer to preserve (e.g. 3% of cap) so that we aim to
  end the month with quota still available.
- `global_target_rps` is a safe *overall* average rate for all clients.
- `our_target_rps` is the share that this app should use after accounting
  for other usage.
- `burst` is how many calls can be done in a short burst by this app.

Exact fields can be simplified later, but this captures the main ideas.


## 5. Sources of quota information

We have three progressively weaker sources of truth:

1. **Tuya portal/API (most authoritative)**
2. **Manual operator readings from the portal**
3. **Local estimations from this app's own call counts**

### 5.1 Semi‑automated portal access using a saved cookie

The Tuya portal login uses an anti-robot slider puzzle, which is awkward to
fully automate reliably. However, after logging in manually in a browser, it is
usually possible to reuse the **session cookie** programmatically until it
expires.

The idea:

1. You log into `https://iot.tuya.com` in a browser and solve the slider.
2. In browser DevTools (Network tab), you locate a request that returns the
   current quota usage (or a page where that value is embedded).
3. You copy:
   - The request URL (or an API endpoint specifically for quota if present).
   - The full `Cookie:` header for that request.
4. You paste them into a small config file for `quota_monitor.py`, e.g.:

   ```yaml
   portal_quota_url: "https://iot.tuya.com/api/.../quota"
   session_cookie: "SESSION=...; other=..."
   ```

5. Every ~6 hours, `quota_monitor.py` performs a single HTTP request with
   that cookie to retrieve the latest usage data.
6. If the cookie expires or the request fails (non-200 or unexpected body),
   the monitor logs this and **does not keep retrying aggressively**. It
   simply falls back to the weaker data sources (manual / estimate) and marks
   `source` accordingly.

This gives "semi-automation": you only need to update the cookie occasionally
(e.g. monthly or when it expires), without having to script the slider.

### 5.2 Manual readings from the portal

A simpler, fully robust fallback is to periodically read the remaining quota
from the portal UI and enter it manually, e.g. via a CLI or direct config
file update.

Workflow idea:

1. Log in to the Tuya portal in a browser.
2. Note down the current month's remaining and/or used calls.
3. Run something like:

   ```bash
   python3 quota_monitor.py manual --remaining 12345 --used 13655
   ```

4. The monitor writes a fresh `tuya_cloud_quota_state.json` with
   `source = "manual"`.

This may be sufficient on its own if you are willing to check the portal
occasionally (e.g. once near the start of each month, and perhaps once mid
month if you are nervous).

### 5.3 Estimations from local usage

When portal data and manual readings are unavailable or stale, `quota_monitor`
can still enforce a **conservative** limit using just local knowledge:

- Know the free-tier monthly cap (e.g. 26k calls).
- Track how many calls this app has made so far this month.
- Assume Home Assistant and any other clients use some unknown fraction, and
  give them generous headroom.

A crude but safe policy could be:

- Reserve a fixed fraction of the cap for "others".
- Allow this app to use only a fraction of the remaining budget, spread across
  the rest of the month.

Example: let this app only use at most 10–12k calls/month. This is **well
below** the full 26k and was historically safe when only HA was running.
The rate can then be treated as a fixed, conservative cap even without
portal visibility.


## 6. Rate calculation ideas

Given **some** estimate of remaining calls and time left in the month, the
monitor can compute a safe rate.

### 6.1 Time window

- Let `month_end_ts` be the UTC timestamp for the first day of the next month
  at `00:00:00`.
- Let `seconds_left = max(1, month_end_ts - now_utc)`.

### 6.2 Safety buffer for "spare day" / 3%

Define a buffer:

- `safety_calls = max(ceil(0.03 * monthly_cap), ceil(monthly_cap / 31))`.

This keeps at least approximately one day's worth of calls unused, ensuring
that errors in estimation or unobserved external spikes don't instantly hit
zero at the month boundary.

### 6.3 With portal or manual numbers

If `remaining_calls` is known from the portal (or manual input):

- `remaining_safe = max(0, remaining_calls - safety_calls)`.
- `global_target_rps = remaining_safe / seconds_left`.

To get this app's fair share:

- Option A (simple): devote a fixed proportion to this app, for example:

  - `our_target_rps = global_target_rps * 0.7`  (70% for this app, 30% spare).

- Option B (more refined): if `used_calls` is also known, and we track
  `our_calls_this_month`, we can estimate other clients' historical usage and
  subtract that from the global rate to leave them room.

### 6.4 Without portal / manual numbers

If portal/manual data is absent or stale, fall back to a fixed conservative
cap for this app, such as:

- `our_monthly_cap = min(monthly_cap * 0.5, some_fixed_number)`
- `our_target_rps = (our_monthly_cap - our_calls_so_far - safety_calls) / seconds_left`

With a floor to prevent negative or too-low values, and an upper bound to
stop sudden jumps, this yields a safe, self-contained estimate.

### 6.5 Smoothing and bursts

To avoid jitter when the target rate is recomputed every few hours:

- Maintain a smoothed rate:

  - `smoothed_rps = alpha * new_rate + (1 - alpha) * old_rate`, with
    `alpha` ≈ 0.3.

- Derive the token-bucket burst from this:

  - `burst = max(1.0, 60 * smoothed_rps)` — at most one minute's worth of
    calls, with a hard minimum of one call.

### 6.6 Staleness and backoff

If the JSON state is older than a threshold (e.g. 12 hours):

- Treat it as stale and:
  - Drop the effective rate towards a conservative floor.
  - Optionally decay the rate slowly over time until new data appears.

If Tuya returns HTTP 429 (too many requests) in practice:

- Temporarily **halve** the effective rate (down to a minimum), record it in
  logs and metrics, and rely on the next monitor run to recompute a safer
  target.


## 7. Coordination with Home Assistant

- Home Assistant is the only other known consumer.
- Historically, HA alone did not hit the cap.
- For now, treat HA as "external usage" that this app must leave room for,
  not something it can directly coordinate with.

Possible strategies (incremental):

1. **Phase 1 — Conservative partitioning**
   - Hard-code a maximum monthly budget for this app (e.g. 10–12k calls/month),
     leaving the rest to HA and future clients.
   - This alone may be enough to stop quota exhaustion.

2. **Phase 2 — Portal-aware adaptation**
   - Add the semi-automated portal fetch and/or manual readings.
   - Adjust this app's rate up or down based on real remaining quota and an
     estimate of how much HA has actually consumed so far.

The design above allows starting with the simpler Phase 1 and layering on
portal awareness later without large changes.


## 8. Operational model (sketch)

**Cron scheduling** (example only; not implemented):

- Every 6 hours, run the monitor once and log its output:

  ```cron
  5 */6 * * * /usr/bin/python3 /home/pi/code/electricity_monitoring/quota_monitor.py --once >> /var/log/quota_monitor.log 2>&1
  ```

**Monitor behaviour (conceptual):**

1. Load configuration (monthly cap, portal URL/cookie if any).
2. Determine current month and time window.
3. Try to fetch fresh quota data from the portal using the session cookie.
4. If portal fetch fails, look for recent manual values.
5. If neither is present/fresh, switch to estimation-only mode.
6. Compute `remaining_safe`, `global_target_rps`, `our_target_rps`, `burst`.
7. Write `tuya_cloud_quota_state.json` atomically.

**Consumer behaviour (conceptual):**

1. On startup and periodically:
   - Read and validate the JSON state.
   - If fresh, apply `our_target_rps` and `burst` to the token bucket.
   - If stale/invalid, fall back to a conservative default rate.
2. Maintain a local `our_calls_this_month` counter for future refinement.


## 9. Future work and open questions

Questions to answer when picking this up again:

1. Where exactly on the Tuya portal is the remaining quota shown, and is there
   an underlying API call we can reuse with a cookie?
2. How often are you willing to copy/paste a session cookie or manual
   remaining quota value (e.g. once per month, more often)?
3. What is an acceptable minimum polling rate (calls per device per minute)
   for this app if the quota situation is tight?
4. Do you want this app to expose Graphite metrics about its own quota
   decisions (current target RPS, remaining_calls from portal, staleness), to
   help you debug behaviour?

This document should be enough context to design and implement a small
`quota_monitor.py` script and corresponding changes to `tuya_cloud_to_graphite.py`
when time allows, without having to rediscover the reasoning behind the
approach.
