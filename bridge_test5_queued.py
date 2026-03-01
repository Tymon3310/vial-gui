#!/usr/bin/env python3
"""
Diagnostic: Is the Vial 0xFE response queued and returned as the NEXT response?

Hypothesis: The wireless module or bridge swallows the 0xFE response, but it
stays in a buffer and comes back as the response to the next command.

Test:
1. Send VIA 0x01 (expect: 0x01 response)
2. Send Vial 0xFE 0x00 with SHORT timeout (1s), then immediately send VIA 0x01
3. Read response — is it 0x01 (VIA proto) or 0x06 (Vial keyboard ID)?
4. Read again — is there a second response?
5. Repeat with different commands to build a pattern

Also tests the "read without sending" approach — just read after 0xFE with long timeout.

Usage: python3 bridge_test5_queued.py
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


def send_only(d, msg, label):
    """Send without reading."""
    msg = msg + b"\x00" * (MSG_LEN - len(msg))
    d.write(b"\x00" + msg)
    print(f"  {label}: SENT {msg[:4].hex(' ')}")


def read_only(d, label, timeout=3000):
    """Read without sending."""
    t0 = time.monotonic()
    data = bytes(d.read(MSG_LEN, timeout))
    elapsed = (time.monotonic() - t0) * 1000
    # Skip state notifications
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

    # Warm up
    print("\n=== Warm up ===")
    sr(d, bytes([0x01]), "VIA 0x01")
    flush(d)

    # Test A: Send 0xFE, then immediately read with long timeout
    print("\n=== Test A: Send 0xFE, long read (10s) ===")
    print("  If response appears after long delay, it's queued/buffered")
    send_only(d, bytes([0xFE, 0x00]), "Vial 0xFE 0x00")
    r = read_only(d, "read-1", timeout=10000)
    if r:
        if r[0] == 0x06:
            print("  ** Got Vial response (0x06)! Just delayed! **")
        else:
            print(f"  ** Got response but byte 0 = 0x{r[0]:02X} (unexpected) **")
    flush(d, timeout=500)

    # Test B: Send 0xFE, short timeout, then send VIA 0x01, check what comes back
    print("\n=== Test B: 0xFE (1s timeout) then VIA 0x01 ===")
    send_only(d, bytes([0xFE, 0x00]), "Vial 0xFE")
    r = read_only(d, "read-after-0xFE", timeout=1000)  # likely timeout
    print("  Now sending VIA 0x01...")
    send_only(d, bytes([0x01]), "VIA 0x01")
    r = read_only(d, "read-1st", timeout=3000)
    if r:
        if r[0] == 0x06:
            print("  ** First response is Vial (0x06)! Queued response! **")
        elif r[0] == 0x01:
            print("  ** First response is VIA (0x01). Vial response was dropped. **")
    r2 = read_only(d, "read-2nd", timeout=3000)
    if r2:
        print(f"  ** Second response exists! byte0=0x{r2[0]:02X} **")
    flush(d, timeout=500)

    # Test C: Send 0xFE, NO timeout wait, immediately send 0x01
    print("\n=== Test C: 0xFE then immediately 0x01 (no wait) ===")
    send_only(d, bytes([0xFE, 0x00]), "Vial 0xFE")
    time.sleep(0.01)  # 10ms pause
    send_only(d, bytes([0x01]), "VIA 0x01")
    r = read_only(d, "read-1st", timeout=3000)
    if r:
        if r[0] == 0x06:
            print("  ** Got Vial response first! **")
        elif r[0] == 0x01:
            print("  ** Got VIA response first. **")
    r2 = read_only(d, "read-2nd", timeout=3000)
    if r2:
        print(f"  ** Second response: byte0=0x{r2[0]:02X} **")
    flush(d, timeout=500)

    # Test D: Send two 0x01 commands (normal case) to see if responses pair correctly
    print("\n=== Test D: Control — two VIA 0x01 commands ===")
    send_only(d, bytes([0x01]), "VIA 0x01 #1")
    time.sleep(0.01)
    send_only(d, bytes([0x01]), "VIA 0x01 #2")
    r = read_only(d, "read-1st", timeout=3000)
    r2 = read_only(d, "read-2nd", timeout=3000)
    flush(d, timeout=500)

    # Test E: Send 0xFE, read multiple times with 500ms each
    print("\n=== Test E: 0xFE then read 6x at 500ms ===")
    send_only(d, bytes([0xFE, 0x00]), "Vial 0xFE")
    for i in range(6):
        read_only(d, f"read-{i}", timeout=500)
    flush(d, timeout=500)

    # Test F: After all the 0xFE attempts, is there accumulated junk?
    print("\n=== Test F: Clean VIA 0x01 after everything ===")
    sr(d, bytes([0x01]), "VIA 0x01 final")
    flush(d)

    d.close()

    print("\n=== Analysis ===")
    print("Test A: Does 0xFE response arrive with long timeout?")
    print("Test B: Does queued 0xFE response come back with next command?")
    print("Test C: Does 0xFE response arrive when sent back-to-back?")
    print("Test D: Control test for normal request/response pairing")
    print("Test E: Does 0xFE response arrive with extended polling?")


if __name__ == "__main__":
    main()
