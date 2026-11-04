# daikin.py - MicroPython Daikin AC Controller Engine
import json
import time
import socket
import ssl
import gc
import urandom

MODE_MAP = {
    "auto": "1",
    "dry": "2",
    "cool": "3",
    "heat": "4",
    "fan": "6",
}
MODE_REV_MAP = {
    "0": "auto",
    "1": "auto",
    "7": "auto",
    "2": "dry",
    "3": "cool",
    "4": "heat",
    "6": "fan",
}

def generate_uuid():
    """Generate a random 32-character hexadecimal UUID string."""
    return "".join(f"{urandom.getrandbits(8):02x}" for _ in range(16))

def unquote(s):
    """Decode percent-encoded URL string."""
    if not s or "%" not in s:
        return s
    res = []
    i = 0
    n = len(s)
    while i < n:
        if s[i] == "%" and i + 2 < n:
            try:
                res.append(chr(int(s[i+1:i+3], 16)))
                i += 3
                continue
            except ValueError:
                pass
        res.append(s[i])
        i += 1
    return "".join(res)

def _parse_temp(val, default=25.0):
    if val in ("--", "M", None, ""):
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default

def daikin_http_request(ip, path, headers=None, port=443, use_ssl=True, timeout=2.5):
    """Fast socket HTTP/HTTPS client with Content-Length cutoff for Daikin adapters."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    
    try:
        addr = socket.getaddrinfo(ip, port)[0][-1]
        s.connect(addr)
        
        if use_ssl:
            try:
                ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                ctx.verify_mode = ssl.CERT_NONE
                s = ctx.wrap_socket(s, server_hostname=ip)
            except (AttributeError, TypeError):
                try:
                    s = ssl.wrap_socket(s, cert_reqs=ssl.CERT_NONE, server_hostname=ip)
                except (TypeError, ValueError):
                    s = ssl.wrap_socket(s)

        req = f"GET {path} HTTP/1.1\r\nHost: {ip}\r\nConnection: close\r\n"
        if headers:
            for k, v in headers.items():
                req += f"{k}: {v}\r\n"
        req += "\r\n"
        s.write(req.encode("utf-8"))

        raw = b""
        header_b = b""
        body_b = b""
        content_length = None

        # 1. Read until headers complete
        while True:
            chunk = s.read(256)
            if not chunk:
                break
            raw += chunk
            if b"\r\n\r\n" in raw:
                header_b, body_b = raw.split(b"\r\n\r\n", 1)
                break
            elif b"\n\n" in raw:
                header_b, body_b = raw.split(b"\n\n", 1)
                break

        if not header_b and raw:
            header_b = raw

        # 2. Extract status code and Content-Length
        status_code = 200
        first_line = header_b.split(b"\n")[0]
        toks = first_line.split(b" ")
        if len(toks) >= 2 and toks[1].isdigit():
            status_code = int(toks[1])

        for line in header_b.split(b"\n"):
            line_clean = line.strip().lower()
            if line_clean.startswith(b"content-length:"):
                try:
                    content_length = int(line_clean.split(b":", 1)[1].strip())
                except ValueError:
                    pass

        # 3. Read body up to Content-Length immediately (avoids 4s socket timeout freeze!)
        if content_length is not None:
            while len(body_b) < content_length:
                needed = content_length - len(body_b)
                chunk = s.read(min(256, needed))
                if not chunk:
                    break
                body_b += chunk
        else:
            while True:
                try:
                    chunk = s.read(256)
                    if not chunk:
                        break
                    body_b += chunk
                except (socket.timeout, OSError):
                    break

        body = body_b.decode("utf-8", "ignore")
        return status_code, body
    finally:
        try:
            s.close()
        except Exception:
            pass
        gc.collect()



class DaikinController:
    def __init__(self, device_id, name, ip, key, client_uuid=""):
        self.device_id = device_id
        self.name = name
        self.ip = ip
        self.key = key
        self.uuid = client_uuid or generate_uuid()
        self.use_ssl = True
        self.port = 443

        self._basic_cache = None
        self._model_cache = None
        self._last_state = None
        self._last_ctrl = None
        self._last_runtime = 0
        self._last_clock = ""
        self._last_command_time = 0
        self._last_heavy_check = 0

    @property
    def is_configured(self):
        if not self.ip or "XXX" in self.ip or self.ip == "0.0.0.0":
            return False
        if not self.key or "PLACEHOLDER" in self.key:
            return False
        return True

    def get_cached_state(self):
        return self._last_state

    def get_default_state(self):
        return {
            "id": self.device_id,
            "name": self.name or self.device_id,
            "adapter_name": "",
            "ip": self.ip,
            "configured": self.is_configured,
            "status": "online" if self.is_configured else "unconfigured",
            "supported_modes": ["cool", "dry", "fan"],
            "min_temp": 18.0,
            "max_temp": 32.0,
            "supports_swing": True,
            "fan_steps": 5,
            "power": False,
            "mode": "cool",
            "mode_raw": "3",
            "target_temp": 25.0,
            "indoor_temp": None,
            "outdoor_temp": None,
            "humidity": None,
            "compressor_freq": 0,
            "fan_rate": "A",
            "fan_dir": "0",
            "today_runtime_mins": 0,
            "device_clock": "",
            "mac": "",
            "firmware": "",
            "model_type": "aircon",
            "err": "0",
        }

    def _parse_response(self, text):
        result = {}
        for token in text.strip().split(","):
            if "=" in token:
                k, v = token.split("=", 1)
                result[k] = v
        return result

    def _request(self, path):
        headers = {"X-Daikin-uuid": self.uuid}
        try:
            status, body = daikin_http_request(
                self.ip, path, headers=headers, port=self.port, use_ssl=self.use_ssl, timeout=2.0
            )
            return status, self._parse_response(body)
        except Exception as e:
            # If HTTPS fails initially, attempt fallback to plain HTTP (port 80)
            if self.use_ssl and not self._last_state:
                try:
                    status, body = daikin_http_request(
                        self.ip, path, headers=headers, port=80, use_ssl=False, timeout=2.0
                    )
                    self.use_ssl = False
                    self.port = 80
                    return status, self._parse_response(body)
                except Exception:
                    pass
            return 500, {"ret": "ERROR", "error": str(e)}

    def register(self):
        if not self.is_configured:
            return False
        path = f"/common/register_terminal?key={self.key}"
        status, parsed = self._request(path)
        return parsed.get("ret") == "OK"

    def query_endpoint(self, path):
        if not self.is_configured:
            return {"ret": "UNCONFIGURED"}

        status, parsed = self._request(path)
        if parsed.get("ret") in ("AUTH_FAIL", "ADV_NG") or status in (401, 403):
            print(f"[{self.name or self.device_id}] Auth required. Registering key...")
            if self.register():
                status, parsed = self._request(path)
        return parsed

    def get_full_state(self):
        if not self.is_configured:
            return {
                "id": self.device_id,
                "name": self.name,
                "ip": self.ip,
                "configured": False,
                "status": "unconfigured"
            }

        sensors = self.query_endpoint("/aircon/get_sensor_info")
        ctrl = self.query_endpoint("/aircon/get_control_info")

        if not self._basic_cache or self._basic_cache.get("ret") != "OK":
            self._basic_cache = self.query_endpoint("/common/basic_info")
        if not self._model_cache or self._model_cache.get("ret") != "OK":
            self._model_cache = self.query_endpoint("/aircon/get_model_info")

        if ctrl.get("ret") != "OK":
            if self._last_state:
                return self._last_state
            return {
                "id": self.device_id,
                "name": self.name,
                "ip": self.ip,
                "configured": True,
                "status": "offline",
                "error": ctrl.get("error", "Unit not reachable")
            }

        self._last_ctrl = dict(ctrl)

        raw_name = self._basic_cache.get("name") if self._basic_cache else None
        adapter_name = unquote(raw_name) if raw_name else ""

        model_info = self._model_cache if (self._model_cache and self._model_cache.get("ret") == "OK") else {}
        supported_modes = ["cool", "fan"]

        if model_info.get("en_dry", model_info.get("s_dry", "1")) != "0":
            supported_modes.append("dry")
        if model_info.get("en_auto") == "1" or model_info.get("s_auto") == "1":
            supported_modes.append("auto")
        elif "en_auto" not in model_info and "s_auto" not in model_info and ctrl.get("dt1") not in (None, "", "--"):
            if model_info.get("type") != "C":
                supported_modes.append("auto")

        m_type = model_info.get("type", "")
        heat_l = model_info.get("heat_l")
        has_heat = (
            m_type != "C" and 
            heat_l not in (None, "", "0", "NOTSUPPORT", "NA") and 
            bool(heat_l)
        )
        if has_heat:
            supported_modes.append("heat")
        elif not model_info and m_type != "C":
            dt4_val = ctrl.get("dt4")
            if dt4_val and dt4_val not in ("--", "M", "0", "0.0", "NA"):
                supported_modes.append("heat")

        ordered_modes = [m for m in ("cool", "dry", "fan", "auto", "heat") if m in supported_modes]

        cool_l = _parse_temp(model_info.get("cool_l"), 18.0)
        cool_h = _parse_temp(model_info.get("cool_h"), 32.0)

        recent_cmd = (time.time() - self._last_command_time) < 4.0

        state = {
            "id": self.device_id,
            "name": self.name or adapter_name or self.device_id,
            "adapter_name": adapter_name,
            "ip": self.ip,
            "configured": True,
            "status": "online",
            "supported_modes": ordered_modes,
            "min_temp": cool_l,
            "max_temp": cool_h,
            "supports_swing": model_info.get("en_fdir", model_info.get("s_fdir", "1")) != "0",
            "fan_steps": int(model_info.get("frate_steps", "5")) if model_info.get("frate_steps", "").isdigit() else 5,
            "power": (self._last_state.get("power") if recent_cmd and self._last_state else (ctrl.get("pow") == "1")),
            "mode": (self._last_state.get("mode") if recent_cmd and self._last_state else MODE_REV_MAP.get(ctrl.get("mode"), "cool")),
            "mode_raw": (self._last_state.get("mode_raw") if recent_cmd and self._last_state else ctrl.get("mode")),
            "target_temp": (self._last_state.get("target_temp") if recent_cmd and self._last_state else _parse_temp(ctrl.get("stemp"), 25.0)),
            "indoor_temp": float(sensors.get("htemp", "0.0")) if sensors.get("htemp") not in ("--", None) else None,
            "outdoor_temp": float(sensors.get("otemp", "0.0")) if sensors.get("otemp") not in ("--", None) else None,
            "humidity": float(sensors.get("hhum", "0")) if sensors.get("hhum") not in ("--", None) else None,
            "compressor_freq": int(sensors.get("cmpfreq", "0")) if sensors.get("cmpfreq") not in ("--", None) else 0,
            "fan_rate": (self._last_state.get("fan_rate") if recent_cmd and self._last_state else ctrl.get("f_rate", "A")),
            "fan_dir": (self._last_state.get("fan_dir") if recent_cmd and self._last_state else ctrl.get("f_dir", "0")),
            "today_runtime_mins": self._last_runtime,
            "device_clock": self._last_clock,
            "mac": self._basic_cache.get("mac", "") if self._basic_cache else "",
            "firmware": self._basic_cache.get("ver", "") if self._basic_cache else "",
            "model_type": self._basic_cache.get("type", "aircon") if self._basic_cache else "aircon",
            "err": ctrl.get("err", sensors.get("err", "0")),
            "raw_ctrl": ctrl,
            "raw_sensors": sensors
        }
        self._last_state = state
        return state

    def update_settings(self, **kwargs):
        if not self.is_configured:
            return {"ret": "UNCONFIGURED", "error": "Device not configured"}

        self._last_command_time = time.time()

        current = self._last_ctrl
        if not current or current.get("ret") != "OK":
            current = self.query_endpoint("/aircon/get_control_info")
            if current.get("ret") == "OK":
                self._last_ctrl = dict(current)
            else:
                return {"ret": "ERROR", "error": "Could not read control info"}

        payload = {
            "pow": current.get("pow", "0"),
            "mode": current.get("mode", "3"),
            "stemp": current.get("stemp", "25.0"),
            "shum": current.get("shum", "0"),
            "f_rate": current.get("f_rate", "A"),
            "f_dir": current.get("f_dir", "0"),
        }

        if "power" in kwargs:
            payload["pow"] = "1" if kwargs["power"] else "0"
        if "mode" in kwargs:
            m = str(kwargs["mode"]).lower()
            target_code = MODE_MAP.get(m, m)
            target_name = MODE_REV_MAP.get(target_code, m)
            if self._last_state and "supported_modes" in self._last_state:
                if target_name not in self._last_state["supported_modes"]:
                    return {"ret": "PARAM NG", "error": f"Mode '{target_name}' is not supported"}
            payload["mode"] = target_code
        if "target_temp" in kwargs:
            payload["stemp"] = f"{float(kwargs['target_temp']):.1f}"
        if "fan_rate" in kwargs:
            payload["f_rate"] = str(kwargs["fan_rate"]).upper()
        if "fan_dir" in kwargs:
            payload["f_dir"] = str(kwargs["fan_dir"])

        for k in ("pow", "stemp", "shum", "f_rate", "f_dir"):
            if k in kwargs:
                payload[k] = str(kwargs[k])

        target_mode = payload.get("mode", "3")
        if target_mode in ("0", "1", "3", "4", "7"):
            if payload.get("stemp") in ("--", "M", None, ""):
                dt_key = f"dt{target_mode}"
                fallback = current.get(dt_key) or current.get("b_stemp") or "25.0"
                payload["stemp"] = fallback if fallback not in ("--", "M", None, "") else "25.0"
        elif target_mode == "2":
            if "target_temp" not in kwargs and "stemp" not in kwargs:
                payload["stemp"] = current.get("dt2") or "M"
        elif target_mode == "6":
            if "target_temp" not in kwargs and "stemp" not in kwargs:
                payload["stemp"] = "--"

        # Build query string
        q_parts = [f"{k}={payload[k]}" for k in payload]
        path = f"/aircon/set_control_info?{'&'.join(q_parts)}"

        status, parsed = self._request(path)
        if parsed.get("ret") in ("AUTH_FAIL", "ADV_NG") or status in (401, 403):
            if self.register():
                status, parsed = self._request(path)

        if parsed.get("ret") == "OK":
            if not self._last_ctrl:
                self._last_ctrl = {}
            self._last_ctrl.update(payload)
            self._last_ctrl["ret"] = "OK"

            if self._last_state:
                self._last_state["power"] = payload["pow"] == "1"
                self._last_state["mode"] = MODE_REV_MAP.get(payload["mode"], "cool")
                self._last_state["mode_raw"] = payload["mode"]
                self._last_state["target_temp"] = _parse_temp(payload.get("stemp"), 25.0)
                self._last_state["fan_rate"] = payload["f_rate"]
                self._last_state["fan_dir"] = payload["f_dir"]
                self._last_state["raw_ctrl"] = dict(self._last_ctrl)
                synth = dict(self._last_state)
                synth["daikin_res"] = parsed
                return synth

        return parsed


class DaikinDeviceManager:
    def __init__(self, config_path="daikin_config.json"):
        self.config_path = config_path
        self.devices = {}
        self.load_config()

    def load_config(self):
        try:
            with open(self.config_path, "r") as f:
                cfg = json.load(f)
        except Exception:
            return

        updated = False
        loaded_devices = {}
        for dev_data in cfg.get("devices", []):
            dev_id = dev_data.get("id")
            if not dev_id:
                continue

            name = dev_data.get("name", "")
            ip = dev_data.get("ip", "")
            key = dev_data.get("key", "")
            client_uuid = dev_data.get("uuid", "")

            if not client_uuid:
                client_uuid = generate_uuid()
                dev_data["uuid"] = client_uuid
                updated = True

            ctrl = DaikinController(
                device_id=dev_id,
                name=name,
                ip=ip,
                key=key,
                client_uuid=client_uuid
            )
            loaded_devices[dev_id] = ctrl

        self.devices = loaded_devices
        if updated:
            self.save_config()

    def save_config(self):
        device_list = []
        for dev in self.devices.values():
            device_list.append({
                "id": dev.device_id,
                "name": dev.name,
                "ip": dev.ip,
                "key": dev.key,
                "uuid": dev.uuid
            })
        try:
            with open(self.config_path, "w") as f:
                json.dump({"devices": device_list}, f)
        except Exception as e:
            print(f"[Config Save Error] {e}")

    def get_device(self, dev_id):
        return self.devices.get(dev_id)

    def get_summaries(self):
        summaries = []
        for dev in self.devices.values():
            cached = dev.get_cached_state()
            if cached:
                item = dict(cached)
                item["id"] = dev.device_id
                item["name"] = dev.name or cached.get("name") or dev.device_id
                item["ip"] = dev.ip
                item["configured"] = dev.is_configured
                summaries.append(item)
            else:
                summaries.append(dev.get_default_state())
        return summaries

    def upsert_device(self, dev_id=None, name="", ip="", key=""):
        if not dev_id:
            idx = 1
            while f"ac_{idx}" in self.devices:
                idx += 1
            dev_id = f"ac_{idx}"

        if dev_id in self.devices:
            ctrl = self.devices[dev_id]
            if name:
                ctrl.name = name
            if ip:
                ctrl.ip = ip
            if key:
                ctrl.key = key
        else:
            ctrl = DaikinController(
                device_id=dev_id,
                name=name or f"Daikin AC ({ip})",
                ip=ip,
                key=key,
                client_uuid=generate_uuid()
            )
            self.devices[dev_id] = ctrl

        self.save_config()
        return ctrl

    def delete_device(self, dev_id):
        if dev_id in self.devices:
            del self.devices[dev_id]
            self.save_config()
            return True
        return False

    def poll_all(self):
        for ctrl in self.devices.values():
            if ctrl.is_configured:
                try:
                    ctrl.get_full_state()
                except Exception as e:
                    print(f"[{ctrl.name}] Poll error: {e}")
                time.sleep(0.1)
                gc.collect()
