#!/usr/bin/env python3
"""
Diagnostic to test bridge byte-0 filtering hypothesis.

Hypothesis: The bridge dongle only forwards responses where byte 0
matches the request's byte 0. This would explain why Vial 0xFE commands
timeout (Vial overwrites byte 0 with response data) while VIA 0x01
commands work (VIA echoes byte 0).

Tests:
1. VIA 0x01 (GET_PROTOCOL_VERSION) - byte 0 echoed -> should work
2. VIA 0x02 (GET_KEYBOARD_VALUE, uptime) - byte 0 echoed -> should work
3. Keychron 0xA0 - byte 0 preserved -> should work
4. Unknown 0x50 - byte 0 becomes 0xFF (unhandled) -> if dropped, confirms hypothesis
5. Unknown 0x99 - same test, different byte
6. Vial 0xFE 0x00 - byte 0 overwritten -> expected to fail (the bug)
7. VIA 0x0D (GET_LAYER_COUNT) - byte 0 echoed -> should work

Usage: sudo python3 bridge_test3.py
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
    if data:
        # Check if it's a state notification and skip it
        if data[0] in (0xBC, 0xE2):
            print(f"  {label}: [state notification 0x{data[0]:02X}, re-reading...]")
            data = bytes(d.read(MSG_LEN, timeout))
            elapsed = (time.monotonic() - t0) * 1000
    if data:
        print(
            f"  {label} ({elapsed:.0f}ms): "
            f"TX[0]=0x{msg[0]:02X} -> RX[0]=0x{data[0]:02X}  |  "
            f"{data[:16].hex(' ')}"
        )
        return data
    else:
        print(f"  {label} ({elapsed:.0f}ms): TX[0]=0x{msg[0]:02X} -> TIMEOUT")
        return None


def flush(d):
    while bytes(d.read(MSG_LEN, 100)):
        pass


def main():
    desc = find_bridge()
    if not desc:
        print("No bridge raw HID found")
        sys.exit(1)

    d = hid.Device(path=desc["path"])
    print(f"Opened bridge: PID=0x{desc['product_id']:04X} path={desc['path']}")

    # FR handshake
    print("\n=== FR Handshake ===")
    sr(d, bytes([0xB1]), "B1 (protocol ver)")
    sr(d, bytes([0xB2]), "B2 (state)")
    sr(d, bytes([0xB3]), "B3 (fw version)")
    flush(d)

    # Test 1: VIA 0x01 - byte 0 is echoed (should work)
    print("\n=== Test 1: VIA 0x01 (GET_PROTOCOL_VERSION) ===")
    print("  Expected: byte 0 stays 0x01 (echoed) -> response arrives")
    sr(d, bytes([0x01]), "VIA 0x01", timeout=5000)
    flush(d)

    # Test 2: VIA 0x02 (GET_KEYBOARD_VALUE, uptime=0x01) - byte 0 echoed
    print("\n=== Test 2: VIA 0x02 (GET_KEYBOARD_VALUE, uptime=0x01) ===")
    print("  Expected: byte 0 stays 0x02 (echoed) -> response arrives")
    sr(d, bytes([0x02, 0x01]), "VIA 0x02", timeout=5000)
    flush(d)

    # Test 3: Keychron 0xA0 - byte 0 preserved
    print("\n=== Test 3: Keychron 0xA0 (GET_PROTOCOL_VERSION) ===")
    print("  Expected: byte 0 stays 0xA0 -> response arrives")
    sr(d, bytes([0xA0]), "KC 0xA0", timeout=5000)
    flush(d)

    # Test 4: Unknown 0x50 - VIA sets byte 0 to 0xFF (id_unhandled)
    print("\n=== Test 4: Unknown 0x50 (should become 0xFF/unhandled) ===")
    print("  If TIMEOUT -> bridge filters by byte 0")
    print("  If returns 0xFF -> bridge is transparent, issue is elsewhere")
    sr(d, bytes([0x50]), "Unknown 0x50", timeout=5000)
    flush(d)

    # Test 5: Unknown 0x99 - another unhandled byte
    print("\n=== Test 5: Unknown 0x99 ===")
    print("  If TIMEOUT -> confirms bridge filtering")
    sr(d, bytes([0x99]), "Unknown 0x99", timeout=5000)
    flush(d)

    # Test 6: Vial 0xFE 0x00 - byte 0 overwritten with protocol ver (0x06)
    print("\n=== Test 6: Vial 0xFE 0x00 (GET_KEYBOARD_ID) ===")
    print("  Expected: TIMEOUT (byte 0 becomes 0x06, not 0xFE)")
    sr(d, bytes([0xFE, 0x00]), "Vial 0xFE", timeout=5000)
    flush(d)

    # Test 7: VIA 0x0D (GET_LAYER_COUNT) - byte 0 echoed
    print("\n=== Test 7: VIA 0x0D (GET_LAYER_COUNT) ===")
    print("  Expected: byte 0 stays 0x0D -> response arrives")
    sr(d, bytes([0x0D]), "VIA 0x0D", timeout=5000)
    flush(d)

    d.close()
    print("\n=== Summary ===")
    print("If tests 1-3,7 succeed and tests 4-6 timeout,")
    print("it confirms the bridge filters responses by byte 0.")
    print("If test 4 returns 0xFF, the bridge is transparent")
    print("and the issue is firmware-side (crash in vial_handle_cmd).")


if __name__ == "__main__":
    main()
