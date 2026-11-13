# main.py - Asynchronous MicroPython Daikin AC Controller & Web Server
try:
    import uasyncio as asyncio
except ImportError:
    import asyncio
import json
import os
import gc
import socket
import time

try:
    import network
except ImportError:
    class _MockWLAN:
        def __init__(self, mode):
            pass
        def active(self, val=None):
            return True
        def isconnected(self):
            return False
        def ifconfig(self):
            return ("127.0.0.1", "255.255.255.0", "127.0.0.1", "1.1.1.1")
    class network:
        STA_IF = 0
        WLAN = _MockWLAN
import config
from daikin import DaikinDeviceManager
from discover import async_discover_units, discover_units

# ==============================================================================
# Global In-Memory Shared Cache & Device Manager
# ==============================================================================
def get_free_heap():
    """Return free memory in bytes on MicroPython, or 0 on CPython."""
    try:
        return gc.mem_free()
    except (AttributeError, ValueError):
        return 0

manager = DaikinDeviceManager(config.CONFIG_FILE, config.CACHE_FILE)

# Shared thread-safe / async-safe in-memory cache
# All HTTP read queries strictly read from this cache to respond in single-digit ms!
app_state = {
    "units": {},
    "last_poll": 0,
    "system": {
        "heap_free": get_free_heap(),
        "uptime": 0
    }
}

# Pre-populate in-memory cache from manager devices (flash cache or defaults)
for dev in manager.devices.values():
    app_state["units"][dev.device_id] = dev.get_cached_state() or dev.get_default_state()

last_client_seen = time.time()
poll_wake_event = asyncio.Event()


def mark_client_active():
    """Record client activity and wake the background poller if idle."""
    global last_client_seen
    last_client_seen = time.time()
    poll_wake_event.set()


# ==============================================================================
# HTTP Response & Streaming Helpers (Memory Optimized)
# ==============================================================================
def get_mime_type(path):
    if path.endswith(".html"):
        return "text/html; charset=utf-8"
    elif path.endswith(".css"):
        return "text/css; charset=utf-8"
    elif path.endswith(".js"):
        return "application/javascript; charset=utf-8"
    elif path.endswith(".json"):
        return "application/json; charset=utf-8"
    elif path.endswith(".png"):
        return "image/png"
    elif path.endswith(".ico"):
        return "image/x-icon"
    return "text/plain; charset=utf-8"


async def send_json(writer, data, status_code=200):
    """Serialize and transmit JSON response with non-blocking drain."""
    body = json.dumps(data)
    writer.write(f"HTTP/1.1 {status_code} OK\r\n".encode())
    writer.write(b"Content-Type: application/json; charset=utf-8\r\n")
    writer.write(f"Content-Length: {len(body)}\r\n".encode())
    writer.write(b"Access-Control-Allow-Origin: *\r\n")
    writer.write(b"Access-Control-Allow-Methods: GET, POST, OPTIONS\r\n")
    writer.write(b"Access-Control-Allow-Headers: Content-Type\r\n")
    writer.write(b"Connection: close\r\n\r\n")
    writer.write(body.encode("utf-8"))
    await writer.drain()


async def send_cors(writer):
    """Respond to OPTIONS CORS preflight or empty 204 requests."""
    writer.write(b"HTTP/1.1 204 No Content\r\n")
    writer.write(b"Access-Control-Allow-Origin: *\r\n")
    writer.write(b"Access-Control-Allow-Methods: GET, POST, OPTIONS\r\n")
    writer.write(b"Access-Control-Allow-Headers: Content-Type\r\n")
    writer.write(b"Connection: close\r\n\r\n")
    await writer.drain()


async def serve_file(writer, filepath, mime_type, req_headers=None, has_version_query=False):
    """Stream static files in 1024-byte chunks with Gzip pre-compression support.
    
    Streaming chunks avoids loading 20KB-60KB assets into memory at once, protecting
    the ESP32 from allocation spikes and heap exhaustion.
    """
    try:
        gz_path = filepath + ".gz"
        use_gzip = False
        if req_headers and "gzip" in req_headers.get("accept-encoding", "").lower():
            try:
                os.stat(gz_path)
                filepath = gz_path
                use_gzip = True
            except OSError:
                pass

        size = os.stat(filepath)[6]
        writer.write(b"HTTP/1.1 200 OK\r\n")
        writer.write(f"Content-Type: {mime_type}\r\n".encode())
        if use_gzip:
            writer.write(b"Content-Encoding: gzip\r\n")
        writer.write(f"Content-Length: {size}\r\n".encode())
        writer.write(b"Access-Control-Allow-Origin: *\r\n")
        if has_version_query:
            writer.write(b"Cache-Control: public, max-age=604800, immutable\r\n")
        else:
            writer.write(b"Cache-Control: no-cache\r\n")
        writer.write(b"Connection: close\r\n\r\n")
        await writer.drain()

        with open(filepath, "rb") as f:
            while True:
                chunk = f.read(1024)
                if not chunk:
                    break
                writer.write(chunk)
                await writer.drain()
    except Exception as e:
        err = f"File error: {e}"
        writer.write(b"HTTP/1.1 404 Not Found\r\n")
        writer.write(f"Content-Length: {len(err)}\r\n".encode())
        writer.write(b"Connection: close\r\n\r\n")
        writer.write(err.encode())
        await writer.drain()


# ==============================================================================
# Non-Blocking HTTP Request Handler Coroutine
# ==============================================================================
async def client_handler(reader, writer):
    """Asynchronous HTTP client connection handler coroutine."""
    try:
        # 1. Asynchronously read HTTP request line
        req_line = await reader.readline()
        if not req_line:
            return

        parts = req_line.decode("utf-8", "ignore").strip().split(" ")
        if len(parts) < 2:
            return

        method = parts[0].upper()
        full_path = parts[1]
        path, _, query_str = full_path.partition("?")

        # 2. Asynchronously read HTTP request headers
        content_length = 0
        req_headers = {}
        while True:
            hline = await reader.readline()
            if not hline or hline in (b"\r\n", b"\n"):
                break
            line_str = hline.decode("utf-8", "ignore")
            if ":" in line_str:
                hk, hv = line_str.split(":", 1)
                hk_clean = hk.strip().lower()
                hv_clean = hv.strip()
                req_headers[hk_clean] = hv_clean
                if hk_clean == "content-length":
                    try:
                        content_length = int(hv_clean)
                    except ValueError:
                        pass

        # 3. Asynchronously read request body if present
        body_str = ""
        if content_length > 0:
            body_bytes = await reader.readexactly(content_length)
            body_str = body_bytes.decode("utf-8", "ignore")

        # 4. Handle CORS preflight OPTIONS
        if method == "OPTIONS":
            await send_cors(writer)
            return

        # 5. Suppress favicon logs
        if path == "/favicon.ico":
            await send_cors(writer)
            return

        # 6. API Route: List devices summary
        # STRICT REQUIREMENT: Strictly served from in-memory cache in single-digit ms!
        if method in ("GET", "HEAD") and path == "/api/devices":
            mark_client_active()
            cached_devices = list(app_state["units"].values())
            await send_json(writer, {"devices": cached_devices})
            return

        # 7. API Route: UDP Discovery
        if method in ("GET", "HEAD") and path == "/api/discover":
            raw_discovered = await async_discover_units(timeout=2.0)
            results = []
            for u in raw_discovered:
                matched = None
                for ctrl in manager.devices.values():
                    if ctrl.ip == u["ip"]:
                        matched = ctrl
                        break
                results.append({
                    "ip": u["ip"],
                    "detected_name": u["name"],
                    "ssid": u["ssid"],
                    "mac": u["mac"],
                    "configured": bool(matched and matched.is_configured),
                    "configured_id": matched.device_id if matched else None,
                    "configured_name": matched.name if matched else u["name"],
                    "has_key": bool(matched and matched.key),
                })
            await send_json(writer, {"discovered": results})
            return

        # 8. API Route: Single device state or dump
        if method in ("GET", "HEAD") and path.startswith("/api/devices/"):
            subparts = path.strip("/").split("/")
            if len(subparts) >= 3:
                dev_id = subparts[2]
                device = manager.get_device(dev_id)
                if not device:
                    await send_json(writer, {"error": "Device not found"}, 404)
                    return

                # Hardware diagnostic dump
                if len(subparts) == 4 and subparts[3] == "dump":
                    telemetry = {
                        "basic_info": await device.query_endpoint("/common/basic_info"),
                        "sensor_info": await device.query_endpoint("/aircon/get_sensor_info"),
                        "control_info": await device.query_endpoint("/aircon/get_control_info"),
                        "model_info": await device.query_endpoint("/aircon/get_model_info"),
                        "week_power": await device.query_endpoint("/aircon/get_week_power"),
                        "datetime_info": await device.query_endpoint("/common/get_datetime"),
                    }
                    await send_json(writer, telemetry)
                    return

                # STRICT REQUIREMENT: Single-digit millisecond response from in-memory RAM cache!
                mark_client_active()
                cached = app_state["units"].get(dev_id)
                if cached:
                    await send_json(writer, cached)
                    return
                default_st = device.get_default_state()
                app_state["units"][dev_id] = default_st
                await send_json(writer, default_st)
                return

        # 9. API Route: Control Daikin Unit
        if method == "POST" and path.startswith("/api/devices/") and path.endswith("/control"):
            mark_client_active()
            subparts = path.strip("/").split("/")
            if len(subparts) == 4:
                dev_id = subparts[2]
                device = manager.get_device(dev_id)
                if not device:
                    await send_json(writer, {"error": "Device not found"}, 404)
                    return

                try:
                    payload = json.loads(body_str) if body_str else {}
                    res = await device.update_settings(**payload)

                    if isinstance(res, dict) and "id" in res and "power" in res:
                        # Instant cache update with synthetic state
                        app_state["units"][dev_id] = res
                        await send_json(writer, res)
                        return

                    if isinstance(res, dict) and res.get("ret") != "OK":
                        err_code = res.get("ret", "REJECTED")
                        await send_json(writer, {
                            "error": f"Daikin hardware error ({err_code})",
                            "daikin_res": res
                        }, 400)
                        return

                    updated = await device.get_full_state()
                    updated["daikin_res"] = res
                    app_state["units"][dev_id] = updated
                    await send_json(writer, updated)
                    return
                except Exception as e:
                    await send_json(writer, {"error": str(e)}, 500)
                    return

        # 10. API Route: Save/Pair Unit
        if method == "POST" and path == "/api/devices/save":
            try:
                payload = json.loads(body_str) if body_str else {}
                dev_id = payload.get("id")
                name = payload.get("name", "").strip()
                ip = payload.get("ip", "").strip()
                key = payload.get("key", "").strip()

                if not ip:
                    await send_json(writer, {"error": "IP address required"}, 400)
                    return

                if not dev_id:
                    for ctrl in manager.devices.values():
                        if ctrl.ip == ip:
                            dev_id = ctrl.device_id
                            break

                ctrl = manager.upsert_device(dev_id=dev_id, name=name, ip=ip, key=key)
                reg_ok = await ctrl.register()
                state = await ctrl.get_full_state()
                app_state["units"][ctrl.device_id] = state
                manager.save_cache()

                await send_json(writer, {
                    "success": True,
                    "handshake_ok": reg_ok,
                    "device": {
                        "id": ctrl.device_id,
                        "name": ctrl.name,
                        "ip": ctrl.ip,
                        "configured": ctrl.is_configured,
                        "status": state.get("status", "unknown"),
                        "power": state.get("power", False),
                        "indoor_temp": state.get("indoor_temp"),
                        "target_temp": state.get("target_temp"),
                    }
                })
                return
            except Exception as e:
                await send_json(writer, {"error": str(e)}, 500)
                return

        # 11. API Route: Delete Device
        if method == "POST" and path == "/api/devices/delete":
            try:
                payload = json.loads(body_str) if body_str else {}
                dev_id = payload.get("id")
                if not dev_id:
                    await send_json(writer, {"error": "Device ID required"}, 400)
                    return
                ok = manager.delete_device(dev_id)
                if dev_id in app_state["units"]:
                    del app_state["units"][dev_id]
                manager.save_cache()
                await send_json(writer, {"success": ok})
                return
            except Exception as e:
                await send_json(writer, {"error": str(e)}, 500)
                return

        # 12. Static Web Dashboard Routes
        has_version = bool(query_str)
        if path in ("/", "/index.html", "/dashboard"):
            await serve_file(writer, "web/index.html", "text/html; charset=utf-8", req_headers, False)
            return
        elif path in ("/style.css", "/web/style.css"):
            await serve_file(writer, "web/style.css", "text/css; charset=utf-8", req_headers, has_version)
            return
        elif path in ("/app.js", "/web/app.js"):
            await serve_file(writer, "web/app.js", "application/javascript; charset=utf-8", req_headers, has_version)
            return

        # 13. 404 Fallback Route
        writer.write(b"HTTP/1.1 404 Not Found\r\nContent-Length: 9\r\nConnection: close\r\n\r\nNot Found")
        await writer.drain()

    except Exception as exc:
        print(f"[Client Error] {exc}")
    finally:
        # STRICT REQUIREMENT: Cleanly drain, close, and wait_closed client writer
        try:
            await writer.drain()
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


# ==============================================================================
# Background Polling Task (Decoupled Telemetry Polling)
# ==============================================================================
async def poll_units_task():
    """Background polling task iterating through configured Daikin AC units.
    
    - Decoupled from HTTP request handling.
    - Loops through all 4 configured endpoints with cooperative non-blocking streams.
    - Stores incoming telemetry directly into the in-memory cache app_state['units'].
    - Isolates network drops/timeouts per unit with try...except (OSError, asyncio.TimeoutError).
    """
    print("[*] Background Daikin adapter polling task started.")
    # On initial boot, pause briefly so the web server can serve the first page immediately
    await asyncio.sleep(2)
    was_idle = True

    while True:
        configured_devs = [c for c in manager.devices.values() if c.is_configured]
        if not configured_devs:
            await asyncio.sleep(5)
            continue

        now = time.time()
        has_turbo = any(getattr(c, "_turbo_restore", None) for c in configured_devs)
        is_active = (now - last_client_seen) < 40 or has_turbo

        if not is_active:
            was_idle = True
            poll_wake_event.clear()
            try:
                await asyncio.wait_for(poll_wake_event.wait(), timeout=900)
                deep_sweep = True
                was_idle = False
            except asyncio.TimeoutError:
                deep_sweep = True
        else:
            deep_sweep = was_idle
            was_idle = False

        # Stagger polls evenly across target cycle (e.g. 30s / 4 units = 7.5s interval per unit)
        interval = max(config.POLL_STAGGER_MIN_SECS, config.POLL_CYCLE_TARGET_SECS / len(configured_devs))

        for ctrl in configured_devs:
            try:
                # Wrap network I/O per unit in robust exception isolation guard
                # Allow 25s total for multi-endpoint TLS sequence on deep sweeps
                unit_state = await asyncio.wait_for(
                    ctrl.get_full_state(deep=deep_sweep),
                    timeout=25.0
                )
                app_state["units"][ctrl.device_id] = unit_state
            except (OSError, asyncio.TimeoutError) as exc:
                print(f"[{ctrl.name}] Network poll error ({type(exc).__name__}): {exc}")
                # Mark unit as offline in cache while preserving last known values
                if ctrl.device_id in app_state["units"]:
                    app_state["units"][ctrl.device_id]["status"] = "offline"
            except Exception as exc:
                print(f"[{ctrl.name}] Unexpected poll error: {exc}")

            # Cooperative yield so WebREPL, HTTP server, and scheduler are never starved
            gc.collect()
            await asyncio.sleep(interval)

        manager.save_cache()
        gc.collect()


# ==============================================================================
# Periodic Heap Hygiene Maintenance Task
# ==============================================================================
async def gc_task(interval_secs=30):
    """Periodic background task ensuring total heap footprint stays within ESP32 headroom."""
    while True:
        await asyncio.sleep(interval_secs)
        before = get_free_heap()
        gc.collect()
        after = get_free_heap()
        app_state["system"]["heap_free"] = after
        # Diagnostic yield point ensuring system ticks and scheduler run unimpeded
        await asyncio.sleep_ms(0)


async def webrepl_tick_task():
    """Keep uasyncio ipoll() waking every 200ms so WebREPL interrupts are serviced."""
    while True:
        await asyncio.sleep_ms(200)


# ==============================================================================
# Application Lifecycle & Entry Point
# ==============================================================================
async def main():
    wlan = network.WLAN(network.STA_IF)
    ip = wlan.ifconfig()[0] if wlan.isconnected() else "127.0.0.1"

    print("==================================================")
    print("  Daikin AC Multi-Unit Asynchronous Controller")
    print(f"  Live Dashboard:   http://{ip}:{config.HTTP_PORT}")
    print(f"  Configured Units: {len(manager.devices)}")
    print(f"  Initial Free Heap:{get_free_heap()} bytes")
    print("==================================================")

    # 1. Spawn periodic garbage collection & heap hygiene task
    t_gc = asyncio.create_task(gc_task(config.GC_INTERVAL_SECS))

    # 2. Spawn background Daikin adapter polling task
    t_poll = asyncio.create_task(poll_units_task())

    # 3. Spawn lightweight WebREPL heartbeat task
    t_webrepl = asyncio.create_task(webrepl_tick_task())

    # 4. Start non-blocking HTTP server
    # Note: MicroPython's uasyncio.start_server creates a non-blocking socket
    # and explicitly executes `s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)`
    # prior to socket binding.
    server = await asyncio.start_server(
        client_handler,
        config.HTTP_HOST,
        config.HTTP_PORT
    )
    print(f"[*] Non-blocking HTTP Server listening on port {config.HTTP_PORT} (SO_REUSEADDR active)...")

    try:
        # Keep main event loop running cooperatively
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        print("[*] Main task cancelled. Initiating cooperative shutdown...")
    finally:
        print("[*] Closing server and background tasks...")
        server.close()
        await server.wait_closed()
        t_webrepl.cancel()
        t_poll.cancel()
        t_gc.cancel()
        await asyncio.sleep(0.1)
        gc.collect()
        print("[+] Server stopped cleanly.")


def start():
    """Application entry point invoked via asyncio.run(main())."""
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[!] Execution interrupted by KeyboardInterrupt. Stopping gracefully...")
    except Exception as exc:
        print(f"\n[Fatal Server Error] {exc}")


if __name__ == "__main__":
    start()
