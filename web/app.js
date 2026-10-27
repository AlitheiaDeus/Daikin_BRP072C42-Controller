// Daikin Local Climate Hub Client
let devices = [];
let activeDeviceId = null;
let activeDeviceState = null;
let isUpdating = false;

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
function showToast(message, isError = false) {
  toastMsg.textContent = message;
  toast.style.borderLeftColor = isError ? "var(--accent-danger)" : "var(--accent-cyan)";
  toast.classList.add("show");
  setTimeout(() => toast.classList.remove("show"), 3200);
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
    if (document.hidden || isBusy || isUpdating) return;
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

    let badgeText = d.configured ? "Ready" : "Setup";
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

// Instant Tab Switcher (0ms feedback + aborts in-flight requests)
function switchTab(newDeviceId) {
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

  // 3. Immediately blank stats and show "Syncing..."
  activeDeviceView.classList.remove("hidden");
  unconfiguredView.classList.add("hidden");
  blankActiveFields(devMeta);
  isBusy = true;
  setStatus("busy", "Syncing with AC...");

  // 4. Fetch real live state on-demand
  loadDeviceState(newDeviceId);
}

// Load single device state on-demand with cancellation of older requests
async function loadDeviceState(id, isBackground = false) {
  // Abort any previous pending request so late responses never spasm the UI
  if (abortController) {
    abortController.abort();
  }
  abortController = new AbortController();
  const currentRequestId = ++activeRequestId;

  isBusy = true;
  if (!isBackground) {
    setStatus("busy", "Syncing with AC...");
  }

  try {
    const res = await fetch(`/api/devices/${id}`, { signal: abortController.signal });
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

  // Power
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
  valTargetTemp.textContent = (state.target_temp !== undefined && state.target_temp !== null) 
    ? Number(state.target_temp).toFixed(1) 
    : "24.0";

  // Mode chips
  document.querySelectorAll("#mode-selector .chip-btn").forEach(btn => {
    btn.classList.toggle("active", btn.dataset.mode === state.mode);
  });

  // Fan chips
  document.querySelectorAll("#fan-selector .chip-btn").forEach(btn => {
    btn.classList.toggle("active", btn.dataset.fan === state.fan_rate);
  });

  // Swing chips
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

// Send Control Command
async function sendCommand(patch) {
  if (!activeDeviceId || isUpdating) return;
  isUpdating = true;
  setStatus("busy", "Sending command...");

  try {
    const res = await fetch(`/api/devices/${activeDeviceId}/control`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch)
    });
    const updated = await res.json();
    if (updated.error) throw new Error(updated.error);
    
    activeDeviceState = updated;
    renderActiveView(updated);
    showToast("Command applied to AC");
    startCountdown();
  } catch (err) {
    showToast(`Error: ${err.message}`, true);
    setStatus("error", "Command failed");
  } finally {
    isUpdating = false;
  }
}

// Event Listeners
document.getElementById("btn-power").addEventListener("click", () => {
  if (!activeDeviceState) return;
  const newPower = !activeDeviceState.power;
  sendCommand({ power: newPower });
});

document.getElementById("btn-temp-up").addEventListener("click", () => {
  if (!activeDeviceState) return;
  const curr = parseFloat(valTargetTemp.textContent) || 24.0;
  if (curr < 32.0) {
    sendCommand({ target_temp: curr + 0.5 });
  }
});

document.getElementById("btn-temp-down").addEventListener("click", () => {
  if (!activeDeviceState) return;
  const curr = parseFloat(valTargetTemp.textContent) || 24.0;
  if (curr > 18.0) {
    sendCommand({ target_temp: curr - 0.5 });
  }
});

// Mode buttons
document.querySelectorAll("#mode-selector .chip-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    sendCommand({ mode: btn.dataset.mode });
  });
});

// Fan buttons
document.querySelectorAll("#fan-selector .chip-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    sendCommand({ fan_rate: btn.dataset.fan });
  });
});

// Swing buttons
document.querySelectorAll("#swing-selector .chip-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    sendCommand({ fan_dir: btn.dataset.swing });
  });
});

// Refresh button (refreshes active unit on-demand)
btnRefresh.addEventListener("click", () => {
  if (activeDeviceId) {
    showToast("Refreshing active unit...");
    loadDeviceState(activeDeviceId);
  }
});

// Auto-refresh active AC when tab is restored from background
document.addEventListener("visibilitychange", () => {
  if (!document.hidden && activeDeviceId && !isUpdating) {
    loadDeviceState(activeDeviceId);
  }
});

// Init on load
fetchDevices();
