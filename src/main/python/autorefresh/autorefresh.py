import logging
import sys
import threading

from PyQt5.QtCore import QObject, pyqtSignal


class AutorefreshLocker:
    def __init__(self, autorefresh):
        self.autorefresh = autorefresh

    def __enter__(self):
        self.autorefresh._lock()

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.autorefresh._unlock()


class Autorefresh(QObject):
    instance = None
    devices_updated = pyqtSignal(object, bool)
    # Emitted (on the main thread) when an async select_device() finishes.
    # The argument is (device_or_None, error_message_or_None).
    device_opened = pyqtSignal(object, object)

    def __init__(self):
        super().__init__()

        self.devices = []
        self.current_device = None

        Autorefresh.instance = self

        if sys.platform == "emscripten":
            from autorefresh.autorefresh_thread_web import AutorefreshThreadWeb

            self.thread = AutorefreshThreadWeb()
        elif sys.platform.startswith("win"):
            from autorefresh.autorefresh_thread_win import AutorefreshThreadWin

            self.thread = AutorefreshThreadWin()
        else:
            from autorefresh.autorefresh_thread import AutorefreshThread

            self.thread = AutorefreshThread()

        self.thread.devices_updated.connect(self.on_devices_updated)
        self.thread.start()

    def _lock(self):
        self.thread.lock()

    def _unlock(self):
        self.thread.unlock()

    @classmethod
    def lock(cls):
        return AutorefreshLocker(cls.instance)

    def load_dummy(self, data):
        self.thread.load_dummy(data)

    def sideload_via_json(self, data):
        self.thread.sideload_via_json(data)

    def load_via_stack(self, data):
        self.thread.load_via_stack(data)

    def _get_override_json(self, device):
        """Return the JSON override for a device, or None."""
        if device.sideload:
            return self.thread.sideload_json
        elif device.via_stack:
            return self.thread.via_stack_json["definitions"][device.via_id]
        return None

    def select_device(self, idx):
        """
        Select and open a device.

        For VialBridgeKeyboard devices, the open() is done in a background
        thread so the UI is not blocked.  Returns True if the device will be
        opened asynchronously (caller should wait for device_opened signal),
        False if the device was opened synchronously (normal path).
        """
        from vial_device import VialBridgeKeyboard

        # Lock the autorefresh thread during open() to prevent it from
        # probing HID devices while we're connecting (especially important
        # for bridge devices where opening the same hidraw path would
        # conflict with the active connection).
        self.thread.lock()
        async_started = False
        try:
            if self.current_device is not None:
                self.current_device.close()
            self.current_device = None
            if 0 <= idx < len(self.devices):
                self.current_device = self.devices[idx]

            if self.current_device is None:
                self.thread.set_device(None)
                return False

            # For bridge devices, open asynchronously to avoid blocking the UI
            if isinstance(self.current_device, VialBridgeKeyboard):
                device = self.current_device
                override_json = self._get_override_json(device)
                # Keep autorefresh locked while the background thread runs;
                # unlock happens inside the thread when it finishes.
                async_started = True
                threading.Thread(
                    target=self._async_open_device,
                    args=(device, override_json),
                    daemon=True,
                ).start()
                return True  # async — caller waits for device_opened signal

            # Synchronous path for normal (USB) devices
            override_json = self._get_override_json(self.current_device)
            self.current_device.open(override_json)
            self.thread.set_device(self.current_device)
            return False
        finally:
            # Only unlock if we didn't hand off to async path
            # (async path unlocks in _async_open_device)
            if not async_started:
                self.thread.unlock()

    def _async_open_device(self, device, override_json):
        """Background thread: open a bridge device, then signal the main thread."""
        error = None
        try:
            device.open(override_json)
        except Exception as e:
            logging.error("Async device open failed: %s", e)
            error = str(e)
            device = None

        # Update state and unlock the autorefresh thread
        self.current_device = device
        self.thread.set_device(device)
        self.thread.unlock()

        # Signal the main thread that opening is complete.
        logging.info(
            "Bridge async open complete (error=%s), emitting device_opened", error
        )
        self.device_opened.emit(device, error)

    def on_devices_updated(self, devices, changed):
        self.devices = devices
        self.devices_updated.emit(devices, changed)

    def update(self, quiet=True, hard=False):
        self.thread.update(quiet, hard)
