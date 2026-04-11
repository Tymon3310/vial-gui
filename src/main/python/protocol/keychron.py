# SPDX-License-Identifier: GPL-2.0-or-later
"""
Keychron-specific HID protocol implementation.

This module implements communication with Keychron keyboards that support
the Keychron raw HID protocol for features like:
- Dynamic debounce
- Snap Click (SOCD for regular keyboards)
- Per-key RGB and Mixed RGB
- Analog Matrix (Hall Effect) settings
- Wireless power management
- USB report rate
- NKRO toggle
"""

import logging
import struct
import sys
from protocol.base_protocol import BaseProtocol

# Main command IDs (data[0])
KC_GET_PROTOCOL_VERSION = 0xA0
KC_GET_FIRMWARE_VERSION = 0xA1
KC_GET_SUPPORT_FEATURE = 0xA2
KC_GET_DEFAULT_LAYER = 0xA3
KC_GET_BATTERY_LEVEL = 0xAC
KC_MISC_CMD_GROUP = 0xA7
KC_KEYCHRON_RGB = 0xA8
KC_ANALOG_MATRIX = 0xA9
KC_WIRELESS_DFU = 0xAA
KC_FACTORY_TEST = 0xAB

# Feature flags (byte 0) from KC_GET_SUPPORT_FEATURE
FEATURE_DEFAULT_LAYER = 0x01
FEATURE_BLUETOOTH = 0x02
FEATURE_P24G = 0x04
FEATURE_ANALOG_MATRIX = 0x08
FEATURE_STATE_NOTIFY = 0x10
FEATURE_DYNAMIC_DEBOUNCE = 0x20
FEATURE_SNAP_CLICK = 0x40
FEATURE_KEYCHRON_RGB = 0x80

# Feature flags (byte 1) - shifted by 8
FEATURE_QUICK_START = 0x0100
FEATURE_NKRO = 0x0200

# Feature flags (byte 2) - shifted by 16
# These require reading data[4] from KC_GET_SUPPORT_FEATURE.
# NOTE: Bits 16 (Bootloader_Jump), 17 (Performance_Mode), and 18
# (Support_Toggle) exist in the Keychron Launcher but are ZMK/mouse-only
# features (e.g. Keychron V Ultra series). They share sub-command IDs with
# FACTORY_RESET (0x11) and NKRO_GET (0x12) because ZMK devices don't have
# those commands. We intentionally omit them here since Vial targets QMK.

# Misc command group sub-commands (data[1] when data[0] = 0xA7)
MISC_GET_PROTOCOL_VER = 0x01
DFU_INFO_GET = 0x02
LANGUAGE_GET = 0x03
LANGUAGE_SET = 0x04
DEBOUNCE_GET = 0x05
DEBOUNCE_SET = 0x06
SNAP_CLICK_GET_INFO = 0x07
SNAP_CLICK_GET = 0x08
SNAP_CLICK_SET = 0x09
SNAP_CLICK_SAVE = 0x0A
WIRELESS_LPM_GET = 0x0B
WIRELESS_LPM_SET = 0x0C
REPORT_RATE_GET = 0x0D
REPORT_RATE_SET = 0x0E
DIP_SWITCH_GET = 0x0F
DIP_SWITCH_SET = 0x10
FACTORY_RESET = 0x11
NKRO_GET = 0x12
NKRO_SET = 0x13

# Misc feature support flags (from MISC_GET_PROTOCOL_VER)
MISC_DFU_INFO = 0x01
MISC_LANGUAGE = 0x02
MISC_DEBOUNCE = 0x04
MISC_SNAP_CLICK = 0x08
MISC_WIRELESS_LPM = 0x10
MISC_REPORT_RATE = 0x20
MISC_QUICK_START = 0x40
MISC_NKRO = 0x80

# Debounce types
DEBOUNCE_SYM_DEFER_GLOBAL = 0
DEBOUNCE_SYM_DEFER_PER_ROW = 1
DEBOUNCE_SYM_DEFER_PER_KEY = 2
DEBOUNCE_SYM_EAGER_PER_ROW = 3
DEBOUNCE_SYM_EAGER_PER_KEY = 4
DEBOUNCE_ASYM_EAGER_DEFER_PER_KEY = 5
DEBOUNCE_NONE = 6

DEBOUNCE_TYPE_NAMES = {
    DEBOUNCE_SYM_DEFER_GLOBAL: "Symmetric Defer (Global)",
    DEBOUNCE_SYM_DEFER_PER_ROW: "Symmetric Defer (Per Row)",
    DEBOUNCE_SYM_DEFER_PER_KEY: "Symmetric Defer (Per Key)",
    DEBOUNCE_SYM_EAGER_PER_ROW: "Symmetric Eager (Per Row)",
    DEBOUNCE_SYM_EAGER_PER_KEY: "Symmetric Eager (Per Key)",
    DEBOUNCE_ASYM_EAGER_DEFER_PER_KEY: "Asymmetric Eager-Defer (Per Key)",
    DEBOUNCE_NONE: "None",
}

# Snap Click types (SOCD for regular keyboards)
SNAP_CLICK_TYPE_NONE = 0
SNAP_CLICK_TYPE_REGULAR = 1
SNAP_CLICK_TYPE_LAST_INPUT = 2
SNAP_CLICK_TYPE_FIRST_KEY = 3
SNAP_CLICK_TYPE_SECOND_KEY = 4
SNAP_CLICK_TYPE_NEUTRAL = 5

SNAP_CLICK_TYPE_NAMES = {
    SNAP_CLICK_TYPE_NONE: "Disabled",
    SNAP_CLICK_TYPE_REGULAR: "Last Key Priority (simple)",
    SNAP_CLICK_TYPE_LAST_INPUT: "Last Key Priority (re-activates held key)",
    SNAP_CLICK_TYPE_FIRST_KEY: "Absolute Priority: Key 1",
    SNAP_CLICK_TYPE_SECOND_KEY: "Absolute Priority: Key 2",
    SNAP_CLICK_TYPE_NEUTRAL: "Cancel (both keys cancel out)",
}

SNAP_CLICK_TYPE_TOOLTIPS = {
    SNAP_CLICK_TYPE_NONE: "This pair is inactive.",
    SNAP_CLICK_TYPE_REGULAR: "When both keys are pressed, the most recently pressed key wins. "
    "Releasing either key unregisters only that key.",
    SNAP_CLICK_TYPE_LAST_INPUT: "When both keys are pressed, the most recently pressed key wins. "
    "Releasing the winning key re-activates the still-held losing key. "
    "(Stock launcher 'Last Key Priority')",
    SNAP_CLICK_TYPE_FIRST_KEY: "Key 1 always takes priority when both are pressed. "
    "Releasing Key 1 re-activates Key 2 if still held. "
    "(Stock launcher 'Absolute Priority')",
    SNAP_CLICK_TYPE_SECOND_KEY: "Key 2 always takes priority when both are pressed. "
    "Releasing Key 2 re-activates Key 1 if still held. "
    "(Stock launcher 'Absolute Priority' — reversed)",
    SNAP_CLICK_TYPE_NEUTRAL: "When both keys are pressed simultaneously, neither key registers. "
    "Releasing one key re-activates the other. "
    "(Stock launcher 'Cancel Mode')",
}

# USB Report Rate dividers
REPORT_RATE_8000HZ = 0
REPORT_RATE_4000HZ = 1
REPORT_RATE_2000HZ = 2
REPORT_RATE_1000HZ = 3
REPORT_RATE_500HZ = 4
REPORT_RATE_250HZ = 5
REPORT_RATE_125HZ = 6

REPORT_RATE_NAMES = {
    REPORT_RATE_8000HZ: "8000 Hz",
    REPORT_RATE_4000HZ: "4000 Hz",
    REPORT_RATE_2000HZ: "2000 Hz",
    REPORT_RATE_1000HZ: "1000 Hz",
    REPORT_RATE_500HZ: "500 Hz",
    REPORT_RATE_250HZ: "250 Hz",
    REPORT_RATE_125HZ: "125 Hz",
}

# RGB sub-commands (data[1] when data[0] = 0xA8)
RGB_GET_PROTOCOL_VER = 0x01
RGB_SAVE = 0x02
GET_INDICATORS_CONFIG = 0x03
SET_INDICATORS_CONFIG = 0x04
RGB_GET_LED_COUNT = 0x05
RGB_GET_LED_IDX = 0x06
PER_KEY_RGB_GET_TYPE = 0x07
PER_KEY_RGB_SET_TYPE = 0x08
PER_KEY_RGB_GET_COLOR = 0x09
PER_KEY_RGB_SET_COLOR = 0x0A
MIXED_EFFECT_RGB_GET_INFO = 0x0B
MIXED_EFFECT_RGB_GET_REGIONS = 0x0C
MIXED_EFFECT_RGB_SET_REGIONS = 0x0D
MIXED_EFFECT_RGB_GET_EFFECT_LIST = 0x0E
MIXED_EFFECT_RGB_SET_EFFECT_LIST = 0x0F

# Per-key RGB effect types
PER_KEY_RGB_SOLID = 0
PER_KEY_RGB_BREATHING = 1
PER_KEY_RGB_REACTIVE_SIMPLE = 2
PER_KEY_RGB_REACTIVE_MULTI_WIDE = 3
PER_KEY_RGB_REACTIVE_SPLASH = 4

PER_KEY_RGB_TYPE_NAMES = {
    PER_KEY_RGB_SOLID: "Solid",
    PER_KEY_RGB_BREATHING: "Breathing",
    PER_KEY_RGB_REACTIVE_SIMPLE: "Reactive Simple",
    PER_KEY_RGB_REACTIVE_MULTI_WIDE: "Reactive Multi Wide",
    PER_KEY_RGB_REACTIVE_SPLASH: "Reactive Splash",
}

# Analog Matrix sub-commands (data[1] when data[0] = 0xA9)
AMC_GET_VERSION = 0x01
AMC_GET_PROFILES_INFO = 0x10
AMC_SELECT_PROFILE = 0x11
AMC_GET_PROFILE_RAW = 0x12
AMC_SET_PROFILE_NAME = 0x13
AMC_SET_TRAVEL = 0x14
AMC_SET_ADVANCE_MODE = 0x15
AMC_SET_SOCD = 0x16
AMC_RESET_PROFILE = 0x1E
AMC_SAVE_PROFILE = 0x1F
AMC_GET_CURVE = 0x20
AMC_SET_CURVE = 0x21
AMC_GET_GAME_CONTROLLER_MODE = 0x22
AMC_SET_GAME_CONTROLLER_MODE = 0x23
AMC_GET_REALTIME_TRAVEL = 0x30
AMC_CALIBRATE = 0x40
AMC_GET_CALIBRATE_STATE = 0x41
AMC_GET_CALIBRATED_VALUE = 0x42

# Analog key modes
AKM_GLOBAL = 0
AKM_REGULAR = 1
AKM_RAPID = 2
AKM_DKS = 3
AKM_GAMEPAD = 4
AKM_TOGGLE = 5

AKM_MODE_NAMES = {
    AKM_GLOBAL: "Global",
    AKM_REGULAR: "Regular",
    AKM_RAPID: "Rapid Trigger",
    AKM_DKS: "Dynamic Keystroke",
    AKM_GAMEPAD: "Gamepad",
    AKM_TOGGLE: "Toggle",
}

# Advance mode types
ADV_MODE_CLEAR = 0
ADV_MODE_OKMC = 1
ADV_MODE_GAME_CONTROLLER = 2
ADV_MODE_TOGGLE = 3

# SOCD prioritization types (for HE keyboards)
SOCD_PRI_NONE = 0
SOCD_PRI_DEEPER_TRAVEL = 1
SOCD_PRI_DEEPER_TRAVEL_SINGLE = 2
SOCD_PRI_LAST_KEYSTROKE = 3
SOCD_PRI_KEY_1 = 4
SOCD_PRI_KEY_2 = 5
SOCD_PRI_NEUTRAL = 6

SOCD_TYPE_NAMES = {
    SOCD_PRI_NONE: "Disabled",
    SOCD_PRI_DEEPER_TRAVEL: "Deeper Travel",
    SOCD_PRI_DEEPER_TRAVEL_SINGLE: "Deeper Travel (Single)",
    SOCD_PRI_LAST_KEYSTROKE: "Last Keystroke",
    SOCD_PRI_KEY_1: "Key 1 Priority",
    SOCD_PRI_KEY_2: "Key 2 Priority",
    SOCD_PRI_NEUTRAL: "Neutral",
}

# Calibration states
CALIB_OFF = 0
CALIB_ZERO_TRAVEL_POWER_ON = 1
CALIB_ZERO_TRAVEL_MANUAL = 2
CALIB_FULL_TRAVEL_MANUAL = 3
CALIB_SAVE_AND_EXIT = 4
CALIB_CLEAR = 5

# OKMC (DKS) action bitfield values — stored as 4-bit nibbles per event slot
# Semantics from action_okmc.c: bits 0-2 drive sequential key events
OKMC_ACTION_NONE = 0b000  # 0 — no action
OKMC_ACTION_RELEASE = 0b001  # 1 — key release
OKMC_ACTION_PRESS = 0b010  # 2 — key press
OKMC_ACTION_TAP = 0b110  # 6 — press then release
OKMC_ACTION_RE_PRESS = 0b111  # 7 — release, press, release

OKMC_ACTION_NAMES = {
    OKMC_ACTION_NONE: "None",
    OKMC_ACTION_RELEASE: "Release",
    OKMC_ACTION_PRESS: "Press",
    OKMC_ACTION_TAP: "Tap",
    OKMC_ACTION_RE_PRESS: "Re-press",
}

# Gamepad axis/direction and button assignments (game_controller_common.h)
GC_X_AXIS_LEFT = 0
GC_X_AXIS_RIGHT = 1
GC_Y_AXIS_DOWN = 2
GC_Y_AXIS_UP = 3
GC_Z_AXIS_N = 4
GC_Z_AXIS_P = 5
GC_RX_AXIS_LEFT = 6
GC_RX_AXIS_RIGHT = 7
GC_RY_AXIS_DOWN = 8
GC_RY_AXIS_UP = 9
GC_RZ_AXIS_N = 10
GC_RZ_AXIS_P = 11
GC_AXIS_MAX = 12  # first button index

GC_AXIS_NAMES = {
    GC_X_AXIS_LEFT: "X- (Left)",
    GC_X_AXIS_RIGHT: "X+ (Right)",
    GC_Y_AXIS_DOWN: "Y- (Down)",
    GC_Y_AXIS_UP: "Y+ (Up)",
    GC_Z_AXIS_N: "Z-",
    GC_Z_AXIS_P: "Z+",
    GC_RX_AXIS_LEFT: "RX- (Left)",
    GC_RX_AXIS_RIGHT: "RX+ (Right)",
    GC_RY_AXIS_DOWN: "RY- (Down)",
    GC_RY_AXIS_UP: "RY+ (Up)",
    GC_RZ_AXIS_N: "RZ-",
    GC_RZ_AXIS_P: "RZ+",
}
# Add buttons 0-31 (indices 13-44)
for _i in range(32):
    GC_AXIS_NAMES[GC_AXIS_MAX + 1 + _i] = f"Button {_i}"

GC_MASK_XINPUT = 0x01
GC_MASK_TYPING = 0x02

# Response status codes
KC_SUCCESS = 0
KC_FAIL = 1


class ProtocolKeychron(BaseProtocol):
    """Keychron-specific protocol mixin for the Keyboard class."""

    def _init_keychron_attrs(self):
        """Initialize Keychron attributes. Called from reload_keychron."""
        # Feature support flags
        self.keychron_features = 0
        self.keychron_misc_features = 0
        self.keychron_protocol_version = 0
        self.keychron_misc_protocol_version = 0
        self.keychron_firmware_version = ""
        self.keychron_mcu_info = (
            ""  # MCU chip name from DFU_INFO_GET (e.g. "STM32L432")
        )

        # Debounce settings
        self.keychron_debounce_type = DEBOUNCE_SYM_DEFER_GLOBAL
        self.keychron_debounce_time = 5

        # NKRO
        self.keychron_nkro_enabled = False
        self.keychron_nkro_supported = False
        self.keychron_nkro_adaptive = False

        # Report rate (v1 = single rate, v2 = dual USB / 2.4 GHz)
        self.keychron_report_rate = REPORT_RATE_1000HZ
        self.keychron_report_rate_mask = 0x7F  # which rates are supported
        self.keychron_poll_rate_version = 1  # 1=single, 2=dual
        self.keychron_poll_rate_usb = REPORT_RATE_1000HZ
        self.keychron_poll_rate_usb_mask = 0x7F
        self.keychron_poll_rate_24g = REPORT_RATE_1000HZ
        self.keychron_poll_rate_24g_mask = 0x7F

        # Connection mode (detected from bridge or direct USB)
        # On the web, if the bridge is active, we're connected via 2.4 GHz.
        if sys.platform == "emscripten":
            from hidproxy import hiddevice

            self.keychron_connection_mode = 0 if hiddevice._bridge_active else 2
        else:
            self.keychron_connection_mode = 2  # 0=2.4G, 1=BT, 2=USB

        # Snap Click
        self.keychron_snap_click_count = 0
        self.keychron_snap_click_entries = []

        # Wireless LPM
        self.keychron_wireless_backlit_time = 30
        self.keychron_wireless_idle_time = 300
        self.keychron_battery_level = 0  # 0-100%, only meaningful when on wireless

        # Per-key RGB
        self.keychron_rgb_protocol_version = 0
        self.keychron_led_count = 0
        self.keychron_per_key_rgb_type = PER_KEY_RGB_SOLID
        self.keychron_per_key_colors = []  # list of (H, S, V) tuples
        self.keychron_os_indicator_config = None
        self.keychron_led_matrix = {}  # dict of (row, col) -> LED index

        # Mixed RGB
        self.keychron_mixed_rgb_layers = 0
        self.keychron_mixed_rgb_effects_per_layer = 0
        self.keychron_mixed_rgb_regions = []
        self.keychron_mixed_rgb_effects = []

        # Analog Matrix
        self.keychron_analog_version = 0
        self.keychron_analog_profile_count = 0
        self.keychron_analog_current_profile = 0
        self.keychron_analog_profile_size = 0
        self.keychron_analog_okmc_count = 0
        self.keychron_analog_socd_count = 0
        self.keychron_analog_profiles = []
        self.keychron_analog_curve = []
        self.keychron_analog_game_controller_mode = 0

    def reload_keychron(self):
        """Load Keychron-specific features from the keyboard."""
        # Initialize all attributes first
        self._init_keychron_attrs()

        logging.info("Keychron: Starting reload_keychron")

        # First, check if this is a Keychron keyboard by trying to get protocol version
        try:
            data = self.usb_send(
                self.dev, struct.pack("B", KC_GET_PROTOCOL_VERSION), retries=3
            )
            logging.info(
                "Keychron: KC_GET_PROTOCOL_VERSION response: %s",
                data[:8].hex() if data else "None",
            )
            if data[0] == 0xFF:
                # Not a Keychron keyboard or command not supported
                logging.info("Keychron: Got 0xFF, not a Keychron keyboard")
                self.keychron_features = 0
                return
            self.keychron_protocol_version = data[1]
            logging.info(
                "Keychron: Protocol version: %d", self.keychron_protocol_version
            )
        except (OSError, RuntimeError, struct.error) as e:
            logging.warning("Keychron: Exception during protocol version check: %s", e)
            self.keychron_features = 0
            return

        # Get supported features
        data = self.usb_send(
            self.dev, struct.pack("B", KC_GET_SUPPORT_FEATURE), retries=3
        )
        logging.info(
            "Keychron: KC_GET_SUPPORT_FEATURE response: %s",
            data[:8].hex() if data else "None",
        )
        if data[0] != 0xFF:
            # Features are at data[2] and data[3] (data[1] is unused padding)
            self.keychron_features = data[2] | (data[3] << 8)
            logging.info("Keychron: Features detected: 0x%04X", self.keychron_features)
        else:
            logging.info("Keychron: Got 0xFF for features, aborting")
            self.keychron_features = 0
            return

        # Get firmware version
        data = self.usb_send(
            self.dev, struct.pack("B", KC_GET_FIRMWARE_VERSION), retries=3
        )
        if data[0] != 0xFF:
            # Firmware version is a null-terminated string
            self.keychron_firmware_version = (
                data[1:].split(b"\x00")[0].decode("utf-8", errors="ignore")
            )
            logging.info(
                "Keychron: Firmware version: %s", self.keychron_firmware_version
            )

        # Get MCU chip info via DFU_INFO_GET
        # Response: data[2]=success, data[3]=DFU_INFO_CHIP(1), data[4]=len, data[5..5+len]=MCU string
        data = self.usb_send(
            self.dev,
            struct.pack("BB", KC_MISC_CMD_GROUP, DFU_INFO_GET),
            retries=3,
        )
        if (
            data[0] == KC_MISC_CMD_GROUP
            and data[1] == DFU_INFO_GET
            and data[2] == 0  # success
            and data[3] == 1  # DFU_INFO_CHIP tag
        ):
            chip_len = data[4]
            self.keychron_mcu_info = data[5 : 5 + chip_len].decode(
                "utf-8", errors="ignore"
            )
            logging.info("Keychron: MCU chip: %s", self.keychron_mcu_info)

        # Get misc protocol version and features
        data = self.usb_send(
            self.dev,
            struct.pack("BB", KC_MISC_CMD_GROUP, MISC_GET_PROTOCOL_VER),
            retries=3,
        )
        logging.info(
            "Keychron: KC_MISC_CMD_GROUP response: %s",
            data[:8].hex() if data else "None",
        )
        if data[0] == KC_MISC_CMD_GROUP and data[1] == MISC_GET_PROTOCOL_VER:
            self.keychron_misc_protocol_version = data[3] | (data[4] << 8)
            self.keychron_misc_features = data[5] | (data[6] << 8)
            logging.info(
                "Keychron: Misc protocol version: %d, misc features: 0x%04X",
                self.keychron_misc_protocol_version,
                self.keychron_misc_features,
            )

        logging.info(
            "Keychron: Feature detection - debounce=%s nkro=%s report_rate=%s snap_click=%s wireless=%s rgb=%s analog=%s",
            self.has_keychron_debounce(),
            self.has_keychron_nkro(),
            self.has_keychron_report_rate(),
            self.has_keychron_snap_click(),
            self.has_keychron_wireless(),
            self.has_keychron_rgb(),
            self.has_keychron_analog(),
        )

        # Load individual features
        if self.has_keychron_debounce():
            self._reload_debounce()

        if self.has_keychron_nkro():
            self._reload_nkro()

        if self.has_keychron_report_rate():
            self._reload_report_rate()

        if self.has_keychron_snap_click():
            self._reload_snap_click()

        if self.has_keychron_wireless():
            self._reload_wireless_lpm()
            self.keychron_battery_level = self.get_keychron_battery_level()

        if self.has_keychron_rgb():
            self._reload_keychron_rgb()

        if self.has_keychron_analog():
            self._reload_analog_matrix()

        logging.info("Keychron: reload_keychron complete")

    # Feature detection helpers
    def has_keychron_features(self):
        """Check if this keyboard supports any Keychron features."""
        return getattr(self, "keychron_features", 0) != 0

    def has_keychron_debounce(self):
        """Check if dynamic debounce is supported."""
        return bool(
            getattr(self, "keychron_features", 0) & FEATURE_DYNAMIC_DEBOUNCE
        ) or bool(getattr(self, "keychron_misc_features", 0) & MISC_DEBOUNCE)

    def has_keychron_nkro(self):
        """Check if NKRO toggle is supported."""
        return bool(getattr(self, "keychron_features", 0) & FEATURE_NKRO) or bool(
            getattr(self, "keychron_misc_features", 0) & MISC_NKRO
        )

    def has_keychron_report_rate(self):
        """Check if USB report rate setting is supported."""
        return bool(getattr(self, "keychron_misc_features", 0) & MISC_REPORT_RATE)

    def has_keychron_snap_click(self):
        """Check if Snap Click (SOCD) is supported."""
        return bool(getattr(self, "keychron_features", 0) & FEATURE_SNAP_CLICK) or bool(
            getattr(self, "keychron_misc_features", 0) & MISC_SNAP_CLICK
        )

    def has_keychron_wireless(self):
        """Check if wireless features are supported."""
        return bool(
            getattr(self, "keychron_features", 0) & (FEATURE_BLUETOOTH | FEATURE_P24G)
        ) or bool(getattr(self, "keychron_misc_features", 0) & MISC_WIRELESS_LPM)

    def has_keychron_rgb(self):
        """Check if Keychron RGB features are supported."""
        return bool(getattr(self, "keychron_features", 0) & FEATURE_KEYCHRON_RGB)

    def has_keychron_analog(self):
        """Check if Analog Matrix (Hall Effect) is supported."""
        import os

        if os.environ.get("DEBUG_FORCE_HE", "").lower() in ("1", "true", "yes"):
            return True
        return bool(getattr(self, "keychron_features", 0) & FEATURE_ANALOG_MATRIX)

    def has_keychron_dfu(self):
        """Check if STM32 DFU flashing is supported (stm32-dfu bootloader keyboards)."""
        # Detected via MISC_DFU_INFO flag or MCU info string containing "STM32"
        has_dfu_info = bool(getattr(self, "keychron_misc_features", 0) & MISC_DFU_INFO)
        has_stm32_mcu = "STM32" in getattr(self, "keychron_mcu_info", "")
        return has_dfu_info or has_stm32_mcu

    def has_keychron_default_layer(self):
        """Check if KC_GET_DEFAULT_LAYER is supported."""
        return bool(getattr(self, "keychron_features", 0) & FEATURE_DEFAULT_LAYER)

    def get_keychron_default_layer(self):
        """
        Query the keyboard for the current default layer (set by DIP switch).

        Returns:
            int: Default layer index, or -1 on failure.
        """
        data = self.usb_send(
            self.dev, struct.pack("B", KC_GET_DEFAULT_LAYER), retries=3
        )
        if data[0] == KC_GET_DEFAULT_LAYER:
            return data[1]
        return -1

    def get_keychron_battery_level(self):
        """
        Query the keyboard for the current battery level.

        Returns:
            int: Battery percentage (0-100), or 0 if on USB/unsupported.
        """
        data = self.usb_send(
            self.dev, struct.pack("B", KC_GET_BATTERY_LEVEL), retries=3
        )
        if data[0] == KC_GET_BATTERY_LEVEL:
            return data[1]
        return 0

    # Debounce methods
    def _reload_debounce(self):
        """Load debounce settings."""
        data = self.usb_send(
            self.dev, struct.pack("BB", KC_MISC_CMD_GROUP, DEBOUNCE_GET), retries=3
        )
        if (
            data[0] == KC_MISC_CMD_GROUP
            and data[1] == DEBOUNCE_GET
            and data[2] == KC_SUCCESS
        ):
            # data[3] = 0 for QMK
            self.keychron_debounce_type = data[4]
            self.keychron_debounce_time = data[5]

    def set_keychron_debounce(self, debounce_type, debounce_time):
        """Set debounce settings."""
        data = self.usb_send(
            self.dev,
            struct.pack(
                "BBBB", KC_MISC_CMD_GROUP, DEBOUNCE_SET, debounce_type, debounce_time
            ),
            retries=3,
        )
        if (
            data is not None
            and data[0] == KC_MISC_CMD_GROUP
            and data[1] == DEBOUNCE_SET
            and data[2] == KC_SUCCESS
        ):
            self.keychron_debounce_type = debounce_type
            self.keychron_debounce_time = debounce_time
            return True
        return False

    # NKRO methods
    def _reload_nkro(self):
        """Load NKRO settings."""
        data = self.usb_send(
            self.dev, struct.pack("BB", KC_MISC_CMD_GROUP, NKRO_GET), retries=3
        )
        if (
            data[0] == KC_MISC_CMD_GROUP
            and data[1] == NKRO_GET
            and data[2] == KC_SUCCESS
        ):
            flags = data[3]
            self.keychron_nkro_enabled = bool(flags & 0x01)
            self.keychron_nkro_supported = bool(flags & 0x02)
            self.keychron_nkro_adaptive = bool(flags & 0x04)

    def set_keychron_nkro(self, enabled):
        """Set NKRO enabled/disabled."""
        data = self.usb_send(
            self.dev,
            struct.pack("BBB", KC_MISC_CMD_GROUP, NKRO_SET, 1 if enabled else 0),
            retries=3,
        )
        if (
            data is not None
            and data[0] == KC_MISC_CMD_GROUP
            and data[1] == NKRO_SET
            and data[2] == KC_SUCCESS
        ):
            self.keychron_nkro_enabled = enabled
            return True
        return False

    # Report rate methods
    def _reload_report_rate(self):
        """Load USB report rate settings, detecting v1 (single) or v2 (dual) format."""
        # Poll Rate Detect: misc protocol version 3 = dual rate (v2), else single (v1)
        # This reuses MISC_GET_PROTOCOL_VER (sub-cmd 1) which was already called, so
        # keychron_misc_protocol_version is already set. The launcher instantiates the
        # v2 (Te class, dual rate) poll rate handler when misc_protocol_version == 3.
        if self.keychron_misc_protocol_version == 3:
            self.keychron_poll_rate_version = 2
        else:
            self.keychron_poll_rate_version = 1

        data = self.usb_send(
            self.dev, struct.pack("BB", KC_MISC_CMD_GROUP, REPORT_RATE_GET), retries=3
        )
        if (
            data[0] == KC_MISC_CMD_GROUP
            and data[1] == REPORT_RATE_GET
            and data[2] == KC_SUCCESS
        ):
            if self.keychron_poll_rate_version == 2:
                # v2 dual rate: data[3]=current_usb, data[4]=support_usb,
                #               data[5]=support_fr,  data[6]=current_fr
                self.keychron_poll_rate_usb = data[3]
                self.keychron_poll_rate_usb_mask = data[4] if len(data) > 4 else 0x7F
                self.keychron_poll_rate_24g_mask = data[5] if len(data) > 5 else 0x7F
                self.keychron_poll_rate_24g = data[6] if len(data) > 6 else data[3]
                # Keep legacy fields in sync for backward compatibility
                self.keychron_report_rate = self.keychron_poll_rate_usb
                self.keychron_report_rate_mask = self.keychron_poll_rate_usb_mask
                logging.info(
                    "Keychron: Poll rate v2 (dual) - USB=%d (mask=0x%02X), "
                    "2.4G=%d (mask=0x%02X)",
                    self.keychron_poll_rate_usb,
                    self.keychron_poll_rate_usb_mask,
                    self.keychron_poll_rate_24g,
                    self.keychron_poll_rate_24g_mask,
                )
            else:
                # v1 single rate: data[3]=rate, data[4]=support mask
                self.keychron_report_rate = data[3]
                self.keychron_report_rate_mask = data[4] if len(data) > 4 else 0x7F
                self.keychron_poll_rate_usb = self.keychron_report_rate
                self.keychron_poll_rate_usb_mask = self.keychron_report_rate_mask

    def set_keychron_report_rate(self, rate):
        """Set USB report rate (v1 single rate)."""
        data = self.usb_send(
            self.dev,
            struct.pack("BBB", KC_MISC_CMD_GROUP, REPORT_RATE_SET, rate),
            retries=3,
        )
        if (
            data is not None
            and data[0] == KC_MISC_CMD_GROUP
            and data[1] == REPORT_RATE_SET
            and data[2] == KC_SUCCESS
        ):
            self.keychron_report_rate = rate
            self.keychron_poll_rate_usb = rate
            return True
        return False

    def set_keychron_poll_rate_v2(self, usb_rate, fr_rate):
        """Set dual polling rates (v2): separate USB and 2.4 GHz rates."""
        data = self.usb_send(
            self.dev,
            struct.pack("BBBB", KC_MISC_CMD_GROUP, REPORT_RATE_SET, usb_rate, fr_rate),
            retries=3,
        )
        if (
            data is not None
            and data[0] == KC_MISC_CMD_GROUP
            and data[1] == REPORT_RATE_SET
            and data[2] == KC_SUCCESS
        ):
            self.keychron_poll_rate_usb = usb_rate
            self.keychron_poll_rate_24g = fr_rate
            self.keychron_report_rate = usb_rate
            return True
        return False

    # Snap Click methods
    def _reload_snap_click(self):
        """Load Snap Click settings."""
        import logging

        # Get count
        data = self.usb_send(
            self.dev,
            struct.pack("BB", KC_MISC_CMD_GROUP, SNAP_CLICK_GET_INFO),
            retries=3,
        )
        logging.info(
            "Keychron: SNAP_CLICK_GET_INFO response: %s",
            data[:8].hex() if data else "None",
        )
        if (
            data is not None
            and data[0] == KC_MISC_CMD_GROUP
            and data[1] == SNAP_CLICK_GET_INFO
            and data[2] == KC_SUCCESS
        ):
            # Count is at data[3] (data[2] is success flag)
            self.keychron_snap_click_count = data[3]
            logging.info(
                "Keychron: Snap Click count: %d", self.keychron_snap_click_count
            )

        # Get all entries
        self.keychron_snap_click_entries = []
        if self.keychron_snap_click_count > 0:
            # Each entry is 3 bytes (type, key1, key2), we can fetch up to 8 per packet
            idx = 0
            while idx < self.keychron_snap_click_count:
                count = min(8, self.keychron_snap_click_count - idx)
                data = self.usb_send(
                    self.dev,
                    struct.pack("BBBB", KC_MISC_CMD_GROUP, SNAP_CLICK_GET, idx, count),
                    retries=3,
                )
                logging.info(
                    "Keychron: SNAP_CLICK_GET response: %s",
                    data[:32].hex() if data else "None",
                )
                if (
                    data is not None
                    and data[0] == KC_MISC_CMD_GROUP
                    and data[1] == SNAP_CLICK_GET
                    and data[2] == KC_SUCCESS
                ):
                    # Each snap_click_config_t is 3 bytes: type(1) + key[0](1) + key[1](1)
                    for i in range(count):
                        offset = 3 + i * 3
                        entry = {
                            "type": data[offset],
                            "key1": data[offset + 1],
                            "key2": data[offset + 2],
                        }
                        self.keychron_snap_click_entries.append(entry)
                        logging.info(
                            "Keychron: Snap Click entry %d: type=%d key1=0x%02X key2=0x%02X",
                            idx + i,
                            entry["type"],
                            entry["key1"],
                            entry["key2"],
                        )
                idx += count

    def set_keychron_snap_click(self, index, snap_type, key1, key2):
        """Set a single Snap Click entry."""
        # snap_click_config_t uses uint8_t key[2], clamp to 8 bits
        key1 = key1 & 0xFF
        key2 = key2 & 0xFF
        data = self.usb_send(
            self.dev,
            struct.pack(
                "BBBBBBB",
                KC_MISC_CMD_GROUP,
                SNAP_CLICK_SET,
                index,
                1,
                snap_type,
                key1,
                key2,
            ),
            retries=3,
        )
        if (
            data is not None
            and data[0] == KC_MISC_CMD_GROUP
            and data[1] == SNAP_CLICK_SET
            and data[2] == KC_SUCCESS
        ):
            if index < len(self.keychron_snap_click_entries):
                self.keychron_snap_click_entries[index] = {
                    "type": snap_type,
                    "key1": key1,
                    "key2": key2,
                }
            return True
        return False

    def save_keychron_snap_click(self):
        """Save Snap Click settings to EEPROM."""
        data = self.usb_send(
            self.dev, struct.pack("BB", KC_MISC_CMD_GROUP, SNAP_CLICK_SAVE), retries=3
        )
        return (
            data is not None
            and data[0] == KC_MISC_CMD_GROUP
            and data[1] == SNAP_CLICK_SAVE
            and data[2] == KC_SUCCESS
        )

    def save_keychron_settings(self):
        """Serialize all Keychron settings to a JSON-compatible dict for layout save."""
        data = {}
        if self.has_keychron_debounce():
            data["debounce"] = {
                "type": self.keychron_debounce_type,
                "time": self.keychron_debounce_time,
            }
        if self.has_keychron_nkro() and not self.keychron_nkro_adaptive:
            data["nkro"] = {"enabled": self.keychron_nkro_enabled}
        if self.has_keychron_report_rate():
            if self.keychron_poll_rate_version == 2:
                data["report_rate_v2"] = {
                    "usb": self.keychron_poll_rate_usb,
                    "fr": self.keychron_poll_rate_24g,
                }
            else:
                data["report_rate"] = self.keychron_report_rate
        if self.has_keychron_wireless():
            data["wireless_lpm"] = {
                "backlit_time": self.keychron_wireless_backlit_time,
                "idle_time": self.keychron_wireless_idle_time,
            }
        if self.has_keychron_snap_click() and self.keychron_snap_click_count > 0:
            data["snap_click"] = self.keychron_snap_click_entries[:]
        if self.has_keychron_rgb():
            rgb = {}
            # Save the active VialRGB global effect so we can re-apply it on
            # restore — without this the global effect keeps running on top of
            # restored per-key colors.
            if getattr(self, "lighting_vialrgb", False):
                rgb["vialrgb_mode"] = getattr(self, "rgb_mode", 0)
                rgb["vialrgb_speed"] = getattr(self, "rgb_speed", 128)
                rgb["vialrgb_hsv"] = list(getattr(self, "rgb_hsv", (0, 255, 255)))
            rgb["per_key_rgb_type"] = self.keychron_per_key_rgb_type
            # Store colors as [H, S, V] lists (JSON doesn't support tuples)
            rgb["per_key_colors"] = [list(c) for c in self.keychron_per_key_colors]
            if self.keychron_os_indicator_config is not None:
                cfg = self.keychron_os_indicator_config
                rgb["os_indicator"] = {
                    "disable_mask": cfg.get("disable_mask", 0),
                    "hue": cfg.get("hue", 0),
                    "sat": cfg.get("sat", 255),
                    "val": cfg.get("val", 255),
                }
            if self.keychron_mixed_rgb_layers > 0:
                rgb["mixed_rgb_regions"] = list(self.keychron_mixed_rgb_regions)
                rgb["mixed_rgb_effects"] = [
                    list(layer) for layer in self.keychron_mixed_rgb_effects
                ]
            data["rgb"] = rgb
        if self.has_keychron_analog() and self.keychron_analog_profile_count > 0:
            analog = {}
            analog["current_profile"] = self.keychron_analog_current_profile
            analog["curve"] = list(self.keychron_analog_curve)
            analog["game_controller_mode"] = self.keychron_analog_game_controller_mode
            profiles = []
            for p in range(self.keychron_analog_profile_count):
                prof = {}
                prof["name"] = self.get_keychron_analog_profile_name(p)
                # Key configs: store as "row,col" -> settings dict
                key_configs = self.get_keychron_analog_key_configs(p) or {}
                prof["key_configs"] = {
                    "{},{}".format(r, c): cfg for (r, c), cfg in key_configs.items()
                }
                # SOCD pairs
                prof["socd_pairs"] = self.get_keychron_analog_socd_pairs(p)
                # OKMC (DKS) slot configs
                okmc = self.get_keychron_analog_okmc_configs(p)
                prof["okmc_configs"] = okmc if okmc is not None else []
                profiles.append(prof)
            analog["profiles"] = profiles
            data["analog"] = analog
        return data

    def restore_keychron_settings(self, data):
        """Restore Keychron settings from a dict loaded from a layout file."""
        if not data:
            return
        if "debounce" in data and self.has_keychron_debounce():
            d = data["debounce"]
            self.set_keychron_debounce(
                d.get("type", self.keychron_debounce_type),
                d.get("time", self.keychron_debounce_time),
            )
        if (
            "nkro" in data
            and self.has_keychron_nkro()
            and not self.keychron_nkro_adaptive
        ):
            self.set_keychron_nkro(data["nkro"].get("enabled", False))
        if "report_rate" in data and self.has_keychron_report_rate():
            rate = data["report_rate"]
            if self.keychron_report_rate_mask & (1 << rate):
                self.set_keychron_report_rate(rate)
        if "report_rate_v2" in data and self.has_keychron_report_rate():
            rv2 = data["report_rate_v2"]
            usb = rv2.get("usb", self.keychron_poll_rate_usb)
            fr = rv2.get("fr", self.keychron_poll_rate_24g)
            if self.keychron_poll_rate_version == 2:
                self.set_keychron_poll_rate_v2(usb, fr)
            else:
                # Fallback: apply USB rate as single rate
                if self.keychron_report_rate_mask & (1 << usb):
                    self.set_keychron_report_rate(usb)
        if "wireless_lpm" in data and self.has_keychron_wireless():
            w = data["wireless_lpm"]
            self.set_keychron_wireless_lpm(
                w.get("backlit_time", self.keychron_wireless_backlit_time),
                w.get("idle_time", self.keychron_wireless_idle_time),
            )
        if "snap_click" in data and self.has_keychron_snap_click():
            for i, entry in enumerate(data["snap_click"]):
                if i >= self.keychron_snap_click_count:
                    break
                self.set_keychron_snap_click(
                    i,
                    entry.get("type", 0),
                    entry.get("key1", 0),
                    entry.get("key2", 0),
                )
            if self.keychron_snap_click_count > 0:
                self.save_keychron_snap_click()
        if "rgb" in data and self.has_keychron_rgb():
            rgb = data["rgb"]
            # Per-key RGB type
            if "per_key_rgb_type" in rgb:
                self.set_keychron_per_key_rgb_type(rgb["per_key_rgb_type"])
            # Per-key colors — only restore as many as the keyboard currently has
            for i, color in enumerate(rgb.get("per_key_colors", [])):
                if i >= self.keychron_led_count:
                    break
                h, s, v = color[0], color[1], color[2]
                self.set_keychron_per_key_color(i, h, s, v)
            # OS indicator config
            if "os_indicator" in rgb and self.keychron_os_indicator_config is not None:
                ind = rgb["os_indicator"]
                self.set_keychron_os_indicator_config(
                    ind.get("disable_mask", 0),
                    ind.get("hue", 0),
                    ind.get("sat", 255),
                    ind.get("val", 255),
                )
            # Mixed RGB regions and effects — only if firmware has mixed RGB
            if (
                "mixed_rgb_regions" in rgb
                and self.keychron_mixed_rgb_layers > 0
                and self.keychron_led_count > 0
            ):
                regions = rgb["mixed_rgb_regions"]
                # Clamp to current LED count
                regions = regions[: self.keychron_led_count]
                self.set_mixed_rgb_regions(0, regions)
            if "mixed_rgb_effects" in rgb and self.keychron_mixed_rgb_layers > 0:
                for region, effects in enumerate(rgb["mixed_rgb_effects"]):
                    if region >= self.keychron_mixed_rgb_layers:
                        break
                    self.set_mixed_rgb_effect_list(region, 0, effects)
            # Flush all RGB changes to EEPROM
            self.save_keychron_rgb()
            # Restore the active VialRGB global effect (mode/speed/HSV).
            # This must happen AFTER save_keychron_rgb() so the per-key data
            # is already written, and AFTER setting the mode so it takes effect
            # immediately without bleed from the previously-active effect.
            if getattr(self, "lighting_vialrgb", False) and "vialrgb_mode" in rgb:
                saved_mode = rgb["vialrgb_mode"]
                saved_speed = rgb.get("vialrgb_speed", 128)
                saved_hsv = rgb.get("vialrgb_hsv", [0, 255, 255])
                # Apply the saved state to firmware RAM (no-EEPROM variant used
                # internally by _vialrgb_set_mode) then persist via save_rgb().
                self.rgb_mode = saved_mode
                self.rgb_speed = saved_speed
                self.rgb_hsv = tuple(saved_hsv)
                self._vialrgb_set_mode()
                self.save_rgb()
        if "analog" in data and self.has_keychron_analog():
            analog = data["analog"]
            rows = getattr(self, "rows", 0)
            cols = getattr(self, "cols", 0)
            profile_count = self.keychron_analog_profile_count
            for p, prof in enumerate(analog.get("profiles", [])):
                if p >= profile_count:
                    break
                # Restore profile name
                name = prof.get("name", "")
                if name:
                    self.set_keychron_analog_profile_name(p, name)
                # Restore per-key travel configs.
                # Group keys by (mode, act_pt, sens, rls_sens) to minimise USB traffic:
                # find the most common combo and apply it globally, then patch outliers.
                key_configs = prof.get("key_configs", {})
                if key_configs and rows > 0 and cols > 0:
                    parsed = {}
                    for key_str, cfg in key_configs.items():
                        try:
                            r, c = (int(x) for x in key_str.split(","))
                        except (ValueError, AttributeError):
                            continue
                        if r < rows and c < cols:
                            parsed[(r, c)] = cfg
                    if parsed:
                        # Find most common (mode, act_pt, sens, rls_sens) tuple
                        from collections import Counter

                        travel_tuples = [
                            (
                                cfg.get("mode", 1),
                                cfg.get("actuation_point", 20),
                                cfg.get("sensitivity", 3),
                                cfg.get("release_sensitivity", 3),
                            )
                            for cfg in parsed.values()
                        ]
                        most_common_travel = Counter(travel_tuples).most_common(1)[0][0]
                        mode_g, act_pt_g, sens_g, rls_g = most_common_travel
                        # Apply global setting to all keys
                        self.set_keychron_analog_travel(
                            p, mode_g, act_pt_g, sens_g, rls_g, entire=True
                        )
                        # Apply per-key overrides for keys that differ from the global
                        # Group outliers by their travel combo to batch by row_mask
                        override_groups = {}
                        for (r, c), cfg in parsed.items():
                            combo = (
                                cfg.get("mode", 1),
                                cfg.get("actuation_point", 20),
                                cfg.get("sensitivity", 3),
                                cfg.get("release_sensitivity", 3),
                            )
                            if combo != most_common_travel:
                                override_groups.setdefault(combo, []).append((r, c))
                        for (
                            mode_o,
                            act_pt_o,
                            sens_o,
                            rls_o,
                        ), keys in override_groups.items():
                            # Build per-row column bitmasks
                            row_mask = [0] * rows
                            for r, c in keys:
                                row_mask[r] |= 1 << c
                            self.set_keychron_analog_travel(
                                p,
                                mode_o,
                                act_pt_o,
                                sens_o,
                                rls_o,
                                entire=False,
                                row_mask=row_mask,
                            )
                        # Restore advance modes per key
                        for (r, c), cfg in parsed.items():
                            adv = cfg.get("adv_mode", 0)
                            adv_data = cfg.get("adv_mode_data", 0)
                            if adv == ADV_MODE_TOGGLE:
                                self.set_keychron_analog_advance_mode_toggle(p, r, c)
                            elif adv == ADV_MODE_GAME_CONTROLLER:
                                self.set_keychron_analog_advance_mode_gamepad(
                                    p, r, c, adv_data
                                )
                            elif adv == ADV_MODE_OKMC:
                                # DKS: the slot config is in okmc_configs[adv_data]
                                okmc_list = prof.get("okmc_configs", [])
                                if adv_data < len(okmc_list):
                                    slot = okmc_list[adv_data]
                                    self.set_keychron_analog_advance_mode_dks(
                                        p,
                                        r,
                                        c,
                                        adv_data,
                                        slot.get("shallow_act", 0),
                                        slot.get("shallow_deact", 0),
                                        slot.get("deep_act", 0),
                                        slot.get("deep_deact", 0),
                                        slot.get("keycodes", [0, 0, 0, 0]),
                                        slot.get("actions", [{}, {}, {}, {}]),
                                    )
                            # ADV_MODE_CLEAR (0) means no advance mode — no call needed
                # Restore SOCD pairs
                for i, pair in enumerate(prof.get("socd_pairs", [])):
                    if i >= getattr(self, "keychron_analog_socd_count", 0):
                        break
                    self.set_keychron_analog_socd(
                        p,
                        pair.get("row1", 0),
                        pair.get("col1", 0),
                        pair.get("row2", 0),
                        pair.get("col2", 0),
                        i,
                        pair.get("type", 0),
                    )
                # Flush profile to EEPROM
                self.save_keychron_analog_profile(p)
            # Restore global analog settings
            if "curve" in analog and len(analog["curve"]) >= 4:
                self.set_keychron_analog_curve(analog["curve"])
            if "game_controller_mode" in analog:
                self.set_keychron_analog_game_controller_mode(
                    analog["game_controller_mode"]
                )
            # Re-select current profile last
            if "current_profile" in analog:
                cp = analog["current_profile"]
                if cp < profile_count:
                    self.select_keychron_analog_profile(cp)

    def _reload_wireless_lpm(self):
        """Load wireless low power mode settings."""
        data = self.usb_send(
            self.dev, struct.pack("BB", KC_MISC_CMD_GROUP, WIRELESS_LPM_GET), retries=3
        )
        if (
            data[0] == KC_MISC_CMD_GROUP
            and data[1] == WIRELESS_LPM_GET
            and data[2] == KC_SUCCESS
        ):
            self.keychron_wireless_backlit_time = data[3] | (data[4] << 8)
            self.keychron_wireless_idle_time = data[5] | (data[6] << 8)

    def set_keychron_wireless_lpm(self, backlit_time, idle_time):
        """Set wireless low power mode settings."""
        # Minimum values
        backlit_time = max(5, backlit_time)
        idle_time = max(60, idle_time)
        data = self.usb_send(
            self.dev,
            struct.pack(
                "<BBHH", KC_MISC_CMD_GROUP, WIRELESS_LPM_SET, backlit_time, idle_time
            ),
            retries=3,
        )
        if (
            data is not None
            and data[0] == KC_MISC_CMD_GROUP
            and data[1] == WIRELESS_LPM_SET
            and data[2] == KC_SUCCESS
        ):
            self.keychron_wireless_backlit_time = backlit_time
            self.keychron_wireless_idle_time = idle_time
            return True
        return False

    # Keychron RGB methods
    def _reload_keychron_rgb(self):
        """Load Keychron RGB settings."""
        # Get protocol version
        data = self.usb_send(
            self.dev,
            struct.pack("BB", KC_KEYCHRON_RGB, RGB_GET_PROTOCOL_VER),
            retries=3,
        )
        if data[0] == KC_KEYCHRON_RGB and data[1] == RGB_GET_PROTOCOL_VER:
            self.keychron_rgb_protocol_version = data[3] | (data[4] << 8)

        # Get LED count
        data = self.usb_send(
            self.dev, struct.pack("BB", KC_KEYCHRON_RGB, RGB_GET_LED_COUNT), retries=3
        )
        if (
            data[0] == KC_KEYCHRON_RGB
            and data[1] == RGB_GET_LED_COUNT
            and data[2] == KC_SUCCESS
        ):
            self.keychron_led_count = data[3]

        # Get per-key RGB type
        data = self.usb_send(
            self.dev,
            struct.pack("BB", KC_KEYCHRON_RGB, PER_KEY_RGB_GET_TYPE),
            retries=3,
        )
        if (
            data[0] == KC_KEYCHRON_RGB
            and data[1] == PER_KEY_RGB_GET_TYPE
            and data[2] == KC_SUCCESS
        ):
            self.keychron_per_key_rgb_type = data[3]

        # Get OS indicator config
        data = self.usb_send(
            self.dev,
            struct.pack("BB", KC_KEYCHRON_RGB, GET_INDICATORS_CONFIG),
            retries=3,
        )
        if (
            data[0] == KC_KEYCHRON_RGB
            and data[1] == GET_INDICATORS_CONFIG
            and data[2] == KC_SUCCESS
        ):
            self.keychron_os_indicator_config = {
                "available_mask": data[3],
                "disable_mask": data[4],
                "hue": data[5],
                "sat": data[6],
                "val": data[7],
            }

        # Get per-key colors (in batches of 9)
        self.keychron_per_key_colors = []
        if self.keychron_led_count > 0:
            idx = 0
            while idx < self.keychron_led_count:
                count = min(9, self.keychron_led_count - idx)
                data = self.usb_send(
                    self.dev,
                    struct.pack(
                        "BBBB", KC_KEYCHRON_RGB, PER_KEY_RGB_GET_COLOR, idx, count
                    ),
                    retries=3,
                )
                if (
                    data[0] == KC_KEYCHRON_RGB
                    and data[1] == PER_KEY_RGB_GET_COLOR
                    and data[2] == KC_SUCCESS
                ):
                    for i in range(count):
                        offset = 3 + i * 3
                        self.keychron_per_key_colors.append(
                            (data[offset], data[offset + 1], data[offset + 2])
                        )
                idx += count

        # Get mixed RGB info
        data = self.usb_send(
            self.dev,
            struct.pack("BB", KC_KEYCHRON_RGB, MIXED_EFFECT_RGB_GET_INFO),
            retries=3,
        )
        if (
            data[0] == KC_KEYCHRON_RGB
            and data[1] == MIXED_EFFECT_RGB_GET_INFO
            and data[2] == KC_SUCCESS
        ):
            self.keychron_mixed_rgb_layers = data[3]
            self.keychron_mixed_rgb_effects_per_layer = data[4]

        # Load mixed RGB regions if layers are configured
        if self.keychron_mixed_rgb_layers > 0 and self.keychron_led_count > 0:
            self.keychron_mixed_rgb_regions = self.get_mixed_rgb_regions()
            # Load effects for each region
            self.keychron_mixed_rgb_effects = []
            for region in range(self.keychron_mixed_rgb_layers):
                effects = self.get_mixed_rgb_effect_list(region)
                self.keychron_mixed_rgb_effects.append(effects)

        # Load LED matrix mapping (row, col) -> LED index
        self.reload_led_matrix_mapping()

    def set_keychron_per_key_rgb_type(self, rgb_type):
        """Set per-key RGB effect type."""
        data = self.usb_send(
            self.dev,
            struct.pack("BBB", KC_KEYCHRON_RGB, PER_KEY_RGB_SET_TYPE, rgb_type),
            retries=3,
        )
        if (
            data[0] == KC_KEYCHRON_RGB
            and data[1] == PER_KEY_RGB_SET_TYPE
            and data[2] == KC_SUCCESS
        ):
            self.keychron_per_key_rgb_type = rgb_type
            return True
        return False

    def set_keychron_per_key_color(self, index, h, s, v):
        """Set color for a single LED."""
        data = self.usb_send(
            self.dev,
            struct.pack(
                "BBBBBBB", KC_KEYCHRON_RGB, PER_KEY_RGB_SET_COLOR, index, 1, h, s, v
            ),
            retries=3,
        )
        if (
            data[0] == KC_KEYCHRON_RGB
            and data[1] == PER_KEY_RGB_SET_COLOR
            and data[2] == KC_SUCCESS
        ):
            if index < len(self.keychron_per_key_colors):
                self.keychron_per_key_colors[index] = (h, s, v)
            return True
        return False

    def set_keychron_os_indicator_config(self, disable_mask, h, s, v):
        """Set OS indicator configuration."""
        data = self.usb_send(
            self.dev,
            struct.pack(
                "BBBBBB", KC_KEYCHRON_RGB, SET_INDICATORS_CONFIG, disable_mask, h, s, v
            ),
            retries=3,
        )
        if (
            data[0] == KC_KEYCHRON_RGB
            and data[1] == SET_INDICATORS_CONFIG
            and data[2] == KC_SUCCESS
        ):
            # Preserve available_mask (read-only from firmware) when updating cache
            available_mask = (self.keychron_os_indicator_config or {}).get(
                "available_mask", 0
            )
            self.keychron_os_indicator_config = {
                "available_mask": available_mask,
                "disable_mask": disable_mask,
                "hue": h,
                "sat": s,
                "val": v,
            }
            return True
        return False

    def save_keychron_rgb(self):
        """Save RGB settings to EEPROM."""
        data = self.usb_send(
            self.dev, struct.pack("BB", KC_KEYCHRON_RGB, RGB_SAVE), retries=3
        )
        return (
            data[0] == KC_KEYCHRON_RGB and data[1] == RGB_SAVE and data[2] == KC_SUCCESS
        )

    # Mixed RGB methods
    def get_mixed_rgb_regions(self, start=0, count=None):
        """
        Get LED region assignments for Mixed RGB.

        Args:
            start: Starting LED index
            count: Number of LEDs to fetch (defaults to all remaining)

        Returns:
            List of region assignments (0 to EFFECT_LAYERS-1) for each LED
        """
        if count is None:
            count = self.keychron_led_count - start

        regions = []
        idx = start
        remaining = count

        while remaining > 0:
            # Max 29 regions per packet (32 - cmd - subcmd - success)
            batch = min(29, remaining)
            data = self.usb_send(
                self.dev,
                struct.pack(
                    "BBBB",
                    KC_KEYCHRON_RGB,
                    MIXED_EFFECT_RGB_GET_REGIONS,
                    idx,
                    batch,
                ),
                retries=3,
            )
            if (
                data[0] == KC_KEYCHRON_RGB
                and data[1] == MIXED_EFFECT_RGB_GET_REGIONS
                and data[2] == KC_SUCCESS
            ):
                for i in range(batch):
                    regions.append(data[3 + i])
            else:
                # Failed to get regions
                break
            idx += batch
            remaining -= batch

        return regions

    def set_mixed_rgb_regions(self, start, regions):
        """
        Set LED region assignments for Mixed RGB.

        Args:
            start: Starting LED index
            regions: List of region assignments to set

        Returns:
            True if successful
        """
        idx = start
        offset = 0

        while offset < len(regions):
            # Max 28 regions per packet (32 - cmd - subcmd - start - count)
            batch = min(28, len(regions) - offset)
            packet = struct.pack(
                "BBBB",
                KC_KEYCHRON_RGB,
                MIXED_EFFECT_RGB_SET_REGIONS,
                idx,
                batch,
            )
            packet += bytes(regions[offset : offset + batch])

            data = self.usb_send(self.dev, packet, retries=3)
            if not (
                data[0] == KC_KEYCHRON_RGB
                and data[1] == MIXED_EFFECT_RGB_SET_REGIONS
                and data[2] == KC_SUCCESS
            ):
                return False
            idx += batch
            offset += batch

        return True

    def get_mixed_rgb_effect_list(self, region, start=0, count=None):
        """
        Get effect list for a Mixed RGB region.

        Args:
            region: Region index (0 to EFFECT_LAYERS-1)
            start: Starting effect index
            count: Number of effects to fetch

        Returns:
            List of effect dicts with keys: effect, hue, sat, speed, time
        """
        if count is None:
            count = self.keychron_mixed_rgb_effects_per_layer

        effects = []
        idx = start
        remaining = count

        while remaining > 0:
            # Max 3 effects per packet (each effect is 8 bytes)
            batch = min(3, remaining)
            data = self.usb_send(
                self.dev,
                struct.pack(
                    "BBBBB",
                    KC_KEYCHRON_RGB,
                    MIXED_EFFECT_RGB_GET_EFFECT_LIST,
                    region,
                    idx,
                    batch,
                ),
                retries=3,
            )
            if (
                data[0] == KC_KEYCHRON_RGB
                and data[1] == MIXED_EFFECT_RGB_GET_EFFECT_LIST
                and data[2] == KC_SUCCESS
            ):
                for i in range(batch):
                    offset = 3 + i * 8
                    effect_data = {
                        "effect": data[offset],
                        "hue": data[offset + 1],
                        "sat": data[offset + 2],
                        "speed": data[offset + 3],
                        "time": struct.unpack_from("<I", data, offset + 4)[0],
                    }
                    effects.append(effect_data)
            else:
                break
            idx += batch
            remaining -= batch

        return effects

    def set_mixed_rgb_effect_list(self, region, start, effects):
        """
        Set effect list for a Mixed RGB region.

        Args:
            region: Region index (0 to EFFECT_LAYERS-1)
            start: Starting effect index
            effects: List of effect dicts with keys: effect, hue, sat, speed, time

        Returns:
            True if successful
        """
        idx = start
        offset = 0

        while offset < len(effects):
            # Max 3 effects per packet
            batch = min(3, len(effects) - offset)
            packet = struct.pack(
                "BBBBB",
                KC_KEYCHRON_RGB,
                MIXED_EFFECT_RGB_SET_EFFECT_LIST,
                region,
                idx,
                batch,
            )
            for i in range(batch):
                eff = effects[offset + i]
                packet += struct.pack(
                    "<BBBBI",
                    eff.get("effect", 0),
                    eff.get("hue", 0),
                    eff.get("sat", 255),
                    eff.get("speed", 128),
                    eff.get("time", 5000),
                )

            data = self.usb_send(self.dev, packet, retries=3)
            if not (
                data[0] == KC_KEYCHRON_RGB
                and data[1] == MIXED_EFFECT_RGB_SET_EFFECT_LIST
                and data[2] == KC_SUCCESS
            ):
                return False
            idx += batch
            offset += batch

        return True

    def get_led_indices_for_row(self, row, col_mask):
        """
        Get LED indices for a specific row.

        Args:
            row: Matrix row number
            col_mask: 24-bit mask indicating which columns to query

        Returns:
            List of LED indices for each column (0xFF means no LED)
        """
        # Pack: command, sub-command, row, col_mask (3 bytes little-endian)
        data = self.usb_send(
            self.dev,
            struct.pack(
                "<BBBBBBB",
                KC_KEYCHRON_RGB,
                RGB_GET_LED_IDX,
                row,
                col_mask & 0xFF,
                (col_mask >> 8) & 0xFF,
                (col_mask >> 16) & 0xFF,
                0,  # padding
            ),
            retries=3,
        )
        if (
            data[0] == KC_KEYCHRON_RGB
            and data[1] == RGB_GET_LED_IDX
            and data[2] == KC_SUCCESS
        ):
            # LED indices start at data[3]
            return list(data[3 : 3 + 24])  # max 24 columns
        return None

    def reload_led_matrix_mapping(self):
        """
        Reload the LED matrix mapping from the keyboard.

        This builds a dict mapping (row, col) -> LED index for all keys
        that have LEDs. Keys without LEDs will not be in the dict.
        """
        if not self.has_keychron_rgb():
            return

        self.keychron_led_matrix = {}

        # rows and cols are attributes from the Keyboard class (this is a mixin)
        rows = getattr(self, "rows", 0)
        cols = getattr(self, "cols", 0)

        # Query each row
        for row in range(rows):
            # Query all columns in this row (create a mask for all cols)
            col_mask = (1 << cols) - 1
            led_indices = self.get_led_indices_for_row(row, col_mask)
            if led_indices:
                for col in range(min(cols, len(led_indices))):
                    led_idx = led_indices[col]
                    if led_idx != 0xFF:
                        self.keychron_led_matrix[(row, col)] = led_idx

    # Analog Matrix methods
    def _reload_analog_matrix(self):
        """Load Analog Matrix (Hall Effect) settings."""
        # Get version
        data = self.usb_send(
            self.dev, struct.pack("BB", KC_ANALOG_MATRIX, AMC_GET_VERSION), retries=3
        )
        if data[0] == KC_ANALOG_MATRIX and data[1] == AMC_GET_VERSION:
            self.keychron_analog_version = data[
                2
            ]  # firmware writes 1 byte: KC_ANALOG_MATRIX_VERSION & 0xFF

        # Get profiles info
        data = self.usb_send(
            self.dev,
            struct.pack("BB", KC_ANALOG_MATRIX, AMC_GET_PROFILES_INFO),
            retries=3,
        )
        if data[0] == KC_ANALOG_MATRIX and data[1] == AMC_GET_PROFILES_INFO:
            self.keychron_analog_current_profile = data[2]
            self.keychron_analog_profile_count = data[3]
            self.keychron_analog_profile_size = data[4] | (data[5] << 8)
            self.keychron_analog_okmc_count = data[6]
            self.keychron_analog_socd_count = data[7]

        # Get joystick curve
        data = self.usb_send(
            self.dev, struct.pack("BB", KC_ANALOG_MATRIX, AMC_GET_CURVE), retries=3
        )
        if data[0] == KC_ANALOG_MATRIX and data[1] == AMC_GET_CURVE:
            # Firmware writes point_t[4] directly at data[2] with NO status byte.
            # Each point_t is {uint8_t x, uint8_t y} = 2 bytes.
            self.keychron_analog_curve = []
            for i in range(4):
                x = data[2 + i * 2]
                y = data[3 + i * 2]
                self.keychron_analog_curve.append((x, y))

        # Get game controller mode
        data = self.usb_send(
            self.dev,
            struct.pack("BB", KC_ANALOG_MATRIX, AMC_GET_GAME_CONTROLLER_MODE),
            retries=3,
        )
        if (
            data[0] == KC_ANALOG_MATRIX
            and data[1] == AMC_GET_GAME_CONTROLLER_MODE
            and data[2] == KC_SUCCESS
        ):
            self.keychron_analog_game_controller_mode = data[3]

    def select_keychron_analog_profile(self, profile_index):
        """Select an analog profile."""
        data = self.usb_send(
            self.dev,
            struct.pack("BBB", KC_ANALOG_MATRIX, AMC_SELECT_PROFILE, profile_index),
            retries=3,
        )
        if (
            data[0] == KC_ANALOG_MATRIX
            and data[1] == AMC_SELECT_PROFILE
            and data[2] == KC_SUCCESS
        ):
            self.keychron_analog_current_profile = profile_index
            return True
        return False

    def set_keychron_analog_travel(
        self, profile, mode, act_pt, sens, rls_sens, entire=True, row_mask=None
    ):
        """
        Set analog travel/actuation settings.

        Args:
            profile: Profile index
            mode: AKM_GLOBAL / AKM_REGULAR / AKM_RAPID
            act_pt: Actuation point in 0.1mm units (1-39)
            sens: Rapid Trigger press sensitivity in 0.1mm units
            rls_sens: Rapid Trigger release sensitivity in 0.1mm units
            entire: If True, apply to all keys; if False use row_mask
            row_mask: List of per-row 24-bit column bitmasks (one int per row).
                      Firmware reads 3 bytes per row via memcpy.
        """
        logging.info(
            "set_keychron_analog_travel: profile=%s mode=%s act_pt=%s sens=%s rls_sens=%s entire=%s row_mask=%s",
            profile,
            mode,
            act_pt,
            sens,
            rls_sens,
            entire,
            row_mask[:6] if row_mask else None,
        )
        if entire:
            # Apply globally
            data = self.usb_send(
                self.dev,
                struct.pack(
                    "BBBBBBBBB",
                    KC_ANALOG_MATRIX,
                    AMC_SET_TRAVEL,
                    profile,
                    mode,
                    act_pt,
                    sens,
                    rls_sens,
                    1,
                    0,
                ),
                retries=3,
            )
        else:
            # Apply to specific keys using row_mask.
            # Firmware expects MATRIX_ROWS * 3 bytes: 3 LE bytes per row (24-bit col bitmask).
            packet = struct.pack(
                "BBBBBBBB",
                KC_ANALOG_MATRIX,
                AMC_SET_TRAVEL,
                profile,
                mode,
                act_pt,
                sens,
                rls_sens,
                0,  # entire=0
            )
            if row_mask:
                # row_mask is a list of ints (one per row, each a 24-bit col bitmask)
                for mask in row_mask:
                    packet += struct.pack("<I", mask & 0xFFFFFF)[:3]
            data = self.usb_send(self.dev, packet, retries=3)
        logging.info(
            "set_keychron_analog_travel: response=%s",
            data[:6].hex() if data else None,
        )
        return (
            data[0] == KC_ANALOG_MATRIX
            and data[1] == AMC_SET_TRAVEL
            and data[2] == KC_SUCCESS
        )

    def set_keychron_analog_socd(
        self, profile, row1, col1, row2, col2, index, socd_type
    ):
        """Set SOCD pair for analog keyboard."""
        data = self.usb_send(
            self.dev,
            struct.pack(
                "BBBBBBBBB",
                KC_ANALOG_MATRIX,
                AMC_SET_SOCD,
                profile,
                row1,
                col1,
                row2,
                col2,
                index,
                socd_type,
            ),
            retries=3,
        )
        return (
            data[0] == KC_ANALOG_MATRIX
            and data[1] == AMC_SET_SOCD
            and data[2] == KC_SUCCESS
        )

    def save_keychron_analog_profile(self, profile):
        """Save analog profile to EEPROM."""
        data = self.usb_send(
            self.dev,
            struct.pack("BBB", KC_ANALOG_MATRIX, AMC_SAVE_PROFILE, profile),
            retries=3,
        )
        return (
            data[0] == KC_ANALOG_MATRIX
            and data[1] == AMC_SAVE_PROFILE
            and data[2] == KC_SUCCESS
        )

    def reset_keychron_analog_profile(self, profile):
        """Reset analog profile to defaults."""
        data = self.usb_send(
            self.dev,
            struct.pack("BBB", KC_ANALOG_MATRIX, AMC_RESET_PROFILE, profile),
            retries=3,
        )
        return (
            data[0] == KC_ANALOG_MATRIX
            and data[1] == AMC_RESET_PROFILE
            and data[2] == KC_SUCCESS
        )

    def set_keychron_analog_curve(self, curve_points):
        """Set joystick response curve (4 x point_t {x, y} pairs)."""
        packet = struct.pack("BB", KC_ANALOG_MATRIX, AMC_SET_CURVE)
        for x, y in curve_points[:4]:
            packet += struct.pack("BB", x, y)
        data = self.usb_send(self.dev, packet, retries=3)
        if (
            data[0] == KC_ANALOG_MATRIX
            and data[1] == AMC_SET_CURVE
            and data[2] == KC_SUCCESS
        ):
            self.keychron_analog_curve = list(curve_points[:4])
            return True
        return False

    def set_keychron_analog_game_controller_mode(self, mode):
        """Set game controller mode."""
        data = self.usb_send(
            self.dev,
            struct.pack("BBB", KC_ANALOG_MATRIX, AMC_SET_GAME_CONTROLLER_MODE, mode),
            retries=3,
        )
        if (
            data[0] == KC_ANALOG_MATRIX and data[1] == AMC_SET_GAME_CONTROLLER_MODE
            # Firmware does NOT write a status byte for this command;
            # data[2] still holds the mode value we sent.
        ):
            self.keychron_analog_game_controller_mode = mode
            return True
        return False

    def get_keychron_realtime_travel(self, row, col):
        """Get real-time travel value for a key (for debugging/visualization)."""
        data = self.usb_send(
            self.dev,
            struct.pack("BBBB", KC_ANALOG_MATRIX, AMC_GET_REALTIME_TRAVEL, row, col),
            retries=1,
        )
        if (
            data[0] == KC_ANALOG_MATRIX
            and data[1] == AMC_GET_REALTIME_TRAVEL
            and data[2] == KC_SUCCESS
        ):
            return {
                "row": data[3],
                "col": data[4],
                "travel_mm": data[5],  # in 0.1mm units
                "travel_raw": data[6],
                "value": data[7] | (data[8] << 8),
                "zero": data[9] | (data[10] << 8),
                "full": data[11] | (data[12] << 8),
                "state": data[13],
            }
        return None

    def start_keychron_calibration(self, calib_type):
        """Start calibration process."""
        data = self.usb_send(
            self.dev,
            struct.pack("BBB", KC_ANALOG_MATRIX, AMC_CALIBRATE, calib_type),
            retries=3,
        )
        return (
            data[0] == KC_ANALOG_MATRIX
            and data[1] == AMC_CALIBRATE
            and data[2] == KC_SUCCESS
        )

    def get_keychron_calibration_state(self):
        """
        Get current calibration state.

        Firmware response layout for AMC_GET_CALIBRATE_STATE:
          data[2] = calibrated bitmask (bit0=CALI_ZERO_TRAVEL, bit1=CALI_FULL_TRAVEL)
                    NOTE: this is NOT a success/fail code
          data[3] = cali_state enum (current calibration state)
          data[4..] = calib_state_matrix rows (3 bytes each, 24-bit col bitmask)

        Returns:
            Dict with keys:
              - calibrated: bitmask of which calibrations are done
              - state: current cali_state enum value
            or None on failure.
        """
        data = self.usb_send(
            self.dev,
            struct.pack("BB", KC_ANALOG_MATRIX, AMC_GET_CALIBRATE_STATE),
            retries=3,
        )
        if data[0] == KC_ANALOG_MATRIX and data[1] == AMC_GET_CALIBRATE_STATE:
            return {
                "calibrated": data[
                    2
                ],  # bitmask: bit0=zero calibrated, bit1=full calibrated
                "state": data[3],  # current calibration state enum
            }
        return None

    def get_keychron_analog_profile_raw(self, profile, offset=0, size=32):
        """
        Read raw profile data from the keyboard.

        This allows reading the per-key actuation settings stored in the profile.
        The profile structure is:
        - analog_key_config_t global (4 bytes)
        - analog_key_config_t key_config[rows][cols] (4 bytes per key)
        - okmc_config_t okmc[okmc_count]
        - socd_config_t socd[socd_count]
        - uint8_t name[30]
        - uint16_t crc16

        Each analog_key_config_t is 4 bytes:
        - mode:2, act_pt:6 (1 byte)
        - rpd_trig_sen:6, rpd_trig_sen_deact:6 (split across bytes)
        - adv_mode:4, adv_mode_data (remaining bits)

        Args:
            profile: Profile index (0-based)
            offset: Byte offset into the profile data
            size: Number of bytes to read (max 26 per call)

        Returns:
            bytes of raw profile data, or None on failure
        """
        # Limit size to what fits in response (32 - 6 header bytes = 26 max)
        size = min(size, 26)

        data = self.usb_send(
            self.dev,
            struct.pack(
                "BBBBBB",
                KC_ANALOG_MATRIX,
                AMC_GET_PROFILE_RAW,
                profile,
                offset & 0xFF,  # data[3] = offset LSB (firmware: (data[4]<<8)|data[3])
                (offset >> 8) & 0xFF,  # data[4] = offset MSB
                size,  # data[5] = size
            ),
            retries=3,
        )
        if data[0] == KC_ANALOG_MATRIX and data[1] == AMC_GET_PROFILE_RAW:
            # Firmware does NOT set data[2]=success for this command — data[2] stays as
            # the echoed profile index. Data starts at data[6].
            return bytes(data[6 : 6 + size])
        return None

    def get_keychron_analog_key_configs(self, profile):
        """
        Read all per-key actuation configurations from a profile.

        Returns:
            Dict mapping (row, col) -> settings dict, or None on failure.
            Each settings dict contains:
            - mode: 0=Global, 1=Regular, 2=Rapid
            - actuation_point: 0-40 (0.1mm units, 0=use global)
            - sensitivity: 0-40 (0.1mm units, 0=use global)
            - release_sensitivity: 0-40 (0.1mm units, 0=use global)
            - adv_mode: 0-5 (advance mode type)
            - adv_mode_data: raw byte (okmc_idx for DKS, js_axis for Gamepad)
            - js_axis: alias of adv_mode_data for Gamepad keys
        """
        rows = getattr(self, "rows", 0)
        cols = getattr(self, "cols", 0)

        if rows == 0 or cols == 0:
            return None

        # Global config is at offset 0, 4 bytes
        # Per-key configs start at offset 4, 4 bytes per key
        # Total per-key data = rows * cols * 4 bytes

        global_offset = 0
        per_key_offset = 4
        per_key_size = rows * cols * 4

        # Read global config first
        global_data = self.get_keychron_analog_profile_raw(profile, global_offset, 4)
        if not global_data or len(global_data) < 4:
            logging.warning("Failed to read global analog config")
            return None

        global_config = self._parse_analog_key_config(global_data)
        logging.info(
            "Analog global config: mode=%d, act_pt=%d, sens=%d, rls=%d",
            global_config["mode"],
            global_config["actuation_point"],
            global_config["sensitivity"],
            global_config["release_sensitivity"],
        )

        # Read per-key configs in chunks
        key_configs = {}
        offset = per_key_offset
        remaining = per_key_size
        all_data = bytearray()

        while remaining > 0:
            chunk_size = min(24, remaining)  # Read 6 keys at a time (24 bytes)
            chunk = self.get_keychron_analog_profile_raw(profile, offset, chunk_size)
            if not chunk:
                logging.warning("Failed to read analog key config at offset %d", offset)
                break
            all_data.extend(chunk)
            offset += chunk_size
            remaining -= chunk_size

        # Parse per-key configs
        idx = 0
        for row in range(rows):
            for col in range(cols):
                if idx + 4 <= len(all_data):
                    key_data = all_data[idx : idx + 4]
                    config = self._parse_analog_key_config(key_data)

                    # If values are 0, inherit actuation params from global
                    # (adv_mode/adv_mode_data are per-key, never inherited)
                    if config["mode"] == 0:
                        config["mode"] = global_config["mode"]
                    if config["actuation_point"] == 0:
                        config["actuation_point"] = global_config["actuation_point"]
                    if config["sensitivity"] == 0:
                        config["sensitivity"] = global_config["sensitivity"]
                    if config["release_sensitivity"] == 0:
                        config["release_sensitivity"] = global_config[
                            "release_sensitivity"
                        ]

                    key_configs[(row, col)] = config
                    idx += 4
                else:
                    # Missing data - use global config
                    key_configs[(row, col)] = {
                        "mode": global_config["mode"],
                        "actuation_point": global_config["actuation_point"],
                        "sensitivity": global_config["sensitivity"],
                        "release_sensitivity": global_config["release_sensitivity"],
                        "adv_mode": 0,
                        "adv_mode_data": 0,
                        "js_axis": 0,
                    }

        return key_configs

    def _parse_analog_key_config(self, data):
        """
        Parse a 4-byte analog_key_config_t structure.

        Layout:
        - Byte 0: mode:2 (bits 0-1), act_pt:6 (bits 2-7)
        - Byte 1: rpd_trig_sen:6 (bits 0-5), rpd_trig_sen_deact[0:2] (bits 6-7)
        - Byte 2: rpd_trig_sen_deact[2:6] (bits 0-3), adv_mode:4 (bits 4-7)
        - Byte 3: adv_mode_data (js_axis for Gamepad, okmc_idx for DKS)

        Returns:
            Dict with mode, actuation_point, sensitivity, release_sensitivity,
            adv_mode, adv_mode_data (js_axis for Gamepad keys)
        """
        if len(data) < 4:
            return {
                "mode": 1,
                "actuation_point": 20,
                "sensitivity": 3,
                "release_sensitivity": 3,
                "adv_mode": 0,
                "adv_mode_data": 0,
                "js_axis": 0,
            }

        byte0 = data[0]
        byte1 = data[1]
        byte2 = data[2]
        byte3 = data[3]

        mode = byte0 & 0x03
        act_pt = (byte0 >> 2) & 0x3F
        rpd_trig_sen = byte1 & 0x3F
        # Release sensitivity spans bytes 1-2
        rpd_trig_sen_deact = ((byte1 >> 6) & 0x03) | ((byte2 & 0x0F) << 2)
        adv_mode = (byte2 >> 4) & 0x0F
        adv_mode_data = byte3

        return {
            "mode": mode if mode > 0 else 1,  # Treat 0 (global) as regular
            "actuation_point": act_pt if act_pt > 0 else 20,
            "sensitivity": rpd_trig_sen if rpd_trig_sen > 0 else 3,
            "release_sensitivity": rpd_trig_sen_deact if rpd_trig_sen_deact > 0 else 3,
            "adv_mode": adv_mode,
            "adv_mode_data": adv_mode_data,
            "js_axis": adv_mode_data,  # convenience alias for Gamepad mode
        }

    def get_keychron_analog_socd_pairs(self, profile):
        """
        Read SOCD pair configurations from a profile.

        Returns:
            List of SOCD pair dicts, each containing:
            - type: SOCD type (0=disabled, 1-6=various modes)
            - row1, col1: First key position
            - row2, col2: Second key position
        """
        rows = getattr(self, "rows", 0)
        cols = getattr(self, "cols", 0)
        socd_count = getattr(self, "keychron_analog_socd_count", 0)

        if socd_count == 0:
            return []

        # Calculate offset to SOCD data in profile
        # Profile structure: global(4) + keys(rows*cols*4) + okmc(okmc_count*?) + socd
        # For now, we'll use a simpler approach - read from known offset
        # Each socd_config_t is 3 bytes (packed bitfields):
        #   byte0: key_1_row:3 (bits 2:0), key_1_col:5 (bits 7:3)
        #   byte1: key_2_row:3 (bits 2:0), key_2_col:5 (bits 7:3)
        #   byte2: type

        # The SOCD section starts after global + per-key configs + OKMC configs
        # Since OKMC size varies, we'll calculate based on profile size
        global_size = 4
        per_key_size = rows * cols * 4
        okmc_count = getattr(self, "keychron_analog_okmc_count", 0)
        okmc_size = (
            okmc_count * 19
        )  # Each okmc_config_t is 19 bytes: travel(3) + keycode[4](8) + action[4](8)

        socd_offset = global_size + per_key_size + okmc_size
        socd_data_size = socd_count * 3

        # Read SOCD data
        all_data = bytearray()
        offset = socd_offset
        remaining = socd_data_size

        while remaining > 0:
            chunk_size = min(24, remaining)  # 8 SOCD entries (3 bytes each) at a time
            chunk = self.get_keychron_analog_profile_raw(profile, offset, chunk_size)
            if not chunk:
                break
            all_data.extend(chunk)
            offset += chunk_size
            remaining -= chunk_size

        # Parse SOCD pairs from packed bitfield structs
        socd_pairs = []
        for i in range(socd_count):
            idx = i * 3
            if idx + 3 <= len(all_data):
                b0 = all_data[idx]
                b1 = all_data[idx + 1]
                b2 = all_data[idx + 2]
                # socd_config_t packed bitfield layout:
                # byte0: key_1_row:3 (bits 2:0), key_1_col:5 (bits 7:3)
                # byte1: key_2_row:3 (bits 2:0), key_2_col:5 (bits 7:3)
                socd_pairs.append(
                    {
                        "type": b2,
                        "row1": b0 & 0x07,
                        "col1": (b0 >> 3) & 0x1F,
                        "row2": b1 & 0x07,
                        "col2": (b1 >> 3) & 0x1F,
                    }
                )
            else:
                socd_pairs.append(
                    {
                        "type": SOCD_PRI_NONE,
                        "row1": 0,
                        "col1": 0,
                        "row2": 0,
                        "col2": 0,
                    }
                )

        return socd_pairs

    def get_keychron_analog_profile_name(self, profile):
        """
        Read the profile name string from the keyboard.

        Profile name is stored at offset:
          4 + rows*cols*4 + okmc_count*19 + socd_count*3
          length = PROFILE_NAME_LEN (30 bytes, null-terminated string)
          CRC is a separate uint16_t field after the name.

        Returns:
            str: Profile name (may be empty).
        """
        rows = getattr(self, "rows", 0)
        cols = getattr(self, "cols", 0)
        okmc_count = getattr(self, "keychron_analog_okmc_count", 0)
        socd_count = getattr(self, "keychron_analog_socd_count", 0)

        name_offset = 4 + rows * cols * 4 + okmc_count * 19 + socd_count * 3
        # Read 30 bytes (full PROFILE_NAME_LEN)
        name_data = self.get_keychron_analog_profile_raw(profile, name_offset, 30)
        if not name_data:
            return ""
        return name_data.split(b"\x00")[0].decode("utf-8", errors="ignore")

    def set_keychron_analog_profile_name(self, profile, name):
        """
        Set the profile name on the keyboard.

        Args:
            profile: Profile index
            name: str, max 30 characters (firmware PROFILE_NAME_LEN)

        Returns:
            True on success.
        """
        name_bytes = name.encode("utf-8")[:30]
        name_len = len(name_bytes)
        packet = struct.pack(
            "BBBB", KC_ANALOG_MATRIX, AMC_SET_PROFILE_NAME, profile, name_len
        )
        packet += name_bytes
        data = self.usb_send(self.dev, packet, retries=3)
        return (
            data[0] == KC_ANALOG_MATRIX
            and data[1] == AMC_SET_PROFILE_NAME
            and data[2] == KC_SUCCESS
        )

    def set_keychron_analog_advance_mode_clear(self, profile, row, col):
        """
        Clear advance mode (DKS/Gamepad/Toggle) from a key, reverting it to regular/rapid.

        Args:
            profile: Profile index
            row, col: Key matrix position

        Returns:
            True on success.
        """
        packet = struct.pack(
            "BBBBBBB",
            KC_ANALOG_MATRIX,
            AMC_SET_ADVANCE_MODE,
            profile,
            ADV_MODE_CLEAR,
            row,
            col,
            0,  # index (unused for clear)
        )
        data = self.usb_send(self.dev, packet, retries=3)
        return (
            data[0] == KC_ANALOG_MATRIX
            and data[1] == AMC_SET_ADVANCE_MODE
            and data[2] == KC_SUCCESS
        )

    def set_keychron_analog_advance_mode_dks(
        self,
        profile,
        row,
        col,
        okmc_index,
        shallow_act,
        shallow_deact,
        deep_act,
        deep_deact,
        keycodes,
        actions,
    ):
        """
        Set Dynamic Keystroke (DKS/OKMC) advance mode on a key.

        HID packet layout (confirmed from firmware profile_set_adv_mode):
          [0xA9][0x15][prof][ADV_MODE_OKMC=1][row][col][okmc_index]
          [shallow_act][shallow_deact][deep_act][deep_deact]
          [kc0_lo][kc0_hi][kc1_lo][kc1_hi][kc2_lo][kc2_hi][kc3_lo][kc3_hi]
          [action0_byte0][action0_byte1][action1_byte0][action1_byte1]
          [action2_byte0][action2_byte1][action3_byte0][action3_byte1]

        Args:
            profile: Profile index
            row, col: Key matrix position
            okmc_index: Which OKMC slot to use (0 to okmc_count-1)
            shallow_act/deact, deep_act/deact: Travel thresholds in 0.1mm (0-63)
            keycodes: list of 4 uint16 HID keycodes
            actions: list of 4 dicts, each with keys:
                     shallow_act, shallow_deact, deep_act, deep_deact (each 0-15)

        Returns:
            True on success.
        """
        # Firmware profile_set_adv_mode() (profile.c:246-249) reads travel as
        # 4 individual bytes, NOT the 3-byte packed bitfield storage format:
        #   okmc_config.travel.shallow_act   = data[5];
        #   okmc_config.travel.shallow_deact = data[6];
        #   okmc_config.travel.deep_act      = data[7];
        #   okmc_config.travel.deep_deact    = data[8];
        # Each value is masked to 6 bits by the bitfield assignment.
        packet = struct.pack(
            "BBBBBBBBBBB",
            KC_ANALOG_MATRIX,
            AMC_SET_ADVANCE_MODE,
            profile,
            ADV_MODE_OKMC,
            row,
            col,
            okmc_index,
            shallow_act & 0x3F,
            shallow_deact & 0x3F,
            deep_act & 0x3F,
            deep_deact & 0x3F,
        )
        # Keycodes (4 × uint16 LE)
        for kc in (list(keycodes) + [0, 0, 0, 0])[:4]:
            packet += struct.pack("<H", kc & 0xFFFF)
        # Actions (4 × okmc_action_t = 4 × 2 bytes)
        # Each action: byte0 = shallow_act:4 [3:0] | shallow_deact:4 [7:4]
        #              byte1 = deep_act:4    [3:0] | deep_deact:4    [7:4]
        for act in (list(actions) + [{}, {}, {}, {}])[:4]:
            b0 = (act.get("shallow_act", 0) & 0x0F) | (
                (act.get("shallow_deact", 0) & 0x0F) << 4
            )
            b1 = (act.get("deep_act", 0) & 0x0F) | (
                (act.get("deep_deact", 0) & 0x0F) << 4
            )
            packet += bytes([b0, b1])

        data = self.usb_send(self.dev, packet, retries=3)
        return (
            data[0] == KC_ANALOG_MATRIX
            and data[1] == AMC_SET_ADVANCE_MODE
            and data[2] == KC_SUCCESS
        )

    def set_keychron_analog_advance_mode_gamepad(self, profile, row, col, js_axis):
        """
        Set Gamepad axis advance mode on a key.

        Args:
            profile: Profile index
            row, col: Key matrix position
            js_axis: Joystick axis index

        Returns:
            True on success.
        """
        packet = struct.pack(
            "BBBBBBB",
            KC_ANALOG_MATRIX,
            AMC_SET_ADVANCE_MODE,
            profile,
            ADV_MODE_GAME_CONTROLLER,
            row,
            col,
            js_axis,
        )
        data = self.usb_send(self.dev, packet, retries=3)
        return (
            data[0] == KC_ANALOG_MATRIX
            and data[1] == AMC_SET_ADVANCE_MODE
            and data[2] == KC_SUCCESS
        )

    def set_keychron_analog_advance_mode_toggle(self, profile, row, col):
        """
        Set Toggle advance mode on a key.

        Args:
            profile: Profile index
            row, col: Key matrix position

        Returns:
            True on success.
        """
        packet = struct.pack(
            "BBBBBBB",
            KC_ANALOG_MATRIX,
            AMC_SET_ADVANCE_MODE,
            profile,
            ADV_MODE_TOGGLE,
            row,
            col,
            0,  # index unused
        )
        data = self.usb_send(self.dev, packet, retries=3)
        return (
            data[0] == KC_ANALOG_MATRIX
            and data[1] == AMC_SET_ADVANCE_MODE
            and data[2] == KC_SUCCESS
        )

    def get_keychron_analog_calibrated_value(self, row, col):
        """
        Get calibrated zero/full travel values for a specific key.

        Firmware response:
          data[2] = echoed row
          data[3] = echoed col
          data[4] = status (0=success, 1=fail)
          data[5] = zero_travel & 0xFF
          data[6] = (zero_travel >> 8) & 0xFF
          data[7] = full_travel & 0xFF
          data[8] = (full_travel >> 8) & 0xFF
          data[9..12] = scale_factor as 4-byte IEEE 754 float

        Args:
            row, col: Key matrix position

        Returns:
            Dict with keys: zero_travel, full_travel, scale_factor
            or None on failure.
        """
        data = self.usb_send(
            self.dev,
            struct.pack("BBBB", KC_ANALOG_MATRIX, AMC_GET_CALIBRATED_VALUE, row, col),
            retries=3,
        )
        if (
            data[0] == KC_ANALOG_MATRIX
            and data[1] == AMC_GET_CALIBRATED_VALUE
            and data[4] == KC_SUCCESS  # status is at data[4]
        ):
            zero_travel = data[5] | (data[6] << 8)
            full_travel = data[7] | (data[8] << 8)
            scale_factor = struct.unpack_from("<f", data, 9)[0]
            return {
                "zero_travel": zero_travel,
                "full_travel": full_travel,
                "scale_factor": scale_factor,
            }
        return None

    def get_keychron_analog_okmc_configs(self, profile):
        """
        Read all OKMC (DKS) slot configurations from a profile.

        Each okmc_config_t is 19 bytes:
          - okmc_traval_config_t travel (3 bytes, packed bitfields)
          - uint16_t keycode[4] (8 bytes, 4 × LE uint16)
          - okmc_action_t action[4] (8 bytes, 4 × 2 bytes)

        Travel bitfield layout:
          byte0: shallow_act:6 [5:0], shallow_deact[1:0] [7:6]
          byte1: shallow_deact[3:2] [3:0], deep_act[3:0] [7:4]
          byte2: deep_act[5:4] [1:0], deep_deact:6 [7:2]

        Action bitfield layout (each action is 2 bytes):
          byte0: shallow_act:4 [3:0], shallow_deact:4 [7:4]
          byte1: deep_act:4    [3:0], deep_deact:4    [7:4]

        Returns:
            List of okmc config dicts, one per OKMC slot:
            {
              "shallow_act": int,   # 0-63 in 0.1mm units
              "shallow_deact": int,
              "deep_act": int,
              "deep_deact": int,
              "keycodes": [kc0, kc1, kc2, kc3],  # HID keycodes (uint16)
              "actions": [
                {"shallow_act": 0-15, "shallow_deact": 0-15,
                 "deep_act": 0-15, "deep_deact": 0-15},
                ...  (4 total)
              ]
            }
            or None on failure.
        """
        rows = getattr(self, "rows", 0)
        cols = getattr(self, "cols", 0)
        okmc_count = getattr(self, "keychron_analog_okmc_count", 0)
        if okmc_count == 0:
            return []

        okmc_offset = 4 + rows * cols * 4
        okmc_total = okmc_count * 19

        # Read all OKMC data
        all_data = bytearray()
        offset = okmc_offset
        remaining = okmc_total

        while remaining > 0:
            chunk_size = min(19, remaining)  # read one slot at a time to stay aligned
            chunk = self.get_keychron_analog_profile_raw(profile, offset, chunk_size)
            if not chunk:
                logging.warning("Failed to read OKMC config at offset %d", offset)
                return None
            all_data.extend(chunk)
            offset += chunk_size
            remaining -= chunk_size

        result = []
        for i in range(okmc_count):
            base = i * 19
            if base + 19 > len(all_data):
                break

            # Parse travel (3 bytes)
            tb0 = all_data[base]
            tb1 = all_data[base + 1]
            tb2 = all_data[base + 2]
            shallow_act = tb0 & 0x3F
            shallow_deact = ((tb0 >> 6) & 0x03) | ((tb1 & 0x0F) << 2)
            deep_act = ((tb1 >> 4) & 0x0F) | ((tb2 & 0x03) << 4)
            deep_deact = (tb2 >> 2) & 0x3F

            # Parse keycodes (4 × uint16 LE starting at base+3)
            keycodes = list(struct.unpack_from("<HHHH", all_data, base + 3))

            # Parse actions (4 × 2 bytes starting at base+11)
            actions = []
            for j in range(4):
                ab0 = all_data[base + 11 + j * 2]
                ab1 = all_data[base + 12 + j * 2]
                actions.append(
                    {
                        "shallow_act": ab0 & 0x0F,
                        "shallow_deact": (ab0 >> 4) & 0x0F,
                        "deep_act": ab1 & 0x0F,
                        "deep_deact": (ab1 >> 4) & 0x0F,
                    }
                )

            result.append(
                {
                    "shallow_act": shallow_act,
                    "shallow_deact": shallow_deact,
                    "deep_act": deep_act,
                    "deep_deact": deep_deact,
                    "keycodes": keycodes,
                    "actions": actions,
                }
            )

        return result
