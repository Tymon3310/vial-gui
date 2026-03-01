#!/usr/bin/env python3
"""
Focused diagnostic for Vial 0xFE command over bridge.
"""

import sys
import time

try:
    import hidraw as hid
except ImportError:
    import hid

MSG_LEN = 32


def main():
    # Find bridge 0xFF60 interface
    rawhid = None
    for dev in hid.enumerate():
        if (
            dev["vendor_id"] == 0x3434
            and dev["product_id"] in (0xD030, 0xD031)
            and dev["usage_page"] == 0xFF60
            and dev["usage"] == 0x61
        ):
            rawhid = dev
            break

    if not rawhid:
        print("No bridge raw HID found")
        sys.exit(1)

    d = hid.device()
    d.open_path(rawhid["path"])

    # FR handshake (minimal)
    def sr(msg, label, timeout=2000):
        msg = msg + b"\x00" * (MSG_LEN - len(msg))
        d.write(b"\x00" + msg)
        data = bytes(d.read(MSG_LEN, timeout_ms=timeout))
        if data:
            print(f"  {label}: {data[:20].hex(' ')}")
        else:
            print(f"  {label}: TIMEOUT")
        return data

    print("=== FR handshake ===")
    sr(bytes([0xB1]), "B1")
    sr(bytes([0xB2]), "B2")
    sr(bytes([0xB3]), "B3")

    # Flush
    while True:
        data = bytes(d.read(MSG_LEN, timeout_ms=100))
        if not data:
            break
        print(f"  flush: {data[:16].hex(' ')}")

    print("\n=== VIA 0x01 (should work) ===")
    sr(bytes([0x01]), "0x01", timeout=5000)

    print("\n=== Vial 0xFE 0x00 (keyboard ID) ===")
    msg = bytes([0xFE, 0x00]) + b"\x00" * 30
    t0 = time.monotonic()
    d.write(b"\x00" + msg)
    print(f"  TX: fe 00 ...")

    # Try reading with increasing timeouts
    for i in range(5):
        t1 = time.monotonic()
        data = bytes(d.read(MSG_LEN, timeout_ms=3000))
        elapsed = (time.monotonic() - t0) * 1000
        if data:
            print(f"  RX[{i}] ({elapsed:.0f}ms): {data.hex(' ')}")
            break
        else:
            print(f"  RX[{i}] ({elapsed:.0f}ms): timeout")

    # Also try sending and immediately reading without any gap
    print("\n=== Vial 0xFE 0x00 (retry, non-blocking reads first) ===")
    # Flush first
    while bytes(d.read(MSG_LEN, timeout_ms=100)):
        pass

    msg = bytes([0xFE, 0x00]) + b"\x00" * 30
    d.write(b"\x00" + msg)
    print(f"  TX: fe 00 ...")

    # Non-blocking poll for 10 seconds
    t0 = time.monotonic()
    while time.monotonic() - t0 < 10:
        data = bytes(d.read(MSG_LEN, timeout_ms=500))
        elapsed = (time.monotonic() - t0) * 1000
        if data:
            print(f"  RX ({elapsed:.0f}ms): {data.hex(' ')}")
            break
    else:
        print(f"  No response in 10 seconds")

    # Try Vial 0xFE 0x01 (get size) too
    print("\n=== Vial 0xFE 0x01 (get size) ===")
    while bytes(d.read(MSG_LEN, timeout_ms=100)):
        pass
    sr(bytes([0xFE, 0x01]), "0xFE 0x01", timeout=5000)

    # Try 0xFE 0x02 (get def page 0)
    print("\n=== Vial 0xFE 0x02 (get def) ===")
    while bytes(d.read(MSG_LEN, timeout_ms=100)):
        pass
    sr(bytes([0xFE, 0x02, 0x00, 0x00]), "0xFE 0x02", timeout=5000)

    d.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
