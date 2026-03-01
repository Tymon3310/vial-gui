# SPDX-License-Identifier: GPL-2.0-or-later
import logging
import os
import pathlib
import sys
import time
import threading
from logging.handlers import RotatingFileHandler

from PyQt5.QtCore import QCoreApplication, QStandardPaths
from PyQt5.QtGui import QPalette
from PyQt5.QtWidgets import QApplication, QWidget, QScrollArea, QFrame

from hidproxy import hid
from keycodes.keycodes import Keycode
from keymaps import KEYMAPS

tr = QCoreApplication.translate

# For Vial keyboard
VIAL_SERIAL_NUMBER_MAGIC = "vial:f64c2b3c"

# For bootloader
VIBL_SERIAL_NUMBER_MAGIC = "vibl:d4f8159c"

MSG_LEN = 32

# these should match what we have in vial-qmk/keyboards/vial_example
# so that people don't accidentally reuse a sample keyboard UID
EXAMPLE_KEYBOARDS = [
    0xD4A36200603E3007,  # vial_stm32f103_vibl
    0x32F62BC2EEF2237B,  # vial_atmega32u4
    0x38CEA320F23046A5,  # vial_stm32f072
    0xBED2D31EC59A0BD8,  # vial_stm32f401
]

# anything starting with this prefix should not be allowed
EXAMPLE_KEYBOARD_PREFIX = 0xA6867BDFD3B00F


def hid_send(dev, msg, retries=1):
    if len(msg) > MSG_LEN:
        raise RuntimeError("message must be less than 32 bytes")
    msg += b"\x00" * (MSG_LEN - len(msg))

    data = b""
    first = True

    while retries > 0:
        retries -= 1
        if not first:
            # emscripten (vial-web) runs single-threaded without ASYNCIFY;
            # time.sleep() would call emscripten_sleep and crash the runtime.
            # The JS spinlock in vialglue_read_device handles waiting, so
            # skipping the sleep here is safe.
            if sys.platform != "emscripten":
                time.sleep(0.5)
        first = False
        try:
            # add 00 at start for hidapi report id
            if dev.write(b"\x00" + msg) != MSG_LEN + 1:
                continue

            data = bytes(dev.read(MSG_LEN, timeout_ms=500))
            if not data:
                continue
        except OSError as e:
            logging.debug("HID communication error (retry %d): %s", retries, e)
            continue
        break

    if not data:
        raise RuntimeError("failed to communicate with the device")
    return data


def is_rawhid(desc, quiet):
    if desc["usage_page"] != 0xFF60 or desc["usage"] != 0x61:
        if not quiet:
            logging.warning(
                "is_rawhid: {} does not match - usage_page={:04X} usage={:02X}".format(
                    desc["path"], desc["usage_page"], desc["usage"]
                )
            )
        return False

    # there's no reason to check for permission issues on mac or windows
    # and mac won't let us reopen an opened device
    # so skip the rest of the checks for non-linux
    if not sys.platform.startswith("linux"):
        return True

    dev = hid.device()

    try:
        dev.open_path(desc["path"])
    except OSError as e:
        if not quiet:
            logging.warning(
                "is_rawhid: {} does not match - open_path error {}".format(
                    desc["path"], e
                )
            )
        return False

    dev.close()
    return True


def is_bridge_hid(desc, quiet=False):
    """Check if a HID device descriptor matches a Keychron 2.4 GHz bridge/dongle."""
    from protocol.bridge import BRIDGE_USAGE_PAGE, BRIDGE_USAGE

    if desc["usage_page"] != BRIDGE_USAGE_PAGE or desc["usage"] != BRIDGE_USAGE:
        return False
    if not quiet:
        logging.info(
            "is_bridge_hid: %s matches bridge (usage_page=0x%04X, usage=0x%02X)",
            desc["path"],
            desc["usage_page"],
            desc["usage"],
        )
    return True


# Cache for bridge probe results: path -> (result, via_id, timestamp)
# Positive results are cached permanently (re-probing would conflict with
# an active HID connection).  Negative results expire after a TTL so the
# bridge is re-probed once a keyboard connects wirelessly.
_bridge_probe_cache = {}
_bridge_probe_lock = threading.Lock()
_BRIDGE_NEGATIVE_CACHE_TTL = 5  # seconds


def find_vial_devices(
    via_stack_json,
    sideload_vid=None,
    sideload_pid=None,
    quiet=False,
    active_bridge_path=None,
):
    from vial_device import (
        VialBootloader,
        VialKeyboard,
        VialDummyKeyboard,
        VialBridgeKeyboard,
    )

    filtered = []
    bridge_descs = []  # collect bridge descriptors for a second pass
    bridge_rawhid_paths = set()  # track active bridge paths for cache eviction

    for dev in hid.enumerate():
        if dev["vendor_id"] == sideload_vid and dev["product_id"] == sideload_pid:
            if not quiet:
                logging.info(
                    "Trying VID={:04X}, PID={:04X}, serial={}, path={} - sideload".format(
                        dev["vendor_id"],
                        dev["product_id"],
                        dev["serial_number"],
                        dev["path"],
                    )
                )
            if is_rawhid(dev, quiet):
                filtered.append(VialKeyboard(dev, sideload=True))
        elif VIAL_SERIAL_NUMBER_MAGIC in dev["serial_number"]:
            if not quiet:
                logging.info(
                    "Matching VID={:04X}, PID={:04X}, serial={}, path={} - vial serial magic".format(
                        dev["vendor_id"],
                        dev["product_id"],
                        dev["serial_number"],
                        dev["path"],
                    )
                )
            if is_rawhid(dev, quiet):
                filtered.append(VialKeyboard(dev))
        elif VIBL_SERIAL_NUMBER_MAGIC in dev["serial_number"]:
            if not quiet:
                logging.info(
                    "Matching VID={:04X}, PID={:04X}, serial={}, path={} - vibl serial magic".format(
                        dev["vendor_id"],
                        dev["product_id"],
                        dev["serial_number"],
                        dev["path"],
                    )
                )
            filtered.append(VialBootloader(dev))
        elif (
            str(dev["vendor_id"] * 65536 + dev["product_id"])
            in via_stack_json["definitions"]
        ):
            if not quiet:
                logging.info(
                    "Matching VID={:04X}, PID={:04X}, serial={}, path={} - VIA stack".format(
                        dev["vendor_id"],
                        dev["product_id"],
                        dev["serial_number"],
                        dev["path"],
                    )
                )
            if is_rawhid(dev, quiet):
                filtered.append(VialKeyboard(dev, via_stack=True))
        elif is_bridge_hid(dev, quiet):
            # Keychron 2.4 GHz bridge/dongle -- collect for second pass
            bridge_descs.append(dev)

    # Second pass: for each bridge, find sibling 0xFF60 interface and probe.
    # Use a lock to prevent concurrent find_vial_devices() calls (from the
    # autorefresh thread and the main thread) from both probing the same
    # bridge simultaneously, which corrupts the HID communication.
    with _bridge_probe_lock:
        for bridge_desc in bridge_descs:
            try:
                # Find the 0xFF60/0x61 raw HID interface on the same VID/PID
                rawhid_desc = None
                for dev in hid.enumerate():
                    if (
                        dev["vendor_id"] == bridge_desc["vendor_id"]
                        and dev["product_id"] == bridge_desc["product_id"]
                        and dev["usage_page"] == 0xFF60
                        and dev["usage"] == 0x61
                    ):
                        rawhid_desc = dev
                        break
                if rawhid_desc is None:
                    if not quiet:
                        logging.warning(
                            "Bridge VID=%04X PID=%04X: no sibling 0xFF60 interface found",
                            bridge_desc["vendor_id"],
                            bridge_desc["product_id"],
                        )
                    continue

                rawhid_path = rawhid_desc["path"]
                bridge_rawhid_paths.add(rawhid_path)

                # Check probe cache.  Positive results are cached permanently
                # (re-probing would conflict with an active HID connection).
                # Negative results expire after _BRIDGE_NEGATIVE_CACHE_TTL so
                # the bridge gets re-probed once a keyboard connects wirelessly.
                cached = _bridge_probe_cache.get(rawhid_path)
                if cached is not None:
                    cached_result, cached_via_id, cached_time = cached
                    if cached_result:
                        # Positive — use cached result, don't re-probe
                        bridge_dev = VialBridgeKeyboard(bridge_desc, rawhid_desc)
                        bridge_dev.via_id = cached_via_id
                        filtered.append(bridge_dev)
                        continue
                    else:
                        # Negative — check TTL
                        if time.monotonic() - cached_time < _BRIDGE_NEGATIVE_CACHE_TTL:
                            continue
                        # Expired — fall through to re-probe
                        del _bridge_probe_cache[rawhid_path]

                # Don't probe if the main thread currently has this device open.
                # This prevents the autorefresh thread from interfering with an
                # active wireless session after a cache miss (e.g. replug).
                if active_bridge_path is not None and rawhid_path == active_bridge_path:
                    if not quiet:
                        logging.info(
                            "Bridge: skipping probe for active device %s",
                            rawhid_path,
                        )
                    continue

                bridge_dev = VialBridgeKeyboard(bridge_desc, rawhid_desc)
                if not quiet:
                    logging.info(
                        "Bridge VID=%04X PID=%04X detect=%s rawhid=%s - probing",
                        bridge_desc["vendor_id"],
                        bridge_desc["product_id"],
                        bridge_desc["path"],
                        rawhid_desc["path"],
                    )
                # Probe the bridge -- this opens the 0xFF60 interface, runs FR
                # handshake, and checks if a keyboard is wirelessly connected
                if bridge_dev.probe():
                    _bridge_probe_cache[rawhid_path] = (True, bridge_dev.via_id, 0)
                    filtered.append(bridge_dev)
                    if not quiet:
                        logging.info("Bridge: wireless keyboard detected")
                else:
                    _bridge_probe_cache[rawhid_path] = (False, None, time.monotonic())
                    if not quiet:
                        logging.info("Bridge: no wireless keyboard connected")
            except Exception as e:
                if not quiet:
                    logging.warning("Bridge probe failed: %s", e)

    # Deduplicate: if the same keyboard is connected via both USB and bridge,
    # prefer the USB connection and suppress the bridge entry
    usb_via_ids = set()
    for dev in filtered:
        if isinstance(dev, VialKeyboard) and not isinstance(dev, VialBridgeKeyboard):
            usb_via_ids.add(dev.via_id)
    filtered = [
        dev
        for dev in filtered
        if not isinstance(dev, VialBridgeKeyboard) or dev.via_id not in usb_via_ids
    ]

    # Evict cache entries for bridges no longer present
    stale = [p for p in _bridge_probe_cache if p not in bridge_rawhid_paths]
    for p in stale:
        del _bridge_probe_cache[p]

    if sideload_vid == sideload_pid == 0:
        filtered.append(VialDummyKeyboard())

    return filtered


def chunks(data, sz):
    for i in range(0, len(data), sz):
        yield data[i : i + sz]


def pad_for_vibl(msg):
    """Pads message to vibl fixed 64-byte length"""
    if len(msg) > 64:
        raise RuntimeError("vibl message too long")
    return msg + b"\x00" * (64 - len(msg))


def init_logger():
    logging.basicConfig(level=logging.INFO)
    directory = QStandardPaths.writableLocation(QStandardPaths.AppLocalDataLocation)
    pathlib.Path(directory).mkdir(parents=True, exist_ok=True)
    path = os.path.join(directory, "vial.log")
    handler = RotatingFileHandler(path, maxBytes=5 * 1024 * 1024, backupCount=5)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s - %(levelname)s - %(module)s:%(lineno)d - %(message)s"
        )
    )
    logging.getLogger().addHandler(handler)


def make_scrollable(layout):
    w = QWidget()
    w.setLayout(layout)
    w.setObjectName("w")
    scroll = QScrollArea()
    scroll.setFrameShape(QFrame.NoFrame)
    scroll.setStyleSheet("QScrollArea { background-color:transparent; }")
    w.setStyleSheet("#w { background-color:transparent; }")
    scroll.setWidgetResizable(True)
    scroll.setWidget(w)
    return scroll


class KeycodeDisplay:
    keymap_override = KEYMAPS[0][1]
    clients = []

    @classmethod
    def get_label(cls, code):
        """Get label for a specific keycode"""
        if cls.code_is_overriden(code):
            return cls.keymap_override[Keycode.find_outer_keycode(code).qmk_id]
        return Keycode.label(code)

    @classmethod
    def code_is_overriden(cls, code):
        """Check whether a country-specific keymap overrides a code"""
        key = Keycode.find_outer_keycode(code)
        return key is not None and key.qmk_id in cls.keymap_override

    @classmethod
    def display_keycode(cls, widget, code):
        text = cls.get_label(code)
        tooltip = Keycode.tooltip(code)
        mask = Keycode.is_mask(code)
        mask_text = ""
        inner = Keycode.find_inner_keycode(code)
        if inner:
            mask_text = cls.get_label(inner.qmk_id)
        if mask:
            text = text.split("\n")[0]
        widget.masked = mask
        widget.setText(text)
        widget.setMaskText(mask_text)
        widget.setToolTip(tooltip)
        if cls.code_is_overriden(code):
            widget.setColor(QApplication.palette().color(QPalette.Link))
        else:
            widget.setColor(None)
        if inner and mask and cls.code_is_overriden(inner.qmk_id):
            widget.setMaskColor(QApplication.palette().color(QPalette.Link))
        else:
            widget.setMaskColor(None)

    @classmethod
    def set_keymap_override(cls, override):
        cls.keymap_override = override
        for client in cls.clients:
            client.on_keymap_override()

    @classmethod
    def notify_keymap_override(cls, client):
        cls.clients.append(client)
        client.on_keymap_override()

    @classmethod
    def unregister_keymap_override(cls, client):
        cls.clients.remove(client)

    @classmethod
    def relabel_buttons(cls, buttons):
        for widget in buttons:
            qmk_id = widget.keycode.qmk_id
            if qmk_id in KeycodeDisplay.keymap_override:
                label = KeycodeDisplay.keymap_override[qmk_id]
                highlight_color = QApplication.palette().color(QPalette.Link).getRgb()
                widget.setStyleSheet(
                    "QPushButton {color: rgb%s;}" % str(highlight_color)
                )
            else:
                label = widget.keycode.label
                widget.setStyleSheet("QPushButton {}")
            widget.setText(label.replace("&", "&&"))
