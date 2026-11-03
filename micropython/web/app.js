// Daikin Local Climate Hub Client
let devices = [];
let activeDeviceId = null;
let activeDeviceState = null;
let isUpdating = false;

// Optimistic UI & Debounced Command Queue
let pendingPatch = {};
let debounceTimer = null;
let inFlightRequest = false;
let lastConfirmedState = null;

// Request tracking to prevent out-of-order race conditions
let activeRequestId = 0;
let abortController = null;

// Countdown timer state
const SYNC_INTERVAL_SEC = 15;
let syncCountdown = SYNC_INTERVAL_SEC;
let countdownTimer = null;

// Elements
const deviceTabsContainer = document.getElementById("device-tabs");
const loadingState = document.getElementById("loading-state");
const activeDeviceView = document.getElementById("active-device-view");
const unconfiguredView = document.getElementById("unconfigured-device-view");

const activeDeviceName = document.getElementById("active-device-name");
const activeDeviceIp = document.getElementById("active-device-ip");
const btnPower = document.getElementById("btn-power");
const valTargetTemp = document.getElementById("val-target-temp");
const labelModeSummary = document.getElementById("label-mode-summary");
const dialGlow = document.getElementById("dial-glow");

const statHtemp = document.getElementById("stat-htemp");
const statOtemp = document.getElementById("stat-otemp");
const statHhum = document.getElementById("stat-hhum");
const statCmpfreq = document.getElementById("stat-cmpfreq");
const statRuntime = document.getElementById("stat-runtime");
const statError = document.getElementById("stat-error");
const hwMac = document.getElementById("hw-mac");
const hwFirmware = document.getElementById("hw-firmware");
const hwClock = document.getElementById("hw-clock");

const statusText = document.getElementById("status-text");
const statusPulse = document.querySelector(".status-pulse");
const btnRefresh = document.getElementById("btn-refresh");
const toast = document.getElementById("toast");
const toastMsg = document.getElementById("toast-message");

// Setup View Elements
const setupDeviceTitle = document.getElementById("setup-device-title");
const setupIdPreview = document.getElementById("setup-id-preview");
const setupNamePreview = document.getElementById("setup-name-preview");

let isBusy = false;

// Set Status Badge with State & Message
function setStatus(type, message) {
  statusText.textContent = message;
  if (statusPulse) {
    statusPulse.className = "status-pulse";
    if (type === "busy") statusPulse.classList.add("status-busy");
    else if (type === "error") statusPulse.classList.add("status-error");
  }
}

// Show Toast
let toastTimeout = null;
function showToast(message, isError = false) {
  toastMsg.textContent = message;
  toast.style.borderLeftColor = isError ? "var(--accent-danger)" : "var(--accent-cyan)";
  toast.classList.add("show");
  if (toastTimeout) clearTimeout(toastTimeout);
  const duration = isError ? 6500 : 3200;
  toastTimeout = setTimeout(() => toast.classList.remove("show"), duration);
}

// Stop Countdown
function stopCountdown() {
  if (countdownTimer) {
    clearInterval(countdownTimer);
    countdownTimer = null;
  }
}

// Reset Countdown Timer
function startCountdown() {
  stopCountdown();
  syncCountdown = SYNC_INTERVAL_SEC;
  if (!isBusy) {
    setStatus("online", `Live (Sync in ${syncCountdown}s)`);
  }

  countdownTimer = setInterval(() => {
    if (document.hidden || isBusy || isUpdating || inFlightRequest || debounceTimer || Object.keys(pendingPatch).length > 0) return;
    syncCountdown--;
    if (syncCountdown <= 0) {
      syncCountdown = SYNC_INTERVAL_SEC;
      if (activeDeviceId) {
        loadDeviceState(activeDeviceId, true);
      }
    } else {
      setStatus("online", `Live (Sync in ${syncCountdown}s)`);
    }
  }, 1000);
}

// Blank live fields immediately on tab switch so previous AC values never linger
function blankActiveFields(deviceMeta) {
  activeDeviceName.textContent = deviceMeta.name || deviceMeta.id;
  activeDeviceIp.textContent = `${deviceMeta.ip} • Model: aircon`;

  valTargetTemp.textContent = "--";
  labelModeSummary.textContent = "CONNECTING...";
  dialGlow.style.opacity = "0.2";
  btnPower.classList.remove("is-on");

  // Deactivate buttons until real state arrives
  document.querySelectorAll("#mode-selector .chip-btn").forEach(btn => btn.classList.remove("active"));
  document.querySelectorAll("#fan-selector .chip-btn").forEach(btn => btn.classList.remove("active"));
  document.querySelectorAll("#swing-selector .chip-btn").forEach(btn => btn.classList.remove("active"));

  statHtemp.innerHTML = `--<span class="stat-unit">°C</span>`;
  statOtemp.innerHTML = `--<span class="stat-unit">°C</span>`;
  statHhum.innerHTML = `--<span class="stat-unit">%</span>`;
  statCmpfreq.innerHTML = `--<span class="stat-unit">Hz</span>`;
  statRuntime.innerHTML = `--<span class="stat-unit">m</span>`;
  statError.textContent = "Checking...";
  statError.className = "stat-value";

  hwMac.textContent = "--";
  hwFirmware.textContent = "--";
  if (hwClock) hwClock.textContent = "--";
}

// Fetch all devices (lightweight instant init, no AC pinging)
async function fetchDevices() {
  try {
    const res = await fetch("/api/devices");
    if (!res.ok) throw new Error("Failed to load device list");
    const data = await res.json();
    devices = data.devices || [];

    if (!activeDeviceId || !devices.find(d => d.id === activeDeviceId)) {
      const firstConfigured = devices.find(d => d.configured);
      activeDeviceId = firstConfigured ? firstConfigured.id : (devices[0]?.id || null);
    }

    renderTabs();

    if (activeDeviceId) {
      switchTab(activeDeviceId);
    } else {
      renderUnconfiguredView({ name: "No AC Units Paired", ip: "Scan your Wi-Fi below to get started" });
    }
  } catch (err) {
    setStatus("error", "Server Disconnected");
    console.warn("Init error:", err);
  } finally {
    loadingState.classList.add("hidden");
  }
}

// Render Tabs Bar
function renderTabs() {
  deviceTabsContainer.innerHTML = "";
  devices.forEach(d => {
    const tab = document.createElement("button");
    tab.className = `device-tab ${d.id === activeDeviceId ? "active" : ""}`;
    tab.id = `tab-${d.id}`;

    let badgeText = "Setup";
    if (d.configured) {
      if (d.power !== undefined) {
        badgeText = d.power ? `${d.target_temp || 24}°C` : "OFF";
      } else {
        badgeText = "Ready";
      }
    }
    tab.innerHTML = `
      <span>${d.name || d.id}</span>
      <span class="tab-badge">${badgeText}</span>
    `;

    tab.addEventListener("click", () => {
      if (activeDeviceId !== d.id) {
        switchTab(d.id);
      }
    });

    deviceTabsContainer.appendChild(tab);
  });
}

// Instant Tab Switcher (0ms feedback from server RAM cache)
function switchTab(newDeviceId) {
  if (debounceTimer) {
    clearTimeout(debounceTimer);
    debounceTimer = null;
    flushPendingPatch();
  }
  pendingPatch = {};
  stopCountdown();
  activeDeviceId = newDeviceId;

  // 1. Immediately update active tab highlight
  document.querySelectorAll(".device-tab").forEach(tab => {
    tab.classList.toggle("active", tab.id === `tab-${newDeviceId}`);
  });

  const devMeta = devices.find(d => d.id === newDeviceId) || { id: newDeviceId, name: newDeviceId, ip: "", configured: false };

  // 2. If unconfigured slot, show setup card immediately
  if (!devMeta.configured) {
    renderUnconfiguredView(devMeta);
    setStatus("online", "Unconfigured Slot");
    return;
  }

  // 3. Switch view panel and load from server RAM cache silently (<2ms)
  activeDeviceView.classList.remove("hidden");
  unconfiguredView.classList.add("hidden");

  // 4. Fetch state from RAM cache immediately
  loadDeviceState(newDeviceId, true);
}

// Load single device state on-demand with cancellation of older requests
async function loadDeviceState(id, isBackground = false, forceRefresh = false) {
  // Abort any previous pending request so late responses never spasm the UI
  if (abortController) {
    abortController.abort();
  }
  abortController = new AbortController();
  const currentRequestId = ++activeRequestId;

  if (!isBackground) {
    isBusy = true;
    setStatus("busy", "Syncing with AC...");
  }

  try {
    const url = forceRefresh ? `/api/devices/${id}?refresh=1` : `/api/devices/${id}`;
    const res = await fetch(url, { signal: abortController.signal });
    if (!res.ok) throw new Error("HTTP connection error");
    const state = await res.json();

    // Discard stale responses if user clicked another tab in the meantime
    if (currentRequestId !== activeRequestId || id !== activeDeviceId) {
      return;
    }

    activeDeviceState = state;
    if (!state.configured) {
      renderUnconfiguredView(state);
    } else {
      renderActiveView(state);
      isBusy = false;
      startCountdown();
    }
  } catch (err) {
    if (err.name === "AbortError") {
      // Clean cancellation, do nothing
      return;
    }
    if (currentRequestId === activeRequestId) {
      setStatus("error", "Unit Offline / Unreachable");
      labelModeSummary.textContent = "UNIT UNREACHABLE";
      console.warn(`[${id}] Sync error:`, err);
    }
  } finally {
    if (currentRequestId === activeRequestId) {
      isBusy = false;
    }
  }
}

// Render Unconfigured State
function renderUnconfiguredView(state) {
  activeDeviceView.classList.add("hidden");
  unconfiguredView.classList.remove("hidden");

  setupDeviceTitle.textContent = `${state.name} Not Configured`;
  setupIdPreview.textContent = state.id;
  setupNamePreview.textContent = state.name;
}

// Render Active State
function renderActiveView(state) {
  unconfiguredView.classList.add("hidden");
  activeDeviceView.classList.remove("hidden");

  activeDeviceName.textContent = state.name;
  activeDeviceIp.textContent = `${state.ip} • Model: ${state.model_type || "aircon"}`;

  // Handle offline state
  if (state.status === "offline") {
    btnPower.classList.remove("is-on");
    labelModeSummary.textContent = "UNIT UNREACHABLE";
    dialGlow.style.opacity = "0.1";
    setStatus("error", `Unit Offline: ${state.error || "Timeout"}`);
    statError.textContent = state.error || "Unreachable";
    statError.className = "stat-value stat-error";
    return;
  }

  lastConfirmedState = JSON.parse(JSON.stringify(state));

  if (state.power) {
    btnPower.classList.add("is-on");
    labelModeSummary.textContent = `${(state.mode || "COOL").toUpperCase()} ACTIVE`;
    dialGlow.style.opacity = "0.7";
  } else {
    btnPower.classList.remove("is-on");
    labelModeSummary.textContent = "UNIT STANDBY / OFF";
    dialGlow.style.opacity = "0.15";
  }

  // Target Temp
  if (state.target_temp !== undefined && state.target_temp !== null) {
    const num = Number(state.target_temp);
    valTargetTemp.textContent = !isNaN(num) ? num.toFixed(1) : String(state.target_temp);
  } else {
    valTargetTemp.textContent = "24.0";
  }

  // Mode chips (Dynamically filtered by autodetected hardware capabilities)
  const supportedModes = Array.isArray(state.supported_modes) && state.supported_modes.length > 0
    ? state.supported_modes
    : ["cool", "dry", "fan", "auto", "heat"];
  document.querySelectorAll("#mode-selector .chip-btn").forEach(btn => {
    const isSupported = supportedModes.includes(btn.dataset.mode);
    btn.style.display = isSupported ? "" : "none";
    btn.classList.toggle("active", btn.dataset.mode === state.mode);
  });

  // Fan chips
  document.querySelectorAll("#fan-selector .chip-btn").forEach(btn => {
    btn.classList.toggle("active", btn.dataset.fan === state.fan_rate);
  });

  // Swing chips (Hide entire vane section if hardware does not support swing)
  const swingSection = document.getElementById("swing-selector")?.closest(".control-section");
  if (swingSection) {
    swingSection.style.display = state.supports_swing === false ? "none" : "";
  }
  document.querySelectorAll("#swing-selector .chip-btn").forEach(btn => {
    btn.classList.toggle("active", btn.dataset.swing === String(state.fan_dir));
  });

  // Telemetry
  statHtemp.innerHTML = state.indoor_temp !== null ? `${state.indoor_temp.toFixed(1)}<span class="stat-unit">°C</span>` : `--<span class="stat-unit">°C</span>`;
  statOtemp.innerHTML = state.outdoor_temp !== null ? `${state.outdoor_temp.toFixed(1)}<span class="stat-unit">°C</span>` : `--<span class="stat-unit">°C</span>`;
  statHhum.innerHTML = state.humidity !== null && state.humidity > 0 ? `${state.humidity}<span class="stat-unit">%</span>` : `N/A`;
  statCmpfreq.innerHTML = `${state.compressor_freq || 0}<span class="stat-unit">Hz</span>`;
  statRuntime.innerHTML = `${state.today_runtime_mins || 0}<span class="stat-unit">m</span>`;
  
  const errCode = state.err || "0";
  statError.textContent = errCode === "0" ? "0 (Normal)" : `Code ${errCode}`;
  statError.className = `stat-value ${errCode === "0" ? "stat-ok" : ""}`;

  hwMac.textContent = state.mac || "--";
  hwFirmware.textContent = `v${state.firmware || "1.16.0"}`;
  if (hwClock) {
    hwClock.textContent = state.device_clock || "--";
  }

  // Update tab badge for this active device
  const activeTab = document.getElementById(`tab-${state.id}`);
  if (activeTab) {
    const badge = activeTab.querySelector(".tab-badge");
    if (badge) {
      badge.textContent = state.power ? `${state.target_temp || 24}°C` : "OFF";
    }
  }
}

// Optimistic UI Updaters (0ms instant visual feedback)
function applyOptimisticPower(power) {
  btnPower.classList.toggle("is-on", power);
  dialGlow.style.opacity = power ? "0.7" : "0.15";
  const activeMode = (pendingPatch.mode || (activeDeviceState && activeDeviceState.mode) || "COOL").toUpperCase();
  labelModeSummary.textContent = power ? `${activeMode} ACTIVE` : "UNIT STANDBY / OFF";

  if (activeDeviceId) {
    const activeTab = document.getElementById(`tab-${activeDeviceId}`);
    if (activeTab) {
      const badge = activeTab.querySelector(".tab-badge");
      if (badge) {
        const curTemp = pendingPatch.target_temp !== undefined ? pendingPatch.target_temp : (activeDeviceState?.target_temp || 24);
        badge.textContent = power ? `${Number(curTemp).toFixed(1)}°C` : "OFF";
      }
    }
  }
}

function applyOptimisticTemp(temp) {
  valTargetTemp.textContent = Number(temp).toFixed(1);
  if (activeDeviceId) {
    const activeTab = document.getElementById(`tab-${activeDeviceId}`);
    if (activeTab) {
      const badge = activeTab.querySelector(".tab-badge");
      const isPowerOn = pendingPatch.power !== undefined ? pendingPatch.power : (activeDeviceState?.power);
      if (badge && isPowerOn) {
        badge.textContent = `${Number(temp).toFixed(1)}°C`;
      }
    }
  }
}

function applyOptimisticMode(mode) {
  document.querySelectorAll("#mode-selector .chip-btn").forEach(btn => {
    btn.classList.toggle("active", btn.dataset.mode === mode);
  });
  const isPowerOn = pendingPatch.power !== undefined ? pendingPatch.power : (activeDeviceState?.power);
  if (isPowerOn) {
    labelModeSummary.textContent = `${mode.toUpperCase()} ACTIVE`;
  }
}

function applyOptimisticFan(fan) {
  document.querySelectorAll("#fan-selector .chip-btn").forEach(btn => {
    btn.classList.toggle("active", btn.dataset.fan === fan);
  });
}

function applyOptimisticSwing(swing) {
  document.querySelectorAll("#swing-selector .chip-btn").forEach(btn => {
    btn.classList.toggle("active", btn.dataset.swing === String(swing));
  });
}

// Queue & Debounce Control Commands
function queuePatch(patch, debounceMs = 350) {
  if (!activeDeviceId) return;

  Object.assign(pendingPatch, patch);
  stopCountdown();
  setStatus("busy", "Updating...");

  if (debounceTimer) {
    clearTimeout(debounceTimer);
  }

  debounceTimer = setTimeout(() => {
    debounceTimer = null;
    flushPendingPatch();
  }, debounceMs);
}

// Flush Accumulated Patch to Server
async function flushPendingPatch() {
  if (!activeDeviceId) return;
  if (inFlightRequest) return;

  const keys = Object.keys(pendingPatch);
  if (keys.length === 0) {
    startCountdown();
    return;
  }

  const patchToSend = { ...pendingPatch };
  pendingPatch = {};
  inFlightRequest = true;
  setStatus("busy", "Applying to AC...");

  try {
    const res = await fetch(`/api/devices/${activeDeviceId}/control`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patchToSend)
    });
    const updated = await res.json();
    if (updated.error) throw new Error(updated.error);

    activeDeviceState = updated;
    lastConfirmedState = JSON.parse(JSON.stringify(updated));

    // If no newer changes were queued while network call was in flight
    if (Object.keys(pendingPatch).length === 0) {
      renderActiveView(updated);
      showToast("Command applied to AC");
      startCountdown();
    } else {
      // User tapped while previous request was in flight; flush next batch immediately
      flushPendingPatch();
    }
  } catch (err) {
    console.error(`[Command Error on ${activeDeviceId}]:`, err);
    showToast(`Error: ${err.message}`, true);
    setStatus("error", "Command failed");
    if (lastConfirmedState) {
      activeDeviceState = JSON.parse(JSON.stringify(lastConfirmedState));
      renderActiveView(lastConfirmedState);
    }
    pendingPatch = {};
    startCountdown();
  } finally {
    inFlightRequest = false;
  }
}

// Event Listeners with 0ms Optimistic Feedback & Debouncing
document.getElementById("btn-power").addEventListener("click", () => {
  if (!activeDeviceState) return;
  const currentPower = pendingPatch.power !== undefined ? pendingPatch.power : !!activeDeviceState.power;
  const nextPower = !currentPower;
  applyOptimisticPower(nextPower);
  queuePatch({ power: nextPower }, 250);
});

document.getElementById("btn-temp-up").addEventListener("click", () => {
  if (!activeDeviceState) return;
  const activeMode = pendingPatch.mode || activeDeviceState.mode;
  if (activeMode === "fan" || activeMode === "dry") {
    showToast(`Temperature is not adjustable in ${activeMode.toUpperCase()} mode`);
    return;
  }
  const maxTemp = (activeDeviceState && activeDeviceState.max_temp !== undefined) ? Number(activeDeviceState.max_temp) : 32.0;
  const currentTemp = pendingPatch.target_temp !== undefined
    ? pendingPatch.target_temp
    : (parseFloat(valTargetTemp.textContent) || 24.0);
  if (currentTemp < maxTemp) {
    const nextTemp = Math.min(maxTemp, Math.round((currentTemp + 0.5) * 10) / 10);
    applyOptimisticTemp(nextTemp);
    queuePatch({ target_temp: nextTemp }, 350);
  }
});

document.getElementById("btn-temp-down").addEventListener("click", () => {
  if (!activeDeviceState) return;
  const activeMode = pendingPatch.mode || activeDeviceState.mode;
  if (activeMode === "fan" || activeMode === "dry") {
    showToast(`Temperature is not adjustable in ${activeMode.toUpperCase()} mode`);
    return;
  }
  const minTemp = (activeDeviceState && activeDeviceState.min_temp !== undefined) ? Number(activeDeviceState.min_temp) : 18.0;
  const currentTemp = pendingPatch.target_temp !== undefined
    ? pendingPatch.target_temp
    : (parseFloat(valTargetTemp.textContent) || 24.0);
  if (currentTemp > minTemp) {
    const nextTemp = Math.max(minTemp, Math.round((currentTemp - 0.5) * 10) / 10);
    applyOptimisticTemp(nextTemp);
    queuePatch({ target_temp: nextTemp }, 350);
  }
});

// Mode buttons
document.querySelectorAll("#mode-selector .chip-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    if (!activeDeviceState) return;
    const nextMode = btn.dataset.mode;
    applyOptimisticMode(nextMode);
    queuePatch({ mode: nextMode }, 250);
  });
});

// Fan buttons
document.querySelectorAll("#fan-selector .chip-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    if (!activeDeviceState) return;
    const nextFan = btn.dataset.fan;
    applyOptimisticFan(nextFan);
    queuePatch({ fan_rate: nextFan }, 250);
  });
});

// Swing buttons
document.querySelectorAll("#swing-selector .chip-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    if (!activeDeviceState) return;
    const nextSwing = btn.dataset.swing;
    applyOptimisticSwing(nextSwing);
    queuePatch({ fan_dir: nextSwing }, 250);
  });
});

// Refresh button (forces live telemetry read from AC hardware)
btnRefresh.addEventListener("click", () => {
  if (activeDeviceId) {
    showToast("Refreshing live from AC...");
    loadDeviceState(activeDeviceId, false, true);
  }
});

// Discovery & Key Management Modal Elements
const modalDiscovery = document.getElementById("modal-discovery");
const btnOpenDiscovery = document.getElementById("btn-open-discovery");
const btnCloseModal = document.getElementById("btn-close-modal");
const btnSetupOpenDiscovery = document.getElementById("btn-setup-open-discovery");
const btnScanNetwork = document.getElementById("btn-scan-network");
const scanBtnText = document.getElementById("scan-btn-text");
const scanStatusPill = document.getElementById("scan-status-pill");
const discoveredCardsContainer = document.getElementById("discovered-cards-container");

function openDiscoveryModal() {
  if (modalDiscovery) {
    modalDiscovery.classList.remove("hidden");
    scanNetwork();
  }
}

function closeDiscoveryModal() {
  if (modalDiscovery) {
    modalDiscovery.classList.add("hidden");
  }
}

if (btnOpenDiscovery) btnOpenDiscovery.addEventListener("click", openDiscoveryModal);
if (btnCloseModal) btnCloseModal.addEventListener("click", closeDiscoveryModal);
if (btnSetupOpenDiscovery) btnSetupOpenDiscovery.addEventListener("click", openDiscoveryModal);

if (modalDiscovery) {
  modalDiscovery.addEventListener("click", (e) => {
    if (e.target === modalDiscovery) closeDiscoveryModal();
  });
}

// Scan local network for Daikin units via UDP
async function scanNetwork() {
  if (!discoveredCardsContainer || !btnScanNetwork) return;
  btnScanNetwork.disabled = true;
  scanBtnText.textContent = "Scanning Wi-Fi...";
  scanStatusPill.textContent = "Broadcasting UDP probe...";

  try {
    const res = await fetch("/api/discover");
    if (!res.ok) throw new Error("Discovery scan failed");
    const data = await res.json();
    renderDiscoveredUnits(data.discovered || []);
    scanStatusPill.textContent = `Found ${data.discovered ? data.discovered.length : 0} Daikin unit(s)`;
  } catch (err) {
    scanStatusPill.textContent = "Scan error: " + err.message;
    showToast("Discovery error: " + err.message, true);
  } finally {
    btnScanNetwork.disabled = false;
    scanBtnText.textContent = "Scan Wi-Fi Subnet";
  }
}

if (btnScanNetwork) {
  btnScanNetwork.addEventListener("click", scanNetwork);
}

// Render discovered units non-sequentially
function renderDiscoveredUnits(units) {
  if (!discoveredCardsContainer) return;
  discoveredCardsContainer.innerHTML = "";
  if (!units || units.length === 0) {
    discoveredCardsContainer.innerHTML = `
      <div style="text-align: center; padding: 40px 20px; color: var(--text-muted);">
        <p>No Daikin units answered the UDP broadcast.</p>
        <p style="font-size: 0.85rem; margin-top: 8px;">Ensure your device is on the same Wi-Fi subnet as the AC adapters.</p>
      </div>
    `;
    return;
  }

  units.forEach((u, idx) => {
    const card = document.createElement("div");
    card.className = `unit-card ${u.configured ? "is-paired" : "is-unpaired"}`;
    card.id = `discovered-card-${idx}`;

    const defaultName = u.configured_name || u.detected_name || `AC ${idx + 1}`;
    const badgeHtml = u.configured 
      ? `<span class="unit-badge badge-paired">● Paired & Configured</span>`
      : `<span class="unit-badge badge-needs-key">🔑 Needs 13-digit Key</span>`;

    card.innerHTML = `
      <div class="unit-card-header">
        <div class="unit-title-row">
          <input type="text" class="unit-name-input" id="name-${idx}" value="${defaultName}" placeholder="Room Name (e.g. Living Room)" />
          ${badgeHtml}
        </div>
      </div>

      <div class="unit-meta-pills">
        <span class="meta-pill">IP: ${u.ip}</span>
        ${u.mac ? `<span class="meta-pill">MAC: ${u.mac}</span>` : ""}
        ${u.ssid ? `<span class="meta-pill">SSID: ${u.ssid}</span>` : ""}
      </div>

      <div class="unit-key-row">
        <div class="key-input-wrapper">
          <input 
            type="text" 
            class="key-input" 
            id="key-${idx}" 
            placeholder="${u.has_key ? '●●●●●●●●●●●●● (Key Saved - Enter new to change)' : 'Enter 13-digit key from sticker photo'}" 
            maxlength="13" 
            autocomplete="off"
            spellcheck="false"
          />
        </div>
        <button class="btn-primary btn-pair" id="btn-save-${idx}">
          <span>Pair & Save</span>
        </button>
      </div>

      <div class="key-feedback" id="feedback-${idx}"></div>
    `;

    const saveBtn = card.querySelector(`#btn-save-${idx}`);
    const nameInput = card.querySelector(`#name-${idx}`);
    const keyInput = card.querySelector(`#key-${idx}`);
    const feedback = card.querySelector(`#feedback-${idx}`);

    saveBtn.addEventListener("click", async () => {
      const roomName = nameInput.value.trim() || defaultName;
      const keyVal = keyInput.value.trim();

      if (!keyVal && !u.has_key) {
        feedback.className = "key-feedback error";
        feedback.textContent = "Please enter the 13-digit key from your photo!";
        return;
      }

      saveBtn.disabled = true;
      feedback.className = "key-feedback busy";
      feedback.textContent = "Connecting to AC & testing handshake...";

      try {
        const payload = {
          id: u.configured_id || undefined,
          name: roomName,
          ip: u.ip,
          key: keyVal || undefined
        };

        const res = await fetch("/api/devices/save", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload)
        });

        const resp = await res.json();
        if (!res.ok || resp.error) throw new Error(resp.error || "Save failed");

        if (resp.handshake_ok) {
          card.className = "unit-card is-paired";
          feedback.className = "key-feedback success";
          feedback.textContent = `✅ Handshake verified! (Status: ${resp.device.status}, Temp: ${resp.device.indoor_temp || 25}°C)`;
          showToast(`Saved & paired ${roomName}`);
        } else {
          feedback.className = "key-feedback error";
          feedback.textContent = "⚠️ Saved, but AC rejected the key (Check sticker key in photo)";
          showToast(`Handshake failed for ${roomName}`, true);
        }

        // Refresh main dashboard tabs dynamically
        await fetchDevices();
      } catch (err) {
        feedback.className = "key-feedback error";
        feedback.textContent = "Error: " + err.message;
      } finally {
        saveBtn.disabled = false;
      }
    });

    discoveredCardsContainer.appendChild(card);
  });
}

// Auto-refresh active AC when tab is restored from background
document.addEventListener("visibilitychange", () => {
  if (!document.hidden && activeDeviceId && !isUpdating) {
    loadDeviceState(activeDeviceId);
  }
});

// Init on load
fetchDevices();
