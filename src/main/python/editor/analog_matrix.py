# SPDX-License-Identifier: GPL-2.0-or-later
"""
Analog Matrix (Hall Effect) Editor - Settings for Hall Effect keyboards.

This editor handles:
- Profile management (multiple profiles)
- Global actuation point settings
- Rapid Trigger configuration
- Per-key actuation settings
- SOCD (Simultaneous Opposite Cardinal Directions) pairs
- Dynamic Keystroke (DKS/OKMC) configuration
- Joystick curve settings
- Calibration
"""

from PyQt5 import QtCore
from PyQt5.QtCore import Qt, QTimer
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
    QTabWidget,
    QMessageBox,
    QDoubleSpinBox,
    QProgressBar,
    QLineEdit,
)

from editor.basic_editor import BasicEditor
from protocol.keychron import (
    AKM_MODE_NAMES,
    AKM_GLOBAL,
    AKM_REGULAR,
    AKM_RAPID,
    AKM_DKS,
    SOCD_TYPE_NAMES,
    SOCD_PRI_NONE,
    CALIB_OFF,
    CALIB_ZERO_TRAVEL_MANUAL,
    CALIB_FULL_TRAVEL_MANUAL,
    CALIB_SAVE_AND_EXIT,
    CALIB_CLEAR,
)
from util import tr
from vial_device import VialKeyboard


class AnalogMatrixEditor(BasicEditor):
    """Editor for Analog Matrix (Hall Effect) keyboard settings."""

    def __init__(self):
        super().__init__()
        self.keyboard = None
        self.realtime_timer = None

        # Main tab widget
        self.tabs = QTabWidget()
        self.addWidget(self.tabs)

        # === Profile Tab ===
        self._create_profile_tab()

        # === Global Settings Tab ===
        self._create_global_settings_tab()

        # === SOCD Tab ===
        self._create_socd_tab()

        # === Calibration Tab ===
        self._create_calibration_tab()

        # === Joystick Tab ===
        self._create_joystick_tab()

        self._updating = False

    def _create_profile_tab(self):
        """Create the profile management tab."""
        profile_widget = QWidget()
        profile_layout = QVBoxLayout()
        profile_widget.setLayout(profile_layout)

        # Profile selection
        profile_group = QGroupBox(tr("AnalogMatrix", "Profile"))
        profile_group_layout = QHBoxLayout()

        profile_group_layout.addWidget(QLabel(tr("AnalogMatrix", "Active Profile:")))
        self.profile_selector = QComboBox()
        self.profile_selector.currentIndexChanged.connect(self.on_profile_changed)
        profile_group_layout.addWidget(self.profile_selector)

        self.btn_save_profile = QPushButton(tr("AnalogMatrix", "Save Profile"))
        self.btn_save_profile.clicked.connect(self.save_current_profile)
        profile_group_layout.addWidget(self.btn_save_profile)

        self.btn_reset_profile = QPushButton(tr("AnalogMatrix", "Reset Profile"))
        self.btn_reset_profile.clicked.connect(self.reset_current_profile)
        profile_group_layout.addWidget(self.btn_reset_profile)

        profile_group_layout.addStretch()
        profile_group.setLayout(profile_group_layout)
        profile_layout.addWidget(profile_group)

        # Profile info
        info_group = QGroupBox(tr("AnalogMatrix", "Profile Information"))
        info_layout = QGridLayout()

        info_layout.addWidget(QLabel(tr("AnalogMatrix", "Profile Count:")), 0, 0)
        self.profile_count_label = QLabel()
        info_layout.addWidget(self.profile_count_label, 0, 1)

        info_layout.addWidget(QLabel(tr("AnalogMatrix", "OKMC Slots:")), 1, 0)
        self.okmc_count_label = QLabel()
        info_layout.addWidget(self.okmc_count_label, 1, 1)

        info_layout.addWidget(QLabel(tr("AnalogMatrix", "SOCD Slots:")), 2, 0)
        self.socd_count_label = QLabel()
        info_layout.addWidget(self.socd_count_label, 2, 1)

        info_layout.addWidget(
            QLabel(tr("AnalogMatrix", "Analog Matrix Version:")), 3, 0
        )
        self.version_label = QLabel()
        info_layout.addWidget(self.version_label, 3, 1)

        info_group.setLayout(info_layout)
        profile_layout.addWidget(info_group)

        profile_layout.addStretch()
        self.tabs.addTab(profile_widget, tr("AnalogMatrix", "Profile"))

    def _create_global_settings_tab(self):
        """Create the global actuation settings tab."""
        global_widget = QWidget()
        global_layout = QVBoxLayout()
        global_widget.setLayout(global_layout)

        # Mode selection
        mode_group = QGroupBox(tr("AnalogMatrix", "Key Mode"))
        mode_layout = QHBoxLayout()

        mode_layout.addWidget(QLabel(tr("AnalogMatrix", "Default Mode:")))
        self.key_mode = QComboBox()
        for mode_id, name in AKM_MODE_NAMES.items():
            if mode_id in [AKM_GLOBAL, AKM_REGULAR, AKM_RAPID]:
                self.key_mode.addItem(name, mode_id)
        self.key_mode.currentIndexChanged.connect(self.on_mode_changed)
        mode_layout.addWidget(self.key_mode)
        mode_layout.addStretch()

        mode_group.setLayout(mode_layout)
        global_layout.addWidget(mode_group)

        # Actuation point settings
        actuation_group = QGroupBox(tr("AnalogMatrix", "Actuation Settings"))
        actuation_layout = QGridLayout()

        # Actuation point (0.1mm units, range 1-40 = 0.1mm to 4.0mm)
        actuation_layout.addWidget(
            QLabel(tr("AnalogMatrix", "Actuation Point (mm):")), 0, 0
        )
        self.actuation_point = QDoubleSpinBox()
        self.actuation_point.setRange(0.1, 4.0)
        self.actuation_point.setSingleStep(0.1)
        self.actuation_point.setDecimals(1)
        self.actuation_point.valueChanged.connect(self.on_actuation_changed)
        actuation_layout.addWidget(self.actuation_point, 0, 1)

        # Rapid Trigger sensitivity
        actuation_layout.addWidget(
            QLabel(tr("AnalogMatrix", "Rapid Trigger Sensitivity (mm):")), 1, 0
        )
        self.rt_sensitivity = QDoubleSpinBox()
        self.rt_sensitivity.setRange(0.1, 4.0)
        self.rt_sensitivity.setSingleStep(0.1)
        self.rt_sensitivity.setDecimals(1)
        self.rt_sensitivity.valueChanged.connect(self.on_actuation_changed)
        actuation_layout.addWidget(self.rt_sensitivity, 1, 1)

        # Rapid Trigger release sensitivity
        actuation_layout.addWidget(
            QLabel(tr("AnalogMatrix", "RT Release Sensitivity (mm):")), 2, 0
        )
        self.rt_release_sensitivity = QDoubleSpinBox()
        self.rt_release_sensitivity.setRange(0.1, 4.0)
        self.rt_release_sensitivity.setSingleStep(0.1)
        self.rt_release_sensitivity.setDecimals(1)
        self.rt_release_sensitivity.valueChanged.connect(self.on_actuation_changed)
        actuation_layout.addWidget(self.rt_release_sensitivity, 2, 1)

        actuation_group.setLayout(actuation_layout)
        global_layout.addWidget(actuation_group)

        # Apply button
        apply_layout = QHBoxLayout()
        apply_layout.addStretch()

        self.btn_apply_global = QPushButton(tr("AnalogMatrix", "Apply to All Keys"))
        self.btn_apply_global.clicked.connect(self.apply_global_settings)
        apply_layout.addWidget(self.btn_apply_global)

        global_layout.addLayout(apply_layout)
        global_layout.addStretch()

        self.tabs.addTab(global_widget, tr("AnalogMatrix", "Actuation"))

    def _create_socd_tab(self):
        """Create the SOCD configuration tab."""
        socd_widget = QWidget()
        socd_layout = QVBoxLayout()
        socd_widget.setLayout(socd_layout)

        info_label = QLabel(
            tr(
                "AnalogMatrix",
                "SOCD (Simultaneous Opposite Cardinal Directions) allows you to configure how the keyboard\n"
                "handles when two opposing direction keys are pressed at the same time.\n"
                "This is commonly used for gaming, especially in fighting games and FPS games.",
            )
        )
        info_label.setWordWrap(True)
        socd_layout.addWidget(info_label)

        # SOCD pairs will be added dynamically
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)

        self.socd_container = QWidget()
        self.socd_container_layout = QVBoxLayout()
        self.socd_container.setLayout(self.socd_container_layout)
        scroll.setWidget(self.socd_container)

        socd_layout.addWidget(scroll, 1)

        self.tabs.addTab(socd_widget, tr("AnalogMatrix", "SOCD"))

    def _create_calibration_tab(self):
        """Create the calibration tab."""
        calib_widget = QWidget()
        calib_layout = QVBoxLayout()
        calib_widget.setLayout(calib_layout)

        info_label = QLabel(
            tr(
                "AnalogMatrix",
                "Calibration allows you to set the zero-point and full-travel for each key.\n"
                "This ensures accurate actuation detection for your specific switches.",
            )
        )
        info_label.setWordWrap(True)
        calib_layout.addWidget(info_label)

        # Calibration controls
        calib_group = QGroupBox(tr("AnalogMatrix", "Calibration Controls"))
        calib_group_layout = QVBoxLayout()

        buttons_layout = QHBoxLayout()

        self.btn_calib_zero = QPushButton(tr("AnalogMatrix", "Calibrate Zero Point"))
        self.btn_calib_zero.clicked.connect(
            lambda: self.start_calibration(CALIB_ZERO_TRAVEL_MANUAL)
        )
        buttons_layout.addWidget(self.btn_calib_zero)

        self.btn_calib_full = QPushButton(tr("AnalogMatrix", "Calibrate Full Travel"))
        self.btn_calib_full.clicked.connect(
            lambda: self.start_calibration(CALIB_FULL_TRAVEL_MANUAL)
        )
        buttons_layout.addWidget(self.btn_calib_full)

        self.btn_calib_save = QPushButton(tr("AnalogMatrix", "Save Calibration"))
        self.btn_calib_save.clicked.connect(
            lambda: self.start_calibration(CALIB_SAVE_AND_EXIT)
        )
        buttons_layout.addWidget(self.btn_calib_save)

        self.btn_calib_clear = QPushButton(tr("AnalogMatrix", "Clear Calibration"))
        self.btn_calib_clear.clicked.connect(
            lambda: self.start_calibration(CALIB_CLEAR)
        )
        buttons_layout.addWidget(self.btn_calib_clear)

        calib_group_layout.addLayout(buttons_layout)

        # Calibration status
        self.calib_status = QLabel(tr("AnalogMatrix", "Status: Not calibrating"))
        calib_group_layout.addWidget(self.calib_status)

        calib_group.setLayout(calib_group_layout)
        calib_layout.addWidget(calib_group)

        # Real-time key travel display
        realtime_group = QGroupBox(tr("AnalogMatrix", "Real-time Key Travel"))
        realtime_layout = QGridLayout()

        realtime_layout.addWidget(QLabel(tr("AnalogMatrix", "Row:")), 0, 0)
        self.realtime_row = QSpinBox()
        self.realtime_row.setRange(0, 20)
        realtime_layout.addWidget(self.realtime_row, 0, 1)

        realtime_layout.addWidget(QLabel(tr("AnalogMatrix", "Col:")), 0, 2)
        self.realtime_col = QSpinBox()
        self.realtime_col.setRange(0, 20)
        realtime_layout.addWidget(self.realtime_col, 0, 3)

        self.btn_start_realtime = QPushButton(tr("AnalogMatrix", "Start Monitoring"))
        self.btn_start_realtime.clicked.connect(self.toggle_realtime_monitoring)
        realtime_layout.addWidget(self.btn_start_realtime, 0, 4)

        # Travel display
        self.travel_progress = QProgressBar()
        self.travel_progress.setRange(0, 40)  # 0 to 4.0mm
        self.travel_progress.setFormat("%v (0.1mm)")
        realtime_layout.addWidget(self.travel_progress, 1, 0, 1, 5)

        self.travel_details = QLabel()
        realtime_layout.addWidget(self.travel_details, 2, 0, 1, 5)

        realtime_group.setLayout(realtime_layout)
        calib_layout.addWidget(realtime_group)

        calib_layout.addStretch()
        self.tabs.addTab(calib_widget, tr("AnalogMatrix", "Calibration"))

    def _create_joystick_tab(self):
        """Create the joystick/gamepad settings tab."""
        joystick_widget = QWidget()
        joystick_layout = QVBoxLayout()
        joystick_widget.setLayout(joystick_layout)

        info_label = QLabel(
            tr(
                "AnalogMatrix",
                "Configure joystick response curve and game controller mode.\n"
                "The response curve affects how key travel translates to analog input.",
            )
        )
        info_label.setWordWrap(True)
        joystick_layout.addWidget(info_label)

        # Game controller mode
        mode_group = QGroupBox(tr("AnalogMatrix", "Game Controller Mode"))
        mode_layout = QHBoxLayout()

        mode_layout.addWidget(QLabel(tr("AnalogMatrix", "Mode:")))
        self.gc_mode = QComboBox()
        self.gc_mode.addItem(tr("AnalogMatrix", "Disabled"), 0)
        self.gc_mode.addItem(tr("AnalogMatrix", "Enabled"), 1)
        self.gc_mode.currentIndexChanged.connect(self.on_gc_mode_changed)
        mode_layout.addWidget(self.gc_mode)
        mode_layout.addStretch()

        mode_group.setLayout(mode_layout)
        joystick_layout.addWidget(mode_group)

        # Response curve
        curve_group = QGroupBox(tr("AnalogMatrix", "Response Curve"))
        curve_layout = QGridLayout()

        self.curve_sliders = []
        for i in range(4):
            curve_layout.addWidget(
                QLabel(tr("AnalogMatrix", "Point {}:").format(i + 1)), i, 0
            )
            slider = QSlider(Qt.Horizontal)
            slider.setRange(0, 255)
            slider.valueChanged.connect(self.on_curve_changed)
            curve_layout.addWidget(slider, i, 1)
            value_label = QLabel("0")
            curve_layout.addWidget(value_label, i, 2)
            self.curve_sliders.append((slider, value_label))

        self.btn_apply_curve = QPushButton(tr("AnalogMatrix", "Apply Curve"))
        self.btn_apply_curve.clicked.connect(self.apply_curve)
        curve_layout.addWidget(self.btn_apply_curve, 4, 0, 1, 3)

        curve_group.setLayout(curve_layout)
        joystick_layout.addWidget(curve_group)

        joystick_layout.addStretch()
        self.tabs.addTab(joystick_widget, tr("AnalogMatrix", "Joystick"))

    def valid(self):
        """Check if this tab should be shown."""
        if not isinstance(self.device, VialKeyboard):
            return False
        kb = self.device.keyboard
        if not hasattr(kb, "has_keychron_analog"):
            return False
        return kb.has_keychron_analog()

    def rebuild(self, device):
        super().rebuild(device)
        if not self.valid():
            return

        self.keyboard = device.keyboard
        self._updating = True

        # Update profile info
        self.profile_count_label.setText(
            str(self.keyboard.keychron_analog_profile_count)
        )
        self.okmc_count_label.setText(str(self.keyboard.keychron_analog_okmc_count))
        self.socd_count_label.setText(str(self.keyboard.keychron_analog_socd_count))
        self.version_label.setText(f"0x{self.keyboard.keychron_analog_version:08X}")

        # Update profile selector
        self.profile_selector.clear()
        for i in range(self.keyboard.keychron_analog_profile_count):
            self.profile_selector.addItem(
                tr("AnalogMatrix", "Profile {}").format(i + 1), i
            )
        if (
            self.keyboard.keychron_analog_current_profile
            < self.profile_selector.count()
        ):
            self.profile_selector.setCurrentIndex(
                self.keyboard.keychron_analog_current_profile
            )

        # Update game controller mode
        idx = self.gc_mode.findData(self.keyboard.keychron_analog_game_controller_mode)
        if idx >= 0:
            self.gc_mode.setCurrentIndex(idx)

        # Update curve sliders
        for i, (slider, label) in enumerate(self.curve_sliders):
            if i < len(self.keyboard.keychron_analog_curve):
                value = self.keyboard.keychron_analog_curve[i]
                slider.setValue(value)
                label.setText(str(value))

        self._updating = False

    def on_profile_changed(self):
        """Handle profile selection change."""
        if self._updating or not self.keyboard:
            return
        profile = self.profile_selector.currentData()
        if profile is not None:
            self.keyboard.select_keychron_analog_profile(profile)

    def save_current_profile(self):
        """Save current profile to EEPROM."""
        if not self.keyboard:
            return
        profile = self.profile_selector.currentData()
        if profile is not None:
            if self.keyboard.save_keychron_analog_profile(profile):
                QMessageBox.information(
                    self.widget(),
                    tr("AnalogMatrix", "Saved"),
                    tr("AnalogMatrix", "Profile {} saved.").format(profile + 1),
                )
            else:
                QMessageBox.warning(
                    self.widget(),
                    tr("AnalogMatrix", "Error"),
                    tr("AnalogMatrix", "Failed to save profile."),
                )

    def reset_current_profile(self):
        """Reset current profile to defaults."""
        if not self.keyboard:
            return
        profile = self.profile_selector.currentData()
        if profile is not None:
            if (
                QMessageBox.question(
                    self.widget(),
                    tr("AnalogMatrix", "Reset Profile"),
                    tr("AnalogMatrix", "Reset Profile {} to defaults?").format(
                        profile + 1
                    ),
                    QMessageBox.Yes | QMessageBox.No,
                )
                == QMessageBox.Yes
            ):
                self.keyboard.reset_keychron_analog_profile(profile)

    def on_mode_changed(self):
        """Handle key mode change."""
        if self._updating:
            return
        # Mode is applied when "Apply to All Keys" is clicked

    def on_actuation_changed(self):
        """Handle actuation settings change."""
        if self._updating:
            return
        # Settings are applied when "Apply to All Keys" is clicked

    def apply_global_settings(self):
        """Apply global settings to all keys."""
        if not self.keyboard:
            return

        profile = self.profile_selector.currentData()
        mode = self.key_mode.currentData()
        act_pt = int(self.actuation_point.value() * 10)  # Convert to 0.1mm units
        sens = int(self.rt_sensitivity.value() * 10)
        rls_sens = int(self.rt_release_sensitivity.value() * 10)

        if self.keyboard.set_keychron_analog_travel(
            profile, mode, act_pt, sens, rls_sens, entire=True
        ):
            QMessageBox.information(
                self.widget(),
                tr("AnalogMatrix", "Applied"),
                tr("AnalogMatrix", "Settings applied to all keys."),
            )
        else:
            QMessageBox.warning(
                self.widget(),
                tr("AnalogMatrix", "Error"),
                tr("AnalogMatrix", "Failed to apply settings."),
            )

    def start_calibration(self, calib_type):
        """Start calibration process."""
        if not self.keyboard:
            return

        if self.keyboard.start_keychron_calibration(calib_type):
            status_messages = {
                CALIB_ZERO_TRAVEL_MANUAL: tr(
                    "AnalogMatrix", "Status: Calibrating zero point..."
                ),
                CALIB_FULL_TRAVEL_MANUAL: tr(
                    "AnalogMatrix", "Status: Calibrating full travel..."
                ),
                CALIB_SAVE_AND_EXIT: tr("AnalogMatrix", "Status: Calibration saved"),
                CALIB_CLEAR: tr("AnalogMatrix", "Status: Calibration cleared"),
            }
            self.calib_status.setText(
                status_messages.get(
                    calib_type, tr("AnalogMatrix", "Status: Calibration in progress...")
                )
            )
        else:
            QMessageBox.warning(
                self.widget(),
                tr("AnalogMatrix", "Error"),
                tr("AnalogMatrix", "Failed to start calibration."),
            )

    def toggle_realtime_monitoring(self):
        """Toggle real-time key travel monitoring."""
        if self.realtime_timer is None:
            self.realtime_timer = QTimer()
            self.realtime_timer.timeout.connect(self.update_realtime_travel)
            self.realtime_timer.start(50)  # 20Hz
            self.btn_start_realtime.setText(tr("AnalogMatrix", "Stop Monitoring"))
        else:
            self.realtime_timer.stop()
            self.realtime_timer = None
            self.btn_start_realtime.setText(tr("AnalogMatrix", "Start Monitoring"))

    def update_realtime_travel(self):
        """Update real-time travel display."""
        if not self.keyboard:
            return

        row = self.realtime_row.value()
        col = self.realtime_col.value()

        travel_data = self.keyboard.get_keychron_realtime_travel(row, col)
        if travel_data:
            self.travel_progress.setValue(travel_data["travel_mm"])
            self.travel_details.setText(
                tr(
                    "AnalogMatrix",
                    "Travel: {}mm | Raw: {} | Value: {} | Zero: {} | Full: {} | State: {}",
                ).format(
                    travel_data["travel_mm"] / 10.0,
                    travel_data["travel_raw"],
                    travel_data["value"],
                    travel_data["zero"],
                    travel_data["full"],
                    travel_data["state"],
                )
            )

    def on_gc_mode_changed(self):
        """Handle game controller mode change."""
        if self._updating or not self.keyboard:
            return
        mode = self.gc_mode.currentData()
        if mode is not None:
            self.keyboard.set_keychron_analog_game_controller_mode(mode)

    def on_curve_changed(self):
        """Handle curve slider change."""
        if self._updating:
            return
        for slider, label in self.curve_sliders:
            label.setText(str(slider.value()))

    def apply_curve(self):
        """Apply joystick response curve."""
        if not self.keyboard:
            return

        curve = [slider.value() for slider, _ in self.curve_sliders]
        if self.keyboard.set_keychron_analog_curve(curve):
            QMessageBox.information(
                self.widget(),
                tr("AnalogMatrix", "Applied"),
                tr("AnalogMatrix", "Curve settings applied."),
            )
        else:
            QMessageBox.warning(
                self.widget(),
                tr("AnalogMatrix", "Error"),
                tr("AnalogMatrix", "Failed to apply curve settings."),
            )

    def deactivate(self):
        """Called when tab is deactivated."""
        if self.realtime_timer:
            self.realtime_timer.stop()
            self.realtime_timer = None
            self.btn_start_realtime.setText(tr("AnalogMatrix", "Start Monitoring"))
