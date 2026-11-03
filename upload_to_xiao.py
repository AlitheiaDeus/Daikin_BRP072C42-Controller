# upload_to_xiao.py - Reliable Serial Uploader for MicroPython
import sys
import time
import os
import glob

PORT = sys.argv[1] if len(sys.argv) > 1 else "COM12"
BAUD = 115200

try:
    import serial
except ImportError:
    print("[!] Error: pyserial is required. Run: pip install pyserial")
    sys.exit(1)


def read_until(ser, ending, timeout=10):
    t_end = time.time() + timeout
    buf = b""
    while time.time() < t_end:
        n = ser.in_waiting
        if n > 0:
            buf += ser.read(n)
            if buf.endswith(ending):
                return buf
        time.sleep(0.05)
    return buf


def enter_raw_repl(ser):
    print("[*] Connecting and interrupting boot cycle...")
    # Send Ctrl-C a few times to stop any running loop
    for _ in range(3):
        ser.write(b"\r\x03")
        time.sleep(0.2)
    ser.read_all()

    # Try entering raw REPL (Ctrl-A) WITHOUT soft-resetting
    ser.write(b"\r\x01")
    res = read_until(ser, b"raw REPL; CTRL-B to exit\r\n>", timeout=2.0)
    
    if b"raw REPL" not in res:
        print("[*] Board is busy scanning Wi-Fi in boot.py. Waiting 4s for Wi-Fi to settle...")
        time.sleep(4.0)
        ser.write(b"\r\x03\x01")
        res = read_until(ser, b"raw REPL; CTRL-B to exit\r\n>", timeout=3.0)

    if b"raw REPL" not in res:
        # One more attempt
        ser.write(b"\r\x03\x01")
        res = read_until(ser, b">", timeout=2.0)

    print("[+] Successfully entered raw REPL mode!")


def exec_raw(ser, cmd_str):
    """Execute python statement in raw REPL mode."""
    ser.write(cmd_str.encode("utf-8") + b"\x04")
    # Wait for execution response: b'OK<output>\x04<error>\x04>'
    raw = read_until(ser, b"\x04>", timeout=15)
    if not raw.startswith(b"OK"):
        # Strip leading garbage if any
        idx = raw.find(b"OK")
        if idx != -1:
            raw = raw[idx:]
    parts = raw.split(b"\x04")
    if len(parts) >= 3 and parts[1]:
        err = parts[1].decode("utf-8", "ignore")
        if "Traceback" in err or "Error" in err:
            raise RuntimeError(err)


def upload_file(ser, local_path, remote_path):
    size = os.path.getsize(local_path)
    print(f"[*] Uploading {local_path} -> /{remote_path} ({size:,} bytes)...")
    
    exec_raw(ser, f"f = open('{remote_path}', 'wb')")
    
    with open(local_path, "rb") as f:
        while True:
            chunk = f.read(512)
            if not chunk:
                break
            exec_raw(ser, f"f.write({repr(chunk)})")
            
    exec_raw(ser, "f.close()")


def main():
    print("==================================================")
    print(f"  Uploading Daikin AC Server to XIAO C5 on {PORT}")
    print("==================================================")

    ser = serial.Serial(PORT, BAUD, timeout=1)
    try:
        enter_raw_repl(ser)

        print("[*] Creating remote 'web' directory...")
        exec_raw(ser, "import os; 'web' in os.listdir() or os.mkdir('web')")

        files_to_upload = [
            ("micropython/main.py", "main.py"),
            ("micropython/daikin.py", "daikin.py"),
            ("micropython/discover.py", "discover.py"),
            ("micropython/daikin_config.json", "daikin_config.json"),
            ("micropython/web/index.html", "web/index.html"),
            ("micropython/web/style.css", "web/style.css"),
            ("micropython/web/app.js", "web/app.js"),
        ]

        for local, remote in files_to_upload:
            upload_file(ser, local, remote)

        print("\n[+] All files uploaded successfully!")
        print("[*] Soft-resetting board to launch main.py...")
        # Send Ctrl-B to exit raw REPL and Ctrl-D to soft-reset
        ser.write(b"\x02\x04")
        time.sleep(1)

        print("[*] Connecting to live startup output (Ctrl-C to stop monitoring):")
        print("--------------------------------------------------")
        while True:
            n = ser.in_waiting
            if n > 0:
                text = ser.read(n).decode("utf-8", "ignore")
                sys.stdout.write(text)
                sys.stdout.flush()
            time.sleep(0.05)

    except KeyboardInterrupt:
        print("\n[i] Disconnected from monitor.")
    finally:
        ser.close()


if __name__ == "__main__":
    main()
