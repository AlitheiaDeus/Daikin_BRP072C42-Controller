import json
import os
import ssl
import sys
import threading
import time
import uuid
import urllib.parse
import urllib3
import requests
from requests.adapters import HTTPAdapter
from typing import Dict, Any, List, Optional

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "daikin_config.json")

MODE_MAP = {
    "auto": "1",
    "dry": "2",
    "cool": "3",
    "heat": "4",
    "fan": "6",
}
# Reverse mapping supporting standard Daikin hardware modes (0, 1, 7 are Auto variants)
MODE_REV_MAP = {
    "0": "auto",
    "1": "auto",
    "7": "auto",
    "2": "dry",
    "3": "cool",
    "4": "heat",
    "6": "fan",
}


def _parse_temp(val: Any, default: float = 25.0) -> float:
    if val in ("--", "M", None, ""):
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


class DaikinLegacySSLAdapter(HTTPAdapter):
    """Transport adapter enabling legacy SSL renegotiation for embedded controllers."""
    def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):
        ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        legacy_flag = getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0x4)
        ctx.options |= legacy_flag
        pool_kwargs["ssl_context"] = ctx
        return super().init_poolmanager(connections, maxsize, block=block, **pool_kwargs)


class DaikinController:
    def __init__(self, device_id: str, name: str, ip: str, key: str, client_uuid: str = ""):
        self.device_id = device_id
        self.name = name
        self.ip = ip
        self.key = key
        self.uuid = client_uuid or uuid.uuid4().hex
        self.base_url = f"https://{self.ip}"
        
        self.session = requests.Session()
        self.session.mount("https://", DaikinLegacySSLAdapter())
        self.session.headers.update({"X-Daikin-uuid": self.uuid})
        self._lock = threading.RLock()
        self._basic_cache = None
        self._model_cache = None
        self._last_state = None
        self._last_ctrl = None
        self._last_runtime = 0
        self._last_command_time = 0

    def get_cached_state(self) -> Optional[dict]:
        return self._last_state

    @property
    def is_configured(self) -> bool:
        if not self.ip or "XXX" in self.ip or self.ip == "0.0.0.0":
            return False
        if not self.key or "PLACEHOLDER" in self.key:
            return False
        return True

    def _parse_response(self, text: str) -> dict:
        result = {}
        for token in text.strip().split(","):
            if "=" in token:
                k, v = token.split("=", 1)
                result[k] = v
        return result

    def register(self) -> bool:
        if not self.is_configured:
            return False
        with self._lock:
            url = f"{self.base_url}/common/register_terminal"
            params = {"key": self.key}
            try:
                res = self.session.get(url, params=params, verify=False, timeout=6)
                parsed = self._parse_response(res.text)
                return parsed.get("ret") == "OK"
            except Exception as exc:
                print(f"[{self.name}] Registration error: {exc}")
                return False

    def query_endpoint(self, path: str) -> dict:
        if not self.is_configured:
            return {"ret": "UNCONFIGURED"}
        url = f"{self.base_url}{path}"
        with self._lock:
            try:
                res = self.session.get(url, verify=False, timeout=4.0)
                parsed = self._parse_response(res.text)
                if parsed.get("ret") in ("AUTH_FAIL", "ADV_NG") or res.status_code in (401, 403):
                    print(f"[{self.name or self.device_id}] Authorization required. Attempting registration handshake...")
                    if self.register():
                        res = self.session.get(url, verify=False, timeout=4.0)
                        parsed = self._parse_response(res.text)
                return parsed
            except Exception as e:
                return {"ret": "ERROR", "error": str(e)}

    def get_full_state(self) -> dict:
        if not self.is_configured:
            return {
                "id": self.device_id,
                "name": self.name,
                "ip": self.ip,
                "configured": False,
                "status": "unconfigured"
            }

        # Query only dynamic operational endpoints each cycle
        sensors = self.query_endpoint("/aircon/get_sensor_info")
        ctrl = self.query_endpoint("/aircon/get_control_info")

        # Cache basic hardware info and model capabilities once
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

        # Query runtime & clock only periodically (every 60s) to keep tab switching instant (<300ms)
        now = time.time()
        if not hasattr(self, "_last_heavy_check") or now - self._last_heavy_check > 60:
            self._last_heavy_check = now
            week_pwr = self.query_endpoint("/aircon/get_week_power")
            if week_pwr.get("ret") == "OK":
                self._last_runtime = int(week_pwr.get("today_runtime", "0"))
            dt = self.query_endpoint("/common/get_datetime")
            if dt.get("ret") == "OK":
                self._last_clock = dt.get("cur", "")

        raw_name = self._basic_cache.get("name") if self._basic_cache else None
        adapter_name = urllib.parse.unquote(raw_name) if raw_name else ""

        # Autodetect hardware capabilities and supported modes
        model_info = self._model_cache if (self._model_cache and self._model_cache.get("ret") == "OK") else {}
        supported_modes = ["cool", "fan"]

        # 1. Dry mode
        if model_info.get("en_dry", model_info.get("s_dry", "1")) != "0":
            supported_modes.append("dry")

        # 2. Auto mode
        if model_info.get("en_auto") == "1" or model_info.get("s_auto") == "1":
            supported_modes.append("auto")
        elif "en_auto" not in model_info and "s_auto" not in model_info and ctrl.get("dt1") not in (None, "", "--"):
            if model_info.get("type") != "C":
                supported_modes.append("auto")

        # 3. Heat mode (type='C' is Cooling-Only; heat pump units specify type='N'/'H' and valid heat_l/h)
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

        # Optimistic lease: if a user command was sent within last 4s, keep user's control values
        recent_cmd = (time.time() - getattr(self, "_last_command_time", 0)) < 4.0

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
            "device_clock": getattr(self, "_last_clock", ""),
            "mac": self._basic_cache.get("mac", "") if self._basic_cache else "",
            "firmware": self._basic_cache.get("ver", "") if self._basic_cache else "",
            "model_type": self._basic_cache.get("type", "aircon") if self._basic_cache else "aircon",
            "err": ctrl.get("err", sensors.get("err", "0")),
            "raw_ctrl": ctrl,
            "raw_sensors": sensors
        }
        self._last_state = state
        return state

    def update_settings(self, **kwargs) -> dict:
        if not self.is_configured:
            return {"ret": "UNCONFIGURED", "error": "Device not configured"}

        # Stamp user action timestamp for optimistic lease protection
        self._last_command_time = time.time()

        with self._lock:
            # 1. Use cached control state if available, avoiding pre-read network roundtrip (~500ms saved)
            current = self._last_ctrl
            if not current or current.get("ret") != "OK":
                if self._last_state and isinstance(self._last_state.get("raw_ctrl"), dict) and self._last_state["raw_ctrl"].get("ret") == "OK":
                    current = self._last_state["raw_ctrl"]
                else:
                    current = self.query_endpoint("/aircon/get_control_info")
                    if current.get("ret") == "OK":
                        self._last_ctrl = dict(current)
                    else:
                        raise RuntimeError(f"Failed to read current AC state for {self.name}: {current.get('error', 'Unknown')}")

            payload = {
                "pow": current.get("pow", "0"),
                "mode": current.get("mode", "3"),
                "stemp": current.get("stemp", "25.0"),
                "shum": current.get("shum", "0"),
                "f_rate": current.get("f_rate", "A"),
                "f_dir": current.get("f_dir", "0"),
            }

            # Map user-friendly values if supplied
            if "power" in kwargs:
                payload["pow"] = "1" if kwargs["power"] else "0"
            if "mode" in kwargs:
                m = str(kwargs["mode"]).lower()
                target_code = MODE_MAP.get(m, m)
                target_name = MODE_REV_MAP.get(target_code, m)
                if self._last_state and "supported_modes" in self._last_state:
                    if target_name not in self._last_state["supported_modes"]:
                        return {"ret": "PARAM NG", "error": f"Mode '{target_name}' is not supported by {self.name}"}
                payload["mode"] = target_code
            if "target_temp" in kwargs:
                payload["stemp"] = f"{float(kwargs['target_temp']):.1f}"
            if "fan_rate" in kwargs:
                payload["f_rate"] = str(kwargs["fan_rate"]).upper()
            if "fan_dir" in kwargs:
                payload["f_dir"] = str(kwargs["fan_dir"])

            # Allow raw Daikin parameter overrides directly (excluding 'mode' which was already mapped)
            for k in ("pow", "stemp", "shum", "f_rate", "f_dir"):
                if k in kwargs:
                    payload[k] = str(kwargs[k])

            # Validate temperature setpoint for the target mode
            target_mode = payload.get("mode", "3")
            if target_mode in ("0", "1", "3", "4", "7"):
                # Temperature-controlled modes require a numeric setpoint
                if payload.get("stemp") in ("--", "M", None, ""):
                    dt_key = f"dt{target_mode}"
                    fallback = current.get(dt_key) or current.get("b_stemp") or "25.0"
                    payload["stemp"] = fallback if fallback not in ("--", "M", None, "") else "25.0"
            elif target_mode == "2":
                # Dry mode: Daikin expects 'M' or '--' or remembered dt2
                if "target_temp" not in kwargs and "stemp" not in kwargs:
                    payload["stemp"] = current.get("dt2") or "M"
            elif target_mode == "6":
                # Fan mode: Daikin expects '--'
                if "target_temp" not in kwargs and "stemp" not in kwargs:
                    payload["stemp"] = "--"

            # 2. Execute single write request (with 1 auto-retry on transient socket drops)
            url = f"{self.base_url}/aircon/set_control_info"
            parsed = {}
            for attempt in range(2):
                try:
                    res = self.session.get(url, params=payload, verify=False, timeout=5.0)
                    parsed = self._parse_response(res.text)
                    if parsed.get("ret") in ("AUTH_FAIL", "ADV_NG") or res.status_code in (401, 403):
                        if self.register():
                            res = self.session.get(url, params=payload, verify=False, timeout=5.0)
                            parsed = self._parse_response(res.text)
                    break
                except Exception as exc:
                    if attempt == 0:
                        time.sleep(0.2)
                        continue
                    return {"ret": "COMM_ERROR", "error": f"Failed to send command to {self.name} ({self.ip}): {exc}"}

            # 3. On success, generate synthetic state in-memory immediately (eliminates 2 extra HTTP calls)
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
    def __init__(self, config_path: str = CONFIG_FILE):
        self.config_path = config_path
        self.devices: Dict[str, DaikinController] = {}
        self.load_config()

    def load_config(self):
        if not os.path.exists(self.config_path):
            return

        with open(self.config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)

        updated = False
        device_list = cfg.get("devices", [])
        
        # Support legacy single-device format
        if not device_list and "uuid" in cfg:
            device_list = [{
                "id": "ac_1",
                "name": "AC 1",
                "ip": "192.168.1.XXX",
                "key": "",
                "uuid": cfg["uuid"]
            }]
            cfg["devices"] = device_list
            updated = True

        for d in device_list:
            dev_id = d.get("id", f"ac_{len(self.devices) + 1}")
            c_uuid = d.get("uuid", "")
            if not c_uuid:
                c_uuid = uuid.uuid4().hex
                d["uuid"] = c_uuid
                updated = True

            ctrl = DaikinController(
                device_id=dev_id,
                name=d.get("name", dev_id),
                ip=d.get("ip", ""),
                key=d.get("key", ""),
                client_uuid=c_uuid
            )
            self.devices[dev_id] = ctrl

        if updated:
            self.save_config(cfg)

    def save_config(self, cfg_data: Optional[dict] = None):
        if cfg_data is None:
            cfg_data = {
                "devices": [
                    {
                        "id": ctrl.device_id,
                        "name": ctrl.name,
                        "ip": ctrl.ip,
                        "key": ctrl.key,
                        "uuid": ctrl.uuid
                    }
                    for ctrl in self.devices.values()
                ]
            }
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(cfg_data, f, indent=2)

    def get_device(self, dev_id: str) -> Optional[DaikinController]:
        return self.devices.get(dev_id)

    def upsert_device(self, dev_id: str, name: str, ip: str, key: str, client_uuid: str = "") -> "DaikinController":
        if not dev_id:
            dev_id = f"ac_{len(self.devices) + 1}"

        existing = self.devices.get(dev_id)
        if existing:
            if name:
                existing.name = name
            if ip:
                existing.ip = ip
                existing.base_url = f"https://{existing.ip}"
            if key:
                existing.key = key.strip()
            if client_uuid:
                existing.uuid = client_uuid
                existing.session.headers.update({"X-Daikin-uuid": existing.uuid})
            ctrl = existing
        else:
            ctrl = DaikinController(
                device_id=dev_id,
                name=name or dev_id,
                ip=ip,
                key=key.strip() if key else "",
                client_uuid=client_uuid or uuid.uuid4().hex
            )
            self.devices[dev_id] = ctrl

        self.save_config()
        return ctrl

    def delete_device(self, dev_id: str) -> bool:
        if dev_id in self.devices:
            del self.devices[dev_id]
            self.save_config()
            return True
        return False

    def get_summaries(self) -> List[dict]:
        summaries = []
        for ctrl in self.devices.values():
            item = {
                "id": ctrl.device_id,
                "name": ctrl.name,
                "ip": ctrl.ip,
                "configured": ctrl.is_configured,
            }
            if ctrl._last_state:
                item["power"] = ctrl._last_state.get("power", False)
                item["target_temp"] = ctrl._last_state.get("target_temp")
                item["indoor_temp"] = ctrl._last_state.get("indoor_temp")
                item["mode"] = ctrl._last_state.get("mode", "cool")
                item["status"] = ctrl._last_state.get("status", "online")
            summaries.append(item)
        return summaries

    def poll_all_parallel(self) -> List[dict]:
        configured = [c for c in self.devices.values() if c.is_configured]
        if not configured:
            return []

        from concurrent.futures import ThreadPoolExecutor, as_completed
        results = {}
        with ThreadPoolExecutor(max_workers=min(len(configured), 8)) as executor:
            future_to_ctrl = {executor.submit(c.get_full_state): c for c in configured}
            for future in as_completed(future_to_ctrl):
                ctrl = future_to_ctrl[future]
                try:
                    state = future.result()
                    results[ctrl.device_id] = state
                except Exception as e:
                    print(f"[Poller] Error polling {ctrl.name}: {e}")
        return list(results.values())

    def get_all_states(self) -> List[dict]:
        return self.poll_all_parallel()


if __name__ == "__main__":
    mgr = DaikinDeviceManager()
    devices = list(mgr.devices.values())
    
    if not devices:
        print("No devices configured in daikin_config.json")
        sys.exit(1)

    target_ac = devices[0]
    print(f"Loaded {len(devices)} device slot(s). Target: {target_ac.name} ({target_ac.ip})")

    if len(sys.argv) > 1 and sys.argv[1].lower() == "dump":
        print(json.dumps(target_ac.get_full_state(), indent=2))
        sys.exit(0)

    state = target_ac.get_full_state()
    print("\n--- Current Status ---")
    print(f"Name:         {state.get('name')}")
    print(f"Status:       {state.get('status')}")
    print(f"Power:        {'ON' if state.get('power') else 'OFF'}")
    print(f"Target Temp:  {state.get('target_temp')} °C")
    print(f"Indoor Temp:  {state.get('indoor_temp')} °C")
    print(f"Outdoor Temp: {state.get('outdoor_temp')} °C")
    print(f"Mode:         {state.get('mode')}")
    print(f"Fan Speed:    {state.get('fan_rate')}")
    print(f"Vane Swing:   {state.get('fan_dir')}")
    print("----------------------\n")