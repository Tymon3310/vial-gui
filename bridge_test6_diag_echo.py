#!/usr/bin/env python3
"""
Test the VIAL_DIAG_ECHO firmware patch.

With VIAL_DIAG_ECHO enabled, the firmware intercepts 0xFE commands in
wireless.c and echoes them back with byte[0]=0xAA, byte[1]=0xBB,
bypassing vial_handle_cmd entirely.

If we get 0xAA back: the wireless transport works, and the issue is
in vial_handle_cmd or the data it generates.

If we still timeout: the wireless transport itself drops the response,
meaning the issue is in lkbt51_send_raw_hid or the LKBT51 module firmware.

Usage: python3 bridge_test6_diag_echo.py
"""

import sys
import time
import hid

MSG_LEN = 32


def find_bridge():
    for dev in hid.enumerate():
        if (
            dev["vendor_id"] == 0x3434
            and dev["product_id"] in (0xD030, 0xD031)
            and dev["usage_page"] == 0xFF60
            and dev["usage"] == 0x61
        ):
            return dev
    return None


def sr(d, msg, label, timeout=3000):
    """Send message and read response."""
    msg = msg + b"\x00" * (MSG_LEN - len(msg))
    t0 = time.monotonic()
    d.write(b"\x00" + msg)
    data = bytes(d.read(MSG_LEN, timeout))
    elapsed = (time.monotonic() - t0) * 1000
    while data and data[0] in (0xBC, 0xE2):
        print(f"  [{label}] state notif 0x{data[0]:02X}, re-reading...")
        data = bytes(d.read(MSG_LEN, timeout))
        elapsed = (time.monotonic() - t0) * 1000
    if data:
        print(f"  {label} ({elapsed:.0f}ms): {data[:16].hex(' ')}")
        return data
    else:
        print(f"  {label} ({elapsed:.0f}ms): TIMEOUT")
        return None


def flush(d, timeout=200):
    count = 0
    while True:
        data = bytes(d.read(MSG_LEN, timeout))
        if not data:
            break
        count += 1
        print(f"  [flush] data: {data[:16].hex(' ')}")
    if count:
        print(f"  [flush] discarded {count} packets")


def main():
    desc = find_bridge()
    if not desc:
        print("No bridge raw HID found")
        sys.exit(1)

    d = hid.Device(path=desc["path"])
    print(f"Opened bridge: PID=0x{desc['product_id']:04X}")

    # FR handshake
    print("\n=== FR Handshake ===")
    sr(d, bytes([0xB1]), "B1")
    sr(d, bytes([0xB2]), "B2")
    sr(d, bytes([0xB3]), "B3")
    flush(d)

    # Warm up with VIA command
    print("\n=== Warm up: VIA 0x01 ===")
    r = sr(d, bytes([0x01]), "VIA 0x01")
    if r:
        print(f"  VIA version: {r[2]}")
    flush(d)

    # Test 1: Send 0xFE 0x00 — firmware rewrites to 0x01 and routes through VIA
    print("\n=== Test 1: Vial 0xFE 0x00 (rewritten to VIA 0x01 by firmware) ===")
    print(
        "  If response arrives with byte0=0x01: VIA path works for rewritten commands"
    )
    print("  If TIMEOUT: something else prevents even rewritten commands from working")
    r = sr(d, bytes([0xFE, 0x00]), "Vial 0xFE->0x01", timeout=5000)
    if r:
        if r[0] == 0x01 and r[1] == 0x00 and r[2] == 0x0B:
            print("  ** SUCCESS! Got VIA protocol response (0x01 0x00 0x0B)! **")
            print("  ** The VIA path works when 0xFE is rewritten to 0x01! **")
            print(
                "  ** This means vial_handle_cmd's buffer modifications cause the drop. **"
            )
        elif r[0] == 0xFE:
            print("  ** Got 0xFE response — diagnostic may not be active. **")
        else:
            print(f"  ** Response: {r[:8].hex(' ')} **")
    else:
        print("  ** TIMEOUT — even rewriting to 0x01 doesn't help! **")
    flush(d, timeout=500)

    # Test 2: Another 0xFE 0x01
    print("\n=== Test 2: Vial 0xFE 0x01 (also rewritten to 0x01) ===")
    r = sr(d, bytes([0xFE, 0x01]), "Vial 0xFE->0x01 #2", timeout=5000)
    if r:
        if r[0] == 0x01:
            print("  ** Also works! Consistent. **")
        else:
            print(f"  ** Response: {r[:8].hex(' ')} **")
    flush(d, timeout=500)

    # Test 3: VIA commands should still work normally
    print("\n=== Test 3: VIA 0x01 (should still work normally) ===")
    r = sr(d, bytes([0x01]), "VIA 0x01")
    if r and r[0] == 0x01:
        print("  VIA still works normally.")
    flush(d)

    # Test 4: Multiple rapid 0xFE commands
    print("\n=== Test 4: Three rapid 0xFE 0x00 commands ===")
    for i in range(3):
        r = sr(d, bytes([0xFE, 0x00]), f"0xFE #{i + 1}", timeout=3000)
        if r and r[0] == 0xAA:
            print(f"  #{i + 1}: Echo received!")
    flush(d, timeout=500)

    # Test 5: Uptime to confirm keyboard is stable
    print("\n=== Test 5: Uptime check ===")
    r = sr(d, bytes([0x02, 0x01]), "uptime")
    if r:
        uptime = (r[2] << 24) | (r[3] << 16) | (r[4] << 8) | r[5]
        print(f"  Uptime: {uptime}ms ({uptime / 1000:.1f}s)")
    flush(d)

    d.close()

    print("\n=== Summary ===")
    print("If 0xAA 0xBB echo received: Transport is fine, bug is in vial_handle_cmd")
    print("If TIMEOUT: Transport drops response, bug is in LKBT51 module or SPI layer")


if __name__ == "__main__":
    main()
