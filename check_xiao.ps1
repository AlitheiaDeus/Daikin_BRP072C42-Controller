# check_xiao.ps1 - Inspect XIAO C5 MicroPython Environment
param (
    [string]$Port = "COM12"
)

Write-Host "==================================================" -ForegroundColor Cyan
Write-Host " Inspecting XIAO C5 on $Port" -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor Cyan

$inspectCode = @'
import os, sys, gc

print("\n=== BOARD & FIRMWARE ===")
try:
    u = os.uname()
    print("Machine:    ", getattr(u, "machine", "Unknown"))
    print("Release:    ", getattr(u, "release", "Unknown"))
    print("Version:    ", getattr(u, "version", "Unknown"))
except Exception as e:
    print("uname error:", e)

print("MicroPython:", sys.version)

gc.collect()
print(f"Free RAM:    {gc.mem_free():,} bytes")

try:
    st = os.statvfs("/")
    free_kb = (st[0] * st[3]) // 1024
    total_kb = (st[0] * st[2]) // 1024
    print(f"Flash Disk:  {free_kb:,} KB free / {total_kb:,} KB total")
except Exception as e:
    print("Flash stat error:", e)

print("\n=== FILESYSTEM CONTENTS ===")
def walk(dir_path=""):
    items = []
    try:
        items = os.listdir(dir_path)
    except Exception as e:
        print(f"Error reading {dir_path}: {e}")
        return
    if not items and not dir_path:
        print("(Filesystem is completely empty)")
        return
    for item in items:
        full = f"{dir_path}/{item}" if dir_path else item
        try:
            mode = os.stat(full)[0]
            if mode & 0x4000:
                print(f"  [DIR]  /{full}")
                walk(full)
            else:
                size = os.stat(full)[6]
                print(f"  [FILE] /{full} ({size:,} bytes)")
        except Exception:
            print(f"  [?]    /{full}")

walk("")
print("===========================\n")
'@

mpremote connect $Port exec "$inspectCode"
