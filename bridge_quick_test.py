#!/usr/bin/env python3
"""Quick bridge diagnostic using hidraw (same as GUI)."""

import sys

sys.path.insert(0, "src/main/python")

import hidraw
import struct
import time

MSG_LEN = 32


def find_bridge_path():
    for dev in hidraw.enumerate():
        if (
            dev["vendor_id"] == 0x3434
            and dev["product_id"] in (0xD030, 0xD031)
            and dev["usage_page"] == 0xFF60
            and dev["usage"] == 0x61
        ):
            return dev["path"]
    return None


path = find_bridge_path()
if not path:
    print("No bridge 0xFF60 interface found")
    # Show all 3434 devices
    for dev in hidraw.enumerate():
        if dev["vendor_id"] == 0x3434:
            print(
                f"  VID={dev['vendor_id']:04X} PID={dev['product_id']:04X} "
                f"usage_page=0x{dev['usage_page']:04X} usage=0x{dev['usage']:02X} "
                f"path={dev['path']}"
            )
    sys.exit(1)

print(f"Found bridge at: {path}")

d = hidraw.device()
d.open_path(path)
print("Opened OK")

# Send FR_GET_PROTOCOL_VERSION (0xB1)
msg = struct.pack("B", 0xB1) + b"\x00" * 31
print(f"Sending 0xB1... (write {len(msg) + 1} bytes with report ID)")
t0 = time.monotonic()
written = d.write(b"\x00" + msg)
print(f"  write returned: {written}")

data = bytes(d.read(MSG_LEN, timeout_ms=2000))
elapsed = (time.monotonic() - t0) * 1000
if data:
    print(f"  Response ({elapsed:.0f}ms): {data[:16].hex(' ')}")
    if data[0] == 0xB1:
        ver = data[2] << 8 | data[1]
        print(f"  Protocol version: {ver}, features: 0x{data[3]:02X} 0x{data[4]:02X}")
else:
    print(f"  No response (timeout after {elapsed:.0f}ms)")

# Try a second read in case there's queued data
data2 = bytes(d.read(MSG_LEN, timeout_ms=500))
if data2:
    print(f"  Extra data in buffer: {data2[:16].hex(' ')}")

d.close()
print("Done")
