# SPDX-License-Identifier: GPL-2.0-or-later
"""
Keychron RGB Editor - Per-key RGB, OS indicators, and Mixed RGB effects.

This editor handles:
- Global RGB mode control (QMK effects, Per-Key RGB, Mixed RGB)
- Per-key RGB colors and effect types
- OS lock indicator configuration (Caps Lock, Num Lock, etc.)
- Mixed RGB effect layers and regions
"""

from PyQt5 import QtCore
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QLabel,
    QWidget,
    QSizePolicy,
    QGroupBox,
    QComboBox,
    QSpinBox,
    QCheckBox,
    QPushButton,
    QScrollArea,
    QFrame,
    QSlider,
    QColorDialog,
    QTabWidget,
    QMessageBox,
)

import logging

from editor.basic_editor import BasicEditor
from editor.rgb_configurator import VIALRGB_EFFECTS
from protocol.keychron import PER_KEY_RGB_TYPE_NAMES, PER_KEY_RGB_SOLID
from util import tr
from vial_device import VialKeyboard
from widgets.rgb_keyboard_widget import RGBKeyboardWidget

# Keychron custom RGB Matrix effects - VialRGB IDs from vialrgb_effects.inc
# VIALRGB_EFFECT_PER_KEY_RGB = 48, VIALRGB_EFFECT_MIXED_RGB = 49
KEYCHRON_CUSTOM_EFFECTS = [
    {"id": 48, "name": "Per-Key RGB"},
    {"id": 49, "name": "Mixed RGB"},
]


class ColorButton(QPushButton):
    """Button that displays and selects a color."""

    color_changed = pyqtSignal(int, int, int)  # H, S, V

    def __init__(self, parent=None):
        super().__init__(parent)
        self.h = 0
        self.s = 255
        self.v = 255
        self.setFixedSize(40, 30)
        self._update_style()
        self.clicked.connect(self._on_clicked)

    def set_hsv(self, h, s, v):
        """Set color in HSV format (0-255 range)."""
        self.h = h
        self.s = s
        self.v = v
        self._update_style()

    def _update_style(self):
        """Update button background to show current color."""
        # Convert HSV (0-255) to QColor HSV (0-359, 0-255, 0-255)
        color = QColor.fromHsv(int(self.h * 359 / 255), self.s, self.v)
        self.setStyleSheet(f"background-color: {color.name()}; border: 1px solid #555;")

    def _on_clicked(self):
        """Open color dialog."""
        current = QColor.fromHsv(int(self.h * 359 / 255), self.s, self.v)
        color = QColorDialog.getColor(current, self, tr("KeychronRGB", "Select Color"))
        if color.isValid():
            # Convert back to 0-255 range
            h, s, v, _ = color.getHsv()
            self.h = int(h * 255 / 359) if h >= 0 else 0
            self.s = s
            self.v = v
            self._update_style()
            self.color_changed.emit(self.h, self.s, self.v)


class LEDColorWidget(QFrame):
    """Widget for a single LED's color."""

    def __init__(self, index, parent_editor):
        super().__init__()
        self.index = index
        self.parent_editor = parent_editor

        layout = QHBoxLayout()
        layout.setContentsMargins(2, 2, 2, 2)

        self.label = QLabel(f"{index}")
        self.label.setFixedWidth(30)
        self.label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.label)

        self.color_btn = ColorButton()
        self.color_btn.color_changed.connect(self._on_color_changed)
        layout.addWidget(self.color_btn)

        self.setLayout(layout)

    def set_color(self, h, s, v):
        """Set the LED color."""
        self.color_btn.set_hsv(h, s, v)

    def _on_color_changed(self, h, s, v):
        """Handle color change."""
        self.parent_editor.set_led_color(self.index, h, s, v)


class KeychronRGBEditor(BasicEditor):
    """Editor for Keychron per-key RGB and mixed RGB effects."""

    def __init__(self, layout_editor):
        super().__init__()
        self.layout_editor = layout_editor
        self.keyboard = None
        self.led_widgets = []
        self.rgb_effects = []  # List of available effects for this keyboard

        # === Global RGB Mode Control ===
        mode_group = QGroupBox(tr("KeychronRGB", "Global RGB Mode"))
        mode_layout = QGridLayout()

        # RGB Mode dropdown
        mode_layout.addWidget(QLabel(tr("KeychronRGB", "Effect:")), 0, 0)
        self.rgb_mode = QComboBox()
        self.rgb_mode.currentIndexChanged.connect(self.on_rgb_mode_changed)
        mode_layout.addWidget(self.rgb_mode, 0, 1)

        # Brightness slider
        mode_layout.addWidget(QLabel(tr("KeychronRGB", "Brightness:")), 0, 2)
        self.rgb_brightness = QSlider(Qt.Horizontal)
        self.rgb_brightness.setMinimum(0)
        self.rgb_brightness.setMaximum(255)
        self.rgb_brightness.valueChanged.connect(self.on_rgb_brightness_changed)
        mode_layout.addWidget(self.rgb_brightness, 0, 3)

        # Speed slider
        mode_layout.addWidget(QLabel(tr("KeychronRGB", "Speed:")), 1, 0)
        self.rgb_speed = QSlider(Qt.Horizontal)
        self.rgb_speed.setMinimum(0)
        self.rgb_speed.setMaximum(255)
        self.rgb_speed.valueChanged.connect(self.on_rgb_speed_changed)
        mode_layout.addWidget(self.rgb_speed, 1, 1)

        # Color button
        mode_layout.addWidget(QLabel(tr("KeychronRGB", "Color:")), 1, 2)
        self.rgb_color = ColorButton()
        self.rgb_color.color_changed.connect(self.on_rgb_color_changed)
        mode_layout.addWidget(self.rgb_color, 1, 3)

        mode_group.setLayout(mode_layout)
        self.addWidget(mode_group)

        # Main tab widget
        self.tabs = QTabWidget()
        self.addWidget(self.tabs)

        # === Per-Key RGB Tab ===
        per_key_widget = QWidget()
        per_key_layout = QVBoxLayout()
        per_key_widget.setLayout(per_key_layout)

        # Effect type selection
        effect_group = QGroupBox(tr("KeychronRGB", "Per-Key RGB Effect"))
        effect_layout = QHBoxLayout()

        effect_layout.addWidget(QLabel(tr("KeychronRGB", "Effect Type:")))
        self.effect_type = QComboBox()
        for type_id, name in PER_KEY_RGB_TYPE_NAMES.items():
            self.effect_type.addItem(name, type_id)
        self.effect_type.currentIndexChanged.connect(self.on_effect_type_changed)
        effect_layout.addWidget(self.effect_type)
        effect_layout.addStretch()

        effect_group.setLayout(effect_layout)
        per_key_layout.addWidget(effect_group)

        # Keyboard visualization for LED colors
        keyboard_group = QGroupBox(tr("KeychronRGB", "LED Colors"))
        keyboard_layout = QVBoxLayout()

        # Info and controls row
        info_row = QHBoxLayout()
        self.led_count_label = QLabel()
        info_row.addWidget(self.led_count_label)
        info_row.addStretch()

        # Select all button
        self.btn_select_all = QPushButton(tr("KeychronRGB", "Select All"))
        self.btn_select_all.clicked.connect(self.on_select_all)
        info_row.addWidget(self.btn_select_all)

        # Deselect all button
        self.btn_deselect_all = QPushButton(tr("KeychronRGB", "Deselect All"))
        self.btn_deselect_all.clicked.connect(self.on_deselect_all)
        info_row.addWidget(self.btn_deselect_all)

        keyboard_layout.addLayout(info_row)

        # RGB Keyboard widget
        self.rgb_keyboard = RGBKeyboardWidget(layout_editor)
        self.rgb_keyboard.key_selected.connect(self.on_rgb_key_selected)
        self.rgb_keyboard.key_deselected.connect(self.on_rgb_key_deselected)

        # Put keyboard in scroll area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.rgb_keyboard)
        keyboard_layout.addWidget(scroll, 1)

        # Color picker for selected keys
        color_row = QHBoxLayout()
        color_row.addWidget(QLabel(tr("KeychronRGB", "Selected Key Color:")))
        self.selected_color = ColorButton()
        self.selected_color.color_changed.connect(self.on_selected_color_changed)
        color_row.addWidget(self.selected_color)
        self.selected_info = QLabel(tr("KeychronRGB", "Click a key to select it"))
        color_row.addWidget(self.selected_info)
        color_row.addStretch()
        keyboard_layout.addLayout(color_row)

        keyboard_group.setLayout(keyboard_layout)
        per_key_layout.addWidget(keyboard_group, 1)

        self.tabs.addTab(per_key_widget, tr("KeychronRGB", "Per-Key RGB"))

        # === OS Indicators Tab ===
        indicators_widget = QWidget()
        indicators_layout = QVBoxLayout()
        indicators_widget.setLayout(indicators_layout)

        indicators_group = QGroupBox(tr("KeychronRGB", "OS Lock Indicator Settings"))
        indicators_grid = QGridLayout()

        # Checkboxes for each indicator
        indicators_grid.addWidget(
            QLabel(tr("KeychronRGB", "Disable indicators:")), 0, 0
        )

        self.indicator_numlock = QCheckBox(tr("KeychronRGB", "Num Lock"))
        self.indicator_numlock.stateChanged.connect(self.on_indicator_changed)
        indicators_grid.addWidget(self.indicator_numlock, 0, 1)

        self.indicator_capslock = QCheckBox(tr("KeychronRGB", "Caps Lock"))
        self.indicator_capslock.stateChanged.connect(self.on_indicator_changed)
        indicators_grid.addWidget(self.indicator_capslock, 0, 2)

        self.indicator_scrolllock = QCheckBox(tr("KeychronRGB", "Scroll Lock"))
        self.indicator_scrolllock.stateChanged.connect(self.on_indicator_changed)
        indicators_grid.addWidget(self.indicator_scrolllock, 0, 3)

        # Indicator color
        indicators_grid.addWidget(QLabel(tr("KeychronRGB", "Indicator Color:")), 1, 0)
        self.indicator_color = ColorButton()
        self.indicator_color.color_changed.connect(self.on_indicator_color_changed)
        indicators_grid.addWidget(self.indicator_color, 1, 1)

        indicators_group.setLayout(indicators_grid)
        indicators_layout.addWidget(indicators_group)
        indicators_layout.addStretch()

        self.tabs.addTab(indicators_widget, tr("KeychronRGB", "OS Indicators"))

        # === Mixed RGB Tab ===
        mixed_widget = QWidget()
        mixed_layout = QVBoxLayout()
        mixed_widget.setLayout(mixed_layout)

        # Explanation label at top
        explanation = QLabel(
            tr(
                "KeychronRGB",
                "Mixed RGB lets you divide your keyboard into regions, each with its own effect playlist. "
                "Effects in each region cycle through automatically based on their duration.",
            )
        )
        explanation.setWordWrap(True)
        explanation.setStyleSheet("color: #888; font-style: italic; padding: 5px;")
        mixed_layout.addWidget(explanation)

        # Keyboard visualization for regions
        regions_group = QGroupBox(tr("KeychronRGB", "1. Assign Keys to Regions"))
        regions_layout = QVBoxLayout()

        # Region assignment controls row
        region_ctrl_row = QHBoxLayout()

        # Region selection dropdown
        region_ctrl_row.addWidget(QLabel(tr("KeychronRGB", "Paint Region:")))
        self.mixed_region_select = QComboBox()
        self.mixed_region_select.setMinimumWidth(120)
        region_ctrl_row.addWidget(self.mixed_region_select)

        # Apply button
        self.btn_apply_region = QPushButton(tr("KeychronRGB", "Apply to Selected Keys"))
        self.btn_apply_region.clicked.connect(self.on_apply_region)
        region_ctrl_row.addWidget(self.btn_apply_region)

        region_ctrl_row.addStretch()

        # Selection buttons
        self.btn_mixed_select_all = QPushButton(tr("KeychronRGB", "Select All"))
        self.btn_mixed_select_all.clicked.connect(self.on_mixed_select_all)
        region_ctrl_row.addWidget(self.btn_mixed_select_all)

        self.btn_mixed_deselect_all = QPushButton(tr("KeychronRGB", "Clear Selection"))
        self.btn_mixed_deselect_all.clicked.connect(self.on_mixed_deselect_all)
        region_ctrl_row.addWidget(self.btn_mixed_deselect_all)

        regions_layout.addLayout(region_ctrl_row)

        # Selection info
        self.mixed_selection_info = QLabel(
            tr(
                "KeychronRGB",
                "Click or drag to select keys, then assign them to a region",
            )
        )
        self.mixed_selection_info.setStyleSheet("color: #666;")
        regions_layout.addWidget(self.mixed_selection_info)

        # RGB Keyboard widget for regions
        self.mixed_rgb_keyboard = RGBKeyboardWidget(layout_editor)
        self.mixed_rgb_keyboard.key_selected.connect(self.on_mixed_key_selected)
        self.mixed_rgb_keyboard.key_deselected.connect(self.on_mixed_key_deselected)

        # Put keyboard in scroll area
        mixed_scroll = QScrollArea()
        mixed_scroll.setWidgetResizable(True)
        mixed_scroll.setWidget(self.mixed_rgb_keyboard)
        regions_layout.addWidget(mixed_scroll, 1)

        # Region legend
        self.region_legend = QLabel()
        self.region_legend.setWordWrap(True)
        regions_layout.addWidget(self.region_legend)

        regions_group.setLayout(regions_layout)
        mixed_layout.addWidget(regions_group, 1)

        # Effects editor for each region - now with better explanation
        effects_group = QGroupBox(
            tr("KeychronRGB", "2. Configure Effect Playlist for Each Region")
        )
        effects_layout = QVBoxLayout()

        # Effect playlist explanation
        playlist_info = QLabel(
            tr(
                "KeychronRGB",
                "Each region can have multiple effects that cycle automatically. "
                "Set 'Disabled' to stop the playlist at that slot.",
            )
        )
        playlist_info.setWordWrap(True)
        playlist_info.setStyleSheet("color: #888; font-style: italic;")
        effects_layout.addWidget(playlist_info)

        # Region tabs for effects
        self.region_effects_tabs = QTabWidget()
        effects_layout.addWidget(self.region_effects_tabs)

        effects_group.setLayout(effects_layout)
        mixed_layout.addWidget(effects_group)

        self.tabs.addTab(mixed_widget, tr("KeychronRGB", "Mixed RGB"))

        # Save button
        buttons_layout = QHBoxLayout()
        buttons_layout.addStretch()
        self.btn_save = QPushButton(tr("KeychronRGB", "Save to Keyboard"))
        self.btn_save.clicked.connect(self.save_to_keyboard)
        buttons_layout.addWidget(self.btn_save)
        self.addLayout(buttons_layout)

        self._updating = False

    def valid(self):
        """Check if this tab should be shown."""
        if not isinstance(self.device, VialKeyboard):
            return False
        kb = self.device.keyboard
        if not hasattr(kb, "has_keychron_rgb"):
            return False
        return kb.has_keychron_rgb()

    def rebuild(self, device):
        super().rebuild(device)
        if not self.valid():
            return

        self.keyboard = device.keyboard
        self._updating = True

        # Rebuild RGB mode dropdown with available effects
        self._rebuild_rgb_mode_dropdown()

        # Update global RGB controls
        self._update_rgb_controls()

        # Update effect type
        idx = self.effect_type.findData(self.keyboard.keychron_per_key_rgb_type)
        if idx >= 0:
            self.effect_type.setCurrentIndex(idx)

        # Update LED count label
        self.led_count_label.setText(
            tr("KeychronRGB", "Total LEDs: {}").format(self.keyboard.keychron_led_count)
        )

        # Setup the RGB keyboard widget
        self._setup_rgb_keyboard()

        # Update OS indicators
        if self.keyboard.keychron_os_indicator_config:
            cfg = self.keyboard.keychron_os_indicator_config
            mask = cfg.get("disable_mask", 0)
            self.indicator_numlock.setChecked(bool(mask & 0x01))
            self.indicator_capslock.setChecked(bool(mask & 0x02))
            self.indicator_scrolllock.setChecked(bool(mask & 0x04))
            self.indicator_color.set_hsv(
                cfg.get("hue", 0), cfg.get("sat", 255), cfg.get("val", 255)
            )

        # Update mixed RGB editor
        self._setup_mixed_rgb()

        self._updating = False

    def _rebuild_rgb_mode_dropdown(self):
        """Rebuild the RGB mode dropdown with available effects."""
        self.rgb_effects = []
        self.rgb_mode.clear()

        # Always add Keychron custom effects FIRST (Per-Key RGB and Mixed RGB)
        # These are the primary modes for Keychron keyboards
        for custom_effect in KEYCHRON_CUSTOM_EFFECTS:
            self.rgb_effects.append(custom_effect)
            logging.info(
                "KeychronRGB: Added custom effect: %s (id=%d)",
                custom_effect["name"],
                custom_effect["id"],
            )

        # Check if keyboard has VialRGB support with explicit effect list
        if (
            hasattr(self.keyboard, "rgb_supported_effects")
            and self.keyboard.rgb_supported_effects
        ):
            logging.info(
                "KeychronRGB: rgb_supported_effects = %s",
                sorted(self.keyboard.rgb_supported_effects),
            )
            # Add standard VialRGB effects that are supported
            for effect in VIALRGB_EFFECTS:
                if effect.idx in self.keyboard.rgb_supported_effects:
                    # Skip if already added (custom effects might overlap)
                    if not any(e["id"] == effect.idx for e in self.rgb_effects):
                        self.rgb_effects.append({"id": effect.idx, "name": effect.name})
        else:
            logging.info(
                "KeychronRGB: No explicit rgb_supported_effects, adding all VialRGB effects"
            )
            # VialRGB not available or no explicit effect list
            # Add all standard QMK RGB Matrix effects
            for effect in VIALRGB_EFFECTS:
                # Skip if already added
                if not any(e["id"] == effect.idx for e in self.rgb_effects):
                    self.rgb_effects.append({"id": effect.idx, "name": effect.name})

        # Populate dropdown
        for effect in self.rgb_effects:
            self.rgb_mode.addItem(effect["name"], effect["id"])

    def _update_rgb_controls(self):
        """Update the RGB control widgets from keyboard state."""
        # First ensure defaults are set
        self._ensure_rgb_defaults()

        # Check if VialRGB is available
        has_vialrgb = getattr(self.keyboard, "lighting_vialrgb", False)
        logging.info("KeychronRGB: VialRGB available: %s", has_vialrgb)

        # Block signals while updating to prevent triggering handlers
        self.rgb_mode.blockSignals(True)
        self.rgb_brightness.blockSignals(True)
        self.rgb_speed.blockSignals(True)

        try:
            # Set current mode
            if (
                hasattr(self.keyboard, "rgb_mode")
                and self.keyboard.rgb_mode is not None
            ):
                logging.info(
                    "KeychronRGB: Current rgb_mode: %s", self.keyboard.rgb_mode
                )
                for i, effect in enumerate(self.rgb_effects):
                    if effect["id"] == self.keyboard.rgb_mode:
                        self.rgb_mode.setCurrentIndex(i)
                        break

            # Set brightness
            max_brightness = getattr(self.keyboard, "rgb_maximum_brightness", 255)
            if max_brightness <= 0:
                max_brightness = 255
            self.rgb_brightness.setMaximum(max_brightness)

            if hasattr(self.keyboard, "rgb_hsv") and self.keyboard.rgb_hsv:
                brightness = self.keyboard.rgb_hsv[2]
                logging.info(
                    "KeychronRGB: Current brightness (from rgb_hsv[2]): %s", brightness
                )
                self.rgb_brightness.setValue(brightness)
                # Set color
                self.rgb_color.set_hsv(
                    self.keyboard.rgb_hsv[0],
                    self.keyboard.rgb_hsv[1],
                    255,  # Use full value for display
                )
            else:
                # Default to max brightness
                logging.info(
                    "KeychronRGB: No rgb_hsv, defaulting brightness to %s",
                    max_brightness,
                )
                self.rgb_brightness.setValue(max_brightness)

            # Set speed
            if (
                hasattr(self.keyboard, "rgb_speed")
                and self.keyboard.rgb_speed is not None
            ):
                logging.info(
                    "KeychronRGB: Current rgb_speed: %s", self.keyboard.rgb_speed
                )
                self.rgb_speed.setValue(self.keyboard.rgb_speed)
            else:
                logging.info("KeychronRGB: No rgb_speed, defaulting to 128")
                self.rgb_speed.setValue(128)  # Default mid-speed

        finally:
            # Unblock signals
            self.rgb_mode.blockSignals(False)
            self.rgb_brightness.blockSignals(False)
            self.rgb_speed.blockSignals(False)

    def _ensure_rgb_defaults(self):
        """Ensure RGB-related attributes have valid default values."""
        if not self.keyboard:
            return
        # Initialize rgb_mode if not set
        if not hasattr(self.keyboard, "rgb_mode") or self.keyboard.rgb_mode is None:
            self.keyboard.rgb_mode = 0
        # Initialize rgb_speed if not set
        if not hasattr(self.keyboard, "rgb_speed") or self.keyboard.rgb_speed is None:
            self.keyboard.rgb_speed = 128
        # Initialize rgb_hsv if not set
        if not hasattr(self.keyboard, "rgb_hsv") or self.keyboard.rgb_hsv is None:
            self.keyboard.rgb_hsv = (0, 255, 255)

    def on_rgb_mode_changed(self, index):
        """Handle RGB mode dropdown change."""
        if self._updating or not self.keyboard:
            return
        if index >= 0 and index < len(self.rgb_effects):
            mode_id = self.rgb_effects[index]["id"]
            logging.info(
                "KeychronRGB: Setting RGB mode to %s (%s)",
                mode_id,
                self.rgb_effects[index]["name"],
            )
            if hasattr(self.keyboard, "set_vialrgb_mode"):
                self._ensure_rgb_defaults()
                self.keyboard.set_vialrgb_mode(mode_id)
            else:
                logging.warning("KeychronRGB: set_vialrgb_mode not available")

    def on_rgb_brightness_changed(self, value):
        """Handle RGB brightness slider change."""
        if self._updating or not self.keyboard:
            return
        logging.info("KeychronRGB: Setting brightness to %s", value)
        if hasattr(self.keyboard, "set_vialrgb_brightness"):
            self._ensure_rgb_defaults()
            self.keyboard.set_vialrgb_brightness(value)
        else:
            logging.warning("KeychronRGB: set_vialrgb_brightness not available")

    def on_rgb_speed_changed(self, value):
        """Handle RGB speed slider change."""
        if self._updating or not self.keyboard:
            return
        logging.info("KeychronRGB: Setting speed to %s", value)
        if hasattr(self.keyboard, "set_vialrgb_speed"):
            self._ensure_rgb_defaults()
            self.keyboard.set_vialrgb_speed(value)
        else:
            logging.warning("KeychronRGB: set_vialrgb_speed not available")

    def on_rgb_color_changed(self, h, s, v):
        """Handle RGB color button change."""
        if self._updating or not self.keyboard:
            return
        logging.info("KeychronRGB: Setting color to H=%s S=%s V=%s", h, s, v)
        if hasattr(self.keyboard, "set_vialrgb_color"):
            self._ensure_rgb_defaults()
            # Keep current brightness value
            current_v = self.keyboard.rgb_hsv[2] if self.keyboard.rgb_hsv else 255
            self.keyboard.set_vialrgb_color(h, s, current_v)
        else:
            logging.warning("KeychronRGB: set_vialrgb_color not available")

    def _setup_rgb_keyboard(self):
        """Setup the RGB keyboard widget with current keyboard layout and colors."""
        if not self.keyboard:
            return

        # Set keys from the keyboard layout
        self.rgb_keyboard.set_keys(self.keyboard.keys, self.keyboard.encoders)

        # Set the LED matrix mapping
        self.rgb_keyboard.set_led_matrix(self.keyboard.keychron_led_matrix)

        # Set the LED colors
        self.rgb_keyboard.set_led_colors(self.keyboard.keychron_per_key_colors)

        # Clear selection
        self.rgb_keyboard.deselect_all_keys()
        self.selected_info.setText(tr("KeychronRGB", "Click a key to select it"))

    def on_select_all(self):
        """Handle select all button."""
        self.rgb_keyboard.select_all_keys()
        count = len(self.rgb_keyboard.selected_keys)
        self.selected_info.setText(tr("KeychronRGB", "{} keys selected").format(count))

    def on_deselect_all(self):
        """Handle deselect all button."""
        self.rgb_keyboard.deselect_all_keys()
        self.selected_info.setText(tr("KeychronRGB", "Click a key to select it"))

    def on_rgb_key_selected(self, key):
        """Handle RGB key selection."""
        led_indices = self.rgb_keyboard.get_selected_led_indices()
        count = len(led_indices)

        if count == 1:
            # Single selection - show current color
            led_idx = led_indices[0]
            if led_idx < len(self.keyboard.keychron_per_key_colors):
                h, s, v = self.keyboard.keychron_per_key_colors[led_idx]
                self.selected_color.set_hsv(h, s, v)
            self.selected_info.setText(
                tr("KeychronRGB", "LED {} selected").format(led_idx)
            )
        elif count > 1:
            self.selected_info.setText(
                tr("KeychronRGB", "{} keys selected").format(count)
            )
        else:
            self.selected_info.setText(tr("KeychronRGB", "Click a key to select it"))

    def on_rgb_key_deselected(self):
        """Handle RGB key deselection."""
        self.selected_info.setText(tr("KeychronRGB", "Click a key to select it"))

    def on_selected_color_changed(self, h, s, v):
        """Handle color change for selected keys."""
        if self._updating or not self.keyboard:
            return

        # Apply color to all selected LEDs
        led_indices = self.rgb_keyboard.get_selected_led_indices()
        for led_idx in led_indices:
            self.keyboard.set_keychron_per_key_color(led_idx, h, s, v)
            self.rgb_keyboard.set_led_color(led_idx, h, s, v)

    def on_effect_type_changed(self):
        """Handle effect type change."""
        if self._updating or not self.keyboard:
            return
        effect_type = self.effect_type.currentData()
        self.keyboard.set_keychron_per_key_rgb_type(effect_type)

    def on_indicator_changed(self):
        """Handle indicator checkbox change."""
        if self._updating or not self.keyboard:
            return
        self._update_indicator_config()

    def on_indicator_color_changed(self, h, s, v):
        """Handle indicator color change."""
        if self._updating or not self.keyboard:
            return
        self._update_indicator_config()

    def _update_indicator_config(self):
        """Send updated indicator config to keyboard."""
        mask = 0
        if self.indicator_numlock.isChecked():
            mask |= 0x01
        if self.indicator_capslock.isChecked():
            mask |= 0x02
        if self.indicator_scrolllock.isChecked():
            mask |= 0x04

        self.keyboard.set_keychron_os_indicator_config(
            mask, self.indicator_color.h, self.indicator_color.s, self.indicator_color.v
        )

    def save_to_keyboard(self):
        """Save RGB settings to EEPROM."""
        if not self.keyboard:
            return

        if self.keyboard.save_keychron_rgb():
            QMessageBox.information(
                self.widget(),
                tr("KeychronRGB", "Saved"),
                tr("KeychronRGB", "RGB settings saved to keyboard."),
            )
        else:
            QMessageBox.warning(
                self.widget(),
                tr("KeychronRGB", "Error"),
                tr("KeychronRGB", "Failed to save RGB settings."),
            )

    # === Mixed RGB Methods ===

    # Region colors for visualization (up to 8 regions)
    REGION_COLORS = [
        (0, 255, 255),  # Region 0: Red
        (85, 255, 255),  # Region 1: Green
        (170, 255, 255),  # Region 2: Blue
        (42, 255, 255),  # Region 3: Yellow
        (212, 255, 255),  # Region 4: Purple
        (128, 255, 255),  # Region 5: Cyan
        (21, 255, 255),  # Region 6: Orange
        (234, 255, 255),  # Region 7: Pink
    ]

    def _setup_mixed_rgb(self):
        """Setup the Mixed RGB editor with current data."""
        if not self.keyboard:
            return

        layers = self.keyboard.keychron_mixed_rgb_layers
        effects_per_layer = self.keyboard.keychron_mixed_rgb_effects_per_layer

        logging.info(
            "KeychronRGB: Mixed RGB - %d regions, %d effects per region",
            layers,
            effects_per_layer,
        )

        # Build region legend text with color swatches
        legend_parts = []
        for i in range(layers):
            h, s, v = self.REGION_COLORS[i % len(self.REGION_COLORS)]
            color = QColor.fromHsv(int(h * 359 / 255), s, v)
            # Use colored text for region names
            legend_parts.append(
                f'<span style="color:{color.name()}; font-weight:bold;">Region {i}</span>'
            )

        if legend_parts:
            self.region_legend.setText(
                tr("KeychronRGB", "Color Legend: ") + " | ".join(legend_parts)
            )

        # Setup region dropdown with color indicators
        self.mixed_region_select.clear()
        for i in range(layers):
            h, s, v = self.REGION_COLORS[i % len(self.REGION_COLORS)]
            color = QColor.fromHsv(int(h * 359 / 255), s, v)
            # Create a colored square icon for the dropdown
            self.mixed_region_select.addItem(
                tr("KeychronRGB", "Region {} - {}").format(i, color.name()), i
            )

        # Setup the keyboard widget with region visualization
        self.mixed_rgb_keyboard.set_keys(self.keyboard.keys, self.keyboard.encoders)
        self.mixed_rgb_keyboard.set_led_matrix(self.keyboard.keychron_led_matrix)
        self._update_mixed_keyboard_colors()
        self.mixed_rgb_keyboard.deselect_all_keys()

        # Setup region effects tabs
        self._setup_region_effects_tabs()

    def _update_mixed_keyboard_colors(self):
        """Update the Mixed RGB keyboard visualization with region colors."""
        if not self.keyboard:
            return

        regions = self.keyboard.keychron_mixed_rgb_regions
        led_count = self.keyboard.keychron_led_count

        # Build color list based on region assignments
        colors = []
        for i in range(led_count):
            if i < len(regions):
                region = regions[i]
                h, s, v = self.REGION_COLORS[region % len(self.REGION_COLORS)]
            else:
                h, s, v = (0, 0, 128)  # Gray for unassigned
            colors.append((h, s, v))

        self.mixed_rgb_keyboard.set_led_colors(colors)

    def _setup_region_effects_tabs(self):
        """Setup the effects editor tabs for each region."""
        # Clear existing tabs
        self.region_effects_tabs.clear()

        if not self.keyboard:
            return

        layers = self.keyboard.keychron_mixed_rgb_layers
        effects_per_layer = self.keyboard.keychron_mixed_rgb_effects_per_layer
        all_effects = self.keyboard.keychron_mixed_rgb_effects

        for region in range(layers):
            tab = QWidget()
            tab_layout = QVBoxLayout()
            tab.setLayout(tab_layout)

            # Get effects for this region
            region_effects = all_effects[region] if region < len(all_effects) else []

            # Create widgets for each effect slot
            for slot in range(effects_per_layer):
                effect_frame = QFrame()
                effect_frame.setFrameStyle(QFrame.StyledPanel)
                effect_layout = QHBoxLayout()
                effect_frame.setLayout(effect_layout)

                # Slot number label
                slot_label = QLabel(tr("KeychronRGB", "Effect {}:").format(slot + 1))
                slot_label.setFixedWidth(60)
                slot_label.setStyleSheet("font-weight: bold;")
                effect_layout.addWidget(slot_label)

                # Effect dropdown
                effect_combo = QComboBox()
                effect_combo.addItem(tr("KeychronRGB", "Disabled"), 0)
                for eff in VIALRGB_EFFECTS[1:]:  # Skip "Disable"
                    effect_combo.addItem(eff.name, eff.idx)
                effect_combo.setMinimumWidth(150)

                # Set current value
                if slot < len(region_effects):
                    eff_data = region_effects[slot]
                    idx = effect_combo.findData(eff_data.get("effect", 0))
                    if idx >= 0:
                        effect_combo.setCurrentIndex(idx)

                effect_combo.setProperty("region", region)
                effect_combo.setProperty("slot", slot)
                effect_combo.currentIndexChanged.connect(self._on_region_effect_changed)
                effect_layout.addWidget(effect_combo)

                # Color button
                effect_layout.addWidget(QLabel(tr("KeychronRGB", "Color:")))
                color_btn = ColorButton()
                if slot < len(region_effects):
                    eff_data = region_effects[slot]
                    color_btn.set_hsv(
                        eff_data.get("hue", 0),
                        eff_data.get("sat", 255),
                        255,
                    )
                color_btn.setProperty("region", region)
                color_btn.setProperty("slot", slot)
                color_btn.color_changed.connect(self._on_region_effect_color_changed)
                effect_layout.addWidget(color_btn)

                # Speed slider
                effect_layout.addWidget(QLabel(tr("KeychronRGB", "Speed:")))
                speed_slider = QSlider(Qt.Horizontal)
                speed_slider.setMinimum(0)
                speed_slider.setMaximum(255)
                speed_slider.setFixedWidth(80)
                if slot < len(region_effects):
                    speed_slider.setValue(region_effects[slot].get("speed", 128))
                else:
                    speed_slider.setValue(128)
                speed_slider.setProperty("region", region)
                speed_slider.setProperty("slot", slot)
                speed_slider.valueChanged.connect(self._on_region_effect_speed_changed)
                effect_layout.addWidget(speed_slider)

                # Duration spinbox
                effect_layout.addWidget(QLabel(tr("KeychronRGB", "Duration:")))
                time_spin = QSpinBox()
                time_spin.setMinimum(100)
                time_spin.setMaximum(60000)
                time_spin.setSingleStep(100)
                time_spin.setSuffix(" ms")
                if slot < len(region_effects):
                    time_spin.setValue(region_effects[slot].get("time", 5000))
                else:
                    time_spin.setValue(5000)
                time_spin.setProperty("region", region)
                time_spin.setProperty("slot", slot)
                time_spin.valueChanged.connect(self._on_region_effect_time_changed)
                effect_layout.addWidget(time_spin)

                tab_layout.addWidget(effect_frame)

            tab_layout.addStretch()

            # Get region color for tab - use a colored icon/indicator
            h, s, v = self.REGION_COLORS[region % len(self.REGION_COLORS)]
            color = QColor.fromHsv(int(h * 359 / 255), s, v)
            # Add tab with region color name
            tab_idx = self.region_effects_tabs.addTab(
                tab, tr("KeychronRGB", "Region {} ({})").format(region, color.name())
            )
            # Set tab text color to match region
            self.region_effects_tabs.tabBar().setTabTextColor(tab_idx, color)

    def on_mixed_select_all(self):
        """Handle select all button for mixed RGB keyboard."""
        self.mixed_rgb_keyboard.select_all_keys()
        count = len(self.mixed_rgb_keyboard.selected_keys)
        self.mixed_selection_info.setText(
            tr("KeychronRGB", "{} keys selected").format(count)
        )

    def on_mixed_deselect_all(self):
        """Handle deselect all button for mixed RGB keyboard."""
        self.mixed_rgb_keyboard.deselect_all_keys()
        self.mixed_selection_info.setText(
            tr("KeychronRGB", "Click keys to select them")
        )

    def on_mixed_key_selected(self, key):
        """Handle key selection in mixed RGB keyboard."""
        count = len(self.mixed_rgb_keyboard.selected_keys)
        self.mixed_selection_info.setText(
            tr("KeychronRGB", "{} keys selected").format(count)
        )

    def on_mixed_key_deselected(self):
        """Handle key deselection in mixed RGB keyboard."""
        count = len(self.mixed_rgb_keyboard.selected_keys)
        if count > 0:
            self.mixed_selection_info.setText(
                tr("KeychronRGB", "{} keys selected").format(count)
            )
        else:
            self.mixed_selection_info.setText(
                tr("KeychronRGB", "Click keys to select them")
            )

    def on_apply_region(self):
        """Apply selected region to selected keys."""
        if self._updating or not self.keyboard:
            return

        region = self.mixed_region_select.currentData()
        if region is None:
            return

        led_indices = self.mixed_rgb_keyboard.get_selected_led_indices()
        if not led_indices:
            QMessageBox.warning(
                self.widget(),
                tr("KeychronRGB", "No Selection"),
                tr("KeychronRGB", "Please select keys to assign to a region."),
            )
            return

        # Update local data
        for led_idx in led_indices:
            if led_idx < len(self.keyboard.keychron_mixed_rgb_regions):
                self.keyboard.keychron_mixed_rgb_regions[led_idx] = region

        # Send to keyboard
        if self.keyboard.set_mixed_rgb_regions(
            0, self.keyboard.keychron_mixed_rgb_regions
        ):
            # Update visualization
            self._update_mixed_keyboard_colors()
        else:
            QMessageBox.warning(
                self.widget(),
                tr("KeychronRGB", "Error"),
                tr("KeychronRGB", "Failed to update region assignments."),
            )

    def _on_region_effect_changed(self, index):
        """Handle effect type change for a region effect slot."""
        if self._updating or not self.keyboard:
            return

        sender = self.sender()
        region = sender.property("region")
        slot = sender.property("slot")
        effect_id = sender.currentData()

        self._update_region_effect(region, slot, effect=effect_id)

    def _on_region_effect_color_changed(self, h, s, v):
        """Handle color change for a region effect slot."""
        if self._updating or not self.keyboard:
            return

        sender = self.sender()
        region = sender.property("region")
        slot = sender.property("slot")

        self._update_region_effect(region, slot, hue=h, sat=s)

    def _on_region_effect_speed_changed(self, value):
        """Handle speed change for a region effect slot."""
        if self._updating or not self.keyboard:
            return

        sender = self.sender()
        region = sender.property("region")
        slot = sender.property("slot")

        self._update_region_effect(region, slot, speed=value)

    def _on_region_effect_time_changed(self, value):
        """Handle duration change for a region effect slot."""
        if self._updating or not self.keyboard:
            return

        sender = self.sender()
        region = sender.property("region")
        slot = sender.property("slot")

        self._update_region_effect(region, slot, time=value)

    def _update_region_effect(self, region, slot, **kwargs):
        """Update a specific effect in a region."""
        if not self.keyboard:
            return

        # Get current effects for this region
        if region >= len(self.keyboard.keychron_mixed_rgb_effects):
            return
        effects = self.keyboard.keychron_mixed_rgb_effects[region]
        if slot >= len(effects):
            return

        # Update the specific fields
        eff = effects[slot]
        if "effect" in kwargs:
            eff["effect"] = kwargs["effect"]
        if "hue" in kwargs:
            eff["hue"] = kwargs["hue"]
        if "sat" in kwargs:
            eff["sat"] = kwargs["sat"]
        if "speed" in kwargs:
            eff["speed"] = kwargs["speed"]
        if "time" in kwargs:
            eff["time"] = kwargs["time"]

        # Send updated effect to keyboard
        self.keyboard.set_mixed_rgb_effect_list(region, slot, [eff])
