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
The tumble dryer is a metered TP-Link KP115 (`switch.tumble_dryer`) that has been offline since 04:06 UTC on 2026-10-26,
so it was not monitored during the 2026-10-26 Equiwatt event. The remaining unexplained load therefore needs a new
meter on the dishwasher and washing machine and the dryer plug back online.

### Meter outages and the "negative" unexplained readings

The unexplained line goes negative (aggregate above meter) in 14 % of minutes over 28 days, but almost all of that is
the meter, not the plugs: `home.electricity.meter.power_watts` reads a flat 0 W for all of 2 Oct and 9 Oct and part of
3, 8 and 10 Oct, matching the flat `total_kwh` counter on those days. The tool and `build_unexplained` drop minutes where
the meter reads 0 W. The cause of the outage is not investigated. Without those days, unexplained load has median 507 W,
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
