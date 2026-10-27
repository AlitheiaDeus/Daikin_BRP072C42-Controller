import json
import os
import sys
import mimetypes
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from daikin import DaikinDeviceManager

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
