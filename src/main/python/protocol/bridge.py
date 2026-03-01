# SPDX-License-Identifier: GPL-2.0-or-later
"""
Keychron 2.4 GHz Bridge/Dongle (Forza Receiver) protocol implementation.

This module handles communication with Keychron wireless keyboards through
the 2.4 GHz USB dongle (the "bridge").

The bridge exposes multiple HID collections:
  - Usage page 0x8C, usage 0x01 — detection/enumeration only (report IDs 0xB1/0xB2)
  - Usage page 0xFF60, usage 0x61 — actual communication (no report IDs, 32-byte I/O)

All FR commands AND VIA tunneling go through the 0xFF60 raw HID interface,
using the same report ID 0 (no report ID) protocol as regular keyboards.
The bridge firmware routes based on the first data byte:
  - 0xB1-0xBA → FR commands (handled by bridge itself)
  - Everything else → forwarded to keyboard via 2.4 GHz

Protocol summary:
  Host  ---[hidapi (0xFF60)]-->  Bridge  --[2.4 GHz]-->  Keyboard
  Host  <--[hidapi (0xFF60)]---  Bridge  <-[2.4 GHz]---  Keyboard

FR (Forza Receiver) commands:
  0xB1  Get Protocol Version + feature flags
  0xB2  Get State (paired device slots)
  0xB3  Get Firmware Version
  0xB5  Gamepad Report Enable/Disable
  0xBA  DFU Over VIA

State notifications (unsolicited):
  0xBC  Device connect/disconnect events

See docs/launcher/bridge-dongle-protocol.md for full details.
"""

import logging
import struct
import time

from util import MSG_LEN

# ── FR Command IDs ──────────────────────────────────────────────────────────

FR_GET_PROTOCOL_VERSION = 0xB1
FR_GET_STATE = 0xB2
FR_GET_FW_VERSION = 0xB3
FR_CTL_GAMEPAD_RPT_ENABLE = 0xB5
FR_DFU_OVER_VIA = 0xBA

# ── State notification marker ──────────────────────────────────────────────

FR_STATE_NOTIFY = 0xBC
FR_STATE_NOTIFY_ALT = 0xE2  # alternate marker seen in some firmware versions

# ── Bridge HID interface ───────────────────────────────────────────────────

BRIDGE_USAGE_PAGE = 0x8C  # 140 decimal
BRIDGE_USAGE = 0x01

# ── Feature flags (from FR_GET_PROTOCOL_VERSION response byte 3) ───────────

BRIDGE_FEAT_STATE_NOTIFY_OVER_VIA = 0x80  # bit 7
BRIDGE_FEAT_MULTI_DEVICE_CONNECT = 0x40  # bit 6
BRIDGE_FEAT_MOUSE_DRIVER_OVER_VIA = 0x20  # bit 5
BRIDGE_FEAT_VIA_DISABLE_GAMEPAD_INPUT = 0x10  # bit 4

# ── Connection modes ───────────────────────────────────────────────────────

CONNECTION_MODE_24G = 0
CONNECTION_MODE_BT = 1
CONNECTION_MODE_USB = 2

CONNECTION_MODE_NAMES = {
    CONNECTION_MODE_24G: "2.4 GHz",
    CONNECTION_MODE_BT: "Bluetooth",
    CONNECTION_MODE_USB: "USB",
}

# ── XOR encoding for wireless raw HID ─────────────────────────────────────
# The LKBT51 wireless module crashes when certain byte values (e.g. 0xFE)
# appear in raw HID packets.  XOR-encoding all bytes avoids the problematic
# values.  Must match WIRELESS_RAW_HID_XOR_KEY on the firmware side.

WIRELESS_RAW_HID_XOR_KEY = 0x28


def _xor_encode(data, key=WIRELESS_RAW_HID_XOR_KEY):
    """XOR all bytes in *data* with *key* (returns a new bytes object)."""
    return bytes(b ^ key for b in data)


# ── Device slot ────────────────────────────────────────────────────────────


class BridgeDeviceSlot:
    """Represents a paired device slot on the bridge/receiver."""

    def __init__(self, vid=0, pid=0, connected=False):
        self.vid = vid
        self.pid = pid
        self.connected = connected

    def is_empty(self):
        return self.vid == 0 and self.pid == 0

    def __repr__(self):
        status = "connected" if self.connected else "disconnected"
        if self.is_empty():
            status = "empty"
        return f"BridgeDeviceSlot(vid=0x{self.vid:04X}, pid=0x{self.pid:04X}, {status})"


class BridgeDevice:
    """
    Manages communication with a Keychron 2.4 GHz bridge/dongle.

    Communication goes through the bridge's raw HID interface (usage page
    0xFF60, usage 0x61) — the same collection type as regular keyboards.
    The bridge is *detected* via usage page 0x8C, but actual I/O uses the
    sibling 0xFF60 interface on the same USB device.

    Usage::

        bridge = BridgeDevice(hid_dev)
        if bridge.initialize():
            # Now use bridge.usb_send() to tunnel VIA commands
            data = bridge.usb_send(msg)
    """

    def __init__(self, hid_dev):
        """
        Args:
            hid_dev: An opened hidapi device object for the bridge's
                     0xFF60 raw HID interface.
        """
        self.dev = hid_dev

        # Bridge state
        self.protocol_version = 0
        self.feature_flags_0 = 0
        self.feature_flags_1 = 0
        self.firmware_version = ""
        self.slots = []  # list of BridgeDeviceSlot
        self.connected_slot = None  # index of the first connected slot

        # State notification support
        self._supports_state_notify = False

    def _send_raw(self, msg, retries=3):
        """Send a 32-byte message to the bridge and read a 32-byte response."""
        if len(msg) > MSG_LEN:
            raise RuntimeError("bridge message must be <= 32 bytes")
        msg += b"\x00" * (MSG_LEN - len(msg))

        data = b""
        first = True
        attempt = 0

        while retries > 0:
            retries -= 1
            attempt += 1
            if not first:
                time.sleep(0.1)
            first = False
            try:
                # Report ID 0 + 32 bytes
                written = self.dev.write(b"\x00" + msg)
                if written != MSG_LEN + 1:
                    logging.warning(
                        "Bridge _send_raw: write returned %d (attempt %d)",
                        written,
                        attempt,
                    )
                    continue
                data = bytes(self.dev.read(MSG_LEN, timeout_ms=1000))
                if not data:
                    logging.warning(
                        "Bridge _send_raw: read timeout (attempt %d, cmd=0x%02X)",
                        attempt,
                        msg[0],
                    )
                    continue
                # Filter out unsolicited state notifications
                if len(data) > 0 and data[0] in (FR_STATE_NOTIFY, FR_STATE_NOTIFY_ALT):
                    self._handle_state_notify(data)
                    # Re-read for actual response
                    data = bytes(self.dev.read(MSG_LEN, timeout_ms=1000))
                    if not data:
                        logging.warning(
                            "Bridge _send_raw: read timeout after state notify "
                            "(attempt %d, cmd=0x%02X)",
                            attempt,
                            msg[0],
                        )
                        continue
            except OSError as e:
                logging.debug("Bridge HID error (retry %d): %s", retries, e)
                continue
            break

        if not data:
            raise RuntimeError("failed to communicate with the bridge")
        return data

    def _handle_state_notify(self, data):
        """Handle an unsolicited 0xBC state notification from the bridge."""
        if len(data) < 4:
            return
        logging.info(
            "Bridge state notification: kb1=%d, kb2=%d, mouse_bit=%d",
            data[1],
            data[2],
            (data[3] >> 2) & 1,
        )
        # Update slot connection status if we have slots
        if len(self.slots) >= 2:
            self.slots[0].connected = bool(data[1])
            self.slots[1].connected = bool(data[2])
        self._update_connected_slot()

    def _update_connected_slot(self):
        """Find the first connected slot."""
        self.connected_slot = None
        for i, slot in enumerate(self.slots):
            if slot.connected and not slot.is_empty():
                self.connected_slot = i
                break

    def initialize(self):
        """
        Perform bridge handshake: get protocol version, state, and firmware version.

        Returns:
            True if the bridge has at least one connected device.
        """
        logging.info("Bridge: starting initialization")

        # 1. Get protocol version and feature flags
        try:
            data = self._send_raw(struct.pack("B", FR_GET_PROTOCOL_VERSION))
            if data[0] == FR_GET_PROTOCOL_VERSION:
                self.protocol_version = data[2] << 8 | data[1]
                self.feature_flags_0 = data[3]
                self.feature_flags_1 = data[4] if len(data) > 4 else 0
                self._supports_state_notify = bool(
                    self.feature_flags_0 & BRIDGE_FEAT_STATE_NOTIFY_OVER_VIA
                )
                logging.info(
                    "Bridge: protocol version %d, features 0x%02X 0x%02X, "
                    "state_notify=%s",
                    self.protocol_version,
                    self.feature_flags_0,
                    self.feature_flags_1,
                    self._supports_state_notify,
                )
        except RuntimeError as e:
            logging.warning("Bridge: failed to get protocol version: %s", e)
            return False

        # 2. Get paired device slots
        try:
            data = self._send_raw(struct.pack("B", FR_GET_STATE))
            if data[0] == FR_GET_STATE:
                self.slots = []
                # 3 keyboard slots, each 5 bytes starting at data[2]
                for i in range(3):
                    base = 2 + i * 5
                    if base + 5 <= len(data):
                        vid = data[base] | (data[base + 1] << 8)  # LE
                        pid = data[base + 2] | (data[base + 3] << 8)  # LE
                        connected = bool(data[base + 4])
                        slot = BridgeDeviceSlot(vid, pid, connected)
                        self.slots.append(slot)
                        logging.info("Bridge: slot %d: %s", i, slot)

                self._update_connected_slot()
                logging.info("Bridge: connected slot: %s", self.connected_slot)
        except RuntimeError as e:
            logging.warning("Bridge: failed to get state: %s", e)
            return False

        # 3. Get firmware version (optional, don't fail on this)
        try:
            data = self._send_raw(struct.pack("B", FR_GET_FW_VERSION))
            if data[0] == FR_GET_FW_VERSION:
                self.firmware_version = (
                    data[1:].split(b"\x00")[0].decode("utf-8", errors="ignore")
                )
                logging.info("Bridge: firmware version: %s", self.firmware_version)
        except RuntimeError:
            pass

        # 4. Disable gamepad reports if supported (avoids phantom joystick inputs)
        if self.feature_flags_0 & BRIDGE_FEAT_VIA_DISABLE_GAMEPAD_INPUT:
            try:
                self._send_raw(struct.pack("BB", FR_CTL_GAMEPAD_RPT_ENABLE, 0))
                logging.info("Bridge: disabled gamepad report forwarding")
            except RuntimeError:
                pass

        return self.connected_slot is not None

    def has_connected_device(self):
        """Check if the bridge has a wirelessly-connected device."""
        return self.connected_slot is not None

    def get_connected_device_info(self):
        """
        Get VID/PID of the connected device.

        Returns:
            (vid, pid) tuple, or (0, 0) if no device is connected.
        """
        if self.connected_slot is not None and self.connected_slot < len(self.slots):
            slot = self.slots[self.connected_slot]
            return (slot.vid, slot.pid)
        return (0, 0)

    def usb_send(self, msg, retries=3):
        """
        Tunnel a VIA/Vial HID command through the bridge to the keyboard.

        This method has the same signature as util.hid_send() so it can be
        used as a drop-in replacement for Keyboard's usb_send.

        All data is XOR-encoded before sending (and responses are decoded)
        to avoid byte values that crash the LKBT51 wireless module.

        Args:
            msg: The raw HID message (up to 32 bytes).
            retries: Number of retry attempts.

        Returns:
            32 bytes of response data from the keyboard (tunneled back
            through the bridge).
        """
        if not self.has_connected_device():
            raise RuntimeError("No device connected to bridge")
        padded = msg + b"\x00" * (MSG_LEN - len(msg))
        encoded = _xor_encode(padded)
        raw_resp = self._send_raw(encoded, retries=retries)
        return _xor_encode(raw_resp)

    def supports_state_notify(self):
        """Check if the bridge supports real-time connect/disconnect notifications."""
        return self._supports_state_notify

    def poll_state(self):
        """
        Non-blocking check for unsolicited state notification.

        Returns:
            True if state changed, False otherwise.
        """
        try:
            data = bytes(self.dev.read(MSG_LEN, timeout_ms=0))
            if (
                data
                and len(data) > 0
                and data[0] in (FR_STATE_NOTIFY, FR_STATE_NOTIFY_ALT)
            ):
                old_slot = self.connected_slot
                self._handle_state_notify(data)
                return self.connected_slot != old_slot
        except OSError:
            pass
        return False
