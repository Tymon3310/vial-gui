from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QVBoxLayout,
    QLabel,
    QPlainTextEdit,
)

from protocol.constants import (
    VIAL_PROTOCOL_DYNAMIC,
    VIAL_PROTOCOL_KEY_OVERRIDE,
    VIAL_PROTOCOL_ADVANCED_MACROS,
    VIAL_PROTOCOL_EXT_MACROS,
    VIAL_PROTOCOL_QMK_SETTINGS,
)
from protocol.keychron import (
    AKM_MODE_NAMES,
    DEBOUNCE_TYPE_NAMES,
    REPORT_RATE_NAMES,
    SNAP_CLICK_TYPE_NAMES,
)
from vial_device import VialBridgeKeyboard


class AboutKeyboard(QDialog):
    def want_min_vial_fw(self, ver):
        if self.keyboard.sideload:
            return "unsupported - sideloaded keyboard"
        if self.keyboard.vial_protocol < 0:
            return "unsupported - VIA keyboard"
        if self.keyboard.vial_protocol < ver:
            return "unsupported - Vial firmware too old"
        return "unsupported - disabled in firmware"

    def about_tap_dance(self):
        if self.keyboard.tap_dance_count > 0:
            return str(self.keyboard.tap_dance_count)
        return self.want_min_vial_fw(VIAL_PROTOCOL_DYNAMIC)

    def about_combo(self):
        if self.keyboard.combo_count > 0:
            return str(self.keyboard.combo_count)
        return self.want_min_vial_fw(VIAL_PROTOCOL_DYNAMIC)

    def about_key_override(self):
        if self.keyboard.key_override_count > 0:
            return str(self.keyboard.key_override_count)
        return self.want_min_vial_fw(VIAL_PROTOCOL_KEY_OVERRIDE)

    def about_alt_repeat_key(self):
        if self.keyboard.alt_repeat_key_count > 0:
            return str(self.keyboard.alt_repeat_key_count)
        return self.want_min_vial_fw(VIAL_PROTOCOL_KEY_OVERRIDE)

    def about_macro_delays(self):
        if self.keyboard.vial_protocol >= VIAL_PROTOCOL_ADVANCED_MACROS:
            return "yes"
        return self.want_min_vial_fw(VIAL_PROTOCOL_ADVANCED_MACROS)

    def about_macro_ext_keycodes(self):
        if self.keyboard.vial_protocol >= VIAL_PROTOCOL_EXT_MACROS:
            return "yes"
        return self.want_min_vial_fw(VIAL_PROTOCOL_EXT_MACROS)

    def about_qmk_settings(self):
        if self.keyboard.vial_protocol >= VIAL_PROTOCOL_QMK_SETTINGS:
            if len(self.keyboard.supported_settings) == 0:
                return "disabled in firmware"
            return "yes"
        return self.want_min_vial_fw(VIAL_PROTOCOL_QMK_SETTINGS)

    def about_feature(self, feature_name):
        if feature_name in self.keyboard.supported_features:
            return "yes"
        return self.want_min_vial_fw(VIAL_PROTOCOL_DYNAMIC)

    def __init__(self, device, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.Dialog | Qt.Tool)

        self.keyboard = device.keyboard
        self.setWindowTitle("About {}".format(device.title()))

        text = ""
        desc = device.desc

        if isinstance(device, VialBridgeKeyboard):
            # Show the real keyboard identity from the Vial definition
            kb_name = ""
            if self.keyboard.definition:
                kb_name = self.keyboard.definition.get("name", "")
            text += "Keyboard: {}\n".format(kb_name or "Unknown")
            # Real VID/PID from the wireless keyboard (retrieved during open)
            via_id = int(device.via_id)
            real_vid = (via_id >> 16) & 0xFFFF
            real_pid = via_id & 0xFFFF
            text += "VID: {:04X}\n".format(real_vid)
            text += "PID: {:04X}\n".format(real_pid)
            text += "Connection: 2.4 GHz wireless via {}\n".format(
                desc.get("product_string", "Keychron Link")
            )
            text += "Dongle: {}\n".format(desc.get("path", ""))
        else:
            text += "Manufacturer: {}\n".format(desc["manufacturer_string"])
            text += "Product: {}\n".format(desc["product_string"])
            text += "VID: {:04X}\n".format(desc["vendor_id"])
            text += "PID: {:04X}\n".format(desc["product_id"])
            text += "Device: {}\n".format(desc["path"])
        text += "\n"

        if self.keyboard.sideload:
            text += "Sideloaded JSON, Vial functionality is disabled\n\n"
        elif self.keyboard.vial_protocol < 0:
            text += "VIA keyboard, Vial functionality is disabled\n\n"

        text += "VIA protocol: {}\n".format(self.keyboard.via_protocol)
        text += "Vial protocol: {}\n".format(self.keyboard.vial_protocol)
        text += "Vial keyboard ID: {:08X}\n".format(self.keyboard.keyboard_id)
        text += "\n"

        text += "Macro entries: {}\n".format(self.keyboard.macro_count)
        text += "Macro memory: {} bytes\n".format(self.keyboard.macro_memory)
        text += "Macro delays: {}\n".format(self.about_macro_delays())
        text += "Complex (2-byte) macro keycodes: {}\n".format(
            self.about_macro_ext_keycodes()
        )
        text += "\n"

        text += "Tap Dance entries: {}\n".format(self.about_tap_dance())
        text += "Combo entries: {}\n".format(self.about_combo())
        text += "Key Override entries: {}\n".format(self.about_key_override())
        text += "Alt Repeat Key entries: {}\n".format(self.about_alt_repeat_key())
        text += "Caps Word: {}\n".format(self.about_feature("caps_word"))
        text += "Layer Lock: {}\n".format(self.about_feature("layer_lock"))
        text += "\n"

        text += "QMK Settings: {}\n".format(self.about_qmk_settings())

        # Keychron features section
        kb = self.keyboard
        if hasattr(kb, "has_keychron_features") and kb.has_keychron_features():
            text += "\n"
            text += "--- Keychron Features ---\n"
            text += "Firmware version: {}\n".format(
                kb.keychron_firmware_version or "unknown"
            )
            text += "MCU: {}\n".format(kb.keychron_mcu_info or "unknown")
            text += "Protocol version: {}\n".format(kb.keychron_protocol_version)
            text += "\n"

            # Debounce
            if kb.has_keychron_debounce():
                text += "Dynamic Debounce: yes\n"
                text += "  Type: {}\n".format(
                    DEBOUNCE_TYPE_NAMES.get(
                        kb.keychron_debounce_type, str(kb.keychron_debounce_type)
                    )
                )
                text += "  Time: {} ms\n".format(kb.keychron_debounce_time)
            else:
                text += "Dynamic Debounce: not supported\n"

            # NKRO
            if kb.has_keychron_nkro():
                if kb.keychron_nkro_adaptive:
                    text += "NKRO: adaptive (currently: {})\n".format(
                        "enabled" if kb.keychron_nkro_enabled else "disabled"
                    )
                elif kb.keychron_nkro_supported:
                    text += "NKRO: supported (enabled: {})\n".format(
                        "yes" if kb.keychron_nkro_enabled else "no"
                    )
                else:
                    text += "NKRO: not supported\n"
            else:
                text += "NKRO: not supported\n"

            # Report rate
            if kb.has_keychron_report_rate():
                text += "USB Report Rate: yes\n"
                text += "  Current: {}\n".format(
                    REPORT_RATE_NAMES.get(
                        kb.keychron_report_rate, str(kb.keychron_report_rate)
                    )
                )
            else:
                text += "USB Report Rate: not supported\n"

            # Snap Click
            if kb.has_keychron_snap_click():
                text += "Snap Click (SOCD): yes\n"
                text += "  Slots: {}\n".format(kb.keychron_snap_click_count)
            else:
                text += "Snap Click (SOCD): not supported\n"

            # Wireless
            if kb.has_keychron_wireless():
                text += "Wireless LPM: yes\n"
                text += "  Backlit timeout: {} s\n".format(
                    kb.keychron_wireless_backlit_time
                )
                text += "  Idle timeout: {} s\n".format(kb.keychron_wireless_idle_time)
            else:
                text += "Wireless LPM: not supported\n"

            # Per-key RGB
            if kb.has_keychron_rgb():
                text += "Keychron RGB: yes\n"
                text += "  LED count: {}\n".format(kb.keychron_led_count)
            else:
                text += "Keychron RGB: not supported\n"

            # Analog Matrix (Hall Effect)
            if kb.has_keychron_analog():
                text += "Analog Matrix (Hall Effect): yes\n"
                text += "  AMC version: 0x{:08X}\n".format(kb.keychron_analog_version)
                text += "  Profiles: {}\n".format(kb.keychron_analog_profile_count)
                text += "  OKMC (DKS) slots: {}\n".format(kb.keychron_analog_okmc_count)
                text += "  SOCD slots: {}\n".format(kb.keychron_analog_socd_count)
                text += "  Game controller mode: {}\n".format(
                    "enabled" if kb.keychron_analog_game_controller_mode else "disabled"
                )
            else:
                text += "Analog Matrix (Hall Effect): not supported\n"

        font = QFont("monospace")
        font.setStyleHint(QFont.TypeWriter)
        self.textarea = QPlainTextEdit()
        self.textarea.setReadOnly(True)
        self.textarea.setFont(font)

        self.textarea.setPlainText(text)

        self.buttonBox = QDialogButtonBox(QDialogButtonBox.Ok)
        self.buttonBox.accepted.connect(self.accept)
        self.buttonBox.rejected.connect(self.reject)

        self.layout = QVBoxLayout()
        self.layout.addWidget(self.textarea)
        self.layout.addWidget(self.buttonBox)
        self.setLayout(self.layout)

        self.setMinimumSize(500, 400)
        self.adjustSize()
