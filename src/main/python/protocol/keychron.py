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
from protocol.base_protocol import BaseProtocol

# Main command IDs (data[0])
KC_GET_PROTOCOL_VERSION = 0xA0
KC_GET_FIRMWARE_VERSION = 0xA1
KC_GET_SUPPORT_FEATURE = 0xA2
KC_GET_DEFAULT_LAYER = 0xA3
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
    SNAP_CLICK_TYPE_REGULAR: "Regular SOCD",
    SNAP_CLICK_TYPE_LAST_INPUT: "Last Input Wins",
    SNAP_CLICK_TYPE_FIRST_KEY: "First Key Priority",
    SNAP_CLICK_TYPE_SECOND_KEY: "Second Key Priority",
    SNAP_CLICK_TYPE_NEUTRAL: "Neutral (Both Cancel)",
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

        # Debounce settings
        self.keychron_debounce_type = DEBOUNCE_SYM_DEFER_GLOBAL
        self.keychron_debounce_time = 5

        # NKRO
        self.keychron_nkro_enabled = False
        self.keychron_nkro_supported = False
        self.keychron_nkro_adaptive = False

        # Report rate
        self.keychron_report_rate = REPORT_RATE_1000HZ
        self.keychron_report_rate_mask = 0x7F  # which rates are supported

        # Snap Click
        self.keychron_snap_click_count = 0
        self.keychron_snap_click_entries = []

        # Wireless LPM
        self.keychron_wireless_backlit_time = 30
        self.keychron_wireless_idle_time = 300

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
        except Exception as e:
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
            data[0] == KC_MISC_CMD_GROUP
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
            data[0] == KC_MISC_CMD_GROUP
            and data[1] == NKRO_SET
            and data[2] == KC_SUCCESS
        ):
            self.keychron_nkro_enabled = enabled
            return True
        return False

    # Report rate methods
    def _reload_report_rate(self):
        """Load USB report rate settings."""
        data = self.usb_send(
            self.dev, struct.pack("BB", KC_MISC_CMD_GROUP, REPORT_RATE_GET), retries=3
        )
        if (
            data[0] == KC_MISC_CMD_GROUP
            and data[1] == REPORT_RATE_GET
            and data[2] == KC_SUCCESS
        ):
            self.keychron_report_rate = data[3]
            self.keychron_report_rate_mask = data[4] if len(data) > 4 else 0x7F

    def set_keychron_report_rate(self, rate):
        """Set USB report rate."""
        data = self.usb_send(
            self.dev,
            struct.pack("BBB", KC_MISC_CMD_GROUP, REPORT_RATE_SET, rate),
            retries=3,
        )
        if (
            data[0] == KC_MISC_CMD_GROUP
            and data[1] == REPORT_RATE_SET
            and data[2] == KC_SUCCESS
        ):
            self.keychron_report_rate = rate
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
            data[0] == KC_MISC_CMD_GROUP
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
                    data[0] == KC_MISC_CMD_GROUP
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
            data[0] == KC_MISC_CMD_GROUP
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
            data[0] == KC_MISC_CMD_GROUP
            and data[1] == SNAP_CLICK_SAVE
            and data[2] == KC_SUCCESS
        )

    # Wireless LPM methods
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
            data[0] == KC_MISC_CMD_GROUP
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
                "disable_mask": data[3],
                "hue": data[4],
                "sat": data[5],
                "val": data[6],
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
            self.keychron_os_indicator_config = {
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
            self.keychron_analog_version = struct.unpack("<I", data[2:6])[0]

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
        if (
            data[0] == KC_ANALOG_MATRIX
            and data[1] == AMC_GET_CURVE
            and data[2] == KC_SUCCESS
        ):
            self.keychron_analog_curve = []
            for i in range(4):
                self.keychron_analog_curve.append(
                    data[3 + i * 2] | (data[4 + i * 2] << 8)
                )

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
        """Set analog travel/actuation settings."""
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
            # Apply to specific keys using row_mask
            packet = struct.pack(
                "BBBBBBBB",
                KC_ANALOG_MATRIX,
                AMC_SET_TRAVEL,
                profile,
                mode,
                act_pt,
                sens,
                rls_sens,
                0,
            )
            if row_mask:
                packet += bytes(row_mask)
            data = self.usb_send(self.dev, packet, retries=3)
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
        """Set joystick response curve (4 points)."""
        packet = struct.pack("BB", KC_ANALOG_MATRIX, AMC_SET_CURVE)
        for point in curve_points[:4]:
            packet += struct.pack("<H", point)
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
            data[0] == KC_ANALOG_MATRIX
            and data[1] == AMC_SET_GAME_CONTROLLER_MODE
            and data[2] == KC_SUCCESS
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
        """Get current calibration state."""
        data = self.usb_send(
            self.dev,
            struct.pack("BB", KC_ANALOG_MATRIX, AMC_GET_CALIBRATE_STATE),
            retries=3,
        )
        if (
            data[0] == KC_ANALOG_MATRIX
            and data[1] == AMC_GET_CALIBRATE_STATE
            and data[2] == KC_SUCCESS
        ):
            return data[3]
        return CALIB_OFF
