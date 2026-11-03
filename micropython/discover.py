# discover.py - MicroPython Daikin UDP Discovery
import socket
import time

def unquote(s):
    """Decode percent-encoded URL string without external urllib."""
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

def discover_units(timeout=2.0):
    """Broadcast UDP probe on port 30050 and collect responding Daikin adapters."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    except Exception:
        pass
    sock.settimeout(timeout)

    found = []
    seen_ips = set()

    try:
        sock.sendto(b"DAIKIN_UDP/common/basic_info", ("255.255.255.255", 30050))
        t_end = time.time() + timeout
        while time.time() < t_end:
            try:
                data, addr = sock.recvfrom(1024)
                ip = addr[0]
                if ip in seen_ips:
                    continue
                seen_ips.add(ip)
                text = data.decode("utf-8", "ignore")
                tokens = {}
                for tok in text.strip().split(","):
                    if "=" in tok:
                        k, v = tok.split("=", 1)
                        tokens[k] = v
                raw_name = tokens.get("name", "Unknown")
                name = unquote(raw_name)
                found.append({
                    "ip": ip,
                    "name": name,
                    "ssid": tokens.get("ssid", ""),
                    "mac": tokens.get("mac", "")
                })
            except (socket.timeout, OSError):
                break
    except Exception as exc:
        print(f"[Discovery Error] {exc}")
    finally:
        sock.close()
    return found
