# daikin.py - Asynchronous MicroPython Daikin AC Controller Engine
try:
    import uasyncio as asyncio
except ImportError:
    import asyncio
import json
import time
import gc
try:
    import urandom
except ImportError:
    import random as urandom
import config

# ==============================================================================
# Protocol Mappings & Helpers
# ==============================================================================
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
    try:
        return "".join(f"{urandom.getrandbits(8):02x}" for _ in range(16))
    except Exception:
        # Fallback if urandom is limited
        import os
        return "".join(f"{b:02x}" for b in os.urandom(16))


def unquote(s):
    """Decode percent-encoded URL string without external urllib dependencies."""
    if not s or "%" not in s:
        return s
    res = []
    i = 0
    n = len(s)
    while i < n:
        if s[i] == "%" and i + 2 < n:
            try:
                res.append(chr(int(s[i + 1 : i + 3], 16)))
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


# ==============================================================================
# Non-Blocking Asynchronous HTTP/HTTPS Client
# ==============================================================================
async def async_daikin_request(ip, path, headers=None, port=443, use_ssl=True, timeout=3.0):
    """Non-blocking socket stream HTTP/HTTPS client tailored for Daikin adapters.
    
    Reads headers asynchronously, detects Content-Length, and cuts off body reading
    immediately to eliminate the socket EOF timeout latency.
    """
    ssl_ctx = None
    if use_ssl:
        try:
            import ssl
            try:
                ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                ssl_ctx.check_hostname = False
                ssl_ctx.verify_mode = ssl.CERT_NONE
            except (AttributeError, TypeError):
                ssl_ctx = True
        except ImportError:
            use_ssl = False

    reader = None
    writer = None
    try:
        # 1. Establish asynchronous stream connection with timeout guard
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(ip, port, ssl=ssl_ctx),
            timeout=timeout
        )

        # 2. Transmit HTTP request header (HTTP/1.0 matches Daikin embedded web server)
        req = f"GET {path} HTTP/1.0\r\nHost: {ip}\r\nConnection: close\r\n"
        if headers:
            for k, v in headers.items():
                req += f"{k}: {v}\r\n"
        req += "\r\n"
        writer.write(req.encode("utf-8"))
        await writer.drain()

        # 3. Asynchronously read HTTP status line
        first_line = await asyncio.wait_for(reader.readline(), timeout=timeout)
        if not first_line:
            return 500, ""

        status_code = 200
        tokens = first_line.decode("utf-8", "ignore").strip().split(" ")
        if len(tokens) >= 2 and tokens[1].isdigit():
            status_code = int(tokens[1])

        # 4. Asynchronously read headers to determine Content-Length
        content_length = None
        while True:
            hline = await asyncio.wait_for(reader.readline(), timeout=timeout)
            if not hline or hline in (b"\r\n", b"\n"):
                break
            line_str = hline.decode("utf-8", "ignore").strip().lower()
            if line_str.startswith("content-length:"):
                try:
                    content_length = int(line_str.split(":", 1)[1].strip())
                except ValueError:
                    pass

        # 5. Read body up to Content-Length immediately (zero blocking!)
        body_bytes = b""
        if content_length is not None and content_length > 0:
            body_bytes = await asyncio.wait_for(
                reader.readexactly(content_length),
                timeout=timeout
            )
        else:
            # Fallback if no Content-Length provided
            while True:
                chunk = await asyncio.wait_for(reader.read(256), timeout=timeout)
                if not chunk:
                    break
                body_bytes += chunk

        body = body_bytes.decode("utf-8", "ignore")
        return status_code, body

    finally:
        if writer:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass


# ==============================================================================
# Async Daikin Unit Controller
# ==============================================================================
class DaikinController:
    """Non-blocking asynchronous controller for a single Daikin AC unit."""

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
        self._last_sensors = None
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
            "indoor_humidity": None,
            "compressor_freq": 0,
            "cmpfreq": 0,
            "fan_rate": "A",
            "fan_dir": "0",
            "today_runtime_mins": 0,
            "runtime_today": 0,
            "runtime": 0,
            "device_clock": "",
            "ac_clock": "",
            "mac": "",
            "firmware": "",
            "firmware_version": "",
            "model_type": "aircon",
            "err": "0",
            "error_code": 0,
            "turbo": False,
            "turbo_remaining_sec": 0,
        }

    def _parse_response(self, text):
        result = {}
        for token in text.strip().split(","):
            if "=" in token:
                k, v = token.split("=", 1)
                result[k] = v
        return result

    async def _request(self, path):
        headers = {"X-Daikin-uuid": self.uuid}
        try:
            status, body = await async_daikin_request(
                self.ip, path, headers=headers, port=self.port, use_ssl=self.use_ssl, timeout=config.SOCKET_TIMEOUT_SECS
            )
            return status, self._parse_response(body)
        except (OSError, asyncio.TimeoutError) as e:
            return 500, {"ret": "ERROR", "error": str(e)}

    async def register(self):
        if not self.is_configured:
            return False
        path = f"/common/register_terminal?key={self.key}"
        status, parsed = await self._request(path)
        return parsed.get("ret") == "OK"

    async def query_endpoint(self, path):
        if not self.is_configured:
            return {"ret": "UNCONFIGURED"}

        status, parsed = await self._request(path)
        if parsed.get("ret") in ("AUTH_FAIL", "ADV_NG") or status in (401, 403):
            print(f"[{self.name or self.device_id}] Auth required. Registering key...")
            if await self.register():
                status, parsed = await self._request(path)
        return parsed

    async def get_full_state(self, deep=False):
        """Asynchronously query telemetry endpoints and synthesize full unit state."""
        if not self.is_configured:
            return {
                "id": self.device_id,
                "name": self.name,
                "ip": self.ip,
                "configured": False,
                "status": "unconfigured",
            }

        if deep:
            sensors = await self.query_endpoint("/aircon/get_sensor_info")
            await asyncio.sleep_ms(50)
            if sensors.get("ret") == "OK":
                self._last_sensors = dict(sensors)
            elif self._last_sensors:
                sensors = self._last_sensors
        else:
            sensors = self._last_sensors or (self._last_state.get("raw_sensors") if self._last_state else None) or {}

        ctrl = await self.query_endpoint("/aircon/get_control_info")
        await asyncio.sleep_ms(50)

        if deep:
            if not self._basic_cache or self._basic_cache.get("ret") != "OK":
                self._basic_cache = await self.query_endpoint("/common/basic_info")
                await asyncio.sleep_ms(50)
            if not self._model_cache or self._model_cache.get("ret") != "OK":
                self._model_cache = await self.query_endpoint("/aircon/get_model_info")
                await asyncio.sleep_ms(50)

        if ctrl.get("ret") != "OK":
            if self._last_state:
                self._last_state["status"] = "offline"
                return self._last_state
            return {
                "id": self.device_id,
                "name": self.name,
                "ip": self.ip,
                "configured": True,
                "status": "offline",
                "error": ctrl.get("error", "Unit not reachable"),
            }

        self._last_ctrl = dict(ctrl)

        if deep:
            self._last_heavy_check = time.time()
            week_pwr = await self.query_endpoint("/aircon/get_week_power")
            await asyncio.sleep_ms(50)
            if week_pwr.get("ret") == "OK":
                try:
                    self._last_runtime = int(week_pwr.get("today_runtime", "0"))
                except Exception:
                    pass
            dt = await self.query_endpoint("/common/get_datetime")
            await asyncio.sleep_ms(50)
            if dt.get("ret") != "OK":
                dt = await self.query_endpoint("/common/get_datetime?cur=")
                await asyncio.sleep_ms(50)
            if dt.get("ret") == "OK":
                raw_cur = dt.get("cur", "")
                self._last_clock = unquote(raw_cur) if raw_cur else ""

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
            m_type != "C"
            and heat_l not in (None, "", "0", "NOTSUPPORT", "NA")
            and bool(heat_l)
        )
        if has_heat:
            supported_modes.append("heat")
        elif not model_info and m_type != "C":
            dt4_val = ctrl.get("dt4")
            if dt4_val and dt4_val not in ("--", "M", "0", "0.0", "NA"):
                supported_modes.append("heat")

        ordered_modes = [m for m in ("cool", "dry", "fan", "auto", "heat") if m in supported_modes]

        cool_l = _parse_temp(model_info.get("cool_l"), 18.0)
        restore = getattr(self, "_turbo_restore", None)
        is_turbo = False
        remaining = 0
        if restore:
            now = time.time()
            if now >= restore.get("expire_at", 0):
                self._turbo_restore = None
                try:
                    await self.update_settings(
                        target_temp=restore.get("temp", 25.0),
                        fan_rate=restore.get("fan", "A")
                    )
                except Exception:
                    pass
            else:
                is_turbo = True
                remaining = int(restore["expire_at"] - now)

        cool_h = _parse_temp(model_info.get("cool_h"), 32.0)
        recent_cmd = (time.time() - self._last_command_time) < 8.0

        raw_ver = self._basic_cache.get("ver", "") if self._basic_cache else ""
        firmware = raw_ver.replace("_", ".") if raw_ver else ""
        clock = self._last_clock
        runtime = self._last_runtime
        hum = float(sensors.get("hhum", "0")) if sensors.get("hhum") not in ("--", None, "") else None
        cmp_freq = int(sensors.get("cmpfreq", "0")) if sensors.get("cmpfreq") not in ("--", None, "") else 0
        err_val = ctrl.get("err", sensors.get("err", "0"))

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
            "humidity": hum,
            "indoor_humidity": hum,
            "compressor_freq": cmp_freq,
            "cmpfreq": cmp_freq,
            "fan_rate": (self._last_state.get("fan_rate") if recent_cmd and self._last_state else ctrl.get("f_rate", "A")),
            "fan_dir": (self._last_state.get("fan_dir") if recent_cmd and self._last_state else ctrl.get("f_dir", "0")),
            "today_runtime_mins": runtime,
            "runtime_today": runtime,
            "runtime": runtime,
            "turbo": is_turbo,
            "turbo_remaining_sec": remaining,
            "device_clock": clock,
            "ac_clock": clock,
            "mac": self._basic_cache.get("mac", "") if self._basic_cache else "",
            "firmware": firmware or raw_ver,
            "firmware_version": firmware or raw_ver,
            "model_type": self._basic_cache.get("type", "aircon") if self._basic_cache else "aircon",
            "err": err_val,
            "error_code": err_val,
            "raw_ctrl": ctrl,
            "raw_sensors": sensors,
        }
        self._last_state = state
        return state

    async def update_settings(self, **kwargs):
        """Send setting updates using single-write async optimization and return synthetic state."""
        if not self.is_configured:
            return {"ret": "UNCONFIGURED", "error": "Device not configured"}

        current = self._last_ctrl
        if not current or current.get("ret") != "OK":
            current = await self.query_endpoint("/aircon/get_control_info")
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

        if "turbo" in kwargs:
            if kwargs["turbo"]:
                if not getattr(self, "_turbo_restore", None):
                    self._turbo_restore = {
                        "temp": _parse_temp(current.get("stemp"), 25.0),
                        "fan": current.get("f_rate", "A"),
                        "expire_at": time.time() + 900,
                    }
                min_t = self._last_state.get("min_temp", 18.0) if self._last_state else 18.0
                payload["stemp"] = f"{float(min_t):.1f}"
                payload["f_rate"] = "7"
                payload["pow"] = "1"
                payload["mode"] = "3"
            else:
                restore = getattr(self, "_turbo_restore", None)
                if restore:
                    payload["stemp"] = f"{float(restore.get('temp', 25.0)):.1f}"
                    payload["f_rate"] = str(restore.get("fan", "A"))
                    self._turbo_restore = None
        else:
            if (
                "target_temp" in kwargs
                or "fan_rate" in kwargs
                or "stemp" in kwargs
                or "f_rate" in kwargs
            ) and getattr(self, "_turbo_restore", None):
                self._turbo_restore = None

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

        for k in ("pow", "stemp", "shum", "f_rate", "f_dir", "adv"):
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

        status, parsed = await self._request(path)
        if parsed.get("ret") in ("AUTH_FAIL", "ADV_NG") or status in (401, 403):
            if await self.register():
                status, parsed = await self._request(path)

        if parsed.get("ret") == "OK":
            self._last_command_time = time.time()
            if not self._last_ctrl:
                self._last_ctrl = {}
            self._last_ctrl.update(payload)
            self._last_ctrl["ret"] = "OK"

            if not self._last_state:
                self._last_state = self.get_default_state()
            self._last_state["power"] = payload["pow"] == "1"
            self._last_state["mode"] = MODE_REV_MAP.get(payload["mode"], "cool")
            self._last_state["mode_raw"] = payload["mode"]
            self._last_state["target_temp"] = _parse_temp(payload.get("stemp"), 25.0)
            self._last_state["fan_rate"] = payload["f_rate"]
            self._last_state["fan_dir"] = payload["f_dir"]
            restore = getattr(self, "_turbo_restore", None)
            self._last_state["turbo"] = bool(restore)
            self._last_state["turbo_remaining_sec"] = (
                max(0, int(restore["expire_at"] - time.time())) if restore else 0
            )
            self._last_state["raw_ctrl"] = dict(self._last_ctrl)
            synth = dict(self._last_state)
            synth["daikin_res"] = parsed
            return synth

        return parsed


# ==============================================================================
# Device Manager
# ==============================================================================
class DaikinDeviceManager:
    """Manages the collection of configured Daikin units and flash persistence."""

    def __init__(self, config_path=config.CONFIG_FILE, cache_path=config.CACHE_FILE):
        self.config_path = config_path
        self.cache_path = cache_path
        self.devices = {}
        self.load_config()
        self.load_cache()

    def load_cache(self):
        """Restore last known state from flash cache so temperatures are instant on boot."""
        cached = config.load_cache(self.cache_path)
        for dev_id, state in cached.items():
            if dev_id in self.devices and state:
                self.devices[dev_id]._last_state = state
                if "raw_ctrl" in state:
                    self.devices[dev_id]._last_ctrl = state.get("raw_ctrl", {})
                if "raw_sensors" in state:
                    self.devices[dev_id]._last_sensors = state.get("raw_sensors", {})

    def save_cache(self):
        """Persist current states to flash so subsequent reboots are instant."""
        cache_data = {}
        for dev_id, dev in self.devices.items():
            if dev._last_state:
                cache_data[dev_id] = dev._last_state
        if cache_data:
            config.save_cache(cache_data, self.cache_path)

    def load_config(self):
        devices_raw = config.load_config(self.config_path)
        updated = False
        loaded = {}
        for dev_data in devices_raw:
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
                client_uuid=client_uuid,
            )
            loaded[dev_id] = ctrl

        self.devices = loaded
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
                "uuid": dev.uuid,
            })
        config.save_config(device_list, self.config_path)

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
                client_uuid=generate_uuid(),
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

    async def poll_all(self):
        """Asynchronously poll all configured units with cooperative yielding."""
        for ctrl in self.devices.values():
            if ctrl.is_configured:
                try:
                    await ctrl.get_full_state()
                except Exception as e:
                    print(f"[{ctrl.name}] Poll error: {e}")
                await asyncio.sleep(0.1)
                gc.collect()
