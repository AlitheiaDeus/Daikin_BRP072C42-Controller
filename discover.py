import socket
import urllib.parse

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
sock.settimeout(2.5)

print("Broadcasting Daikin UDP probe on port 30050...")
found = []
try:
    sock.sendto(b"DAIKIN_UDP/common/basic_info", ("255.255.255.255", 30050))
    while True:
        data, addr = sock.recvfrom(1024)
        text = data.decode("utf-8", "ignore")
        tokens = dict([tok.split("=", 1) for tok in text.strip().split(",") if "=" in tok])
        name = urllib.parse.unquote(tokens.get("name", "Unknown"))
        found.append({
            "ip": addr[0],
            "name": name,
            "ssid": tokens.get("ssid"),
            "mac": tokens.get("mac")
        })
        print(f"[+] FOUND: IP: {addr[0]} | Name: {name} | SSID: {tokens.get('ssid')} | MAC: {tokens.get('mac')}")
except socket.timeout:
    pass
finally:
    sock.close()

print(f"\nDone. Found {len(found)} Daikin unit(s) on your Wi-Fi.")
