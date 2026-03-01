#!/usr/bin/env python3
"""
Diagnostic script for Keychron bridge VIA tunneling.
Tests FR handshake then attempts VIA commands with verbose output.

Usage: sudo python3 bridge_test.py
"""

import sys
import time

# hidraw is the Linux hidapi backend used by vial-gui
try:
    import hidraw as hid
except ImportError:
    import hid

MSG_LEN = 32
BRIDGE_VID = 0x3434
BRIDGE_PIDS = [0xD030, 0xD031]


def find_bridge_rawhid():
    """Find the bridge's 0xFF60 raw HID interface."""
    bridge_detect = None
    for dev in hid.enumerate():
        if (
            dev["vendor_id"] == BRIDGE_VID
            and dev["product_id"] in BRIDGE_PIDS
            and dev["usage_page"] == 0x8C
            and dev["usage"] == 0x01
        ):
            bridge_detect = dev
            break

    if bridge_detect is None:
        print("ERROR: No bridge device found (usage page 0x8C)")
        return None

    print(
        f"Bridge detected: VID={bridge_detect['vendor_id']:04X} "
        f"PID={bridge_detect['product_id']:04X} "
        f"path={bridge_detect['path']}"
    )

    # Find sibling 0xFF60 interface
    for dev in hid.enumerate():
        if (
            dev["vendor_id"] == bridge_detect["vendor_id"]
            and dev["product_id"] == bridge_detect["product_id"]
            and dev["usage_page"] == 0xFF60
            and dev["usage"] == 0x61
        ):
            print(f"Raw HID interface: path={dev['path']}")
            return dev

    print("ERROR: No sibling 0xFF60 interface found")
    return None


def send_recv(dev, msg, label, timeout_ms=3000):
    """Send a message and read response with verbose output."""
    if len(msg) > MSG_LEN:
        raise RuntimeError("message too long")
    msg_padded = msg + b"\x00" * (MSG_LEN - len(msg))

    print(f"\n--- {label} ---")
    print(f"  TX ({len(msg)} bytes): {msg_padded[:16].hex(' ')}")

    t0 = time.monotonic()
    written = dev.write(b"\x00" + msg_padded)
    t_write = time.monotonic() - t0
    print(f"  write() returned {written} in {t_write * 1000:.1f}ms")

    if written != MSG_LEN + 1:
        print(f"  ERROR: expected {MSG_LEN + 1}, got {written}")
        return None

    # Try multiple reads to catch delayed responses
    for attempt in range(3):
        t0 = time.monotonic()
        data = bytes(dev.read(MSG_LEN, timeout_ms=timeout_ms))
        t_read = time.monotonic() - t0
        if data:
            print(
                f"  RX ({len(data)} bytes, {t_read * 1000:.1f}ms, attempt {attempt}): "
                f"{data[:16].hex(' ')}"
            )
            # Check for state notifications
            if data[0] in (0xBC, 0xE2):
                print(f"  (state notification 0x{data[0]:02X}, reading again...)")
                continue
            return data
        else:
            print(f"  RX: timeout after {t_read * 1000:.1f}ms (attempt {attempt})")
            if attempt < 2:
                print(f"  Retrying read...")
    return None


def main():
    desc = find_bridge_rawhid()
    if desc is None:
        sys.exit(1)

    dev = hid.device()
    dev.open_path(desc["path"])
    print(f"Opened {desc['path']}")

    # --- FR Handshake ---
    resp = send_recv(dev, bytes([0xB1]), "FR_GET_PROTOCOL_VERSION")
    if resp and resp[0] == 0xB1:
        ver = (resp[2] << 8) | resp[1]
        print(f"  Protocol version: {ver}, features: 0x{resp[3]:02X} 0x{resp[4]:02X}")
    else:
        print("  UNEXPECTED RESPONSE")

    resp = send_recv(dev, bytes([0xB2]), "FR_GET_STATE")
    if resp and resp[0] == 0xB2:
        for i in range(3):
            base = 2 + i * 5
            vid = resp[base] | (resp[base + 1] << 8)
            pid = resp[base + 2] | (resp[base + 3] << 8)
            conn = resp[base + 4]
            status = "connected" if conn else ("empty" if vid == 0 else "disconnected")
            print(f"  Slot {i}: VID=0x{vid:04X} PID=0x{pid:04X} ({status})")
    else:
        print("  UNEXPECTED RESPONSE")

    resp = send_recv(dev, bytes([0xB3]), "FR_GET_FW_VERSION")
    if resp and resp[0] == 0xB3:
        fw = resp[1:].split(b"\x00")[0].decode("utf-8", errors="ignore")
        print(f"  Firmware: {fw}")

    # --- Flush any pending data ---
    print("\n--- Flushing buffer ---")
    for i in range(5):
        data = bytes(dev.read(MSG_LEN, timeout_ms=100))
        if data:
            print(f"  Flushed: {data[:16].hex(' ')}")
        else:
            print(f"  Buffer clean after {i} reads")
            break

    # --- VIA Tunneling ---
    print("\n=== VIA TUNNELING TESTS (5s timeout) ===")

    resp = send_recv(dev, bytes([0x01]), "VIA GET_PROTOCOL_VERSION", timeout_ms=5000)
    if resp:
        if resp[0] == 0x01:
            via_ver = (resp[1] << 8) | resp[2]
            print(f"  SUCCESS! VIA protocol version: {via_ver}")
        elif resp[0] in (0xB1, 0xB2, 0xB3):
            print(f"  STALE FR RESPONSE (echo bug still present)")
        else:
            print(f"  Unexpected first byte: 0x{resp[0]:02X}")

    resp = send_recv(dev, bytes([0xFE, 0x00]), "Vial GET_KEYBOARD_ID", timeout_ms=5000)
    if resp:
        if resp[0] == 0xFE:
            uid = resp[4:12].hex()
            print(f"  SUCCESS! Vial UID: {uid}")
        elif resp[0] in (0xB1, 0xB2, 0xB3):
            print(f"  STALE FR RESPONSE")
        else:
            print(f"  Unexpected first byte: 0x{resp[0]:02X}")

    # Also try Keychron-specific command
    resp = send_recv(dev, bytes([0xA0]), "KC_GET_PROTOCOL_VERSION", timeout_ms=5000)
    if resp:
        if resp[0] == 0xA0:
            kc_ver = (resp[1] << 8) | resp[2]
            print(f"  SUCCESS! Keychron protocol version: {kc_ver}")
        else:
            print(f"  First byte: 0x{resp[0]:02X}")

    dev.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
