#!/usr/bin/env python3
"""
Test XOR-encoded wireless Vial communication through the bridge.

This test requires:
  - Firmware built WITH WIRELESS_RAW_HID_XOR_KEY=0x5A (default in wireless.h)
  - Keyboard connected wirelessly via the bridge dongle

Test sequence:
  1. FR handshake (unencoded, handled by bridge)
  2. XOR-encoded VIA commands (tunneled to keyboard)
  3. XOR-encoded Vial 0xFE commands (the ones that previously crashed LKBT51)

Usage: python3 bridge_test7_xor.py
"""

import sys
import time
import hid

MSG_LEN = 32
XOR_KEY = 0x5A


def xor_encode(data):
    """XOR all bytes with the key."""
    padded = data + b"\x00" * (MSG_LEN - len(data))
    return bytes(b ^ XOR_KEY for b in padded)


def xor_decode(data):
    """XOR all bytes with the key (same operation, XOR is symmetric)."""
    return bytes(b ^ XOR_KEY for b in data)


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


def sr_raw(d, msg, label, timeout=3000):
    """Send raw (unencoded) message and read raw response."""
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


def sr_xor(d, msg, label, timeout=3000):
    """Send XOR-encoded message and decode the response."""
    encoded = xor_encode(msg)
    t0 = time.monotonic()
    d.write(b"\x00" + encoded)
    raw_resp = bytes(d.read(MSG_LEN, timeout))
    elapsed = (time.monotonic() - t0) * 1000

    # Handle state notifications (unencoded, from bridge)
    while raw_resp and raw_resp[0] in (0xBC, 0xE2):
        print(f"  [{label}] state notif 0x{raw_resp[0]:02X}, re-reading...")
        raw_resp = bytes(d.read(MSG_LEN, timeout))
        elapsed = (time.monotonic() - t0) * 1000

    if raw_resp:
        decoded = xor_decode(raw_resp)
        print(f"  {label} ({elapsed:.0f}ms):")
        print(f"    wire: {raw_resp[:16].hex(' ')}")
        print(f"    decoded: {decoded[:16].hex(' ')}")
        return decoded
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

    # ── FR Handshake (unencoded — handled by bridge itself) ──
    print("\n=== FR Handshake (unencoded) ===")
    sr_raw(d, bytes([0xB1]), "B1 proto")
    sr_raw(d, bytes([0xB2]), "B2 state")
    sr_raw(d, bytes([0xB3]), "B3 fw")
    flush(d)

    # ── Test 1: XOR-encoded VIA GET_PROTOCOL_VERSION (0x01) ──
    print("\n=== Test 1: XOR VIA 0x01 (GET_PROTOCOL_VERSION) ===")
    r = sr_xor(d, bytes([0x01]), "VIA 0x01")
    if r:
        if r[0] == 0x01:
            via_ver = (r[1] << 8) | r[2]
            print(f"  ** SUCCESS! VIA protocol version: {via_ver} **")
        else:
            print(f"  ** Unexpected byte 0: 0x{r[0]:02X} **")
    flush(d)

    # ── Test 2: XOR-encoded VIA GET_KEYBOARD_VALUE/uptime (0x02 0x01) ──
    print("\n=== Test 2: XOR VIA 0x02 0x01 (uptime) ===")
    r = sr_xor(d, bytes([0x02, 0x01]), "uptime")
    if r:
        if r[0] == 0x02 and r[1] == 0x01:
            uptime = (r[2] << 24) | (r[3] << 16) | (r[4] << 8) | r[5]
            print(f"  ** SUCCESS! Uptime: {uptime}ms ({uptime / 1000:.1f}s) **")
        else:
            print(f"  ** Unexpected response **")
    flush(d)

    # ── Test 3: THE BIG ONE — XOR-encoded Vial 0xFE 0x00 (GET_KEYBOARD_ID) ──
    print(
        "\n=== Test 3: XOR Vial 0xFE 0x00 (GET_KEYBOARD_ID) — previously crashed LKBT51 ==="
    )
    print(
        f"  Sending: 0xFE 0x00 → XOR'd to: 0x{0xFE ^ XOR_KEY:02X} 0x{0x00 ^ XOR_KEY:02X}"
    )
    r = sr_xor(d, bytes([0xFE, 0x00]), "Vial 0xFE 0x00", timeout=5000)
    if r:
        # vial_get_keyboard_id: byte 0 = vial_protocol, bytes 1-8 = keyboard UID
        vial_proto = r[0]
        uid_bytes = r[1:9]
        print(f"  ** SUCCESS!! Vial protocol version: {vial_proto} **")
        print(f"  ** Keyboard UID: {uid_bytes.hex(' ')} **")
        print(f"  ** THE LKBT51 BUG IS BYPASSED! **")
    else:
        print("  ** STILL FAILING — check firmware has XOR decode enabled **")
    flush(d, timeout=500)

    # ── Test 4: Vial 0xFE 0x01 (GET_SIZE) ──
    print("\n=== Test 4: XOR Vial 0xFE 0x01 (GET_SIZE) ===")
    r = sr_xor(d, bytes([0xFE, 0x01]), "Vial 0xFE 0x01", timeout=5000)
    if r:
        size = (r[0] << 24) | (r[1] << 16) | (r[2] << 8) | r[3]
        print(f"  ** SUCCESS! Keyboard definition size: {size} bytes **")
    flush(d, timeout=500)

    # ── Test 5: Vial 0xFE 0x02 (GET_DEF page 0) ──
    print("\n=== Test 5: XOR Vial 0xFE 0x02 p0 (GET_DEF first page) ===")
    r = sr_xor(d, bytes([0xFE, 0x02, 0x00, 0x00]), "Vial 0xFE 0x02", timeout=5000)
    if r:
        print(f"  ** SUCCESS! First 16 bytes of definition: {r[:16].hex(' ')} **")
    flush(d, timeout=500)

    # ── Test 6: Uptime check to confirm keyboard stability ──
    print("\n=== Test 6: Post-Vial uptime check ===")
    r = sr_xor(d, bytes([0x02, 0x01]), "uptime-final")
    if r and r[0] == 0x02 and r[1] == 0x01:
        uptime = (r[2] << 24) | (r[3] << 16) | (r[4] << 8) | r[5]
        print(f"  ** Keyboard stable! Uptime: {uptime}ms ({uptime / 1000:.1f}s) **")
    flush(d)

    # ── Test 7: Multiple rapid Vial commands ──
    print("\n=== Test 7: Rapid-fire Vial commands ===")
    ok = 0
    for i in range(5):
        r = sr_xor(d, bytes([0xFE, 0x00]), f"rapid #{i + 1}", timeout=3000)
        if r and r[0] < 0x10:  # vial protocol version is a small number
            ok += 1
    print(f"  ** {ok}/5 rapid Vial commands succeeded **")
    flush(d, timeout=500)

    d.close()

    print("\n=== Summary ===")
    print(f"XOR key: 0x{XOR_KEY:02X}")
    print("If Tests 3-5 succeeded: LKBT51 bug is fully bypassed!")
    print(
        "If Tests 3-5 timed out: firmware may not have XOR decode, or different issue"
    )


if __name__ == "__main__":
    main()
