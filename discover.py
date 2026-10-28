import socket
import urllib.parse
from typing import List, Dict, Any


def discover_units(timeout: float = 2.0) -> List[Dict[str, Any]]:
    """Broadcast UDP probe on port 30050 and collect responding Daikin adapters."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.settimeout(timeout)
    found = []
    seen_ips = set()
    try:
        sock.sendto(b"DAIKIN_UDP/common/basic_info", ("255.255.255.255", 30050))
        while True:
            data, addr = sock.recvfrom(1024)
            ip = addr[0]
            if ip in seen_ips:
                continue
            seen_ips.add(ip)
            text = data.decode("utf-8", "ignore")
            tokens = dict([tok.split("=", 1) for tok in text.strip().split(",") if "=" in tok])
            raw_name = tokens.get("name", "Unknown")
            name = urllib.parse.unquote(raw_name)
            found.append({
                "ip": ip,
                "name": name,
                "ssid": tokens.get("ssid", ""),
                "mac": tokens.get("mac", "")
            })
    except (socket.timeout, TimeoutError):
        pass
    except Exception as exc:
        print(f"[Discovery Error] {exc}")
    finally:
        sock.close()
    return found


if __name__ == "__main__":
    print("Broadcasting Daikin UDP probe on port 30050...")
    units = discover_units(2.5)
    for u in units:
        print(f"[+] FOUND: IP: {u['ip']} | Name: {u['name']} | SSID: {u['ssid']} | MAC: {u['mac']}")
    print(f"\nDone. Found {len(units)} Daikin unit(s) on your Wi-Fi.")

