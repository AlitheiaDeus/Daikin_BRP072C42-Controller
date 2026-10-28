import json
import os
import sys
import mimetypes
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from daikin import DaikinDeviceManager
from discover import discover_units

PORT = 8080
WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")

manager = DaikinDeviceManager()


class DaikinWebHandler(BaseHTTPRequestHandler):
    def _send_json(self, data: dict, status_code: int = 200):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            pass

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        # Favicon handler to suppress 404 log noise
        if path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return

        # API: List all devices (lightweight instant summary, zero AC network traffic)
        if path == "/api/devices":
            return self._send_json({"devices": manager.get_summaries()})

        # API: Discover Daikin units on the local subnet via UDP
        if path == "/api/discover":
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
            return self._send_json({"discovered": results})

        # API: Get single device status
        if path.startswith("/api/devices/"):
            parts = path.strip("/").split("/")
            if len(parts) >= 3:
                dev_id = parts[2]
                device = manager.get_device(dev_id)
                if not device:
                    return self._send_json({"error": "Device not found"}, 404)
                
                # Check for /api/devices/<id>/dump
                if len(parts) == 4 and parts[3] == "dump":
                    telemetry = {
                        "basic_info": device.query_endpoint("/common/basic_info"),
                        "sensor_info": device.query_endpoint("/aircon/get_sensor_info"),
                        "control_info": device.query_endpoint("/aircon/get_control_info"),
                        "week_power": device.query_endpoint("/aircon/get_week_power")
                    }
                    return self._send_json(telemetry)
                
                try:
                    return self._send_json(device.get_full_state())
                except Exception as e:
                    return self._send_json({
                        "id": dev_id,
                        "name": device.name,
                        "ip": device.ip,
                        "configured": device.is_configured,
                        "status": "offline",
                        "error": str(e)
                    }, 500)

        # Static files
        if path == "/" or path == "":
            file_path = os.path.join(WEB_DIR, "index.html")
        else:
            rel_path = path.lstrip("/")
            file_path = os.path.join(WEB_DIR, rel_path)

        # Security check against traversal
        file_path = os.path.abspath(file_path)
        if not file_path.startswith(os.path.abspath(WEB_DIR)):
            self.send_error(403, "Forbidden")
            return

        if os.path.exists(file_path) and os.path.isfile(file_path):
            mime_type, _ = mimetypes.guess_type(file_path)
            mime_type = mime_type or "application/octet-stream"
            try:
                with open(file_path, "rb") as f:
                    content = f.read()
                self.send_response(200)
                self.send_header("Content-Type", mime_type)
                self.send_header("Content-Length", str(len(content)))
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                self.wfile.write(content)
                return
            except Exception as e:
                self.send_error(500, f"Error reading file: {e}")
                return

        self.send_error(404, "Not Found")

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path.startswith("/api/devices/") and path.endswith("/control"):
            parts = path.strip("/").split("/")
            if len(parts) == 4:
                dev_id = parts[2]
                device = manager.get_device(dev_id)
                if not device:
                    return self._send_json({"error": "Device not found"}, 404)

                content_len = int(self.headers.get("Content-Length", 0))
                post_body = self.rfile.read(content_len).decode("utf-8")
                
                try:
                    payload = json.loads(post_body) if post_body else {}
                    res = device.update_settings(**payload)
                    # Return fresh updated state
                    updated_state = device.get_full_state()
                    updated_state["daikin_res"] = res
                    return self._send_json(updated_state)
                except Exception as e:
                    return self._send_json({"error": str(e)}, 500)

        # API: Save/pair a Daikin unit key & configuration
        if path == "/api/devices/save":
            content_len = int(self.headers.get("Content-Length", 0))
            post_body = self.rfile.read(content_len).decode("utf-8")
            try:
                payload = json.loads(post_body) if post_body else {}
                dev_id = payload.get("id")
                name = payload.get("name", "").strip()
                ip = payload.get("ip", "").strip()
                key = payload.get("key", "").strip()

                if not ip:
                    return self._send_json({"error": "IP address is required"}, 400)

                # Match existing unit by IP if dev_id not specified
                if not dev_id:
                    for ctrl in manager.devices.values():
                        if ctrl.ip == ip:
                            dev_id = ctrl.device_id
                            break

                ctrl = manager.upsert_device(dev_id=dev_id, name=name, ip=ip, key=key)
                # Attempt live handshake registration
                reg_ok = ctrl.register()
                state = ctrl.get_full_state()
                return self._send_json({
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
            except Exception as e:
                return self._send_json({"error": str(e)}, 500)

        # API: Remove a device slot
        if path == "/api/devices/delete":
            content_len = int(self.headers.get("Content-Length", 0))
            post_body = self.rfile.read(content_len).decode("utf-8")
            try:
                payload = json.loads(post_body) if post_body else {}
                dev_id = payload.get("id")
                if not dev_id:
                    return self._send_json({"error": "Device ID is required"}, 400)
                ok = manager.delete_device(dev_id)
                return self._send_json({"success": ok})
            except Exception as e:
                return self._send_json({"error": str(e)}, 500)

        self.send_error(404, "Endpoint not found")

    def log_message(self, format, *args):
        # Concise logging
        print(f"[HTTP] {self.address_string()} - {format % args}")


def run():
    server = ThreadingHTTPServer(("0.0.0.0", PORT), DaikinWebHandler)
    print(f"\n=======================================================")
    print(f"[*] Daikin Multi-Unit Web Controller is LIVE!")
    print(f"[*] Local Access:   http://localhost:{PORT}")
    print(f"[*] LAN / Phone:    http://<PC-LAN-IP>:{PORT}")
    print(f"[*] Press Ctrl+C to stop.")
    print(f"=======================================================\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server.")
        server.server_close()


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    run()
