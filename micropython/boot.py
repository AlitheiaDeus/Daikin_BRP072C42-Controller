# boot.py - MicroPython System Boot & Network Initialization
import gc
import sys
import time
import json

# 1. Early Heap Hygiene & Threshold Configuration
gc.collect()
def get_free_heap():
    try:
        return gc.mem_free()
    except (AttributeError, ValueError):
        return 0

try:
    gc.threshold(gc.mem_free() // 4 + gc.mem_alloc())
except (AttributeError, ValueError):
    pass

print("==================================================")
print("  Daikin AC Bridge - MicroPython Boot Sequence")
print(f"  System:    {sys.platform} ({sys.version})")
print(f"  Boot Heap: {get_free_heap()} bytes free")
print("==================================================")

# 2. Wi-Fi Station Configuration
def setup_network():
    try:
        import network
        sta = network.WLAN(network.STA_IF)
        sta.active(True)
        try:
            sta.config(pm=network.WLAN.PM_NONE)
        except Exception as e:
            print(f"[!] PM config note: {e}")
        time.sleep(0.5)

        # 1. Load credentials & static IP from git-ignored daikin_config.json
        cfg = {}
        try:
            with open("daikin_config.json", "r") as f:
                cfg = json.load(f)
        except Exception:
            pass

        wcfg = cfg.get("wifi", {})
        # No static IP unless daikin_config.json provides one (falls back to DHCP)
        static_cfg = wcfg.get("static_ip")
        known_networks = wcfg.get("networks", [])

        # Fallback to single ssid/password format if present
        if not known_networks and wcfg.get("ssid"):
            known_networks = [(wcfg.get("ssid"), wcfg.get("password", ""))]

        # 2. Apply Static IP Assignment
        if static_cfg:
            try:
                sta.ifconfig(tuple(static_cfg))
                print(f"[+] Static IP configured: {static_cfg[0]} (GW: {static_cfg[2]})")
            except Exception as e:
                print(f"[!] Note on static ifconfig: {e}")

        # 3. Multi-network scan & connect
        if not sta.isconnected() and known_networks:
            try:
                available = [n[0].decode("utf-8", "ignore") for n in sta.scan()]
            except Exception:
                available = []

            for s, p in known_networks:
                if not available or s in available:
                    print(f"[*] Connecting to Wi-Fi SSID '{s}'...")
                    sta.connect(s, p)
                    for _ in range(15):
                        if sta.isconnected():
                            ip = sta.ifconfig()[0]
                            print(f"[+] Wi-Fi Connected to '{s}'! IP: {ip}")
                            return
                        time.sleep(1)

        if sta.isconnected():
            print(f"[+] Wi-Fi Active: IP {sta.ifconfig()[0]}")
        else:
            print("[!] Wi-Fi not connected yet. Awaiting association...")
    except Exception as exc:
        print(f"[!] Network setup error: {exc}")

setup_network()

# 3. WebREPL Daemon Setup (Crucial for Wireless OTA Deployments)
try:
    import webrepl
    webrepl.start()
    print("[+] WebREPL daemon initialized successfully on port 8266.")
except Exception as exc:
    print(f"[*] WebREPL start note: {exc}")

# 4. Final Early Heap Collection
gc.collect()
print(f"[*] Boot complete. Usable heap: {get_free_heap()} bytes.\n")
