# Calibration checks (recorded 2026-10-29)

Question asked: are the readings calibrated? Dated measurements below are historical evidence, not a standing
guarantee. Re-check before relying on them.

## Summary

| Reading | Check | Result |
| --- | --- | --- |
| Whole-house meter (ESP32 pulse reader) | Equiwatt event 2026-10-26 18:00-20:00 | Meter 4.28 kWh (`total_kwh` delta), 4.29 kWh (integral of `power_watts`), Equiwatt reported 4.33 kWh: 1.2 % low. |
| Whole-house meter pulse constant | `pulse_count` / `total_kwh` | 34609 pulses / 8.652 kWh = 4000 imp/kWh, as the firmware notes say. |
| Whole-house meter vs kettle plug | Steps over 72 h | Meter step is roughly 0.8-1.0x the kettle plug reading (e.g. 2026-10-28 07:17: kettle 2872 W, meter 570 to 2895 W). |
| "Quartz server shed" plug (`bfec5b5d459e565242hhz9`) | GPU load test, below | Wall rise 188 W for a GPU rise of about 167 W (ratio 1.13, power-supply losses explain it): consistent to about 10 %. |
| "Shed" supply plug (`bfca739a0f4da457a45his`) | Same test | Stepped 339 to 514 W (+175 W), agreeing with the Quartz plug. |
| Unmonitored load | Equiwatt event 2026-10-26 | Plugs (aggregate) summed to 1.65 kWh against 4.28 kWh at the meter: 2.63 kWh (62 %, about 1.3 kW) unmonitored. Both ovens report, so cooking does not explain it; dishwasher, washing machine and tumble dryer are the likely culprits. |

Conclusion: the meter and the Quartz and Shed plugs are consistent with independent references. The large
`unexplained` line (meter minus aggregate on the Usage panel) is real unmonitored load, not a scaling error. More plugs
are the fix.

## GPU load test (2026-10-29 16:29-16:33 UTC)

Run once, with the queue session's approval, as `tools/power_calibration.py`: 60 s idle, 180 s fp32 matmul load, 60 s
idle, all in one lease (project `power_calibration`, 420 s declared, held 302.4 s, no declaration offence).

- GPU power from `computer.quartz.gpu.power_draw` (1 s collector): about 13 W idle, about 180 W under load (the card's
  rating).
- **Do not trust ad hoc `nvidia-smi` samples taken as a fresh process every few seconds:** they read about 90-100 W
  under the same load, roughly half the 1 s collector. Use the Graphite series.
- Quartz plug: about 81-90 W clean idle, about 269 W under load. The reading before the test (240 W) was with another
  project's GPU job still running, so it is not an idle baseline.
- Tuya plugs update their power reading only every 15-30 s and show coarse steps; use means over minutes, not instants.
- The house meter cannot resolve a 175 W step: it sat at 2.6-3.0 kW with other loads and varied by more than that.
- An earlier fit against the old N desk plug (14 days, slope 0.76 W/W) is not a calibration: that plug also carries other
  kit.

Any repeat needs a fresh approval from the quartz GPU queue session (`tools/power_calibration.py` is not a standing
permission). Run it with the `pytorch_transformer` conda env's interpreter as the launcher, outside the Claude sandbox
(the GPU device is not visible inside it).

## Running commentary during an Equiwatt event

`tools/event_report.py --start <UTC> --end <UTC> [--every 900]` prints cumulative kWh since the event start (meter
`total_kwh` delta), the mean kW so far and over the last interval, the monitored-plug kWh and the unmonitored
remainder, read-only from the Graphite render API. Without `--every` it prints one report now; with `--every 900` it
reports at each 15 minute mark until the end. Run it with the `electricity` env; inside the Claude sandbox the host
`ubuntu64.local:81` must be allowed. `--target-kwh` adds the energy target: the **target is total kWh over the window**
(billable energy), not an average kW, so the line shows kWh used, the expected total at the end, and the average kW
the rest of the window must stay under; the last report gives MET or MISSED. The tool does not know Equiwatt's target:
use the figure Nick supplies (2026-10-29: 1.85 kWh for 17:00-19:00, which the day's house load, 0.3 kW of monitored
plugs plus an unmonitored 0.5-4.5 kW, made unreachable).

## Gym lights in the shed (data point recorded 2026-11-09)

Nick's log, all GMT, during an Equiwatt event: shed lights on for a long time before 18:37; Archie switched the gym
lights on at about 18:27 ("very, very approximately 10 minutes" before 18:37); Nick switched the shed lights off at about
18:38 and left. The gym lights have no plug of their own. The nearest signal is `tuya.shed`, the shed's feed, whose
reading includes the Quartz server plug and the shed freezer plug (`aggregate_hierarchy.json`). The whole-house meter was
no use (blind, and 2-6 kW of other load).

**Method: `tools/estimate_shed_loads.py`** (tests `tests/test_estimate_shed_loads.py`). Two facts rule out subtracting the
plugs' held values by eye. The Shed plug reports in sparse snapshots (a step when the load moves by tens of watts, and a
refresh about hourly: 17:25:20, 18:20:30, 19:31:05, 20:31:05, 21:31:00), and the Quartz plug swings 80-170 W within a
minute (cause not established) while its held reading sits for minutes. So the tool takes only the instants at which the Shed plug
updated, models Quartz's wall power at that second from the 1 s GPU and CPU collectors (2026-11-09 fit on 177 plug
updates: 87.3 + 1.175 x GPU W + 5.17 x CPU load %, residual sd 27.8 W, R^2 0.89), subtracts the held readings of the other
children, and fits the remainder against the logged on/off intervals by least squares. Readings that lie in no interval
fix the baseline: everything else behind the Shed plug, which is calcite, blackpi2, the Nest node and so on.

| GMT | Shed plug W | Quartz (model) W | Residual W | State |
| --- | --- | --- | --- | --- |
| 16:30:25 | 192.1 | 108.6 | 83.5 | before the shed lights |
| 17:25:20 | 212.2 | 103.0 | 109.2 | shed lights on (+20 W step with Quartz flat) |
| 18:20:30 | 274.0 | 105.0 | 169.0 | first raised reading |
| 18:21:05 | 298.3 | 112.0 | 186.3 | |
| 18:24:20 | 380.7 | 161.2 | 219.5 | peak, perhaps a warm-up surge |
| 18:25:50 | 302.9 | 139.3 | 163.6 | |
| 18:29:20 | 281.9 | 139.6 | 142.3 | |
| 18:31:00 | 484.9 | 294.3 | 190.6 | Quartz GPU job started 18:30:50: removed by the model |
| 19:31:05 | 391.8 | 303.0 | 88.8 | back at baseline |
| 20:31:05 | 379.5 | 296.5 | 83.0 | baseline |
| 21:31:00 | 375.4 | 284.9 (+39.8 freezer) | 50.7 | baseline |

Result (baseline 76.5 +/- 14 W, one reading's noise 28 W):

- **Shed lights and gym lights together: about 95 +/- 17 W** above baseline. This holds for every sensible timeline.
- Shed lights alone: 20-30 W (the clean 17:25 step is +20.1 W on the plug and +26 W on the residual). The fit cannot
  separate them on its own (+/- 30 W).
- **Gym lights alone: about 70 W, plausible range 45-95 W** (combined minus shed lights; the fit gives 61-82 W depending
  on whether the 18:24 surge and the 16:30 reading are included). Treat it as a first estimate, not a calibration.

Caveats. (1) The readings begin at 18:20:30, about seven minutes before Archie's remembered 18:27. Fitting the gym lights
from 18:27 instead gives an impossible 93 W for the shed lights and -3 W for the gym, so either the time was nearer 18:20
or something else was switched on then. (2) The Shed plug did not report the shed lights going off (about 18:38) or the
shed freezer starting at 18:36 (+44 W on its own plug): no update came between 18:31:00 and the 19:31:05 refresh, which
shows the gym lights off by then, at an unknown time. (3) The one shed load the Equiwatt event is known to change is the
Amaryllis fridge (held off during events); the Amaryllis plugs read 0 W throughout. (4) Reactive power (V x I from the same snapshots) was tried as a
second signature and is too noisy (+/- 40 VAr). (5) Run with `--until` within a day of now: the 1 s GPU series does not
reach further back.

To tighten it: with the Quartz GPU idle, switch the gym lights on and off several times, a minute apart, and note the
times; each step is one more difference reading, and the error falls as 1/sqrt(steps). A clamp meter on the gym circuit
would settle it outright.

Looking for other sessions in the history (2026-11-10). Nick will not log the gym lights; he described the signal as a
steady draw for 20 minutes to about 90 minutes after dark, one on and one off, on a few days only. The 60 s archive
holds 14 days of the Shed plug, its children and the Quartz GPU/CPU collectors (2026-10-28 on, since Quartz moved into
the shed). Three scans of the Shed residual (Shed minus the Quartz model minus held children) found no session that
stands out: (a) a matched filter for 20-90 minute plateaus of +35 W or more over the 30 minutes either side gives about
a hundred candidates, as many by day as by night; (b) edges of 40-115 W with the Quartz model steady give 600 and pair up
into 190 on/off candidates; (c) in 10-minute bins where the Quartz model is steady (sd 4 W or less) the residual shows
only the known 2026-11-09 evening (90-110 W against the usual 40-70 W), because Quartz is busy on most evenings and
the baseline wanders 40-110 W from night to night (the plug reports a change only when it exceeds a threshold, so a
held value can be tens of watts stale). Conclusion: the existing history cannot separate the gym lights; a better
figure needs the Quartz plug's own reading at the same instants, a clean night, or a clamp meter. The lights are already
inside the Shed plug's total and so inside the whole-house aggregate; at about 70 W they cost about 1.3 p per hour at
18.54 p/kWh, so nothing else depends on the figure. Scratch scripts: /ncloud/Nick/tmp/electricity_monitoring/gym/.

The 16 days before that, 2026-10-12 to 2026-10-27 (Quartz not yet in the shed, so no model needed; scanned 2026-11-11):
no session either, and no better. The shed freezer had no plug of its own until 2026-10-27, so its compressor shows up
in the Shed plug as +46 to +55 W steps lasting 20 to 40 minutes at all hours (about 300 reported changes a day; idle
level 62-66 W), and about once a day a 40-45 minute block of +70 to +98 W with a dozen steps (11:52, 12:24, 17:37,
08:46 ... evenly spread through the day, so not Nick's or Archie's lights). Looking for a flat on/off pair 20-100 minutes
apart with equal steps and at most five changes inside gave 43 pairs, 40 of them freezer-sized (46-55 W). Three were not
freezer-sized and are unconfirmed: 2026-10-13 00:51-01:34 (+105 W, then 62 W after), 2026-10-18 05:47-06:25 (+79 W flat,
then 111 W, then 62 W) and 2026-10-18 22:48-23:27 (121-192 W, not flat); all at hours that do not fit Archie's use.
Whether the gym lights existed in that period is not known.

Why it mattered, and closed (Nick, 2026-11-11). The smart_garden shed heat model assumes all the energy metered by the
Shed plug is released inside the shed; the gym lights are the one load on it that is not. The aim was to subtract
them for a more accurate heat input. Nick closed the investigation as not worth more effort: the gym lights are about
70 W (45-95 W), for 20-90 minutes on a few evenings, so the heat input is overstated by that much while they are on and
by a negligible amount on average. Nothing is subtracted.

## Estimating unmetered devices from their on/off state (recorded 2026-10-30)

`tools/estimate_unmetered.py --device <tuya device name> | --state-metric <graphite path> [--days 28]` pulls one
device's draw out of the unexplained load (meter minus aggregate). At each clean on or off transition it takes the
median unexplained load 2-6 minutes after, minus the median 1-6 minutes before, and reports the median of those steps.
Unrelated devices switch at random times, so they average out; 10 or more events and a spread smaller than the step give
`good` confidence. Tests: `tests/test_estimate_unmetered.py`. Read-only; times UTC.

Results over 28 days (to 2026-10-30), meter outage days excluded:

| Device (state only, no meter) | Events | Result |
| --- | --- | --- |
| Towel rail (`tuya.towel_rail.is_on`) | 59 on, 58 off | 261 W when on (on-steps 260 W, off-drops 264 W), on 16 % of the time, about 42 W average. `good`. |
| Front room fairy lights | 27 on, 28 off | not detectable (tens of watts, below the noise) |
| Dining room lights | 30 on, 31 off | not detectable |
| Small water butt pump (`tuya.electric_blanket`, a leftover name) | none | never on in the window |
| Tado hot water and radiator zones | tested on the earlier 5 minute series | about 10-20 W with ±100 W scatter, no electrical signature (gas) |

Limits: needs a state series sampled at a minute or finer (Tado is every 5 minutes, which the tool flags as unusable), a draw above
about 100 W (the meter steps in about 90 W quanta), and the answer is a median over many cycles, not a live reading.
Cyclic appliances (washing machine, dishwasher) give a start step, not a profile.

What this does not cover: Home Assistant (list from the home_assistant session, 2026-10-30) has no entity at all for the
dishwasher or washing machine, no Alexa integration, and the only other unmetered state signals are the TVs and Sonos.
As of 2026-11-06 the tumble dryer and dishwasher are metered by Tuya plugs (`home.electricity.tuya.tumble_dryer.*`,
`home.electricity.tuya.dishwasher.*`, both moved from plugs that had stopped serving their original purpose). The old
TP-Link KP115 (`switch.tumble_dryer`) was offline from 04:06 UTC on 2026-10-26 and is abandoned, so the dryer was not
monitored during the 2026-10-26 Equiwatt event. The remaining unexplained load therefore needs a new meter on the
washing machine.

### Meter outages and the "negative" unexplained readings

The unexplained line goes negative (aggregate above meter) in 14 % of minutes over 28 days, but almost all of that is
the meter, not the plugs: `home.electricity.meter.power_watts` reads a flat 0 W for all of 2 Oct and 9 Oct and part of
3, 8 and 10 Oct, matching the flat `total_kwh` counter on those days. The tool and `build_unexplained` drop minutes where
the meter reads 0 W. Cause (Nick, 2026-10-30): the optical pulse sensor clipped to the meter's flashing LED is very
sensitive to being knocked; when it is out of line no pulses arrive, so `power_watts` reads 0 W and `total_kwh` goes flat
until it is realigned. A flat meter is therefore a knocked sensor until proved otherwise, and the lost energy is not
recoverable. Since 2026-10-31 `meter_watchdog.py` on blackpi2 emails Nick after 30 minutes of flat pulses or silence
(see [operations](OPERATIONS.md), "Whole-house meter watchdog"). Without those days, unexplained load has median 507 W,
a 01:00-05:00 UTC median of 364 W (10th percentile 214 W, the always-on floor), 95th percentile 2.6 kW, and 1.7 % of
minutes below -200 W (plug readings update every 15-30 s against the meter's own timing, so brief negatives follow oven
and desk-plug transitions; no plug reads persistently high).

## Reading the CPU power counter (RAPL)

`/sys/class/powercap/intel-rapl:0/energy_uj` (CPU package energy, microjoules) is root-only (mode 0400) because
unprivileged RAPL reads leak side-channel information (PLATYPUS, CVE-2020-8694). Reading it is otherwise harmless.
Wall power is roughly (CPU package + GPU + 30-40 W for board, drives and fans) divided by power-supply efficiency; the
i7-12700K package read about 38 W on 2026-10-29 with no GPU load.

On quartz:

- `tools/read-rapl` prints the counter. It is installed root-owned as `/usr/local/sbin/read-rapl`:
  `sudo install -o root -g root -m 0755 tools/read-rapl /usr/local/sbin/read-rapl`
- Sudoers: `/etc/sudoers.d/rapl-read` holds one line, `nickc ALL=(root) NOPASSWD: /usr/local/sbin/read-rapl`
  (edit with `sudo EDITOR=vi visudo -f /etc/sudoers.d/rapl-read`).
- **Why a helper rather than `cat`:** quartz runs `sudo-rs` and the Rust coreutils. `/usr/bin/cat` is a symlink to a
  multi-call binary and a rule naming `/usr/bin/cat` did not match (`sudo -n` asked for a password).
- Power in watts is the difference of two reads divided by the seconds between them (the counter wraps at
  `max_energy_range_uj`, 262143328850 µJ here).
- A sandboxed Claude cannot use sudo (no-new-privileges), so run it with the sandbox bypass.

These commands were first run by mistake on the Mac; the stray `/etc/sudoers.d/rapl-read` there was removed. The rule and
helper belong on quartz only.
