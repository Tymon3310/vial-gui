# SPDX-License-Identifier: GPL-2.0-or-later
import struct
import sys

from protocol.constants import CMD_VIA_VIAL_PREFIX, CMD_VIAL_GET_KEYBOARD_ID

if sys.platform == "emscripten":
    import vialglue
    import json

    # Bridge dongle VID/PID pairs (Keychron Link USB-A and USB-C)
    _BRIDGE_PIDS = {0xD030, 0xD031}
    _BRIDGE_VID = 0x3434

    class hiddevice:
        # Class-level bridge state — shared by all instances.
        # On the web there is only one WebHID device at a time, so
        # class-level state is appropriate.
        _bridge_xor_key = 0  # 0 = no bridge / direct USB
        _bridge_active = False
        # Keyboard VID/PID discovered during bridge handshake
        _bridge_kb_vid = 0
        _bridge_kb_pid = 0

        def open_path(self, path):
            print("opening {}...".format(path))

        def close(self):
            pass

        def write(self, data):
            if self._bridge_xor_key and self._bridge_active:
                # data is report_id (1 byte) + payload (32 bytes)
                # XOR-encode only the payload (skip report ID byte 0x00)
                key = self._bridge_xor_key
                encoded = bytes([data[0]]) + bytes(b ^ key for b in data[1:])
                return vialglue.write_device(encoded)
            return vialglue.write_device(data)

        def read(self, length, timeout_ms=0):
            data = vialglue.read_device()
            if self._bridge_xor_key and self._bridge_active and data:
                # Check for unsolicited bridge state notifications (0xBC, 0xE2)
                # BEFORE XOR decoding — these come from the bridge itself
                # (not the keyboard) and are never XOR-encoded.
                from protocol.bridge import FR_STATE_NOTIFY, FR_STATE_NOTIFY_ALT

                if data[0] in (FR_STATE_NOTIFY, FR_STATE_NOTIFY_ALT):
                    print("[bridge-web] Discarding state notification 0x%02X" % data[0])
                    # Return empty data so hid_send() retries the command
                    return b""
                # XOR-decode the response from the keyboard
                key = self._bridge_xor_key
                data = bytes(b ^ key for b in data)
            return data

    def _bridge_send_raw(msg):
        """
        Send a raw 32-byte FR command to the bridge (no XOR encoding).

        Used during bridge handshake before XOR encoding is activated.
        Returns 32 bytes of response, or empty bytes on timeout.

        If an unsolicited 0xBC/0xE2 state notification is received,
        it is discarded and the read is retried (once).
        """
        from util import MSG_LEN
        from protocol.bridge import FR_STATE_NOTIFY, FR_STATE_NOTIFY_ALT

        if len(msg) > MSG_LEN:
            raise RuntimeError("bridge message must be <= 32 bytes")
        msg += b"\x00" * (MSG_LEN - len(msg))
        # Report ID 0 + 32 bytes payload
        vialglue.write_device(b"\x00" + msg)
        data = vialglue.read_device()
        # Filter unsolicited state notifications (re-read once for real response)
        if data and len(data) > 0 and data[0] in (FR_STATE_NOTIFY, FR_STATE_NOTIFY_ALT):
            print("[bridge-web] Discarding state notification 0x%02X" % data[0])
            # The JS read timeout was consumed, so we need to send the
            # command again to get the actual response.
            vialglue.write_device(b"\x00" + msg)
            data = vialglue.read_device()
        return data

    def _bridge_handshake():
        """
        Run the FR handshake with a Keychron bridge dongle.

        Returns (success, kb_vid, kb_pid) where success is True if a
        keyboard is wirelessly connected.
        """
        from protocol.bridge import (
            FR_GET_PROTOCOL_VERSION,
            FR_GET_STATE,
            FR_GET_FW_VERSION,
            FR_CTL_GAMEPAD_RPT_ENABLE,
            BRIDGE_FEAT_VIA_DISABLE_GAMEPAD_INPUT,
        )

        print("[bridge-web] Starting FR handshake")

        # 1. Get protocol version
        data = _bridge_send_raw(struct.pack("B", FR_GET_PROTOCOL_VERSION))
        if not data or data[0] != FR_GET_PROTOCOL_VERSION:
            print("[bridge-web] FR_GET_PROTOCOL_VERSION failed")
            return False, 0, 0
        protocol_version = data[2] << 8 | data[1]
        feature_flags_0 = data[3]
        print(
            "[bridge-web] Protocol version: %d, features: 0x%02X"
            % (protocol_version, feature_flags_0)
        )

        # 2. Get state (paired device slots)
        data = _bridge_send_raw(struct.pack("B", FR_GET_STATE))
        if not data or data[0] != FR_GET_STATE:
            print("[bridge-web] FR_GET_STATE failed")
            return False, 0, 0

        # Parse keyboard slots (3 slots, 5 bytes each, starting at data[2])
        kb_vid = 0
        kb_pid = 0
        found_connected = False
        for i in range(3):
            base = 2 + i * 5
            if base + 5 <= len(data):
                vid = data[base] | (data[base + 1] << 8)
                pid = data[base + 2] | (data[base + 3] << 8)
                connected = bool(data[base + 4])
                print(
                    "[bridge-web] Slot %d: VID=0x%04X PID=0x%04X connected=%s"
                    % (i, vid, pid, connected)
                )
                if connected and not found_connected and (vid != 0 or pid != 0):
                    kb_vid = vid
                    kb_pid = pid
                    found_connected = True

        if not found_connected:
            print("[bridge-web] No keyboard connected to bridge")
            return False, 0, 0

        # 3. Get firmware version (optional)
        data = _bridge_send_raw(struct.pack("B", FR_GET_FW_VERSION))
        if data and data[0] == FR_GET_FW_VERSION:
            fw_ver = data[1:].split(b"\x00")[0].decode("utf-8", errors="ignore")
            print("[bridge-web] Bridge firmware: %s" % fw_ver)

        # 4. Disable gamepad reports if supported
        if feature_flags_0 & BRIDGE_FEAT_VIA_DISABLE_GAMEPAD_INPUT:
            _bridge_send_raw(struct.pack("BB", FR_CTL_GAMEPAD_RPT_ENABLE, 0))
            print("[bridge-web] Disabled gamepad report forwarding")

        # No drain step on web — vialglue.read_device() has no non-blocking
        # mode, and there are no concurrent threads that could leave stale
        # data in the buffer.

        print(
            "[bridge-web] Handshake complete: keyboard VID=0x%04X PID=0x%04X"
            % (kb_vid, kb_pid)
        )
        return True, kb_vid, kb_pid

    class hid:
        @staticmethod
        def enumerate():
            from util import hid_send

            desc = json.loads(vialglue.get_device_desc())

            # Check if the WebHID device is a Keychron bridge dongle
            is_bridge = (
                desc.get("vendor_id") == _BRIDGE_VID
                and desc.get("product_id") in _BRIDGE_PIDS
            )

            if is_bridge:
                print(
                    "[bridge-web] Detected bridge dongle VID=0x%04X PID=0x%04X"
                    % (desc["vendor_id"], desc["product_id"])
                )
                # Run FR handshake to detect connected keyboard
                success, kb_vid, kb_pid = _bridge_handshake()
                if not success:
                    # No keyboard connected — return empty list so the
                    # GUI shows "no devices detected"
                    print("[bridge-web] No keyboard connected, returning empty list")
                    return []

                # Activate XOR encoding for all subsequent communication
                from protocol.bridge import WIRELESS_RAW_HID_XOR_KEY

                hiddevice._bridge_xor_key = WIRELESS_RAW_HID_XOR_KEY
                hiddevice._bridge_active = True
                hiddevice._bridge_kb_vid = kb_vid
                hiddevice._bridge_kb_pid = kb_pid

                # Build a device descriptor that looks like a directly-
                # connected keyboard so find_vial_devices() picks it up
                # as a normal VialKeyboard.
                desc["vendor_id"] = kb_vid
                desc["product_id"] = kb_pid
                # The usage page/usage are already 0xFF60/0x61 (the JS
                # opened the raw HID collection).

                # Probe with a Vial command (XOR-encoded via hiddevice)
                # to detect Vial vs VIA and inject the serial number.
                dev = hid.device()
                try:
                    data = hid_send(
                        dev,
                        struct.pack(
                            "BB", CMD_VIA_VIAL_PREFIX, CMD_VIAL_GET_KEYBOARD_ID
                        ),
                        retries=20,
                    )
                    uid = data[4:12]
                    if uid != b"\x00" * 8:
                        desc["serial_number"] = "vial:f64c2b3c"
                except RuntimeError:
                    # Communication failed — bridge may have dropped
                    print("[bridge-web] Vial probe failed after bridge handshake")
                    hiddevice._bridge_active = False
                    hiddevice._bridge_xor_key = 0
                    return []

                # Mark as bridge-connected for the UI
                desc["product_string"] = (
                    desc.get("product_string", "Keychron Keyboard") + " [2.4G]"
                )
                return [desc]

            # Non-bridge device — original path
            dev = hid.device()
            data = hid_send(
                dev,
                struct.pack("BB", CMD_VIA_VIAL_PREFIX, CMD_VIAL_GET_KEYBOARD_ID),
                retries=20,
            )
            uid = data[4:12]
            if uid != b"\x00" * 8:
                desc["serial_number"] = "vial:f64c2b3c"
            return [desc]

        @staticmethod
        def device():
            return hiddevice()

elif sys.platform.startswith("linux"):
    import hidraw as hid
else:
    import hid
