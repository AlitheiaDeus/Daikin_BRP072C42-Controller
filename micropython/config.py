# config.py - Centralized Configuration for MicroPython Daikin Bridge
import json

# ==============================================================================
# Server & Network Configuration
# ==============================================================================
HTTP_HOST = "0.0.0.0"
HTTP_PORT = 80

# Wi-Fi Station Credentials (used by boot.py if configured)
WIFI_SSID = ""
WIFI_PASSWORD = ""
WIFI_CONNECT_TIMEOUT_SEC = 15

# ==============================================================================
# Background Task & Polling Intervals (Cooperative Scheduling)
# ==============================================================================
POLL_CYCLE_TARGET_SECS = 30.0     # Target duration for 1 full polling cycle across all units
POLL_STAGGER_MIN_SECS = 3.0       # Minimum yield time between polling successive units
GC_INTERVAL_SECS = 30             # Heap hygiene interval (gc_task)
SOCKET_TIMEOUT_SECS = 5.0         # Non-blocking socket connect & read timeout

# File Paths
CONFIG_FILE = "daikin_config.json"
CACHE_FILE = "devices_cache.json"

# ==============================================================================
# Placeholder Endpoints (fallback if daikin_config.json is absent or has no devices)
# Real IPs, keys and UUIDs live ONLY in the git-ignored daikin_config.json.
# ==============================================================================
DEFAULT_DEVICES = [
    {"id": "ac_1", "name": "AC 1", "ip": "", "key": "", "uuid": ""},
    {"id": "ac_2", "name": "AC 2", "ip": "", "key": "", "uuid": ""},
    {"id": "ac_3", "name": "AC 3", "ip": "", "key": "", "uuid": ""},
    {"id": "ac_4", "name": "AC 4", "ip": "", "key": "", "uuid": ""},
]


def load_config(path=CONFIG_FILE):
    """Load devices configuration from flash or fallback to defaults."""
    try:
        with open(path, "r") as f:
            data = json.load(f)
            devs = data.get("devices", [])
            if devs:
                return devs
    except Exception:
        pass
    return list(DEFAULT_DEVICES)


def save_config(devices_list, path=CONFIG_FILE):
    """Persist devices configuration to flash while preserving wifi settings."""
    try:
        existing = {}
        try:
            with open(path, "r") as f:
                existing = json.load(f)
        except Exception:
            pass
        existing["devices"] = devices_list
        with open(path, "w") as f:
            json.dump(existing, f)
        return True
    except Exception as exc:
        print(f"[Config Save Error] {exc}")
        return False


def load_cache(path=CACHE_FILE):
    """Load cached state dictionary from flash."""
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def save_cache(cache_data, path=CACHE_FILE):
    """Persist state dictionary to flash."""
    try:
        with open(path, "w") as f:
            json.dump(cache_data, f)
        return True
    except Exception:
        return False
