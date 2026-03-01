#!/usr/bin/env python3
"""
Diagnostic: Does sending Vial 0xFE over wireless crash/reset the keyboard?

Strategy:
1. FR handshake
2. Get uptime (0x02 0x01) -> baseline
3. Send Vial 0xFE 0x00 -> expect timeout
4. Get uptime again -> if reset to ~0, keyboard crashed
5. Wait a moment and try uptime again -> confirms keyboard is still alive
6. Send 0xFE 0x00 a second time -> check if it's repeatable
7. Get uptime once more

Also tests:
- Whether any late/queued responses appear after 0xFE
- Whether the keyboard becomes unresponsive after 0xFE

Usage: sudo python3 bridge_test4_uptime.py
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
    """Send message and read response, handling state notifications."""
    msg = msg + b"\x00" * (MSG_LEN - len(msg))
    t0 = time.monotonic()
    d.write(b"\x00" + msg)
    data = bytes(d.read(MSG_LEN, timeout))
    elapsed = (time.monotonic() - t0) * 1000

    # Skip state notifications
    while data and data[0] in (0xBC, 0xE2):
        print(f"  [{label}] state notification 0x{data[0]:02X}, re-reading...")
        data = bytes(d.read(MSG_LEN, timeout))
        elapsed = (time.monotonic() - t0) * 1000

    if data:
        print(f"  {label} ({elapsed:.0f}ms): {data[:16].hex(' ')}")
        return data
    else:
        print(f"  {label} ({elapsed:.0f}ms): TIMEOUT")
        return None


def get_uptime(d, label="uptime"):
    """Send GET_KEYBOARD_VALUE/uptime and return the uptime in ms."""
    resp = sr(d, bytes([0x02, 0x01]), label)
    if resp:
        # Response format: 0x02 0x01 <uptime_32bit_be>
        uptime_ms = (resp[2] << 24) | (resp[3] << 16) | (resp[4] << 8) | resp[5]
        print(f"    -> Uptime: {uptime_ms} ms ({uptime_ms / 1000:.1f}s)")
        return uptime_ms
    return None


def flush(d, timeout=200):
    """Read and discard any pending data."""
    count = 0
    while True:
        data = bytes(d.read(MSG_LEN, timeout))
        if not data:
            break
        count += 1
        print(f"  [flush] unexpected data: {data[:16].hex(' ')}")
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
    sr(d, bytes([0xB1]), "B1 (proto)")
    sr(d, bytes([0xB2]), "B2 (state)")
    sr(d, bytes([0xB3]), "B3 (fw ver)")
    flush(d)

    # Step 1: Baseline uptime
    print("\n=== Step 1: Baseline Uptime ===")
    t1 = get_uptime(d, "uptime-1")
    flush(d)

    # Step 2: Send Vial 0xFE 0x00 (GET_KEYBOARD_ID) - expected to timeout
    print("\n=== Step 2: Send Vial 0xFE 0x00 ===")
    resp = sr(d, bytes([0xFE, 0x00]), "Vial 0xFE", timeout=5000)
    if resp:
        print("  ** UNEXPECTED: Got a response! The bug may be fixed. **")
        print(f"  ** Response: {resp.hex(' ')}")
    flush(d, timeout=500)

    # Step 3: Immediate uptime check
    print("\n=== Step 3: Uptime after 0xFE ===")
    t2 = get_uptime(d, "uptime-2")
    flush(d)

    if t1 is not None and t2 is not None:
        if t2 < t1:
            print(
                f"  *** KEYBOARD RESET DETECTED! Uptime went from {t1}ms to {t2}ms ***"
            )
            print(f"  *** The 0xFE command is crashing the keyboard firmware! ***")
        else:
            delta = t2 - t1
            print(f"  Uptime delta: {delta}ms (keyboard did NOT reset)")

    # Step 4: Wait and re-check
    print("\n=== Step 4: Wait 2s and re-check ===")
    time.sleep(2)
    t3 = get_uptime(d, "uptime-3")
    flush(d)

    if t2 is not None and t3 is not None:
        if t3 < t2:
            print(f"  *** DELAYED RESET! Uptime went from {t2}ms to {t3}ms ***")
        else:
            print(f"  Uptime delta from step 3: {t3 - t2}ms (keyboard stable)")

    # Step 5: Try a different VIA command to confirm keyboard is responsive
    print("\n=== Step 5: Confirm keyboard responsiveness ===")
    sr(d, bytes([0x01]), "VIA 0x01 (proto ver)")
    sr(d, bytes([0xA0]), "KC 0xA0 (kc proto)")
    flush(d)

    # Step 6: Second 0xFE attempt
    print("\n=== Step 6: Second Vial 0xFE 0x00 ===")
    resp = sr(d, bytes([0xFE, 0x00]), "Vial 0xFE #2", timeout=5000)
    if resp:
        print("  ** Got a response on second attempt! **")
    flush(d, timeout=500)

    # Step 7: Final uptime
    print("\n=== Step 7: Final uptime ===")
    t4 = get_uptime(d, "uptime-4")
    flush(d)

    if t3 is not None and t4 is not None:
        if t4 < t3:
            print(f"  *** KEYBOARD RESET after second 0xFE! ***")
        else:
            print(f"  Uptime delta: {t4 - t3}ms (keyboard stable after 2nd 0xFE)")

    # Step 8: Try Vial 0xFE 0x01 (GET_SIZE) and 0xFE 0x02 (GET_DEF page 0)
    print("\n=== Step 8: Other Vial sub-commands ===")
    resp = sr(d, bytes([0xFE, 0x01]), "Vial 0xFE 0x01 (size)", timeout=5000)
    flush(d, timeout=500)

    resp = sr(
        d, bytes([0xFE, 0x02, 0x00, 0x00]), "Vial 0xFE 0x02 (def p0)", timeout=5000
    )
    flush(d, timeout=500)

    t5 = get_uptime(d, "uptime-final")
    if t4 is not None and t5 is not None:
        if t5 < t4:
            print(f"  *** KEYBOARD RESET after other 0xFE commands! ***")
        else:
            print(f"  Keyboard stable. Uptime delta: {t5 - t4}ms")

    d.close()

    print("\n=== Analysis ===")
    print("If uptime resets after 0xFE -> firmware crash (hard fault)")
    print("If uptime continues but 0xFE timeout -> firmware hangs in handler")
    print("  or response is generated but not transmitted")
    print("If keyboard unresponsive after 0xFE -> deadlock/infinite loop")


if __name__ == "__main__":
    main()
