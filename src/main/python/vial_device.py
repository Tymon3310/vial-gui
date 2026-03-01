# SPDX-License-Identifier: GPL-2.0-or-later
import logging
import time

from hidproxy import hid
from protocol.keyboard_comm import Keyboard
from protocol.dummy_keyboard import DummyKeyboard
from util import MSG_LEN, pad_for_vibl


class VialDevice:
    def __init__(self, dev):
        self.desc = dev
        self.dev = None
        self.sideload = False
        self.via_stack = False

    def open(self, override_json=None):
        self.dev = hid.device()
        for x in range(10):
            try:
                self.dev.open_path(self.desc["path"])
                return
            except OSError:
                time.sleep(1)
        raise RuntimeError("unable to open the device")

    def send(self, data):
        # add 00 at start for hidapi report id
        return self.dev.write(b"\x00" + data)

    def recv(self, length, timeout_ms=0):
        return bytes(self.dev.read(length, timeout_ms=timeout_ms))

    def close(self):
        self.dev.close()


class VialKeyboard(VialDevice):
    def __init__(self, dev, sideload=False, via_stack=False):
        super().__init__(dev)
        self.via_id = str(dev["vendor_id"] * 65536 + dev["product_id"])
        self.sideload = sideload
        self.via_stack = via_stack
        self.keyboard = None

    def open(self, override_json=None):
        super().open(override_json)
        self.keyboard = Keyboard(self.dev)
        self.keyboard.reload(override_json)

    def title(self):
        s = "{} {}".format(
            self.desc["manufacturer_string"], self.desc["product_string"]
        ).strip()
        if self.sideload:
            s += " [sideload]"
        elif self.via_stack:
            s += " [VIA]"
        return s

    def get_uid(self):
        try:
            super().open()
        except OSError:
            return b""
        try:
            self.send(b"\xfe\x00" + b"\x00" * 30)
            data = self.recv(MSG_LEN, timeout_ms=500)
            return data[4:12]
        finally:
            super().close()


class VialBridgeKeyboard(VialKeyboard):
    """
    A wireless keyboard connected through a Keychron 2.4 GHz bridge/dongle.

    Supported dongles (detected via usage page 0x8C on one interface):
      - Keychron Link USB-A  (VID 0x3434, PID 0xD030)
      - Keychron Link USB-C  (VID 0x3434, PID 0xD031)

    Detection uses the 0x8C interface, but actual communication goes through
    the sibling 0xFF60/0x61 raw HID interface on the same USB device.

    This class inherits VialKeyboard so all isinstance() checks pass
    and existing editors work without modification.
    """

    def __init__(self, bridge_detect_desc, rawhid_desc):
        # Call VialDevice.__init__ directly -- VialKeyboard.__init__ would
        # try to compute via_id from the bridge descriptor which is fine,
        # but we want to set our own attributes.
        VialDevice.__init__(self, rawhid_desc)
        self.sideload = False
        self.via_stack = False
        self.keyboard = None
        self.bridge = None  # BridgeDevice instance (set during open)
        self._bridge_detect_desc = bridge_detect_desc  # 0x8C descriptor (for detection)
        self._rawhid_desc = rawhid_desc  # 0xFF60 descriptor (for communication)
        # Use bridge VID/PID initially; updated to keyboard's after probe
        self.via_id = str(rawhid_desc["vendor_id"] * 65536 + rawhid_desc["product_id"])

    def probe(self):
        """
        Open the bridge's raw HID interface, run FR handshake, check for a
        connected keyboard.

        Returns True if a keyboard is wirelessly connected.
        The HID device is closed after probing (reopened in open()).
        """
        from protocol.bridge import BridgeDevice

        dev = None
        try:
            dev = hid.device()
            dev.open_path(self._rawhid_desc["path"])
            bridge = BridgeDevice(dev)
            has_device = bridge.initialize()
            if has_device:
                vid, pid = bridge.get_connected_device_info()
                if vid != 0 or pid != 0:
                    self.via_id = str(vid * 65536 + pid)
            dev.close()
            return has_device
        except Exception as e:
            logging.warning("Bridge probe error: %s", e)
            if dev is not None:
                try:
                    dev.close()
                except Exception:
                    pass
            return False

    def open(self, override_json=None):
        """Open the bridge's raw HID interface and set up VIA tunneling."""
        from protocol.bridge import BridgeDevice
        from util import _bridge_probe_lock

        # Hold the bridge probe lock for the entire open() so that no
        # concurrent find_vial_devices() probe can open the same HID
        # device and steal reads.
        with _bridge_probe_lock:
            self._open_locked(override_json)

    def _open_locked(self, override_json):
        """Inner open — must be called with _bridge_probe_lock held."""
        from protocol.bridge import BridgeDevice

        # Open bridge raw HID device (0xFF60 interface)
        self.dev = hid.device()
        for x in range(10):
            try:
                self.dev.open_path(self._rawhid_desc["path"])
                break
            except OSError:
                if x == 9:
                    raise RuntimeError("unable to open the bridge device")
                time.sleep(1)

        # Run FR handshake
        self.bridge = BridgeDevice(self.dev)
        if not self.bridge.initialize():
            raise RuntimeError("No wireless keyboard connected to the bridge")

        # Drain any stale data in the HID read buffer.  The FR handshake or
        # a concurrent probe may have left unsolicited responses/state
        # notifications that would confuse the first XOR-encoded VIA command.
        drained = 0
        while True:
            stale = bytes(self.dev.read(MSG_LEN, timeout_ms=50))
            if not stale:
                break
            drained += 1
            if drained > 20:
                break
        if drained:
            logging.info("Bridge open: drained %d stale HID reports", drained)

        # Update VID/PID from the connected keyboard
        vid, pid = self.bridge.get_connected_device_info()
        if vid != 0 or pid != 0:
            self.via_id = str(vid * 65536 + pid)

        logging.info(
            "Bridge: connected to wireless keyboard VID=%04X PID=%04X",
            vid,
            pid,
        )

        # Create a transport function that tunnels through the bridge.
        # Same signature as util.hid_send(dev, msg, retries=1).
        # Cap retries at 5 — the USB-side callers request retries=20 which
        # is appropriate for direct USB (0.5 s delay each) but excessive for
        # wireless (0.1 s delay × 1 s read timeout → 22 s worst case).
        def bridge_usb_send(dev, msg, retries=1):
            return self.bridge.usb_send(msg, retries=min(retries, 5))

        # Create Keyboard protocol object using the bridge transport
        self.keyboard = Keyboard(self.dev, usb_send=bridge_usb_send)
        self.keyboard.reload(override_json)

        # Mark the keyboard as wirelessly connected (0 = 2.4 GHz)
        if hasattr(self.keyboard, "keychron_connection_mode"):
            self.keyboard.keychron_connection_mode = 0  # CONNECTION_MODE_24G

    def title(self):
        """Display title showing this is a wirelessly-connected device."""
        # Prefer the real keyboard name from the Vial definition JSON
        if self.keyboard and self.keyboard.definition:
            name = self.keyboard.definition.get("name", "")
            if name:
                return "{} [2.4G]".format(name)
        # Fallback to dongle descriptor (before open() completes)
        s = "{} {}".format(
            self._rawhid_desc.get("manufacturer_string", ""),
            self._rawhid_desc.get("product_string", ""),
        ).strip()
        if not s:
            s = "Keychron Keyboard"
        s += " [2.4G]"
        return s

    def get_uid(self):
        """Get the UID of the wirelessly-connected keyboard via the bridge."""
        from protocol.bridge import BridgeDevice
        import struct
        from protocol.constants import (
            CMD_VIA_VIAL_PREFIX,
            CMD_VIAL_GET_KEYBOARD_ID,
        )

        dev = None
        try:
            dev = hid.device()
            dev.open_path(self._rawhid_desc["path"])
            bridge = BridgeDevice(dev)
            if not bridge.initialize():
                dev.close()
                return b""
            msg = struct.pack("BB", CMD_VIA_VIAL_PREFIX, CMD_VIAL_GET_KEYBOARD_ID)
            data = bridge.usb_send(msg, retries=3)
            dev.close()
            return data[4:12]
        except Exception as e:
            logging.warning("Bridge get_uid error: %s", e)
            if dev is not None:
                try:
                    dev.close()
                except Exception:
                    pass
            return b""

    def close(self):
        """Close the bridge connection."""
        if self.dev is not None:
            try:
                self.dev.close()
            except Exception:
                pass
        self.bridge = None
        self.dev = None


class VialBootloader(VialDevice):
    def title(self):
        return "Vial Bootloader [{:04X}:{:04X}]".format(
            self.desc["vendor_id"], self.desc["product_id"]
        )

    def get_uid(self):
        try:
            super().open()
        except OSError:
            return b""
        try:
            self.send(pad_for_vibl(b"VC\x01"))
            data = self.recv(8, timeout_ms=500)
            return data
        finally:
            super().close()


class VialDummyKeyboard(VialKeyboard):
    def __init__(self):
        self.sideload = True
        self.via_stack = False
        self.via_id = "0"
        self.keyboard = None
        self.dev = None
        self.desc = {"path": "/dummy/keyboard"}

    def open(self, override_json=None):
        self.keyboard = DummyKeyboard(None, usb_send=self.raise_usb_send)
        self.keyboard.reload(override_json)

    def title(self):
        return "[Dummy Keyboard]"

    def raise_usb_send(self, *args, **kwargs):
        raise RuntimeError("usb_send - should not be called!")

    def close(self):
        pass
