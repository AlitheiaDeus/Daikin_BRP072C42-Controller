// Daikin Local Climate Hub Client

// 1. Instant Cache Restoration from localStorage (0ms Stale-While-Revalidate)
let devices = [];
try {
  const cachedDevs = localStorage.getItem("daikin_devices_cache");
  if (cachedDevs) devices = JSON.parse(cachedDevs);
} catch (e) {}

let activeDeviceId = null;
try {
  activeDeviceId = localStorage.getItem("daikin_active_device_id") || (devices[0]?.id || null);
} catch (e) {}

let activeDeviceState = null;
try {
  if (activeDeviceId) {
    const cachedState = localStorage.getItem("daikin_active_state_" + activeDeviceId);
    if (cachedState) activeDeviceState = JSON.parse(cachedState);
  }
} catch (e) {}

let isUpdating = false;

// View Mode: "overview" (all units at a glance) or "detail" (single unit controls)
const isMobileUser = window.innerWidth <= 768 || /Android|iPhone|iPad|iPod/i.test(navigator.userAgent);
let currentView = isMobileUser ? "overview" : (localStorage.getItem("daikin_view_pref") || "overview");

// Optimistic UI & Debounced Command Queue for Single Unit View
let pendingPatch = {};
let debounceTimer = null;
let inFlightRequest = false;
let lastConfirmedState = null;

// Per-Device Timers & Patches for Overview Cards
const deviceTimers = {};
const devicePending = {};

// Request tracking to prevent out-of-order race conditions
let activeRequestId = 0;
let abortController = null;

// Countdown timer state
const SYNC_INTERVAL_SEC = 15;
let syncCountdown = SYNC_INTERVAL_SEC;
let countdownTimer = null;

// DOM Elements
const deviceTabsContainer = document.getElementById("device-tabs");
const loadingState = document.getElementById("loading-state");
const overviewView = document.getElementById("overview-view");
const overviewGrid = document.getElementById("overview-grid");
const overviewSubtitle = document.getElementById("overview-subtitle");
const activeDeviceView = document.getElementById("active-device-view");
const unconfiguredView = document.getElementById("unconfigured-device-view");

const btnToggleView = document.getElementById("btn-toggle-view");
const toggleViewIcon = document.getElementById("toggle-view-icon");
const toggleViewText = document.getElementById("toggle-view-text");
const btnBackToOverview = document.getElementById("btn-back-to-overview");
const btnAllOff = document.getElementById("btn-all-off");

const activeDeviceName = document.getElementById("active-device-name");
const activeDeviceIp = document.getElementById("active-device-ip");
const btnEditActiveName = document.getElementById("btn-edit-active-name");
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
const diagnosticsDrawer = document.getElementById("diagnostics-drawer");

const statusText = document.getElementById("status-text");
const statusPulse = document.querySelector(".status-pulse");
const btnRefresh = document.getElementById("btn-refresh");
const toast = document.getElementById("toast");
const toastMsg = document.getElementById("toast-message");

// Rename Modal Elements
const modalRename = document.getElementById("modal-rename");
const btnCloseRenameModal = document.getElementById("btn-close-rename-modal");
const btnCancelRename = document.getElementById("btn-cancel-rename");
const btnSaveRename = document.getElementById("btn-save-rename");
const inputRenameName = document.getElementById("input-rename-name");
let renameTargetDeviceId = null;

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
      if (currentView === "overview") {
        fetchDevices(true);
      } else if (activeDeviceId && currentView === "detail") {
        loadDeviceState(activeDeviceId, true);
      }
    } else {
      setStatus("online", `Live (Sync in ${syncCountdown}s)`);
    }
  }, 1000);
}

// Format Helpers
function modeIcon(mode) {
  switch (mode) {
    case "cool": return "❄️";
    case "dry": return "💧";
    case "fan": return "🌀";
    case "auto": return "⚡";
    case "heat": return "☀️";
    default: return "❄️";
  }
}

function fanLabel(rate) {
  if (rate === "A") return "Auto";
  if (rate === "B") return "Quiet";
  return rate ? `Lvl ${rate}` : "Auto";
}

function swingLabel(dir) {
  if (dir === "1" || dir === 1) return "Vert";
  if (dir === "2" || dir === 2) return "Horiz";
  if (dir === "3" || dir === 3) return "3D";
  return "Off";
}

// View Management: Toggle between Overview and Detailed Controls
function setView(viewMode) {
  currentView = viewMode;
  try {
    localStorage.setItem("daikin_view_pref", viewMode);
  } catch (e) {}

  if (viewMode === "overview") {
    overviewView.classList.remove("hidden");
    activeDeviceView.classList.add("hidden");
    unconfiguredView.classList.add("hidden");

    if (toggleViewIcon) toggleViewIcon.textContent = "🎛️";
    if (toggleViewText) toggleViewText.textContent = "Controls";
    renderOverview();
  } else {
    overviewView.classList.add("hidden");

    if (toggleViewIcon) toggleViewIcon.textContent = "🏠";
    if (toggleViewText) toggleViewText.textContent = "All Units";

    if (activeDeviceId) {
      switchTab(activeDeviceId);
    }
  }

  renderTabs();
}

function openDeviceDetail(deviceId) {
  activeDeviceId = deviceId;
  try {
    localStorage.setItem("daikin_active_device_id", deviceId);
  } catch (e) {}
  setView("detail");
}

// Blank live fields immediately on tab switch ONLY when no state exists
function blankActiveFields(deviceMeta) {
  if (currentView !== "detail") return;
  if (activeDeviceState && activeDeviceState.id === deviceMeta.id) {
    // Preserve existing known state to avoid flashing '--'
    return;
  }

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

// Fetch all devices (Silent background revalidation, 0ms render from cache)
async function fetchDevices(silent = true) {
  // Only show loading spinner on very first visit if zero cached devices exist
  if (devices.length === 0 && loadingState) {
    loadingState.classList.remove("hidden");
  }

  try {
    const res = await fetch("/api/devices");
    if (!res.ok) throw new Error("Failed to load device list");
    const data = await res.json();
    devices = data.devices || [];

    // Cache devices in localStorage
    try {
      localStorage.setItem("daikin_devices_cache", JSON.stringify(devices));
    } catch (e) {}

    if (!activeDeviceId || !devices.find(d => d.id === activeDeviceId)) {
      const firstConfigured = devices.find(d => d.configured);
      activeDeviceId = firstConfigured ? firstConfigured.id : (devices[0]?.id || null);
      if (activeDeviceId) {
        try { localStorage.setItem("daikin_active_device_id", activeDeviceId); } catch (e) {}
      }
    }

    renderTabs();

    if (currentView === "overview") {
      renderOverview();
      overviewView.classList.remove("hidden");
      activeDeviceView.classList.add("hidden");
      unconfiguredView.classList.add("hidden");
    } else {
      if (activeDeviceId) {
        loadDeviceState(activeDeviceId, true);
      } else {
        renderUnconfiguredView({ name: "No AC Units Paired", ip: "Scan your Wi-Fi below to get started" });
      }
    }
  } catch (err) {
    if (!silent) setStatus("error", "Server Disconnected");
    console.warn("Init error:", err);
  } finally {
    if (loadingState) loadingState.classList.add("hidden");
    startCountdown();
  }
}

// Render Tabs Bar (Includes "All Units" as the first persistent tab)
function renderTabs() {
  deviceTabsContainer.innerHTML = "";

  // 1. All Units / Overview Tab
  const overviewTab = document.createElement("button");
  overviewTab.className = `device-tab ${currentView === "overview" ? "active" : ""}`;
  overviewTab.id = "tab-overview";
  const activeCount = devices.filter(d => d.configured && d.power).length;
  overviewTab.innerHTML = `
    <span>🏠 All Units</span>
    <span class="tab-badge">${activeCount > 0 ? `${activeCount} On` : "All Off"}</span>
  `;
  overviewTab.addEventListener("click", () => setView("overview"));
  deviceTabsContainer.appendChild(overviewTab);

  // 2. Individual Device Tabs
  devices.forEach(d => {
    const tab = document.createElement("button");
    const isActiveTab = currentView === "detail" && d.id === activeDeviceId;
    tab.className = `device-tab ${isActiveTab ? "active" : ""}`;
    tab.id = `tab-${d.id}`;

    let badgeText = "Setup";
    if (d.configured) {
      if (d.power !== undefined) {
        badgeText = d.power ? `${Number(d.target_temp || 24).toFixed(1)}°C` : "OFF";
      } else {
        badgeText = "Ready";
      }
    }
    tab.innerHTML = `
      <span>${d.name || d.id}</span>
      <span class="tab-badge">${badgeText}</span>
    `;

    tab.addEventListener("click", () => {
      openDeviceDetail(d.id);
    });

    deviceTabsContainer.appendChild(tab);
  });
}

// Render Overview Cards (All ACs at a glance)
function renderOverview() {
  if (!overviewGrid) return;
  overviewGrid.innerHTML = "";

  const configured = devices.filter(d => d.configured);
  const activeCount = configured.filter(d => d.power).length;
  if (overviewSubtitle) {
    overviewSubtitle.textContent = `${configured.length} unit(s) connected • ${activeCount} active`;
  }

  devices.forEach(d => {
    const card = document.createElement("div");
    const isOnline = d.status !== "offline";
    const isPowerOn = !!d.power && isOnline;
    card.className = `glance-card ${isPowerOn ? "is-on" : "is-off"}`;
    card.id = `glance-card-${d.id}`;

    // Mode badge
    let badgeHtml = "";
    if (!d.configured) {
      badgeHtml = `<span class="glance-badge badge-off">Unpaired</span>`;
    } else if (!isOnline) {
      badgeHtml = `<span class="glance-badge badge-off">Offline</span>`;
    } else if (isPowerOn) {
      const mode = d.mode || "cool";
      badgeHtml = `<span class="glance-badge ${mode === 'heat' ? 'badge-heat' : 'badge-cool'}">${modeIcon(mode)} ${mode.toUpperCase()}</span>`;
    } else {
      badgeHtml = `<span class="glance-badge badge-off">⚪ OFF</span>`;
    }

    const roomName = d.name || d.id;
    const targetTempDisplay = d.target_temp !== undefined && d.target_temp !== null ? Number(d.target_temp).toFixed(1) : "24.0";
    const indoorTempDisplay = d.indoor_temp !== undefined && d.indoor_temp !== null ? Number(d.indoor_temp).toFixed(1) : "--";

    card.innerHTML = `
      <div class="glance-card-header">
        <div class="glance-title-area">
          <div class="glance-title-row">
            <span class="glance-room-name">${roomName}</span>
            <button class="btn-rename btn-rename-card" title="Rename this room" data-id="${d.id}">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M17 3a2.828 2.828 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z"></path>
              </svg>
            </button>
          </div>
          <span class="glance-ip-label">${d.ip || "No IP assigned"} • BRP072C42</span>
        </div>
        ${badgeHtml}
      </div>

      <div class="glance-body">
        <button class="glance-power-btn ${isPowerOn ? 'is-on' : ''}" data-id="${d.id}" aria-label="Toggle Power for ${roomName}">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
            <path d="M18.36 6.64a9 9 0 1 1-12.73 0M12 2v10"/>
          </svg>
          <span class="glance-power-label">${isPowerOn ? 'ON' : 'OFF'}</span>
        </button>

        <div class="glance-temp-group">
          <div class="glance-temp-block">
            <span class="glance-temp-label">Room</span>
            <div class="glance-temp-value">${indoorTempDisplay}<span class="glance-temp-unit">°C</span></div>
          </div>

          <div class="glance-temp-block">
            <span class="glance-temp-label">Set</span>
            <div class="glance-temp-value">${targetTempDisplay}<span class="glance-temp-unit">°C</span></div>
          </div>

          <div class="glance-stepper-col">
            <button class="glance-stepper-btn btn-stepper-up" data-id="${d.id}" aria-label="Increase Temp">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3">
                <line x1="12" y1="5" x2="12" y2="19"></line>
                <line x1="5" y1="12" x2="19" y2="12"></line>
              </svg>
            </button>
            <button class="glance-stepper-btn btn-stepper-down" data-id="${d.id}" aria-label="Decrease Temp">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3">
                <line x1="5" y1="12" x2="19" y2="12"></line>
              </svg>
            </button>
          </div>
        </div>
      </div>

      <div class="glance-footer">
        <span class="glance-meta-text">
          ${isPowerOn ? `Fan: ${fanLabel(d.fan_rate)} • Swing: ${swingLabel(d.fan_dir)}` : "Standby / Idle"}
        </span>
        <button class="glance-btn-detail" data-id="${d.id}">
          <span>Controls</span>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:14px;height:14px;">
            <polyline points="9 18 15 12 9 6"></polyline>
          </svg>
        </button>
      </div>
    `;

    // Rename button
    const renameBtn = card.querySelector(".btn-rename-card");
    if (renameBtn) {
      renameBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        openRenameModal(d.id);
      });
    }

    // Power toggle button
    const powerBtn = card.querySelector(".glance-power-btn");
    if (powerBtn) {
      powerBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        toggleDevicePower(d.id);
      });
    }

    // Stepper buttons
    const upBtn = card.querySelector(".btn-stepper-up");
    if (upBtn) {
      upBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        nudgeDeviceTemp(d.id, 0.5);
      });
    }

    const downBtn = card.querySelector(".btn-stepper-down");
    if (downBtn) {
      downBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        nudgeDeviceTemp(d.id, -0.5);
      });
    }

    // Detail button (Explicit tap required to navigate to detailed controls)
    const detailBtn = card.querySelector(".glance-btn-detail");
    if (detailBtn) {
      detailBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        openDeviceDetail(d.id);
      });
    }

    overviewGrid.appendChild(card);
  });
}

// Quick Control: Power Toggle on Overview Card
async function toggleDevicePower(deviceId) {
  const dev = devices.find(d => d.id === deviceId);
  if (!dev) return;

  const newPower = !dev.power;
  dev.power = newPower;

  // Immediate optimistic update
  renderOverview();
  renderTabs();

  if (deviceId === activeDeviceId && currentView === "detail") {
    applyOptimisticPower(newPower);
  }

  showToast(`${dev.name || dev.id}: Turning ${newPower ? "ON" : "OFF"}...`);

  try {
    const res = await fetch(`/api/devices/${deviceId}/control`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ power: newPower })
    });
    const updated = await res.json();
    if (updated.error) throw new Error(updated.error);
    Object.assign(dev, updated);
    try { localStorage.setItem("daikin_devices_cache", JSON.stringify(devices)); } catch (e) {}
    renderOverview();
    renderTabs();
    showToast(`${dev.name || dev.id}: ${newPower ? "ON" : "OFF"}`);
  } catch (err) {
    showToast(`Error: ${err.message}`, true);
    fetchDevices(true);
  }
}

// Quick Control: Temp +/- Stepper on Overview Card (Debounced)
function nudgeDeviceTemp(deviceId, delta) {
  const dev = devices.find(d => d.id === deviceId);
  if (!dev) return;

  const current = Number(dev.target_temp || 24.0);
  const minTemp = Number(dev.min_temp || 18.0);
  const maxTemp = Number(dev.max_temp || 32.0);
  const nextTemp = Math.min(maxTemp, Math.max(minTemp, Math.round((current + delta) * 10) / 10));

  dev.target_temp = nextTemp;
  renderOverview();
  renderTabs();

  if (deviceId === activeDeviceId && currentView === "detail") {
    applyOptimisticTemp(nextTemp);
  }

  if (!devicePending[deviceId]) devicePending[deviceId] = {};
  devicePending[deviceId].target_temp = nextTemp;

  if (deviceTimers[deviceId]) clearTimeout(deviceTimers[deviceId]);
  deviceTimers[deviceId] = setTimeout(async () => {
    const patch = { ...devicePending[deviceId] };
    delete devicePending[deviceId];
    try {
      const res = await fetch(`/api/devices/${deviceId}/control`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(patch)
      });
      const updated = await res.json();
      if (updated.error) throw new Error(updated.error);
      Object.assign(dev, updated);
      try { localStorage.setItem("daikin_devices_cache", JSON.stringify(devices)); } catch (e) {}
      renderOverview();
      renderTabs();
    } catch (err) {
      showToast(`Error adjusting temperature: ${err.message}`, true);
    }
  }, 350);
}

// Quick Action: Turn All Running Units Off
async function turnAllOff() {
  const activeUnits = devices.filter(d => d.configured && d.power);
  if (activeUnits.length === 0) {
    showToast("All A/C units are already off");
    return;
  }

  activeUnits.forEach(d => { d.power = false; });
  renderOverview();
  renderTabs();
  showToast("Turning all units off...");

  const promises = activeUnits.map(d =>
    fetch(`/api/devices/${d.id}/control`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ power: false })
    })
  );

  await Promise.allSettled(promises);
  await fetchDevices(true);
  showToast("All A/C units turned off");
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
  try { localStorage.setItem("daikin_active_device_id", newDeviceId); } catch (e) {}

  // Update tab highlights
  document.querySelectorAll(".device-tab").forEach(tab => {
    tab.classList.toggle("active", tab.id === `tab-${newDeviceId}`);
  });

  const devMeta = devices.find(d => d.id === newDeviceId) || { id: newDeviceId, name: newDeviceId, ip: "", configured: false };

  // If unconfigured slot, show setup card immediately
  if (!devMeta.configured) {
    renderUnconfiguredView(devMeta);
    setStatus("online", "Unconfigured Slot");
    return;
  }

  // Switch view panel and load from server RAM cache silently (<2ms)
  activeDeviceView.classList.remove("hidden");
  unconfiguredView.classList.add("hidden");
  overviewView.classList.add("hidden");

  // Fetch state from RAM cache immediately
  loadDeviceState(newDeviceId, true);
}

// Load single device state on-demand with cancellation of older requests
async function loadDeviceState(id, isBackground = false, forceRefresh = false) {
  if (abortController) {
    abortController.abort();
  }
  abortController = new AbortController();
  const currentRequestId = ++activeRequestId;

  if (!isBackground && currentView === "detail") {
    isBusy = true;
    setStatus("busy", "Syncing unit...");
    blankActiveFields(devices.find(d => d.id === id) || { id, ip: "..." });
  }

  try {
    const url = forceRefresh ? `/api/devices/${id}?refresh=1` : `/api/devices/${id}`;
    const res = await fetch(url, { signal: abortController.signal });
    if (!res.ok) throw new Error("HTTP connection error");
    const state = await res.json();

    if (currentRequestId !== activeRequestId || id !== activeDeviceId) {
      return;
    }

    // Always update cached device state in devices list
    const d = devices.find(dev => dev.id === state.id);
    if (d) {
      Object.assign(d, state);
      try { localStorage.setItem("daikin_devices_cache", JSON.stringify(devices)); } catch (e) {}
    }

    activeDeviceState = state;
    try {
      localStorage.setItem("daikin_active_state_" + id, JSON.stringify(state));
      localStorage.setItem("daikin_active_device_id", id);
    } catch (e) {}

    if (currentView === "detail") {
      if (!state.configured) {
        renderUnconfiguredView(state);
      } else {
        renderActiveView(state);
      }
    } else if (currentView === "overview") {
      renderOverview();
      renderTabs();
    }

    isBusy = false;
    startCountdown();
  } catch (err) {
    if (err.name === "AbortError") return;
    if (currentRequestId === activeRequestId && currentView === "detail") {
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
  if (currentView === "detail") {
    activeDeviceView.classList.add("hidden");
    overviewView.classList.add("hidden");
    unconfiguredView.classList.remove("hidden");
  }

  setupDeviceTitle.textContent = `${state.name} Not Configured`;
  setupIdPreview.textContent = state.id;
  setupNamePreview.textContent = state.name;
}

// Render Active State
function renderActiveView(state) {
  // Guard: NEVER hide overview or switch to activeDeviceView unless currentView is actually "detail"
  if (currentView === "detail") {
    unconfiguredView.classList.add("hidden");
    overviewView.classList.add("hidden");
    activeDeviceView.classList.remove("hidden");
  }

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

  // Swing chips
  document.querySelectorAll("#swing-selector .chip-btn").forEach(btn => {
    btn.classList.toggle("active", btn.dataset.swing === String(state.fan_dir));
  });

  // Environmental Sensor Readings
  statHtemp.innerHTML = state.indoor_temp !== undefined && state.indoor_temp !== null
    ? `${state.indoor_temp}<span class="stat-unit">°C</span>`
    : `--<span class="stat-unit">°C</span>`;

  statOtemp.innerHTML = state.outdoor_temp !== undefined && state.outdoor_temp !== null
    ? `${state.outdoor_temp}<span class="stat-unit">°C</span>`
    : `--<span class="stat-unit">°C</span>`;

  statHhum.innerHTML = state.indoor_humidity !== undefined && state.indoor_humidity !== null
    ? `${state.indoor_humidity}<span class="stat-unit">%</span>`
    : `--<span class="stat-unit">%</span>`;

  statCmpfreq.innerHTML = state.compressor_freq !== undefined && state.compressor_freq !== null
    ? `${state.compressor_freq}<span class="stat-unit">Hz</span>`
    : `--<span class="stat-unit">Hz</span>`;

  statRuntime.innerHTML = state.runtime_today !== undefined && state.runtime_today !== null
    ? `${state.runtime_today}<span class="stat-unit">m</span>`
    : `--<span class="stat-unit">m</span>`;

  const errCode = state.error_code ?? 0;
  statError.textContent = errCode === 0 ? "0 (Normal)" : `Code ${errCode}`;
  statError.className = errCode === 0 ? "stat-value stat-ok" : "stat-value stat-error";

  // Hardware specs
  hwMac.textContent = state.mac || "--";
  hwFirmware.textContent = state.firmware_version || "--";
  if (hwClock) hwClock.textContent = state.ac_clock || "--";

  // Sync tab badge
  if (activeDeviceId) {
    const tab = document.getElementById(`tab-${activeDeviceId}`);
    if (tab) {
      const badge = tab.querySelector(".tab-badge");
      if (badge) {
        badge.textContent = state.power ? `${Number(state.target_temp || 24).toFixed(1)}°C` : "OFF";
      }
    }
  }

  // Also update corresponding device in devices cache
  const dev = devices.find(devItem => devItem.id === state.id);
  if (dev) {
    Object.assign(dev, state);
  }
}

// Optimistic UI Updaters
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

// Queue & Debounce Control Commands for Active Device
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

    if (Object.keys(pendingPatch).length === 0) {
      if (currentView === "detail") {
        renderActiveView(updated);
      } else if (currentView === "overview") {
        renderOverview();
      }
      showToast("Command applied to AC");
      startCountdown();
    } else {
      flushPendingPatch();
    }
  } catch (err) {
    console.error(`[Command Error on ${activeDeviceId}]:`, err);
    showToast(`Error: ${err.message}`, true);
    setStatus("error", "Command failed");
    if (lastConfirmedState) {
      activeDeviceState = JSON.parse(JSON.stringify(lastConfirmedState));
      if (currentView === "detail") {
        renderActiveView(lastConfirmedState);
      }
    }
    pendingPatch = {};
    startCountdown();
  } finally {
    inFlightRequest = false;
  }
}

// Direct In-Browser Nickname Customization
function openRenameModal(deviceId) {
  const dev = devices.find(d => d.id === deviceId);
  if (!dev) return;

  renameTargetDeviceId = deviceId;
  inputRenameName.value = dev.name || dev.id;
  modalRename.classList.remove("hidden");
  setTimeout(() => {
    inputRenameName.focus();
    inputRenameName.select();
  }, 60);
}

function closeRenameModal() {
  modalRename.classList.add("hidden");
  renameTargetDeviceId = null;
}

async function saveRename() {
  if (!renameTargetDeviceId) return;
  const dev = devices.find(d => d.id === renameTargetDeviceId);
  if (!dev) return;

  const newName = inputRenameName.value.trim();
  if (!newName) {
    showToast("Please enter a room nickname", true);
    return;
  }

  btnSaveRename.disabled = true;
  try {
    const payload = {
      id: dev.id,
      name: newName,
      ip: dev.ip,
      key: dev.key || ""
    };

    const res = await fetch("/api/devices/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });

    const data = await res.json();
    if (!res.ok || data.error) throw new Error(data.error || "Save failed");

    dev.name = newName;
    try { localStorage.setItem("daikin_devices_cache", JSON.stringify(devices)); } catch (e) {}

    if (activeDeviceId === dev.id) {
      activeDeviceName.textContent = newName;
    }

    closeRenameModal();
    renderTabs();
    if (currentView === "overview") {
      renderOverview();
    }
    showToast(`Renamed to "${newName}"`);
  } catch (err) {
    showToast(`Rename failed: ${err.message}`, true);
  } finally {
    btnSaveRename.disabled = false;
  }
}

// Event Listeners for Single Unit Controls
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

// View Toggle & Back Navigation
if (btnToggleView) {
  btnToggleView.addEventListener("click", () => {
    setView(currentView === "overview" ? "detail" : "overview");
  });
}

if (btnBackToOverview) {
  btnBackToOverview.addEventListener("click", () => {
    setView("overview");
  });
}

if (btnAllOff) {
  btnAllOff.addEventListener("click", turnAllOff);
}

// Rename Active Unit from Detail View Header
if (btnEditActiveName) {
  btnEditActiveName.addEventListener("click", () => {
    if (activeDeviceId) openRenameModal(activeDeviceId);
  });
}

// Rename Modal Controls
if (btnCloseRenameModal) btnCloseRenameModal.addEventListener("click", closeRenameModal);
if (btnCancelRename) btnCancelRename.addEventListener("click", closeRenameModal);
if (btnSaveRename) btnSaveRename.addEventListener("click", saveRename);
if (modalRename) {
  modalRename.addEventListener("click", (e) => {
    if (e.target === modalRename) closeRenameModal();
  });
}
if (inputRenameName) {
  inputRenameName.addEventListener("keydown", (e) => {
    if (e.key === "Enter") saveRename();
    else if (e.key === "Escape") closeRenameModal();
  });
}

// Diagnostics Accordion Summary Label Sync
if (diagnosticsDrawer) {
  diagnosticsDrawer.addEventListener("toggle", () => {
    const pill = diagnosticsDrawer.querySelector(".summary-pill");
    if (pill) {
      pill.textContent = diagnosticsDrawer.open ? "Hide" : "Show";
    }
  });
}

// Refresh button (forces live telemetry read from AC hardware)
btnRefresh.addEventListener("click", () => {
  if (currentView === "overview") {
    showToast("Refreshing all units...");
    fetchDevices(false);
  } else if (activeDeviceId) {
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
  if (!document.hidden && !isUpdating) {
    if (currentView === "overview") {
      fetchDevices(true);
    } else if (activeDeviceId && currentView === "detail") {
      loadDeviceState(activeDeviceId, true);
    }
  }
});

// 2. Instant Render at boot time (0ms from Cache before network fetch)
if (devices && devices.length > 0) {
  renderTabs();
  if (currentView === "overview") {
    renderOverview();
    overviewView.classList.remove("hidden");
    activeDeviceView.classList.add("hidden");
    unconfiguredView.classList.add("hidden");
  } else if (activeDeviceState) {
    overviewView.classList.add("hidden");
    renderActiveView(activeDeviceState);
  }
  if (loadingState) loadingState.classList.add("hidden");
}

// 3. Silent background fetch from server RAM cache to reconcile fresh state
fetchDevices(true);
