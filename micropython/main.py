# main.py - Daikin AC MicroPython Web & API Server
import uasyncio as asyncio
import json
import os
import gc
import network
from daikin import DaikinDeviceManager
from discover import discover_units

PORT = 80
manager = DaikinDeviceManager("daikin_config.json")


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
    writer.write(b"HTTP/1.1 204 No Content\r\n")
    writer.write(b"Access-Control-Allow-Origin: *\r\n")
    writer.write(b"Access-Control-Allow-Methods: GET, POST, OPTIONS\r\n")
    writer.write(b"Access-Control-Allow-Headers: Content-Type\r\n")
    writer.write(b"Connection: close\r\n\r\n")
    await writer.drain()


async def serve_file(writer, filepath, mime_type, req_headers=None, has_version_query=False):
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


async def handle_client(reader, writer):
    try:
        req_line = await reader.readline()
        if not req_line:
            return

        parts = req_line.decode("utf-8", "ignore").strip().split(" ")
        if len(parts) < 2:
            return

        method = parts[0].upper()
        full_path = parts[1]
        path, _, query_str = full_path.partition("?")

        # Read headers
        content_length = 0
        req_headers = {}
        while True:
            hline = await reader.readline()
            if not hline or hline == b"\r\n" or hline == b"\n":
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

        # Read body if present
        body_str = ""
        if content_length > 0:
            body_bytes = await reader.readexactly(content_length)
            body_str = body_bytes.decode("utf-8", "ignore")

        # Handle CORS preflight
        if method == "OPTIONS":
            await send_cors(writer)
            return

        # Suppress favicon logs
        if path == "/favicon.ico":
            await send_cors(writer)
            return

        # API: List devices summary
        if method == "GET" and path == "/api/devices":
            await send_json(writer, {"devices": manager.get_summaries()})
            return

        # API: UDP Discovery
        if method == "GET" and path == "/api/discover":
            raw_discovered = discover_units(timeout=2.0)
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

        # API: Single device state or dump
        if method == "GET" and path.startswith("/api/devices/"):
            subparts = path.strip("/").split("/")
            if len(subparts) >= 3:
                dev_id = subparts[2]
                device = manager.get_device(dev_id)
                if not device:
                    await send_json(writer, {"error": "Device not found"}, 404)
                    return

                if len(subparts) == 4 and subparts[3] == "dump":
                    telemetry = {
                        "basic_info": device.query_endpoint("/common/basic_info"),
                        "sensor_info": device.query_endpoint("/aircon/get_sensor_info"),
                        "control_info": device.query_endpoint("/aircon/get_control_info"),
                        "model_info": device.query_endpoint("/aircon/get_model_info"),
                        "week_power": device.query_endpoint("/aircon/get_week_power")
                    }
                    await send_json(writer, telemetry)
                    return

                # GET is ALWAYS served from RAM cache - zero waiting for AC network!
                cached = device.get_cached_state()
                if cached:
                    await send_json(writer, cached)
                    return
                await send_json(writer, device.get_default_state())
                return

        # API: Control Daikin Unit
        if method == "POST" and path.startswith("/api/devices/") and path.endswith("/control"):
            subparts = path.strip("/").split("/")
            if len(subparts) == 4:
                dev_id = subparts[2]
                device = manager.get_device(dev_id)
                if not device:
                    await send_json(writer, {"error": "Device not found"}, 404)
                    return

                try:
                    payload = json.loads(body_str) if body_str else {}
                    res = device.update_settings(**payload)
                    if isinstance(res, dict) and "id" in res and "power" in res:
                        await send_json(writer, res)
                        return
                    if isinstance(res, dict) and res.get("ret") != "OK":
                        err_code = res.get("ret", "REJECTED")
                        await send_json(writer, {
                            "error": f"Daikin hardware error ({err_code})",
                            "daikin_res": res
                        }, 400)
                        return

                    updated = device.get_full_state()
                    updated["daikin_res"] = res
                    await send_json(writer, updated)
                    return
                except Exception as e:
                    await send_json(writer, {"error": str(e)}, 500)
                    return

        # API: Save/Pair Unit
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
                reg_ok = ctrl.register()
                state = ctrl.get_full_state()
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
                        "target_temp": state.get("target_temp")
                    }
                })
                return
            except Exception as e:
                await send_json(writer, {"error": str(e)}, 500)
                return

        # API: Delete Device
        if method == "POST" and path == "/api/devices/delete":
            try:
                payload = json.loads(body_str) if body_str else {}
                dev_id = payload.get("id")
                if not dev_id:
                    await send_json(writer, {"error": "Device ID required"}, 400)
                    return
                ok = manager.delete_device(dev_id)
                await send_json(writer, {"success": ok})
                return
            except Exception as e:
                await send_json(writer, {"error": str(e)}, 500)
                return

        # Static Web Files & Dashboard Route
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

        # 404 Fallback
        writer.write(b"HTTP/1.1 404 Not Found\r\nContent-Length: 9\r\nConnection: close\r\n\r\nNot Found")
        await writer.drain()

    except Exception as exc:
        print(f"[Client Error] {exc}")
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass
        gc.collect()


async def background_poller():
    """Background task polling all Daikin AC units staggered across 16s to keep web server ultra responsive."""
    # On initial boot, pause 2 seconds to let web server serve the first user page immediately without contention
    await asyncio.sleep(2)

    while True:
        configured_devs = [c for c in manager.devices.values() if c.is_configured]
        if not configured_devs:
            await asyncio.sleep(5)
            continue

        # Stagger polls evenly across ~16 seconds (e.g. 4 units -> ~4s interval per unit)
        interval = max(2.5, 16.0 / len(configured_devs))

        for ctrl in configured_devs:
            try:
                ctrl.get_full_state()
                manager.save_cache()
            except Exception as exc:
                print(f"[{ctrl.name}] Poll error: {exc}")
            gc.collect()
            # Give the asyncio loop a generous multi-second window to serve web requests in <5ms
            await asyncio.sleep(interval)


async def main():
    wlan = network.WLAN(network.STA_IF)
    ip = wlan.ifconfig()[0] if wlan.isconnected() else "127.0.0.1"

    print("==================================================")
    print("  Daikin AC Multi-Unit Server (MicroPython)")
    print(f"  Live Dashboard:   http://{ip}")
    print(f"  Configured Units: {len(manager.devices)}")
    print("==================================================")

    # Start background polling task
    asyncio.create_task(background_poller())

    # Start web server
    server = await asyncio.start_server(handle_client, "0.0.0.0", PORT)
    print(f"[*] Web Server listening on port {PORT}...")

    while True:
        await asyncio.sleep(3600)


def start():
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nServer stopped.")
    except Exception as exc:
        print(f"\n[Fatal Server Error] {exc}")


start()

