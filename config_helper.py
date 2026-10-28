#!/usr/bin/env python3
"""
Interactive Non-Sequential CLI Config Helper for Daikin BRP072C42 adapters.
Scans the Wi-Fi network and lets you select, name, and pair units in any order.
"""

import sys
from daikin import DaikinDeviceManager
from discover import discover_units


def print_banner():
    print("=" * 64)
    print("      DAIKIN LAN CONTROLLER - INTERACTIVE SETUP HELPER        ")
    print("=" * 64)
    print(" Scan your Wi-Fi subnet, enter 13-digit keys from sticker     ")
    print(" photos in ANY order, and test live handshakes instantly.     ")
    print("=" * 64)


def run_cli_helper():
    print_banner()
    mgr = DaikinDeviceManager()

    print("\n[+] Broadcasting UDP probe on port 30050 to discover ACs...")
    units = discover_units(timeout=2.5)

    if not units:
        print("\n[!] No Daikin units responded to the UDP broadcast.")
        print("    - Verify your computer is on the same 2.4/5GHz Wi-Fi subnet.")
        print("    - Verify the Daikin Wi-Fi LED is solid or blinking slowly.")
        manual = input("\nWould you like to manually add an IP? (y/N): ").strip().lower()
        if manual == "y":
            ip = input("Enter AC IP address: ").strip()
            name = input("Enter Room Name: ").strip() or "Air Conditioner"
            units = [{"ip": ip, "name": name, "ssid": "", "mac": ""}]
        else:
            return

    while True:
        print("\n--- Discovered Units on Your Wi-Fi ---")
        matched_list = []
        for idx, u in enumerate(units, 1):
            matched = None
            for ctrl in mgr.devices.values():
                if ctrl.ip == u["ip"]:
                    matched = ctrl
                    break

            is_paired = bool(matched and matched.is_configured and matched.key)
            status_tag = "[PAIRED & READY]" if is_paired else "[NEEDS 13-DIGIT KEY]"
            display_name = matched.name if matched else u["name"]

            print(f"  [{idx}] {display_name:<18} IP: {u['ip']:<15} {status_tag}")
            matched_list.append((u, matched))

        print("\nCommands:")
        print("  [1-N] Select unit to pair or rename")
        print("  [r]   Rescan Wi-Fi subnet")
        print("  [q]   Done / Exit")

        choice = input("\nSelect an option: ").strip().lower()
        if choice in ("q", "quit", "exit"):
            print("\nSetup complete! Run 'python server.py' to launch the web controller.")
            break

        if choice == "r":
            print("\n[+] Rescanning Wi-Fi...")
            units = discover_units(timeout=2.5)
            continue

        if not choice.isdigit() or int(choice) < 1 or int(choice) > len(matched_list):
            print("[!] Invalid selection. Please enter a number, 'r', or 'q'.")
            continue

        selected_unit, existing_ctrl = matched_list[int(choice) - 1]
        cur_name = existing_ctrl.name if existing_ctrl else selected_unit["name"]
        has_key = bool(existing_ctrl and existing_ctrl.key)

        print(f"\n--- Configuring {cur_name} ({selected_unit['ip']}) ---")
        new_name = input(f"Room Name [{cur_name}]: ").strip()
        if not new_name:
            new_name = cur_name

        key_prompt = "13-digit key from sticker photo (leave blank to keep existing): " if has_key else "13-digit key from sticker photo: "
        key_input = input(key_prompt).strip()

        if not key_input and not has_key:
            print("[!] Key cannot be empty for a new unit. Pairing cancelled.")
            continue

        # Save and upsert
        dev_id = existing_ctrl.device_id if existing_ctrl else None
        ctrl = mgr.upsert_device(dev_id=dev_id, name=new_name, ip=selected_unit["ip"], key=key_input or None)

        print(f"[+] Connecting to {new_name} ({selected_unit['ip']}) and testing registration handshake...")
        reg_ok = ctrl.register()

        if reg_ok:
            state = ctrl.get_full_state()
            print("  [SUCCESS] Live handshake verified!")
            print(f"  - Status:      {state.get('status')}")
            print(f"  - Power:       {'ON' if state.get('power') else 'OFF'}")
            print(f"  - Room Temp:   {state.get('indoor_temp')} °C")
            print(f"  - Target Temp: {state.get('target_temp')} °C")
            print(f"  - Saved to:    {mgr.config_path}")
        else:
            print("  [WARNING] AC rejected the handshake (check the 13-digit key from the sticker photo).")
            print("  Key and settings saved, but unit will remain offline until a valid key is provided.")


if __name__ == "__main__":
    try:
        run_cli_helper()
    except KeyboardInterrupt:
        print("\n\nSetup aborted by user.")
        sys.exit(0)
