#!/usr/bin/env python3
"""Test Vial 0xFE commands over direct USB (not through bridge)."""

import sys
import time

try:
    import hidraw as hid
except ImportError:
    import hid

MSG_LEN = 32


def main():
    # Find V5 Max direct USB (VID=3434, PID=0950, usage 0xFF60/0x61)
    target = None
    for dev in hid.enumerate():
        if (
            dev["vendor_id"] == 0x3434
            and dev["product_id"] == 0x0950
            and dev["usage_page"] == 0xFF60
            and dev["usage"] == 0x61
        ):
            target = dev
            break

    if not target:
        print("No V5 Max direct USB found (is it plugged in via cable?)")
        sys.exit(1)

    print(f"Found V5 Max: path={target['path']}")
    d = hid.device()
    d.open_path(target["path"])

    def sr(msg, label, timeout=2000):
        msg = msg + b"\x00" * (MSG_LEN - len(msg))
        d.write(b"\x00" + msg)
        data = bytes(d.read(MSG_LEN, timeout_ms=timeout))
        if data:
            print(f"  {label}: {data[:20].hex(' ')}")
        else:
            print(f"  {label}: TIMEOUT")
        return data

    print("=== Direct USB tests ===")
    sr(bytes([0x01]), "VIA 0x01")
    sr(bytes([0xA0]), "KC 0xA0")
    sr(bytes([0xFE, 0x00]), "Vial 0xFE 0x00")
    sr(bytes([0xFE, 0x01]), "Vial 0xFE 0x01")
    sr(bytes([0xFE, 0x02, 0x00, 0x00]), "Vial 0xFE 0x02")

    d.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
