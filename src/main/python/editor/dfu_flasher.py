# SPDX-License-Identifier: GPL-2.0-or-later
"""
DFU Flasher tab for Keychron keyboards with stm32-dfu bootloader.

Desktop flow:
  1. Optionally back up current layout (keymap, macros, settings, Keychron settings).
  2. Reboot keyboard into STM32 DFU mode via the standard QMK jump-to-bootloader command.
  3. Wait for a DFU device to appear (poll dfu-util -l).
  4. Flash the selected .bin file with dfu-util.
  5. Wait for the keyboard to re-enumerate as a Vial device.
  6. Restore the backed-up layout.

Web (Emscripten) flow:
  1. User manually puts keyboard into DFU mode (hold Esc + replug).
  2. User selects a .bin firmware file via the browser file picker.
  3. User clicks Flash; browser pops a WebUSB device picker (user selects the DFU device).
  4. Firmware is flashed via WebUSB DfuSe (STM32 protocol).
  Note: layout backup/restore is not available on web because the HID connection
  is lost when the keyboard reboots into DFU mode.
"""

import datetime
import json
import re
import subprocess
import sys
import time
import traceback
import threading

from PyQt5.QtCore import pyqtSignal, QCoreApplication
from PyQt5.QtGui import QFontDatabase
from PyQt5.QtWidgets import (
    QHBoxLayout,
    QLineEdit,
    QToolButton,
    QPlainTextEdit,
    QProgressBar,
    QFileDialog,
    QDialog,
    QCheckBox,
    QLabel,
)

from editor.basic_editor import BasicEditor
from unlocker import Unlocker
from util import tr, find_vial_devices
from vial_device import VialKeyboard

# How long (seconds) to wait for DFU device before giving up
DFU_WAIT_TIMEOUT = 60
# How long (seconds) to wait for keyboard to re-enumerate after flash
REBOOT_WAIT_TIMEOUT = 30

IS_WEB = sys.platform == "emscripten"


def _run(cmd, timeout=120):
    """Run a subprocess, return (returncode, combined stdout+stderr output)."""
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
        )
        return result.returncode, result.stdout.decode("utf-8", errors="replace")
    except FileNotFoundError as e:
        return (
            -1,
            "Command not found: {}\nMake sure dfu-util is installed and on PATH.".format(
                e
            ),
        )
    except subprocess.TimeoutExpired:
        return -1, "Command timed out after {}s".format(timeout)


def _run_streaming(cmd, line_cb, timeout=120):
    """
    Run a subprocess, calling line_cb(line) for each line of output as it
    arrives (stderr merged into stdout).  Returns the exit code, or -1 on
    error.  Raises FileNotFoundError if the binary is not found.

    dfu-util uses \\r (not \\n) to overwrite its progress line in-place, so we
    read raw chunks and split on both \\r and \\n to surface each update.
    """
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
    except FileNotFoundError:
        raise

    deadline = time.monotonic() + timeout
    buf = ""
    try:
        while True:
            chunk = proc.stdout.read(256)
            if not chunk:
                break
            buf += chunk.decode("utf-8", errors="replace")
            # Split on both \r and \n so dfu-util's in-place progress updates
            # are delivered as individual calls to line_cb.
            parts = re.split(r"[\r\n]", buf)
            buf = parts[-1]  # last part may be incomplete
            for part in parts[:-1]:
                stripped = part.strip()
                if stripped:
                    line_cb(stripped)
            if time.monotonic() > deadline:
                proc.kill()
                proc.wait()
                return -1
        # Flush any remaining buffered text
        if buf.strip():
            line_cb(buf.strip())
        proc.wait()
        return proc.returncode
    except Exception:
        proc.kill()
        proc.wait()
        raise


def _dfu_device_present():
    """Return True if dfu-util can see at least one DFU device."""
    rc, out = _run(["dfu-util", "-l"])
    return rc == 0 and "Found DFU" in out


def _flash_dfu(firmware_path, log_cb, progress_cb):
    """
    Flash firmware using dfu-util, streaming output line-by-line to log_cb
    and reporting fractional progress (0.0–1.0) to progress_cb.
    Returns (success: bool, message: str).

    dfu-util progress lines look like:
        Erase    [=====     ]  50%        65536 bytes
        Download [=====     ]  50%        65536 bytes
    Erase maps to bar 5–50%, Download/Upload maps to 50–100%.
    """
    # :leave tells STM32 DFU to jump to the application immediately after
    # flashing, so the keyboard reboots automatically without needing a replug.
    # QMK wiki: dfu-util -a 0 -d 0483:DF11 -s 0x8000000:leave -D <file>
    cmd = [
        "dfu-util",
        "--device",
        "0483:df11",
        "--alt",
        "0",
        "--dfuse-address",
        "0x08000000:leave",
        "--download",
        firmware_path,
    ]
    log_cb("Running: {}".format(" ".join(cmd)))
    progress_cb(0.05)

    # Regex matching dfu-util's progress line, e.g.:
    #   "Erase    \t[========  ]  80%        102400 bytes"
    #   "Download \t[========  ]  80%        102400 bytes"
    # dfu-util has two phases: Erase (0–50% of bar) and Download (50–100%).
    _progress_re = re.compile(r"^(Erase|Download|Upload)\s+\[.*?\]\s+(\d+)%")

    def _line_cb(line):
        m = _progress_re.match(line)
        if m:
            phase = m.group(1)
            pct = int(m.group(2))
            if phase == "Erase":
                # Erase phase: map 0–100% → bar 5%–50%
                progress_cb(0.05 + pct * 0.45 / 100.0)
            else:
                # Download/Upload phase: map 0–100% → bar 50%–100%
                progress_cb(0.50 + pct * 0.50 / 100.0)
            # Only log the 100% line to avoid flooding the log box
            if pct == 100:
                log_cb(line)
        else:
            log_cb(line)

    try:
        rc = _run_streaming(cmd, _line_cb, timeout=120)
    except FileNotFoundError:
        return (
            False,
            "Command not found: dfu-util\nMake sure dfu-util is installed and on PATH.",
        )

    if rc == 0:
        progress_cb(1.0)
        return True, "Flash complete."
    else:
        return False, "dfu-util exited with code {}".format(rc)


class DfuFlasher(BasicEditor):
    log_signal = pyqtSignal(object)
    progress_signal = pyqtSignal(object)
    complete_signal = pyqtSignal(object)
    error_signal = pyqtSignal(object)

    def __init__(self, main, parent=None):
        super().__init__(parent)

        self.main = main

        self.log_signal.connect(self._on_log)
        self.progress_signal.connect(self._on_progress)
        self.complete_signal.connect(self._on_complete)
        self.error_signal.connect(self._on_error)

        self.selected_firmware_path = ""
        self.selected_firmware_bytes = None  # used on web
        self.layout_restore = None
        self.uid_restore = None

        # ── File selector ──────────────────────────────────────────────────────
        file_selector = QHBoxLayout()
        self.txt_file_selector = QLineEdit()
        self.txt_file_selector.setReadOnly(True)
        self.txt_file_selector.setPlaceholderText("Select a .bin firmware file...")
        file_selector.addWidget(self.txt_file_selector)
        self.btn_select_file = QToolButton()
        self.btn_select_file.setText(tr("DfuFlasher", "Select file..."))
        self.btn_select_file.clicked.connect(self.on_click_select_file)
        file_selector.addWidget(self.btn_select_file)
        self.addLayout(file_selector)

        # ── Log output ─────────────────────────────────────────────────────────
        self.txt_logger = QPlainTextEdit()
        self.txt_logger.setReadOnly(True)
        self.txt_logger.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        self.addWidget(self.txt_logger)

        # ── Options ────────────────────────────────────────────────────────────
        self.chk_restore_layout = QCheckBox()
        self.chk_restore_layout.setText(
            tr("DfuFlasher", "Restore current layout after flashing")
        )
        self.chk_restore_layout.setChecked(True)
        self.addWidget(self.chk_restore_layout)

        # ── Progress + Flash button ────────────────────────────────────────────
        progress_row = QHBoxLayout()
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        progress_row.addWidget(self.progress_bar)
        self.btn_flash = QToolButton()
        self.btn_flash.setText(tr("DfuFlasher", "Flash"))
        self.btn_flash.clicked.connect(self.on_click_flash)
        progress_row.addWidget(self.btn_flash)
        self.addLayout(progress_row)

        if IS_WEB:
            self._web_note = QLabel(
                "Select the firmware file and click Flash.\n"
                "The keyboard will be rebooted into DFU mode automatically.\n"
                "You will then be prompted to select the DFU device, and afterwards\n"
                "to reconnect the keyboard for layout restore."
            )
            self.addWidget(self._web_note)

    # ── BasicEditor interface ──────────────────────────────────────────────────

    def rebuild(self, device):
        super().rebuild(device)
        self.txt_logger.clear()
        self.progress_bar.setValue(0)
        if not self.valid():
            return
        if IS_WEB:
            self.log("Keychron DFU flasher ready (web mode)")
            self.log("Select a .bin firmware file and click Flash.")
            self.log("The keyboard will be rebooted into DFU mode automatically.")
        else:
            kb = self.device.keyboard
            mcu = getattr(kb, "keychron_mcu_info", "")
            fw = getattr(kb, "keychron_firmware_version", "")
            self.log("Keychron DFU flasher ready")
            if mcu:
                self.log("MCU: {}".format(mcu))
            if fw:
                self.log("Current firmware: {}".format(fw))
            self.log("Select a .bin firmware file and click Flash.")

    def valid(self):
        if not isinstance(self.device, VialKeyboard):
            return False
        kb = self.device.keyboard
        return callable(getattr(kb, "has_keychron_dfu", None)) and kb.has_keychron_dfu()

    # ── UI actions ─────────────────────────────────────────────────────────────

    def on_click_select_file(self):
        if IS_WEB:
            self._web_select_file()
        else:
            self._desktop_select_file()

    def _desktop_select_file(self):
        dialog = QFileDialog()
        dialog.setDefaultSuffix("bin")
        dialog.setAcceptMode(QFileDialog.AcceptOpen)
        dialog.setNameFilters(["Firmware binary (*.bin)", "All files (*)"])
        if dialog.exec_() == QDialog.Accepted:
            self.selected_firmware_path = dialog.selectedFiles()[0]
            self.txt_file_selector.setText(self.selected_firmware_path)
            self.log("Selected: {}".format(self.selected_firmware_path))

    def _web_select_file(self):
        """Trigger showOpenFilePicker for .bin files via the JS bridge."""
        import vialglue  # noqa: available on emscripten

        vialglue.load_firmware_bin()

    def on_web_firmware_loaded(self, name, data):
        """Called from JS (via webmain) when the user picks a .bin file on web."""
        self.selected_firmware_bytes = data
        self.selected_firmware_path = name
        self.txt_file_selector.setText(name)
        self.log("Selected: {}  ({} bytes)".format(name, len(data)))

    def on_click_flash(self):
        if IS_WEB:
            self._on_click_flash_web()
        else:
            self._on_click_flash_desktop()

    def _on_click_flash_desktop(self):
        if not self.selected_firmware_path:
            self.log("Error: Please select a firmware .bin file first.")
            return

        self.log("Preparing to flash...")
        print("[dfu] desktop flash started: firmware =", self.selected_firmware_path)
        self.lock_ui()
        self.progress_bar.setValue(0)

        self.layout_restore = None
        self.uid_restore = None

        # Back up layout before rebooting
        if self.chk_restore_layout.isChecked():
            self.log("Backing up current layout...")
            print("[dfu] desktop: backing up layout")
            try:
                self.layout_restore = self.device.keyboard.save_layout()
                self.log(
                    "Layout backed up ({} bytes).".format(len(self.layout_restore))
                )
            except Exception as e:
                self.log("Warning: Failed to back up layout: {}".format(e))
                self.layout_restore = None

        # Grab the UID so we can find the keyboard again after flashing
        try:
            self.uid_restore = self.device.keyboard.get_uid()
            self.log("Keyboard UID: {}".format(self.uid_restore.hex()))
        except Exception as e:
            self.log("Warning: Could not read keyboard UID: {}".format(e))

        # Unlock then jump to bootloader
        Unlocker.unlock(self.device.keyboard)
        self.log("Rebooting into DFU mode...")
        print("[dfu] desktop: unlocking and rebooting into DFU mode")
        try:
            self.device.keyboard.reset()
        except Exception:
            # reset() closes the device; OSError on close is expected
            pass

        threading.Thread(target=self._flash_thread, daemon=True).start()

    def _on_click_flash_web(self):
        if not self.selected_firmware_bytes:
            self.log("Error: Please select a firmware .bin file first.")
            return

        self.layout_restore = None
        self.uid_restore = None
        self.lock_ui()
        self.progress_bar.setValue(0)

        # Unlock on the main thread right now — exactly like the desktop path.
        # _on_click_flash_web is called from a button click so we're on the
        # main thread; Unlocker.unlock() can drive its Qt dialog normally.
        self.log("Unlocking keyboard...")
        try:
            unlocked = Unlocker.unlock(self.device.keyboard)
        except Exception as e:
            unlocked = False
        if not unlocked:
            self.log(
                "Error: Keyboard could not be unlocked.\n"
                "Use Security > Unlock and try again."
            )
            self.unlock_ui(force_refresh=False)
            return

        # All work runs on the main thread as a QTimer state machine — no
        # background thread.  Under Emscripten PROXY_TO_PTHREAD a background
        # pthread cannot easily acquire the GIL or deliver cross-thread Qt
        # signals, so we keep everything on the Qt main pthread and yield
        # control back to the browser event loop between steps via QTimer.
        do_restore = self.chk_restore_layout.isChecked()
        firmware_bytes = bytes(self.selected_firmware_bytes)
        self._web_state = {
            "do_restore": do_restore,
            "firmware_bytes": firmware_bytes,
        }
        self._web_timer = None  # will be set during polling steps
        self._web_step_backup_layout()

    # ── Background flash thread (desktop) ─────────────────────────────────────

    def _flash_thread(self):
        try:
            self._do_flash()
        except Exception as e:
            self.error_signal.emit(
                "Unexpected error: {}\n{}".format(e, traceback.format_exc())
            )

    def _do_flash(self):
        # 1. Wait for DFU device to appear
        self.log_signal.emit(
            "Waiting for DFU device (up to {}s)...".format(DFU_WAIT_TIMEOUT)
        )
        print(
            "[dfu] desktop: waiting for DFU device (up to {}s)".format(DFU_WAIT_TIMEOUT)
        )
        deadline = time.monotonic() + DFU_WAIT_TIMEOUT
        found_dfu = False
        while time.monotonic() < deadline:
            if _dfu_device_present():
                found_dfu = True
                break
            time.sleep(1)

        if not found_dfu:
            print("[dfu] desktop: DFU device not found within timeout")
            self.error_signal.emit(
                "Error: DFU device did not appear within {}s.\n"
                "Make sure the keyboard is in DFU mode and dfu-util is installed.".format(
                    DFU_WAIT_TIMEOUT
                )
            )
            return

        print("[dfu] desktop: DFU device found, starting flash")
        self.log_signal.emit("DFU device found. Starting flash...")
        self.progress_signal.emit(0.05)

        # 2. Flash
        ok, msg = _flash_dfu(
            self.selected_firmware_path,
            log_cb=lambda m: self.log_signal.emit(m),
            progress_cb=lambda p: self.progress_signal.emit(p),
        )
        if not ok:
            print("[dfu] desktop: flash failed:", msg)
            self.error_signal.emit("Error: " + msg)
            return

        print("[dfu] desktop: flash complete")
        self.log_signal.emit(msg)
        self.complete_signal.emit(
            "Flash successful! Waiting for keyboard to restart..."
        )

    # ── Web flash state machine (runs on Qt main thread via QTimer) ───────────
    # Each _web_step_* method does one unit of work then either calls the next
    # step directly or schedules it with QTimer.singleShot so the browser event
    # loop can process pending messages (HID input reports, postMessage, etc.)
    # before we continue.  This avoids blocking the main pthread which would
    # prevent JS events from being delivered.

    def _web_error(self, msg):
        self.log("Error: " + msg)
        self.unlock_ui(force_refresh=False)

    def _web_step_backup_layout(self):
        do_restore = self._web_state["do_restore"]
        if do_restore and self.device and self.device.keyboard:
            self.log("Backing up current layout...")
            try:
                self.layout_restore = self.device.keyboard.save_layout()
                self.log(
                    "Layout backed up ({} bytes).".format(len(self.layout_restore))
                )
            except Exception as e:
                self.log("Warning: Failed to back up layout: {}".format(e))
                self.layout_restore = None
        else:
            self.log("Skipping layout backup.")
        # Yield to browser event loop before the next step
        from PyQt5.QtCore import QTimer

        QTimer.singleShot(0, self._web_step_reset_keyboard)

    def _web_step_reset_keyboard(self):
        # Unlock was already done before the state machine started.
        self.log("Rebooting keyboard into DFU mode...")
        try:
            self.device.keyboard.reset()
        except Exception as e:
            # reset() sends the reboot command then the device disconnects;
            # an OSError or read timeout on close is expected and not a failure.
            self.log("Note: {}".format(e))
        from PyQt5.QtCore import QTimer

        # Give the keyboard 3 s to enumerate as a DFU device before prompting.
        self.log(
            "Waiting for keyboard to reboot into DFU mode...\n"
            "Click the 'Select DFU Device' button that will appear on screen."
        )
        QTimer.singleShot(3000, self._web_step_show_usb_picker)

    def _web_step_show_usb_picker(self):
        import vialglue  # noqa: available on emscripten

        # dfu_show_usb_picker resets dfu_status_ready to 0 before showing the
        # overlay, so the poll loop starts clean.
        vialglue.dfu_show_usb_picker()
        self.log("Waiting for DFU device selection...")
        from PyQt5.QtCore import QTimer

        QTimer.singleShot(200, self._web_step_poll_usb_ready)

    def _web_step_poll_usb_ready(self):
        import vialglue  # noqa: available on emscripten

        status_json = vialglue.dfu_flash_status_poll()
        try:
            status = json.loads(status_json)
        except Exception:
            self._web_error("invalid status from DFU bridge")
            return
        s = status.get("status", "")
        if s == "pending":
            from PyQt5.QtCore import QTimer

            QTimer.singleShot(200, self._web_step_poll_usb_ready)
        elif s == "usb_ready":
            self.log("USB DFU device selected.")
            self._web_step_start_flash()
        elif s == "log":
            self.log(status.get("msg", ""))
            from PyQt5.QtCore import QTimer

            QTimer.singleShot(200, self._web_step_poll_usb_ready)
        elif s == "error":
            self._web_error(status.get("msg", "unknown"))
        else:
            from PyQt5.QtCore import QTimer

            QTimer.singleShot(200, self._web_step_poll_usb_ready)

    def _web_step_start_flash(self):
        import vialglue  # noqa: available on emscripten

        firmware_bytes = self._web_state["firmware_bytes"]
        self.log("Starting DFU flash ({} bytes)...".format(len(firmware_bytes)))
        self.progress_bar.setValue(5)
        vialglue.dfu_flash_start(firmware_bytes)
        from PyQt5.QtCore import QTimer

        QTimer.singleShot(200, self._web_step_poll_flash_done)

    def _web_step_poll_flash_done(self):
        import vialglue  # noqa: available on emscripten

        status_json = vialglue.dfu_flash_status_poll()
        try:
            status = json.loads(status_json)
        except Exception:
            self._web_error("invalid status from DFU bridge")
            return
        s = status.get("status", "")
        if s == "pending":
            from PyQt5.QtCore import QTimer

            QTimer.singleShot(200, self._web_step_poll_flash_done)
        elif s == "done":
            self.log("Flash complete.")
            self.progress_bar.setValue(100)
            self._web_step_maybe_reconnect()
        elif s == "progress":
            pct = status.get("pct", 0)
            self.progress_bar.setValue(int(pct * 100))
            from PyQt5.QtCore import QTimer

            QTimer.singleShot(200, self._web_step_poll_flash_done)
        elif s == "log":
            self.log(status.get("msg", ""))
            from PyQt5.QtCore import QTimer

            QTimer.singleShot(200, self._web_step_poll_flash_done)
        elif s == "error":
            self._web_error(status.get("msg", "unknown"))
        else:
            from PyQt5.QtCore import QTimer

            QTimer.singleShot(200, self._web_step_poll_flash_done)

    def _web_step_maybe_reconnect(self):
        if self.layout_restore:
            self.log(
                "Flash successful! The keyboard is restarting.\n"
                "Click the 'Reconnect Keyboard' button that appeared on screen."
            )
            import vialglue  # noqa: available on emscripten

            vialglue.dfu_show_hid_picker()
            # dfu_show_hid_picker resets dfu_status_ready to 0
            from PyQt5.QtCore import QTimer

            QTimer.singleShot(200, self._web_step_poll_reconnect)
        else:
            self._on_complete("Flash successful!")

    def _web_step_poll_reconnect(self):
        import vialglue  # noqa: available on emscripten

        status_json = vialglue.dfu_flash_status_poll()
        try:
            status = json.loads(status_json)
        except Exception:
            self._web_error("invalid status from reconnect bridge")
            return
        s = status.get("status", "")
        if s == "pending":
            from PyQt5.QtCore import QTimer

            QTimer.singleShot(200, self._web_step_poll_reconnect)
        elif s == "reconnected":
            self.log("Keyboard reconnected. Restoring layout...")
            self._on_complete("web_restore")
        elif s == "error":
            msg = status.get("msg", "unknown error")
            self.log(
                "Warning: Reconnect failed: {}\nLayout restore skipped.".format(msg)
            )
            self._on_complete("Flash successful! (Layout restore skipped)")
        else:
            from PyQt5.QtCore import QTimer

            QTimer.singleShot(200, self._web_step_poll_reconnect)

    # ── Signals (called on main thread) ───────────────────────────────────────

    def _on_log(self, msg):
        self.log(msg)

    def _on_progress(self, progress):
        self.progress_bar.setValue(int(progress * 100))

    def _on_complete(self, msg):
        self.progress_bar.setValue(100)
        if IS_WEB:
            if msg == "web_restore":
                # Reconnect succeeded; the C glue (g_device) now points at the
                # new device.  Reload the keyboard to pick up the new firmware's
                # capabilities, then restore the saved layout.
                self.log("Restoring layout to newly flashed firmware...")
                try:
                    self.device.keyboard.reload()
                    self.device.keyboard.restore_layout(self.layout_restore)
                    self.device.keyboard.lock()
                    self.log("Layout restored successfully.")
                except Exception as e:
                    self.log(
                        "Warning: Layout restore failed: {}\n{}".format(
                            e, traceback.format_exc()
                        )
                    )
                self.unlock_ui(force_refresh=True)
            else:
                self.log(msg)
                self.unlock_ui(force_refresh=False)
        else:
            self.log(msg)
            self._wait_and_restore()

    def _on_error(self, msg):
        self.log(msg)
        self.unlock_ui(force_refresh=False)

    # ── Post-flash reconnect + restore (desktop only) ─────────────────────────

    def _wait_and_restore(self):
        """
        Poll for the Vial keyboard to come back (runs on main thread so we can
        keep the UI responsive via processEvents).
        """
        if not self.uid_restore:
            self.log("No UID recorded — skipping reconnect wait.")
            print("[dfu] desktop: no UID recorded, skipping reconnect wait")
            self.unlock_ui()
            return

        self.log(
            "Waiting for keyboard to re-enumerate (up to {}s)...".format(
                REBOOT_WAIT_TIMEOUT
            )
        )
        print(
            "[dfu] desktop: waiting for keyboard to re-enumerate (up to {}s)".format(
                REBOOT_WAIT_TIMEOUT
            )
        )
        deadline = time.monotonic() + REBOOT_WAIT_TIMEOUT
        found = None
        while time.monotonic() < deadline and found is None:
            QCoreApplication.processEvents()
            time.sleep(1)
            found = self._find_keyboard_with_uid(self.uid_restore)

        if found is None:
            print("[dfu] desktop: keyboard did not re-enumerate within timeout")
            self.log(
                "Keyboard did not re-enumerate within {}s. "
                "Layout restore skipped — reconnect manually.".format(
                    REBOOT_WAIT_TIMEOUT
                )
            )
            self.unlock_ui()
            return

        print("[dfu] desktop: keyboard found, restoring layout")
        self.log("Keyboard found. Restoring layout...")

        if self.layout_restore:
            try:
                found.open()
                self.device = found
                QCoreApplication.processEvents()
                found.keyboard.restore_layout(self.layout_restore)
                found.keyboard.lock()
                found.close()
                self.log("Layout restored.")
                print("[dfu] desktop: layout restored successfully")
            except Exception as e:
                print("[dfu] desktop: layout restore failed:", e)
                self.log(
                    "Warning: Layout restore failed: {}\n{}".format(
                        e, traceback.format_exc()
                    )
                )

        self.unlock_ui()

    def _find_keyboard_with_uid(self, uid):
        """Return the first VialKeyboard whose UID matches, or None."""
        try:
            devices = find_vial_devices({"definitions": {}})
        except Exception:
            return None
        for dev in devices:
            if not isinstance(dev, VialKeyboard):
                continue
            try:
                dev_uid = dev.get_uid()
                if dev_uid and dev_uid == uid:
                    return dev
            except Exception:
                pass
        return None

    # ── Helpers ───────────────────────────────────────────────────────────────

    def log(self, line):
        self.txt_logger.appendPlainText(
            "[{}] {}".format(datetime.datetime.now().strftime("%H:%M:%S"), line)
        )

    def lock_ui(self):
        self.btn_select_file.setEnabled(False)
        self.btn_flash.setEnabled(False)
        self.main.lock_ui()

    def unlock_ui(self, force_refresh=True):
        self.btn_select_file.setEnabled(True)
        self.btn_flash.setEnabled(True)
        self.main.unlock_ui()
        if force_refresh:
            self.main.on_click_refresh()
