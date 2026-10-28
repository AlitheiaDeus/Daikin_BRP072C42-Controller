# Daikin BRP072C42 Optimization & Feature Roadmap

This document outlines architectural findings, latency reduction strategies, and feature suggestions for the Daikin AC LAN Controller and Web Dashboard, specifically optimized for **BRP072C42** Wi-Fi adapters.

---

## 1. Hardware & Protocol Constraints (BRP072C42)

The BRP072C42 series adapters communicate locally via HTTPS, which avoids cloud reliance, but introduces specific latency characteristics:

| Bottleneck | Cause | Impact |
| :--- | :--- | :--- |
| **Microcontroller TLS Overhead** | Low-power embedded CPU performing legacy SSL/TLS negotiation (`OP_LEGACY_SERVER_CONNECT`). | Each new HTTPS request costs **300ms–800ms**. |
| **S21 Internal Serial Bus** | The adapter talks to the indoor unit PCB over a 2400/9600 baud serial interface. | Control commands must be acknowledged by the indoor unit before returning `ret=OK`. |
| **Single-Threaded Server** | The embedded web server cannot handle concurrent HTTP requests. | Overlapping requests will queue up, block, or trigger timeouts. |

---

## 2. Latency Optimization Strategy

### A. Backend: Eliminate Request Amplification (Target: 75% Latency Reduction)

#### Current Flow on Button Click
Currently, a single setting change (e.g., clicking **Temp +0.5** or **Power**) triggers **4 sequential HTTP requests** to the adapter:
1. `update_settings()` calls `/aircon/get_control_info` (~500ms) to inspect existing values.
2. `update_settings()` calls `/aircon/set_control_info` (~600ms) to write the new settings.
3. `server.py` immediately calls `device.get_full_state()`, which fires:
   - `/aircon/get_sensor_info` (~500ms)
   - `/aircon/get_control_info` (~500ms)

*Total Roundtrip:* **2.0s to 3.0s+ per button click.**

#### Optimized Flow (Single Roundtrip)
1. **Use Cached Base State**: Read default values (`pow`, `mode`, `stemp`, `f_rate`, `f_dir`) from `self._last_state` instead of querying the adapter first.
2. **Execute Single Write**: Send the `/aircon/set_control_info` request directly (**1 single HTTP call**).
3. **Synthetic State Generation**: Once the adapter responds with `ret=OK`, immediately update the in-memory state dictionary with the new values and return that to the frontend. Defer sensor updates to the next background sync.

*Expected Result:* Drop command latency from **~2.5s down to ~400ms–600ms**.

---

### B. Frontend: 0ms Perceived Latency (Optimistic UI & Debouncing)

#### 1. Optimistic UI Updates
- When the user clicks **Power**, **Mode**, or **Fan Speed**, update the buttons, glow effects, and labels **instantly (0ms)**.
- If the backend request fails (timeout or network error), display an error toast and revert the UI to the last confirmed state.

#### 2. Temperature Debouncing / Click Accumulation
- Currently, rapid clicks are blocked by `if (isUpdating) return;`, which discards repeated clicks (e.g., tapping `+` three times from 24.0°C to 25.5°C drops the 2nd and 3rd taps).
- **Solution:** Add a 350ms debounce on the temperature dial. Rapid taps update the UI counter immediately (`24.0 -> 24.5 -> 25.0 -> 25.5`), and once the user stops tapping for 350ms, a single consolidated command for `25.5°C` is sent over the network.

---

### C. Server-Side Background Poller (Zero-Wait Tab Switching)

- Introduce a lightweight background worker thread in `server.py` that quietly cycles through the configured units every 20–30 seconds.
- When navigating between tabs in the web dashboard, the UI reads instantly from server memory without making the user wait for a synchronous probe.

---

## 3. High-Value Feature Roadmap

### Feature 1: Global Master Controls ("Turn All Off" & "Eco All")
- Add a quick-action bar at the top of the dashboard:
  - **"All Off" Button**: Iterates through all online AC units and powers them down sequentially. Useful when leaving home or heading to bed.
  - **"Eco All" Preset**: Sets all active units to Cool mode at 26.0°C with Auto Fan.

### Feature 2: Smart Sleep & Night Curves
- Maintaining a static low temperature all night often leads to waking up cold and wastes electricity.
- **Sleep Curve Mode**:
  - Starts at setpoint (e.g. 24.0°C).
  - +1 hour: Automatically raises temperature to 24.5°C.
  - +2 hours: Automatically raises temperature to 25.0°C.
  - Optional: Automatically turns off after specified hours.

### Feature 3: Estimated Power & Energy Consumption
- BRP072C42 adapters report real-time compressor frequency (`cmpfreq` in Hz) and indoor/outdoor temperatures (`htemp` and `otemp`).
- By modeling compressor frequency against nominal rated wattage:
  - Calculate estimated real-time power draw (**Watts / kW**).
  - Track estimated cumulative daily energy consumption (**kWh**).
  - Provide an estimated daily running cost.

### Feature 4: Home Assistant / MQTT Bridge
- Add an optional lightweight MQTT client:
  - Publishes unit states to `daikin/<device_id>/state`.
  - Subscribes to commands at `daikin/<device_id>/set`.
  - Implements **Home Assistant MQTT Discovery** so units automatically appear as native climate entities in Home Assistant, enabling Apple HomeKit, Google Assistant, and advanced automations.

### Feature 5: Maintenance & Air Filter Reminders
- Utilize the `today_runtime` counter to maintain cumulative operating hours.
- Notify the user when a unit exceeds 250–300 operating hours with a *"Clean Air Filter"* prompt on the dashboard card.

---

## 4. Implementation Priority Checklist

| Priority | Item | Component | Complexity | Status |
| :---: | :--- | :--- | :---: | :---: |
| 🔴 **P0** | Collapse command flow to 1 HTTP write call | `daikin.py` | Low | ✅ Completed |
| 🔴 **P0** | Synthetic state return on control POST | `server.py` | Low | ✅ Completed |
| 🔴 **P0** | Optimistic UI updates + temp debouncing | `web/app.js` | Medium | ✅ Completed |
| 🟡 **P1** | Background poller for zero-delay tab switching | `server.py` | Medium | ✅ Completed |
| 🟡 **P1** | Hardware capability autodetection (`/aircon/get_model_info`) | `daikin.py` & `web/` | Low | ✅ Completed |
| 🟡 **P1** | Global "All Off" quick control | `server.py` & `web/` | Low |
| 🟢 **P2** | Smart sleep temperature curves | `server.py` | Medium |
| 🟢 **P2** | Estimated power / energy telemetry | `daikin.py` & `web/` | Medium |
| 🟢 **P2** | MQTT / Home Assistant Discovery bridge | New script / module | Medium |
