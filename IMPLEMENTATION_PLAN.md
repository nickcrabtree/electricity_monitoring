# Home Electricity Monitoring - Implementation Plan

## Overview
Integrate multiple electricity monitoring sources into existing Graphite/Grafana infrastructure at 192.168.86.123:2003

## Phase 1: Smart Plug Integration (PRIORITY - Start Here)

### 1.1 Kasa Smart Plugs with Power Monitoring
**Goal**: Poll TP-Link Kasa plugs for power consumption data and send to Graphite

**Steps**:
1. Install `python-kasa` library: `pip install python-kasa`
2. Discover Kasa devices on network: `kasa discover`
3. Create `kasa_to_graphite.py` script following pattern from `graphite_temperatures.py`:
   - Poll each Kasa plug every 30 seconds
   - Extract power (watts), voltage, current data
   - Send to Carbon at `192.168.86.123:2003`
   - Use metric naming: `home.electricity.kasa.<device_name>.<metric>`
4. Test manually
5. Add to cron/supervisor for continuous monitoring

**Dependencies**: None
**Estimated Time**: 2-3 hours
**Output**: Real-time device-level power monitoring

### 1.2 Tuya/SmartLife Smart Plugs
**Goal**: Poll Tuya devices for power consumption

**Steps**:
1. Install `tinytuya` library: `pip install tinytuya`
2. Run `python -m tinytuya wizard` to scan network and get device IDs/keys
3. Create `tuya_to_graphite.py` script (similar pattern to Kasa):
   - Poll each Tuya plug every 30 seconds
   - Extract power metrics
   - Send to Carbon
   - Use metric naming: `home.electricity.tuya.<device_name>.<metric>`
4. Test manually
5. Add to cron/supervisor

**Dependencies**: Phase 1.1 completion (reuse code patterns)
**Estimated Time**: 2-3 hours
**Output**: Additional device-level monitoring

---

## Phase 2: Smart Meter Pulse Reader

### 2.1 Assemble ESP32 Pulse Reader Kit
**Goal**: Get whole-house electricity monitoring from smart meter LED pulses

**Steps**:
1. Assemble hardware kit (ESP32 + LDR + resistor)
2. Position LDR over smart meter LED
3. Update `~/scripts/electricity_monitor/config.py`:
   - Set WiFi credentials
   - Configure MQTT broker (or direct HTTP endpoint)
   - Set pulses per kWh (check meter spec - likely 1000)
4. Flash MicroPython firmware to ESP32
5. Upload `main.py`, `config.py`, `monitor.py` to ESP32
6. Run calibration via serial REPL: `calibrate()`
7. Test pulse detection

**Dependencies**: None (can run in parallel with Phase 1)
**Estimated Time**: 3-4 hours
**Output**: Whole-house electricity consumption (kWh) and current power (kW)

### 2.2 Integrate ESP32 Data with Graphite
**Goal**: Stream ESP32 data to Graphite

**Options**:
- **Option A** (Simpler): Configure ESP32 to POST JSON to local HTTP endpoint
  - Create `esp32_receiver.py` Flask app that receives HTTP POST
  - Parse JSON and forward to Carbon/Graphite
  
- **Option B** (More robust): Use MQTT
  - Set up Mosquitto MQTT broker on VirtualBox or local machine
  - Configure ESP32 to publish to MQTT
  - Create `mqtt_to_graphite.py` subscriber script

**Recommended**: Option A for quick start, migrate to Option B later

**Steps**:
1. Create HTTP receiver endpoint
2. Update ESP32 config to POST to this endpoint
3. Parse and forward to Graphite with metric: `home.electricity.meter.<metric>`
4. Add to supervisor

**Dependencies**: Phase 2.1 complete
**Estimated Time**: 2 hours
**Output**: Real-time whole-house power consumption in Grafana

---

## Phase 3: DIN Rail Circuit Monitors

### 3.1 Install WiFi DIN Rail Devices
**Goal**: Monitor high-power circuits (cooker, microwave)

**Steps**:
1. Unbox and identify devices (likely Tuya/WiFi enabled)
2. **IMPORTANT**: Hire qualified electrician for installation in consumer unit
3. Configure devices via manufacturer app
4. Add to `tuya_to_graphite.py` or create separate script
5. Use metric naming: `home.electricity.circuit.<circuit_name>.<metric>`

**Dependencies**: Electrician availability
**Estimated Time**: 1 hour setup + electrician time
**Output**: Circuit-level monitoring for heavy appliances

---

## Phase 4: Alternative Smart Meter Data (Glow/MQTT)

### 4.1 Investigate Glow Integration
**Goal**: Get official smart meter data via MQTT (alternative to pulse reader)

**Steps**:
1. Check if you have SMETS2 meter with IHD (In-Home Display)
2. Register at hildebrandglow.co.uk (free)
3. Get MQTT credentials from Glow
4. Create `glow_mqtt_to_graphite.py`:
   - Subscribe to Glow MQTT feed
   - Parse electricity, gas data
   - Forward to Graphite
5. Compare data with ESP32 pulse reader for accuracy

**Dependencies**: SMETS2 meter with IHD
**Estimated Time**: 1-2 hours (if compatible meter)
**Output**: Official smart meter readings (electricity + gas)

---

## Phase 5: Grafana Dashboard Creation

### 5.1 Design Electricity Dashboard
**Goal**: Visualize all electricity data in one place

**Panels**:
1. **Whole House Power** (from ESP32/Glow)
   - Current power (W)
   - Today's consumption (kWh)
   - 7-day trend
2. **Device Breakdown** (from smart plugs)
   - Table: device name, current power, daily total
   - Stacked area chart of all devices
3. **Circuit Monitoring** (from DIN rail devices)
   - Cooker power usage
   - Microwave power usage
4. **Cost Calculation**
   - Multiply kWh by rate (Outfox tariff)
   - Daily/weekly/monthly cost

**Steps**:
1. Access Grafana on VirtualBox machine
2. Create new dashboard: "Home Electricity"
3. Add panels with Graphite queries
4. Configure alerts (high usage, device left on)

**Dependencies**: Phases 1-4 data flowing
**Estimated Time**: 2-3 hours
**Output**: Comprehensive electricity monitoring dashboard

---

## Phase 6: Automation & Orchestration

### 6.1 Create Unified Orchestrator
**Goal**: Single script to manage all electricity monitoring scripts

**Steps**:
1. Extend existing `~/scripts/orchestrator.py` or create new one
2. Add all electricity monitoring scripts:
   - `kasa_to_graphite.py`
   - `tuya_to_graphite.py`
   - `esp32_receiver.py` or `mqtt_to_graphite.py`
   - `glow_mqtt_to_graphite.py` (if used)
3. Add health checks and restart logic
4. Add to cron: `@reboot /home/nickc/scripts/electricity_orchestrator.py`

**Dependencies**: All previous phases
**Estimated Time**: 2 hours
**Output**: Reliable, self-healing monitoring system

### 6.2 Data Retention & Backup
**Goal**: Ensure long-term data preservation

**Steps**:
1. Configure Graphite retention policies:
   - 10-second data for 7 days
   - 1-minute averages for 30 days
   - 10-minute averages for 1 year
2. Set up Graphite backup script
3. Export key metrics to CSV monthly

---

## Quick Start Commands

### Immediate Next Steps (Phase 1.1):
```bash
# Create and activate conda environment
conda create -n electricity python=3.11 -y
conda activate electricity

# Install dependencies
cd ~/code/electricity_monitoring
pip install -r requirements.txt

# Discover Kasa devices
python kasa_to_graphite.py --discover

# Test integration
python kasa_to_graphite.py --once

# Start monitoring
python kasa_to_graphite.py
```

### File Structure
```
~/code/electricity_monitoring/
├── IMPLEMENTATION_PLAN.md          (this file)
├── kasa_to_graphite.py             (Phase 1.1)
├── tuya_to_graphite.py             (Phase 1.2)
├── esp32_receiver.py               (Phase 2.2)
├── mqtt_to_graphite.py             (Phase 2.2 alt)
├── glow_mqtt_to_graphite.py        (Phase 4.1)
├── config.py                       (shared config)
├── graphite_helper.py              (shared Carbon/socket code)
└── requirements.txt                (all dependencies)
```

---

## Timeline Estimate

- **Phase 1**: 4-6 hours (can start immediately)
- **Phase 2**: 5-6 hours (parallel with Phase 1)
- **Phase 3**: 2-4 hours + electrician (depends on availability)
- **Phase 4**: 1-2 hours (optional)
- **Phase 5**: 2-3 hours
- **Phase 6**: 2 hours

**Total**: ~16-23 hours of work

---

## Success Metrics

1. ✅ All Kasa plugs reporting to Graphite
2. ✅ All Tuya plugs reporting to Graphite
3. ✅ ESP32 pulse reader operational
4. ✅ DIN rail monitors installed and reporting
5. ✅ Grafana dashboard showing real-time data
6. ✅ All scripts running under orchestrator
7. ✅ Data retention configured
8. ✅ No data gaps > 5 minutes

---

## Notes

- Graphite server IP: `192.168.86.123:2003`
- Follow naming convention: `home.electricity.<source>.<device>.<metric>`
- Reuse patterns from `~/scripts/graphite_temperatures.py`
- Test each phase before moving to next
- Keep raw data logs for first week for debugging
