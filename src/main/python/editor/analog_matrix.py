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
    AKM_GAMEPAD,
    AKM_TOGGLE,
    ADV_MODE_CLEAR,
    ADV_MODE_OKMC,
    ADV_MODE_GAME_CONTROLLER,
    ADV_MODE_TOGGLE,
    SOCD_TYPE_NAMES,
    SOCD_PRI_NONE,
    CALIB_OFF,
    CALIB_ZERO_TRAVEL_MANUAL,
    CALIB_FULL_TRAVEL_MANUAL,
    CALIB_SAVE_AND_EXIT,
    CALIB_CLEAR,
    OKMC_ACTION_NAMES,
    OKMC_ACTION_NONE,
    GC_AXIS_NAMES,
    GC_AXIS_MAX,
)
import sys

from util import tr
from vial_device import VialKeyboard
from widgets.actuation_keyboard_widget import ActuationKeyboardWidget


def _show_warning(parent, title, text):
    """Show a warning message box (non-blocking on Emscripten)."""
    if sys.platform == "emscripten":
        box = QMessageBox(QMessageBox.Warning, title, text, QMessageBox.Ok, parent)
        box.setModal(True)
        box.setAttribute(Qt.WA_DeleteOnClose)
        box.show()
    else:
        QMessageBox.warning(parent, title, text)


def _show_info(parent, title, text):
    """Show an info message box (non-blocking on Emscripten)."""
    if sys.platform == "emscripten":
        box = QMessageBox(QMessageBox.Information, title, text, QMessageBox.Ok, parent)
        box.setModal(True)
        box.setAttribute(Qt.WA_DeleteOnClose)
        box.show()
    else:
        QMessageBox.information(parent, title, text)


def _ask_question(parent, title, text, on_yes):
    """Ask a yes/no question (non-blocking on Emscripten).

    On Emscripten, shows a non-blocking dialog and calls *on_yes()* when the
    user clicks Yes.  On desktop, uses the blocking static method and calls
    *on_yes()* immediately if the answer is Yes.

    Returns the QMessageBox instance on Emscripten (caller must prevent GC)
    or None on desktop.
    """
    if sys.platform == "emscripten":
        box = QMessageBox(
            QMessageBox.Question,
            title,
            text,
            QMessageBox.Yes | QMessageBox.No,
            parent,
        )
        box.setModal(True)
        box.setAttribute(Qt.WA_DeleteOnClose)

        def _on_finished(result):
            if box.clickedButton() == box.button(QMessageBox.Yes):
                on_yes()

        box.finished.connect(_on_finished)
        box.show()
        return box
    else:
        if (
            QMessageBox.question(parent, title, text, QMessageBox.Yes | QMessageBox.No)
            == QMessageBox.Yes
        ):
            on_yes()
        return None


class AnalogMatrixEditor(BasicEditor):
    """Editor for Analog Matrix (Hall Effect) keyboard settings."""

    def __init__(self):
        super().__init__()
        self.keyboard = None
        self.realtime_timer = None
        self.calibration_timer = None

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

        # === DKS Tab ===
        self._create_dks_tab()

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

        # Profile name editor
        name_group = QGroupBox(tr("AnalogMatrix", "Profile Name"))
        name_layout = QHBoxLayout()
        name_layout.addWidget(QLabel(tr("AnalogMatrix", "Name:")))
        self.profile_name_edit = QLineEdit()
        self.profile_name_edit.setMaxLength(30)
        self.profile_name_edit.setPlaceholderText(
            tr("AnalogMatrix", "Enter profile name (max 30 chars)")
        )
        name_layout.addWidget(self.profile_name_edit)
        self.btn_set_name = QPushButton(tr("AnalogMatrix", "Set Name"))
        self.btn_set_name.clicked.connect(self._apply_profile_name)
        name_layout.addWidget(self.btn_set_name)
        name_group.setLayout(name_layout)
        profile_layout.addWidget(name_group)

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
        """Create the actuation settings tab with keyboard visualization."""
        global_widget = QWidget()
        global_layout = QVBoxLayout()
        global_widget.setLayout(global_layout)

        # Info label
        info_label = QLabel(
            tr(
                "AnalogMatrix",
                "Click keys to select them, then adjust settings below. "
                "Hold Ctrl/Shift for multi-select. Use 'Apply to All' for global changes.",
            )
        )
        info_label.setWordWrap(True)
        global_layout.addWidget(info_label)

        # Keyboard visualization widget
        self.actuation_keyboard = ActuationKeyboardWidget(None)
        self.actuation_keyboard.setMinimumHeight(250)
        self.actuation_keyboard.key_selected.connect(self._on_perkey_key_selected)
        self.actuation_keyboard.key_deselected.connect(self._on_perkey_key_deselected)
        global_layout.addWidget(self.actuation_keyboard, 1)

        # Selection controls
        selection_layout = QHBoxLayout()

        self.btn_select_all = QPushButton(tr("AnalogMatrix", "Select All"))
        self.btn_select_all.clicked.connect(self._perkey_select_all)
        selection_layout.addWidget(self.btn_select_all)

        self.btn_deselect_all = QPushButton(tr("AnalogMatrix", "Deselect All"))
        self.btn_deselect_all.clicked.connect(self._perkey_deselect_all)
        selection_layout.addWidget(self.btn_deselect_all)

        selection_layout.addStretch()

        self.perkey_selection_label = QLabel(tr("AnalogMatrix", "Selected: 0 keys"))
        selection_layout.addWidget(self.perkey_selection_label)

        global_layout.addLayout(selection_layout)

        # Global Defaults group — sets the profile's global slot (inherited by AKM_GLOBAL keys)
        global_defaults_group = QGroupBox(
            tr(
                "AnalogMatrix",
                "Global Defaults (inherited by keys set to 'Global' mode)",
            )
        )
        global_defaults_layout = QGridLayout()

        global_defaults_layout.addWidget(QLabel(tr("AnalogMatrix", "Mode:")), 0, 0)
        self.global_mode = QComboBox()
        # Global slot only supports Regular and Rapid Trigger
        self.global_mode.addItem(AKM_MODE_NAMES[AKM_REGULAR], AKM_REGULAR)
        self.global_mode.addItem(AKM_MODE_NAMES[AKM_RAPID], AKM_RAPID)
        self.global_mode.setToolTip(
            tr(
                "AnalogMatrix",
                "Default mode for keys set to 'Global':\n"
                "  Regular — fixed actuation point\n"
                "  Rapid Trigger — dynamic actuation",
            )
        )
        global_defaults_layout.addWidget(self.global_mode, 0, 1)

        global_defaults_layout.addWidget(
            QLabel(tr("AnalogMatrix", "Actuation Point:")), 0, 2
        )
        self.global_actuation_point = QDoubleSpinBox()
        self.global_actuation_point.setRange(0.1, 3.9)
        self.global_actuation_point.setSingleStep(0.1)
        self.global_actuation_point.setDecimals(1)
        self.global_actuation_point.setValue(2.0)
        self.global_actuation_point.setSuffix(" mm")
        global_defaults_layout.addWidget(self.global_actuation_point, 0, 3)

        global_defaults_layout.addWidget(
            QLabel(tr("AnalogMatrix", "RT Sensitivity:")), 1, 0
        )
        self.global_rt_sensitivity = QDoubleSpinBox()
        self.global_rt_sensitivity.setRange(0.1, 3.9)
        self.global_rt_sensitivity.setSingleStep(0.1)
        self.global_rt_sensitivity.setDecimals(1)
        self.global_rt_sensitivity.setValue(0.3)
        self.global_rt_sensitivity.setSuffix(" mm")
        global_defaults_layout.addWidget(self.global_rt_sensitivity, 1, 1)

        global_defaults_layout.addWidget(
            QLabel(tr("AnalogMatrix", "RT Release:")), 1, 2
        )
        self.global_rt_release = QDoubleSpinBox()
        self.global_rt_release.setRange(0.1, 3.9)
        self.global_rt_release.setSingleStep(0.1)
        self.global_rt_release.setDecimals(1)
        self.global_rt_release.setValue(0.3)
        self.global_rt_release.setSuffix(" mm")
        global_defaults_layout.addWidget(self.global_rt_release, 1, 3)

        self.btn_set_global_defaults = QPushButton(
            tr("AnalogMatrix", "Set Global Defaults")
        )
        self.btn_set_global_defaults.setToolTip(
            tr(
                "AnalogMatrix",
                "Write these values to the profile's global config slot.\n"
                "All keys set to 'Global' mode will use these settings.",
            )
        )
        self.btn_set_global_defaults.clicked.connect(self._apply_global_defaults)
        global_defaults_layout.addWidget(self.btn_set_global_defaults, 2, 0, 1, 4)

        global_defaults_group.setLayout(global_defaults_layout)
        global_layout.addWidget(global_defaults_group)

        # Settings group
        settings_group = QGroupBox(tr("AnalogMatrix", "Actuation Settings"))
        settings_layout = QGridLayout()

        # Mode selection
        settings_layout.addWidget(QLabel(tr("AnalogMatrix", "Mode:")), 0, 0)
        self.key_mode = QComboBox()
        for mode_id, name in AKM_MODE_NAMES.items():
            self.key_mode.addItem(name, mode_id)
        self.key_mode.setToolTip(
            tr(
                "AnalogMatrix",
                "Global: Use profile default settings\n"
                "Regular: Fixed actuation point\n"
                "Rapid Trigger: Dynamic actuation based on key travel direction\n"
                "Dynamic Keystroke: Multi-action DKS (configure in DKS tab)\n"
                "Gamepad: Analog joystick axis assignment\n"
                "Toggle: Key toggles on/off each press",
            )
        )
        self.key_mode.currentIndexChanged.connect(self.on_mode_changed)
        settings_layout.addWidget(self.key_mode, 0, 1)

        # Actuation point (0.1mm units, range 1-39 = 0.1mm to 3.9mm)
        settings_layout.addWidget(QLabel(tr("AnalogMatrix", "Actuation Point:")), 0, 2)
        self.actuation_point = QDoubleSpinBox()
        self.actuation_point.setRange(0.1, 3.9)
        self.actuation_point.setSingleStep(0.1)
        self.actuation_point.setDecimals(1)
        self.actuation_point.setValue(2.0)
        self.actuation_point.setSuffix(" mm")
        self.actuation_point.setToolTip(
            tr(
                "AnalogMatrix",
                "Distance the key must travel before registering a press (0.1-3.9mm)",
            )
        )
        self.actuation_point.valueChanged.connect(self.on_actuation_changed)
        settings_layout.addWidget(self.actuation_point, 0, 3)

        # Rapid Trigger sensitivity
        settings_layout.addWidget(QLabel(tr("AnalogMatrix", "RT Sensitivity:")), 1, 0)
        self.rt_sensitivity = QDoubleSpinBox()
        self.rt_sensitivity.setRange(0.1, 3.9)
        self.rt_sensitivity.setSingleStep(0.1)
        self.rt_sensitivity.setDecimals(1)
        self.rt_sensitivity.setValue(0.3)
        self.rt_sensitivity.setSuffix(" mm")
        self.rt_sensitivity.setToolTip(
            tr(
                "AnalogMatrix",
                "Rapid Trigger press sensitivity - distance key must move down to re-register",
            )
        )
        self.rt_sensitivity.valueChanged.connect(self.on_actuation_changed)
        settings_layout.addWidget(self.rt_sensitivity, 1, 1)

        # Rapid Trigger release sensitivity
        settings_layout.addWidget(QLabel(tr("AnalogMatrix", "RT Release:")), 1, 2)
        self.rt_release_sensitivity = QDoubleSpinBox()
        self.rt_release_sensitivity.setRange(0.1, 3.9)
        self.rt_release_sensitivity.setSingleStep(0.1)
        self.rt_release_sensitivity.setDecimals(1)
        self.rt_release_sensitivity.setValue(0.3)
        self.rt_release_sensitivity.setSuffix(" mm")
        self.rt_release_sensitivity.setToolTip(
            tr(
                "AnalogMatrix",
                "Rapid Trigger release sensitivity - distance key must move up to release",
            )
        )
        self.rt_release_sensitivity.valueChanged.connect(self.on_actuation_changed)
        settings_layout.addWidget(self.rt_release_sensitivity, 1, 3)

        # Gamepad axis/direction (shown only when mode=Gamepad)
        settings_layout.addWidget(QLabel(tr("AnalogMatrix", "Joystick Axis:")), 2, 0)
        self.gamepad_axis = QComboBox()
        for val, name in GC_AXIS_NAMES.items():
            self.gamepad_axis.addItem(name, val)
        self.gamepad_axis.setToolTip(
            tr("AnalogMatrix", "Joystick axis or button to assign to this key")
        )
        settings_layout.addWidget(self.gamepad_axis, 2, 1)

        self._gamepad_row_widgets = [
            settings_layout.itemAtPosition(2, 0).widget()
            if settings_layout.itemAtPosition(2, 0)
            else None,
            self.gamepad_axis,
        ]

        settings_group.setLayout(settings_layout)
        global_layout.addWidget(settings_group)

        # Apply buttons
        apply_layout = QHBoxLayout()
        apply_layout.addStretch()

        self.btn_apply_selected = QPushButton(tr("AnalogMatrix", "Apply to Selected"))
        self.btn_apply_selected.clicked.connect(self._apply_perkey_settings)
        self.btn_apply_selected.setToolTip(
            tr("AnalogMatrix", "Apply settings only to the selected keys")
        )
        apply_layout.addWidget(self.btn_apply_selected)

        self.btn_apply_global = QPushButton(tr("AnalogMatrix", "Apply to All Keys"))
        self.btn_apply_global.clicked.connect(self.apply_global_settings)
        self.btn_apply_global.setToolTip(
            tr("AnalogMatrix", "Apply settings globally to all keys in this profile")
        )
        apply_layout.addWidget(self.btn_apply_global)

        global_layout.addLayout(apply_layout)

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
                "Calibration lets you set the zero-point and full-travel for each key.\n"
                "Start zero calibration first; the firmware will automatically advance to full travel and finish when complete.",
            )
        )
        info_label.setWordWrap(True)
        calib_layout.addWidget(info_label)

        # Calibration controls
        calib_group = QGroupBox(tr("AnalogMatrix", "Calibration Controls"))
        calib_group_layout = QVBoxLayout()

        buttons_layout = QHBoxLayout()

        self.btn_calib_zero = QPushButton(tr("AnalogMatrix", "Start Calibration"))
        self.btn_calib_zero.clicked.connect(
            lambda: self.start_calibration(CALIB_ZERO_TRAVEL_MANUAL)
        )
        buttons_layout.addWidget(self.btn_calib_zero)

        self.btn_calib_full = QPushButton(tr("AnalogMatrix", "Finish Early"))
        self.btn_calib_full.clicked.connect(
            lambda: self.start_calibration(CALIB_SAVE_AND_EXIT)
        )
        self.btn_calib_full.setEnabled(False)
        buttons_layout.addWidget(self.btn_calib_full)

        self.btn_calib_save = QPushButton(tr("AnalogMatrix", "Save Calibration"))
        self.btn_calib_save.clicked.connect(
            lambda: self.start_calibration(CALIB_SAVE_AND_EXIT)
        )
        self.btn_calib_save.setVisible(False)
        buttons_layout.addWidget(self.btn_calib_save)

        self.btn_calib_clear = QPushButton(tr("AnalogMatrix", "Clear Calibration"))
        self.btn_calib_clear.clicked.connect(
            lambda: self.start_calibration(CALIB_CLEAR)
        )
        self.btn_calib_clear.setEnabled(False)
        self.btn_calib_clear.setToolTip(
            tr(
                "AnalogMatrix",
                "Not supported by current firmware (CALIB_CLEAR always fails)",
            )
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

        # Calibrated value readout group
        calval_group = QGroupBox(tr("AnalogMatrix", "Read Calibrated Values"))
        calval_layout = QGridLayout()

        calval_layout.addWidget(QLabel(tr("AnalogMatrix", "Row:")), 0, 0)
        self.calval_row = QSpinBox()
        self.calval_row.setRange(0, 20)
        calval_layout.addWidget(self.calval_row, 0, 1)

        calval_layout.addWidget(QLabel(tr("AnalogMatrix", "Col:")), 0, 2)
        self.calval_col = QSpinBox()
        self.calval_col.setRange(0, 24)
        calval_layout.addWidget(self.calval_col, 0, 3)

        self.btn_read_calval = QPushButton(tr("AnalogMatrix", "Read"))
        self.btn_read_calval.clicked.connect(self._read_calibrated_value)
        calval_layout.addWidget(self.btn_read_calval, 0, 4)

        self.calval_result = QLabel(tr("AnalogMatrix", "Zero: — | Full: — | Scale: —"))
        calval_layout.addWidget(self.calval_result, 1, 0, 1, 5)

        calval_group.setLayout(calval_layout)
        calib_layout.addWidget(calval_group)

        calib_layout.addStretch()
        self.tabs.addTab(calib_widget, tr("AnalogMatrix", "Calibration"))

    def _create_dks_tab(self):
        """Create the DKS (Dynamic Keystroke / OKMC) configuration tab."""
        dks_widget = QWidget()
        dks_layout = QVBoxLayout()
        dks_widget.setLayout(dks_layout)

        info_label = QLabel(
            tr(
                "AnalogMatrix",
                "Dynamic Keystroke (DKS) allows assigning up to 4 keycodes to a key,\n"
                "triggered at different travel depths (shallow press/release, deep press/release).\n"
                "Select a DKS slot, configure the travel thresholds and keycodes, then click Apply.",
            )
        )
        info_label.setWordWrap(True)
        dks_layout.addWidget(info_label)

        # Slot selector
        slot_layout = QHBoxLayout()
        slot_layout.addWidget(QLabel(tr("AnalogMatrix", "DKS Slot:")))
        self.dks_slot_selector = QComboBox()
        self.dks_slot_selector.currentIndexChanged.connect(self._on_dks_slot_changed)
        slot_layout.addWidget(self.dks_slot_selector)
        slot_layout.addStretch()
        dks_layout.addLayout(slot_layout)

        # Travel thresholds group
        travel_group = QGroupBox(
            tr("AnalogMatrix", "Travel Thresholds (0.1 mm units, 0-6.3 mm)")
        )
        travel_layout = QGridLayout()

        travel_layout.addWidget(QLabel(tr("AnalogMatrix", "Shallow Act:")), 0, 0)
        self.dks_shallow_act = QDoubleSpinBox()
        self.dks_shallow_act.setRange(0.0, 6.3)
        self.dks_shallow_act.setSingleStep(0.1)
        self.dks_shallow_act.setDecimals(1)
        self.dks_shallow_act.setSuffix(" mm")
        travel_layout.addWidget(self.dks_shallow_act, 0, 1)

        travel_layout.addWidget(QLabel(tr("AnalogMatrix", "Shallow Deact:")), 0, 2)
        self.dks_shallow_deact = QDoubleSpinBox()
        self.dks_shallow_deact.setRange(0.0, 6.3)
        self.dks_shallow_deact.setSingleStep(0.1)
        self.dks_shallow_deact.setDecimals(1)
        self.dks_shallow_deact.setSuffix(" mm")
        travel_layout.addWidget(self.dks_shallow_deact, 0, 3)

        travel_layout.addWidget(QLabel(tr("AnalogMatrix", "Deep Act:")), 1, 0)
        self.dks_deep_act = QDoubleSpinBox()
        self.dks_deep_act.setRange(0.0, 6.3)
        self.dks_deep_act.setSingleStep(0.1)
        self.dks_deep_act.setDecimals(1)
        self.dks_deep_act.setSuffix(" mm")
        travel_layout.addWidget(self.dks_deep_act, 1, 1)

        travel_layout.addWidget(QLabel(tr("AnalogMatrix", "Deep Deact:")), 1, 2)
        self.dks_deep_deact = QDoubleSpinBox()
        self.dks_deep_deact.setRange(0.0, 6.3)
        self.dks_deep_deact.setSingleStep(0.1)
        self.dks_deep_deact.setDecimals(1)
        self.dks_deep_deact.setSuffix(" mm")
        travel_layout.addWidget(self.dks_deep_deact, 1, 3)

        travel_group.setLayout(travel_layout)
        dks_layout.addWidget(travel_group)

        # Keycodes group — 4 HID keycode spinboxes (hex display)
        kc_group = QGroupBox(tr("AnalogMatrix", "Keycodes (HID hex)"))
        kc_layout = QGridLayout()
        self.dks_keycodes = []
        for i in range(4):
            kc_layout.addWidget(QLabel(tr("AnalogMatrix", "KC{}:").format(i)), 0, i * 2)
            kc_edit = QLineEdit("0x0000")
            kc_edit.setMaxLength(6)
            kc_edit.setToolTip(
                tr("AnalogMatrix", "HID keycode in hex, e.g. 0x0004 for 'A'")
            )
            kc_layout.addWidget(kc_edit, 1, i * 2)
            self.dks_keycodes.append(kc_edit)
        kc_group.setLayout(kc_layout)
        dks_layout.addWidget(kc_group)

        # Actions group — rows = events, columns = keycode slots
        # Each cell: what action fires for that keycode slot on that event
        # Values: None / Release / Press / Tap / Re-press  (firmware bitfield)
        DKS_EVENT_NAMES = [
            tr("AnalogMatrix", "Shallow Act"),
            tr("AnalogMatrix", "Shallow Deact"),
            tr("AnalogMatrix", "Deep Act"),
            tr("AnalogMatrix", "Deep Deact"),
        ]

        actions_group = QGroupBox(
            tr("AnalogMatrix", "Actions — what each keycode does on each event")
        )
        actions_layout = QGridLayout()

        # Header row: KC0 … KC3
        for j in range(4):
            lbl = QLabel(tr("AnalogMatrix", "KC{}").format(j))
            lbl.setToolTip(tr("AnalogMatrix", "Keycode slot {}").format(j))
            actions_layout.addWidget(lbl, 0, j + 1)

        # self.dks_actions[event_idx][kc_slot] = QComboBox
        self.dks_actions = []
        for i, ev_name in enumerate(DKS_EVENT_NAMES):
            actions_layout.addWidget(QLabel(ev_name), i + 1, 0)
            event_combos = []
            for j in range(4):
                combo = QComboBox()
                for val, name in OKMC_ACTION_NAMES.items():
                    combo.addItem(name, val)
                combo.setToolTip(
                    tr(
                        "AnalogMatrix",
                        "Action for keycode slot {} on event '{}':\n"
                        "  None — do nothing\n"
                        "  Press — send key down\n"
                        "  Release — send key up\n"
                        "  Tap — press then release\n"
                        "  Re-press — release, press, release",
                    ).format(j, ev_name)
                )
                actions_layout.addWidget(combo, i + 1, j + 1)
                event_combos.append(combo)
            self.dks_actions.append(event_combos)

        actions_group.setLayout(actions_layout)
        dks_layout.addWidget(actions_group)

        # Apply / assign-to-key buttons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self.btn_dks_assign = QPushButton(
            tr("AnalogMatrix", "Assign DKS to Selected Key")
        )
        self.btn_dks_assign.setToolTip(
            tr(
                "AnalogMatrix",
                "Apply this DKS slot config to the firmware and assign it\n"
                "to all keys currently selected in the Actuation tab.",
            )
        )
        self.btn_dks_assign.clicked.connect(self._apply_dks_to_selected)
        btn_layout.addWidget(self.btn_dks_assign)

        dks_layout.addLayout(btn_layout)
        dks_layout.addStretch()

        self.tabs.addTab(dks_widget, tr("AnalogMatrix", "DKS"))

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

        self.curve_points = []
        point_labels = [
            ("Point 1 (Start)", 0, 0),
            ("Point 2", 10, 31),
            ("Point 3", 30, 95),
            ("Point 4 (End)", 40, 127),
        ]
        for i, (label_text, def_x, def_y) in enumerate(point_labels):
            curve_layout.addWidget(QLabel(tr("AnalogMatrix", label_text)), i, 0)
            x_spin = QSpinBox()
            x_spin.setRange(0, 40)
            x_spin.setValue(def_x)
            x_spin.setSuffix(" travel")
            x_spin.valueChanged.connect(self.on_curve_changed)
            curve_layout.addWidget(QLabel("X:"), i, 1)
            curve_layout.addWidget(x_spin, i, 2)

            y_spin = QSpinBox()
            y_spin.setRange(0, 127)
            y_spin.setValue(def_y)
            y_spin.setSuffix(" output")
            y_spin.valueChanged.connect(self.on_curve_changed)
            curve_layout.addWidget(QLabel("Y:"), i, 3)
            curve_layout.addWidget(y_spin, i, 4)

            self.curve_points.append((x_spin, y_spin))

        self.btn_apply_curve = QPushButton(tr("AnalogMatrix", "Apply Curve"))
        self.btn_apply_curve.clicked.connect(self.apply_curve)
        curve_layout.addWidget(self.btn_apply_curve, 4, 0, 1, 3)

        curve_group.setLayout(curve_layout)
        joystick_layout.addWidget(curve_group)

        joystick_layout.addStretch()
        self.tabs.addTab(joystick_widget, tr("AnalogMatrix", "Joystick"))

    def _on_perkey_key_selected(self, key):
        """Handle key selection in per-key tab."""
        selected = self.actuation_keyboard.get_selected_keys()
        self.perkey_selection_label.setText(
            tr("AnalogMatrix", "Selected: {} keys").format(len(selected))
        )

        # If single key selected, load its settings
        if len(selected) == 1:
            row, col = selected[0]
            settings = self.actuation_keyboard.get_key_setting(row, col)
            if settings:
                self._updating = True
                # Synthesize effective UI mode from base mode + adv_mode
                # Firmware stores mode (2-bit: 0=global, 1=regular, 2=rapid)
                # and adv_mode (0=clear, 1=DKS, 2=gamepad, 3=toggle) separately,
                # but the UI combo uses synthetic AKM_* values
                base_mode = settings.get("mode", AKM_REGULAR)
                adv_mode = settings.get("adv_mode", ADV_MODE_CLEAR)
                if adv_mode == ADV_MODE_OKMC:
                    effective_mode = AKM_DKS
                elif adv_mode == ADV_MODE_GAME_CONTROLLER:
                    effective_mode = AKM_GAMEPAD
                elif adv_mode == ADV_MODE_TOGGLE:
                    effective_mode = AKM_TOGGLE
                else:
                    effective_mode = base_mode
                idx = self.key_mode.findData(effective_mode)
                if idx >= 0:
                    self.key_mode.setCurrentIndex(idx)
                self.actuation_point.setValue(
                    settings.get("actuation_point", 20) / 10.0
                )
                self.rt_sensitivity.setValue(settings.get("sensitivity", 3) / 10.0)
                self.rt_release_sensitivity.setValue(
                    settings.get("release_sensitivity", 3) / 10.0
                )
                # Restore gamepad axis if stored
                js_axis = settings.get("js_axis", 0)
                axis_idx = self.gamepad_axis.findData(js_axis)
                if axis_idx >= 0:
                    self.gamepad_axis.setCurrentIndex(axis_idx)
                self._updating = False
                # Manually update gamepad row visibility since on_mode_changed was suppressed
                show_gamepad = self.key_mode.currentData() == AKM_GAMEPAD
                for widget in self._gamepad_row_widgets:
                    if widget is not None:
                        widget.setVisible(show_gamepad)

    def _on_perkey_key_deselected(self):
        """Handle key deselection in per-key tab."""
        self.perkey_selection_label.setText(tr("AnalogMatrix", "Selected: 0 keys"))

    def _perkey_select_all(self):
        """Select all keys."""
        self.actuation_keyboard.select_all_keys()
        selected = self.actuation_keyboard.get_selected_keys()
        self.perkey_selection_label.setText(
            tr("AnalogMatrix", "Selected: {} keys").format(len(selected))
        )

    def _perkey_deselect_all(self):
        """Deselect all keys."""
        self.actuation_keyboard.deselect_all_keys()
        self.perkey_selection_label.setText(tr("AnalogMatrix", "Selected: 0 keys"))

    def _apply_perkey_settings(self):
        """Apply settings to selected keys."""
        if not self.keyboard:
            return

        selected = self.actuation_keyboard.get_selected_keys()
        if not selected:
            _show_warning(
                self.tabs,
                tr("AnalogMatrix", "No Keys Selected"),
                tr("AnalogMatrix", "Please select at least one key to apply settings."),
            )
            return

        profile = self.profile_selector.currentData()
        mode = self.key_mode.currentData()

        # Special advance modes: route to set_keychron_analog_advance_mode_*
        if mode == AKM_GAMEPAD:
            js_axis = self.gamepad_axis.currentData()
            errors = []
            for row, col in selected:
                ok = self.keyboard.set_keychron_analog_advance_mode_gamepad(
                    profile, row, col, js_axis
                )
                if not ok:
                    errors.append((row, col))
            if errors:
                _show_warning(
                    self.tabs,
                    tr("AnalogMatrix", "Error"),
                    tr(
                        "AnalogMatrix", "Failed to set Gamepad mode on {} key(s)."
                    ).format(len(errors)),
                )
            else:
                _show_info(
                    self.tabs,
                    tr("AnalogMatrix", "Applied"),
                    tr("AnalogMatrix", "Gamepad axis {} assigned to {} key(s).").format(
                        js_axis, len(selected)
                    ),
                )
            return

        if mode == AKM_TOGGLE:
            errors = []
            for row, col in selected:
                ok = self.keyboard.set_keychron_analog_advance_mode_toggle(
                    profile, row, col
                )
                if not ok:
                    errors.append((row, col))
            if errors:
                _show_warning(
                    self.tabs,
                    tr("AnalogMatrix", "Error"),
                    tr(
                        "AnalogMatrix", "Failed to set Toggle mode on {} key(s)."
                    ).format(len(errors)),
                )
            else:
                _show_info(
                    self.tabs,
                    tr("AnalogMatrix", "Applied"),
                    tr("AnalogMatrix", "Toggle mode applied to {} key(s).").format(
                        len(selected)
                    ),
                )
            return

        if mode == AKM_DKS:
            _show_info(
                self.tabs,
                tr("AnalogMatrix", "Use DKS Tab"),
                tr(
                    "AnalogMatrix",
                    "To assign DKS, configure the slot in the DKS tab and click "
                    "'Assign DKS to Selected Key' there.",
                ),
            )
            return

        act_pt = int(round(self.actuation_point.value() * 10))
        sens = int(round(self.rt_sensitivity.value() * 10))
        rls_sens = int(round(self.rt_release_sensitivity.value() * 10))

        # Build per-row 24-bit column bitmasks for selected keys.
        # Firmware expects row_mask[MATRIX_ROWS], each a 24-bit LE value
        # (3 bytes per row via memcpy).
        rows = self.keyboard.rows
        cols = self.keyboard.cols
        row_mask = [0] * rows  # one int (24-bit col bitmask) per row

        for row, col in selected:
            if row < rows and col < cols and col < 24:
                row_mask[row] |= 1 << col

        # Apply to selected keys
        success = self.keyboard.set_keychron_analog_travel(
            profile, mode, act_pt, sens, rls_sens, entire=False, row_mask=row_mask
        )

        if success:
            # Update local visualization
            for row, col in selected:
                self.actuation_keyboard.set_key_setting(
                    row, col, mode, act_pt, sens, rls_sens
                )
            _show_info(
                self.tabs,
                tr("AnalogMatrix", "Applied"),
                tr("AnalogMatrix", "Settings applied to {} keys.").format(
                    len(selected)
                ),
            )
        else:
            _show_warning(
                self.tabs,
                tr("AnalogMatrix", "Error"),
                tr("AnalogMatrix", "Failed to apply settings."),
            )

    def _refresh_perkey_settings(self):
        """Refresh per-key settings from keyboard."""
        if not self.keyboard:
            return
        # Re-read and setup the keyboard widget with fresh settings
        self._setup_actuation_keyboard()

    def _setup_actuation_keyboard(self):
        """Setup the actuation keyboard widget with current layout."""
        if not self.keyboard:
            return

        # Get keyboard layout from the keyboard object
        if hasattr(self.keyboard, "keys"):
            encoders = getattr(self.keyboard, "encoders", [])
            self.actuation_keyboard.set_keys(self.keyboard.keys, encoders)

        rows = getattr(self.keyboard, "rows", 6)
        cols = getattr(self.keyboard, "cols", 20)

        # Try to read per-key settings from the keyboard
        profile = self.profile_selector.currentData()
        if profile is None:
            profile = 0

        key_settings = None
        if hasattr(self.keyboard, "get_keychron_analog_key_configs"):
            try:
                key_settings = self.keyboard.get_keychron_analog_key_configs(profile)
            except Exception:
                pass  # Fall back to defaults

        # Use defaults if reading failed
        if not key_settings:
            key_settings = {}
            for row in range(rows):
                for col in range(cols):
                    key_settings[(row, col)] = {
                        "mode": AKM_REGULAR,
                        "actuation_point": 20,  # 2.0mm
                        "sensitivity": 3,  # 0.3mm
                        "release_sensitivity": 3,  # 0.3mm
                    }

        self.actuation_keyboard.set_key_settings(key_settings)
        self.actuation_keyboard.update()

    def _rebuild_socd_tab(self):
        """Populate SOCD tab with current SOCD pairs from the keyboard."""
        # Clear existing widgets
        self.socd_widgets = []
        while self.socd_container_layout.count():
            item = self.socd_container_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not self.keyboard:
            return

        socd_count = getattr(self.keyboard, "keychron_analog_socd_count", 0)
        if socd_count == 0:
            no_socd_label = QLabel(
                tr("AnalogMatrix", "No SOCD slots available on this keyboard.")
            )
            self.socd_container_layout.addWidget(no_socd_label)
            self.socd_container_layout.addStretch()
            return

        # Add header
        header = QLabel(
            tr(
                "AnalogMatrix",
                "Configure SOCD pairs below. Each pair defines two opposing keys.",
            )
        )
        self.socd_container_layout.addWidget(header)

        # Create widgets for each SOCD slot
        self.socd_widgets = []
        for i in range(socd_count):
            group = QGroupBox(tr("AnalogMatrix", "SOCD Pair {}").format(i + 1))
            layout = QGridLayout()

            # Key 1
            layout.addWidget(QLabel(tr("AnalogMatrix", "Key 1 (Row, Col):")), 0, 0)
            row1_spin = QSpinBox()
            row1_spin.setRange(0, 7)  # 3-bit field in socd_config_t
            layout.addWidget(row1_spin, 0, 1)
            col1_spin = QSpinBox()
            col1_spin.setRange(0, 31)  # 5-bit field in socd_config_t
            layout.addWidget(col1_spin, 0, 2)

            # Key 2
            layout.addWidget(QLabel(tr("AnalogMatrix", "Key 2 (Row, Col):")), 0, 3)
            row2_spin = QSpinBox()
            row2_spin.setRange(0, 7)  # 3-bit field in socd_config_t
            layout.addWidget(row2_spin, 0, 4)
            col2_spin = QSpinBox()
            col2_spin.setRange(0, 31)  # 5-bit field in socd_config_t
            layout.addWidget(col2_spin, 0, 5)

            # SOCD Type
            layout.addWidget(QLabel(tr("AnalogMatrix", "Resolution:")), 1, 0)
            type_combo = QComboBox()
            for type_id, name in SOCD_TYPE_NAMES.items():
                type_combo.addItem(name, type_id)
            layout.addWidget(type_combo, 1, 1, 1, 2)

            # Apply button for this pair
            apply_btn = QPushButton(tr("AnalogMatrix", "Apply"))
            apply_btn.clicked.connect(lambda checked, idx=i: self._apply_socd_pair(idx))
            layout.addWidget(apply_btn, 1, 5)

            group.setLayout(layout)
            self.socd_container_layout.addWidget(group)

            self.socd_widgets.append(
                {
                    "row1": row1_spin,
                    "col1": col1_spin,
                    "row2": row2_spin,
                    "col2": col2_spin,
                    "type": type_combo,
                }
            )

        self.socd_container_layout.addStretch()

    def _apply_socd_pair(self, index):
        """Apply a single SOCD pair configuration."""
        if not self.keyboard or index >= len(self.socd_widgets):
            return

        widgets = self.socd_widgets[index]
        profile = self.profile_selector.currentData()
        if profile is None:
            profile = 0

        row1 = widgets["row1"].value()
        col1 = widgets["col1"].value()
        row2 = widgets["row2"].value()
        col2 = widgets["col2"].value()
        socd_type = widgets["type"].currentData()

        success = self.keyboard.set_keychron_analog_socd(
            profile, row1, col1, row2, col2, index, socd_type
        )

        if success:
            _show_info(
                self.tabs,
                tr("AnalogMatrix", "Applied"),
                tr("AnalogMatrix", "SOCD pair {} configured.").format(index + 1),
            )
        else:
            _show_warning(
                self.tabs,
                tr("AnalogMatrix", "Error"),
                tr("AnalogMatrix", "Failed to apply SOCD configuration."),
            )

    def _reload_global_defaults(self):
        """Read the global config slot from firmware and populate the Global Defaults widgets."""
        if not self.keyboard:
            return
        profile = self.profile_selector.currentData()
        if profile is None:
            return

        # Global config is always at offset 0, 4 bytes, in the profile raw data
        raw = self.keyboard.get_keychron_analog_profile_raw(profile, 0, 4)
        if not raw or len(raw) < 4:
            return

        cfg = self.keyboard._parse_analog_key_config(raw)

        # Mode: global slot only stores Regular (1) or Rapid (2); clamp to those
        mode = cfg.get("mode", AKM_REGULAR)
        if mode not in (AKM_REGULAR, AKM_RAPID):
            mode = AKM_REGULAR
        idx = self.global_mode.findData(mode)
        if idx >= 0:
            self.global_mode.setCurrentIndex(idx)

        # Actuation point (stored as 0.1 mm units; 0 means "use default", treat as 2.0 mm)
        act_pt_raw = cfg.get("actuation_point", 20)
        if act_pt_raw == 0:
            act_pt_raw = 20  # fallback to 2.0 mm
        self.global_actuation_point.setValue(act_pt_raw / 10.0)

        # RT sensitivity (stored as 0.1 mm units; 0 → fallback 3)
        sens_raw = cfg.get("sensitivity", 3)
        if sens_raw == 0:
            sens_raw = 3  # fallback to 0.3 mm
        self.global_rt_sensitivity.setValue(sens_raw / 10.0)

        # RT release sensitivity (stored as 0.1 mm units; 0 → fallback 3)
        rls_raw = cfg.get("release_sensitivity", 3)
        if rls_raw == 0:
            rls_raw = 3  # fallback to 0.3 mm
        self.global_rt_release.setValue(rls_raw / 10.0)

    def _apply_global_defaults(self):
        """Write the Global Defaults group values to the profile's global config slot."""
        if not self.keyboard:
            return
        profile = self.profile_selector.currentData()
        if profile is None:
            return
        mode = self.global_mode.currentData()
        act_pt = int(round(self.global_actuation_point.value() * 10))
        sens = int(round(self.global_rt_sensitivity.value() * 10))
        rls_sens = int(round(self.global_rt_release.value() * 10))
        logging.info(
            "AnalogMatrix: _apply_global_defaults: profile=%s mode=%s act_pt=%s sens=%s rls_sens=%s",
            profile,
            mode,
            act_pt,
            sens,
            rls_sens,
        )
        # entire=True writes the global slot (not per-key); firmware rejects AKM_GLOBAL here
        success = self.keyboard.set_keychron_analog_travel(
            profile, mode, act_pt, sens, rls_sens, entire=True
        )
        logging.info("AnalogMatrix: _apply_global_defaults: success=%s", success)
        if success:
            _show_info(
                self.tabs,
                tr("AnalogMatrix", "Applied"),
                tr("AnalogMatrix", "Global defaults updated for Profile {}.").format(
                    profile + 1
                ),
            )
        else:
            _show_warning(
                self.tabs,
                tr("AnalogMatrix", "Error"),
                tr("AnalogMatrix", "Failed to set global defaults."),
            )

    def valid(self):
        """Check if this tab should be shown."""
        if not isinstance(self.device, VialKeyboard):
            return False
        kb = self.device.keyboard
        if not hasattr(kb, "has_keychron_analog"):
            return False
        return kb.has_keychron_analog()

    def _apply_profile_name(self):
        """Send the profile name to the keyboard."""
        if not self.keyboard:
            return
        profile = self.profile_selector.currentData()
        if profile is None:
            return
        name = self.profile_name_edit.text().strip()
        if self.keyboard.set_keychron_analog_profile_name(profile, name):
            _show_info(
                self.tabs,
                tr("AnalogMatrix", "Name Set"),
                tr("AnalogMatrix", "Profile {} name updated.").format(profile + 1),
            )
        else:
            _show_warning(
                self.tabs,
                tr("AnalogMatrix", "Error"),
                tr("AnalogMatrix", "Failed to set profile name."),
            )

    def _on_dks_slot_changed(self):
        """Load DKS slot settings from keyboard when slot selection changes."""
        if self._updating or not self.keyboard:
            return
        profile = self.profile_selector.currentData()
        if profile is None:
            return
        slot = self.dks_slot_selector.currentData()
        if slot is None:
            return
        okmc_configs = None
        try:
            okmc_configs = self.keyboard.get_keychron_analog_okmc_configs(profile)
        except Exception:
            pass
        if not okmc_configs or slot >= len(okmc_configs):
            return
        cfg = okmc_configs[slot]
        self._updating = True
        self.dks_shallow_act.setValue(cfg["shallow_act"] / 10.0)
        self.dks_shallow_deact.setValue(cfg["shallow_deact"] / 10.0)
        self.dks_deep_act.setValue(cfg["deep_act"] / 10.0)
        self.dks_deep_deact.setValue(cfg["deep_deact"] / 10.0)
        for i, kc_edit in enumerate(self.dks_keycodes):
            kc = cfg["keycodes"][i] if i < len(cfg["keycodes"]) else 0
            kc_edit.setText(f"0x{kc:04X}")
        # dks_actions[event_idx][kc_slot]: event order = shallow_act, shallow_deact, deep_act, deep_deact
        event_keys = ["shallow_act", "shallow_deact", "deep_act", "deep_deact"]
        for ev_idx, ev_key in enumerate(event_keys):
            for kc_slot, combo in enumerate(self.dks_actions[ev_idx]):
                act = cfg["actions"][kc_slot] if kc_slot < len(cfg["actions"]) else {}
                val = act.get(ev_key, OKMC_ACTION_NONE)
                idx = combo.findData(val)
                combo.setCurrentIndex(idx if idx >= 0 else 0)
        self._updating = False

    def _apply_dks_to_selected(self):
        """Apply DKS config from the DKS tab to selected keys in the Actuation tab."""
        if not self.keyboard:
            return
        profile = self.profile_selector.currentData()
        if profile is None:
            profile = 0
        slot = self.dks_slot_selector.currentData()
        if slot is None:
            slot = 0

        # Parse travel thresholds (convert mm back to 0.1mm int)
        shallow_act = int(round(self.dks_shallow_act.value() * 10))
        shallow_deact = int(round(self.dks_shallow_deact.value() * 10))
        deep_act = int(round(self.dks_deep_act.value() * 10))
        deep_deact = int(round(self.dks_deep_deact.value() * 10))

        # Parse keycodes
        keycodes = []
        for kc_edit in self.dks_keycodes:
            try:
                keycodes.append(int(kc_edit.text(), 16))
            except ValueError:
                keycodes.append(0)

        # Parse actions: firmware expects actions[kc_slot] with event keys
        # dks_actions[event_idx][kc_slot] → build actions[kc_slot][event_key]
        event_keys = ["shallow_act", "shallow_deact", "deep_act", "deep_deact"]
        actions = []
        for kc_slot in range(4):
            act = {}
            for ev_idx, ev_key in enumerate(event_keys):
                act[ev_key] = self.dks_actions[ev_idx][kc_slot].currentData()
            actions.append(act)

        # Get selected keys from Actuation tab widget
        selected = self.actuation_keyboard.get_selected_keys()
        if not selected:
            _show_warning(
                self.tabs,
                tr("AnalogMatrix", "No Keys Selected"),
                tr("AnalogMatrix", "Select keys in the Actuation tab first."),
            )
            return

        errors = []
        for row, col in selected:
            ok = self.keyboard.set_keychron_analog_advance_mode_dks(
                profile,
                row,
                col,
                slot,
                shallow_act,
                shallow_deact,
                deep_act,
                deep_deact,
                keycodes,
                actions,
            )
            if not ok:
                errors.append((row, col))

        if errors:
            _show_warning(
                self.tabs,
                tr("AnalogMatrix", "Error"),
                tr("AnalogMatrix", "Failed to apply DKS to {} key(s).").format(
                    len(errors)
                ),
            )
        else:
            _show_info(
                self.tabs,
                tr("AnalogMatrix", "Applied"),
                tr("AnalogMatrix", "DKS slot {} applied to {} key(s).").format(
                    slot, len(selected)
                ),
            )

    def _read_calibrated_value(self):
        """Read and display the calibrated zero/full travel values for a key."""
        if not self.keyboard:
            return
        row = self.calval_row.value()
        col = self.calval_col.value()
        result = None
        try:
            result = self.keyboard.get_keychron_analog_calibrated_value(row, col)
        except Exception:
            pass
        if result:
            self.calval_result.setText(
                tr("AnalogMatrix", "Zero: {} | Full: {} | Scale: {:.4f}").format(
                    result["zero_travel"],
                    result["full_travel"],
                    result["scale_factor"],
                )
            )
        else:
            self.calval_result.setText(
                tr(
                    "AnalogMatrix", "Failed to read calibrated values for ({}, {})."
                ).format(row, col)
            )

    def rebuild(self, device):
        super().rebuild(device)
        if not self.valid():
            return

        self.keyboard = device.keyboard
        self._updating = True

        # Stop any running realtime monitoring timer from previous device
        if self.realtime_timer:
            self.realtime_timer.stop()
            self.realtime_timer = None
            self.btn_start_realtime.setText(tr("AnalogMatrix", "Start Monitoring"))
        self._stop_calibration_polling()
        self.calib_status.setText(tr("AnalogMatrix", "Status: Not calibrating"))
        self.btn_calib_zero.setEnabled(True)
        self.btn_calib_full.setEnabled(False)

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

        # Update curve points
        for i, (x_spin, y_spin) in enumerate(self.curve_points):
            if i < len(self.keyboard.keychron_analog_curve):
                x, y = self.keyboard.keychron_analog_curve[i]
                x_spin.setValue(x)
                y_spin.setValue(y)

        # Setup per-key actuation keyboard
        self._setup_actuation_keyboard()

        # Update calibration/realtime spinbox ranges to match actual matrix
        rows = getattr(self.keyboard, "rows", 6)
        cols = getattr(self.keyboard, "cols", 20)
        self.realtime_row.setRange(0, max(0, rows - 1))
        self.realtime_col.setRange(0, max(0, cols - 1))
        self.calval_row.setRange(0, max(0, rows - 1))
        self.calval_col.setRange(0, max(0, cols - 1))

        # Populate Global Defaults group from firmware global config slot
        self._reload_global_defaults()

        # Populate SOCD tab with firmware data
        self._rebuild_socd_tab()
        self._populate_socd_from_firmware()

        # Populate DKS slot selector
        self.dks_slot_selector.clear()
        for i in range(self.keyboard.keychron_analog_okmc_count):
            self.dks_slot_selector.addItem(
                tr("AnalogMatrix", "Slot {}").format(i + 1), i
            )

        # Load profile name
        profile = self.keyboard.keychron_analog_current_profile
        try:
            name = self.keyboard.get_keychron_analog_profile_name(profile)
            self.profile_name_edit.setText(name)
        except Exception:
            self.profile_name_edit.setText("")

        self._updating = False

        # Load initial DKS slot 0 data (must be after _updating=False)
        if self.dks_slot_selector.count() > 0:
            self._on_dks_slot_changed()

    def on_profile_changed(self):
        """Handle profile selection change."""
        if self._updating or not self.keyboard:
            return
        profile = self.profile_selector.currentData()
        if profile is None:
            return
        self.keyboard.select_keychron_analog_profile(profile)
        # Reload profile name
        try:
            name = self.keyboard.get_keychron_analog_profile_name(profile)
            self.profile_name_edit.setText(name)
        except Exception:
            self.profile_name_edit.setText("")
        # Reload global defaults for this profile
        self._reload_global_defaults()
        # Reload per-key actuation data for this profile
        self._setup_actuation_keyboard()
        # Reload SOCD from firmware for this profile
        self._populate_socd_from_firmware()
        # Reload DKS slot 0 if available
        if self.dks_slot_selector.count() > 0:
            self.dks_slot_selector.blockSignals(True)
            self.dks_slot_selector.setCurrentIndex(0)
            self.dks_slot_selector.blockSignals(False)
            self._on_dks_slot_changed()

    def _populate_socd_from_firmware(self):
        """Pre-populate SOCD widgets with values read from the keyboard."""
        if not self.keyboard or not hasattr(self, "socd_widgets"):
            return
        profile = self.profile_selector.currentData()
        if profile is None:
            profile = 0
        socd_pairs = None
        try:
            socd_pairs = self.keyboard.get_keychron_analog_socd_pairs(profile)
        except Exception:
            return
        if not socd_pairs:
            return
        for i, widgets in enumerate(self.socd_widgets):
            if i < len(socd_pairs):
                pair = socd_pairs[i]
                self._updating = True
                widgets["row1"].setValue(pair.get("row1", 0))
                widgets["col1"].setValue(pair.get("col1", 0))
                widgets["row2"].setValue(pair.get("row2", 0))
                widgets["col2"].setValue(pair.get("col2", 0))
                idx = widgets["type"].findData(pair.get("type", SOCD_PRI_NONE))
                if idx >= 0:
                    widgets["type"].setCurrentIndex(idx)
                self._updating = False

    def save_current_profile(self):
        """Save current profile to EEPROM."""
        if not self.keyboard:
            return
        profile = self.profile_selector.currentData()
        if profile is not None:
            if self.keyboard.save_keychron_analog_profile(profile):
                _show_info(
                    self.tabs,
                    tr("AnalogMatrix", "Saved"),
                    tr("AnalogMatrix", "Profile {} saved.").format(profile + 1),
                )
            else:
                _show_warning(
                    self.tabs,
                    tr("AnalogMatrix", "Error"),
                    tr("AnalogMatrix", "Failed to save profile."),
                )

    def reset_current_profile(self):
        """Reset current profile to defaults."""
        if not self.keyboard:
            return
        profile = self.profile_selector.currentData()
        if profile is not None:

            def _do_reset():
                self.keyboard.reset_keychron_analog_profile(profile)
                # Refresh UI with the reset profile's data
                self._reload_global_defaults()
                self._setup_actuation_keyboard()
                self._populate_socd_from_firmware()
                if self.dks_slot_selector.count() > 0:
                    self._on_dks_slot_changed()

            self._reset_question_box = _ask_question(
                self.tabs,
                tr("AnalogMatrix", "Reset Profile"),
                tr("AnalogMatrix", "Reset Profile {} to defaults?").format(profile + 1),
                _do_reset,
            )

    def on_mode_changed(self):
        """Handle key mode change — show/hide Gamepad axis row."""
        if self._updating:
            return
        mode = self.key_mode.currentData()
        # Show gamepad axis spinbox only when Gamepad mode is selected
        show_gamepad = mode == AKM_GAMEPAD
        for widget in self._gamepad_row_widgets:
            if widget is not None:
                widget.setVisible(show_gamepad)

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

        # Firmware rejects modes > AKM_RAPID for global config
        # (profile.c: if mode > AKM_RAPID ... return false)
        if mode > AKM_RAPID:
            _show_warning(
                self.tabs,
                tr("AnalogMatrix", "Invalid Mode"),
                tr(
                    "AnalogMatrix",
                    "Only Regular and Rapid Trigger modes can be applied globally.\n"
                    "DKS, Gamepad, Toggle must be set per-key.",
                ),
            )
            return

        act_pt = int(round(self.actuation_point.value() * 10))  # Convert to 0.1mm units
        sens = int(round(self.rt_sensitivity.value() * 10))
        rls_sens = int(round(self.rt_release_sensitivity.value() * 10))

        if self.keyboard.set_keychron_analog_travel(
            profile, mode, act_pt, sens, rls_sens, entire=True
        ):
            _show_info(
                self.tabs,
                tr("AnalogMatrix", "Applied"),
                tr("AnalogMatrix", "Settings applied to all keys."),
            )
        else:
            _show_warning(
                self.tabs,
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
            if calib_type == CALIB_ZERO_TRAVEL_MANUAL:
                self.btn_calib_zero.setEnabled(False)
                self.btn_calib_full.setEnabled(False)
                self._start_calibration_polling()
            elif calib_type in (CALIB_SAVE_AND_EXIT, CALIB_CLEAR):
                self._stop_calibration_polling()
                self.btn_calib_zero.setEnabled(True)
                self.btn_calib_full.setEnabled(False)
        else:
            _show_warning(
                self.tabs,
                tr("AnalogMatrix", "Error"),
                tr("AnalogMatrix", "Failed to start calibration."),
            )

    def _start_calibration_polling(self):
        """Poll firmware calibration state while calibration is active."""
        if self.calibration_timer is None:
            self.calibration_timer = QTimer()
            self.calibration_timer.timeout.connect(self._poll_calibration_state)
        if not self.calibration_timer.isActive():
            self.calibration_timer.start(100)

    def _stop_calibration_polling(self):
        """Stop polling firmware calibration state."""
        if self.calibration_timer and self.calibration_timer.isActive():
            self.calibration_timer.stop()

    def _poll_calibration_state(self):
        """Update calibration status from the firmware state machine."""
        if not self.keyboard:
            self._stop_calibration_polling()
            return

        state = self.keyboard.get_keychron_calibration_state()
        if not state:
            return

        cali_state = state.get("state", CALIB_OFF)
        if cali_state == CALIB_ZERO_TRAVEL_MANUAL:
            self.calib_status.setText(
                tr("AnalogMatrix", "Status: Calibrating zero point...")
            )
            self.btn_calib_full.setEnabled(False)
        elif cali_state == CALIB_FULL_TRAVEL_MANUAL:
            self.calib_status.setText(
                tr("AnalogMatrix", "Status: Calibrating full travel...")
            )
            self.btn_calib_full.setEnabled(True)
        elif cali_state == CALIB_OFF:
            calibrated = state.get("calibrated", 0)
            if calibrated & 0x02:
                self.calib_status.setText(
                    tr("AnalogMatrix", "Status: Calibration complete")
                )
            else:
                self.calib_status.setText(
                    tr("AnalogMatrix", "Status: Not calibrating")
                )
            self.btn_calib_zero.setEnabled(True)
            self.btn_calib_full.setEnabled(False)
            self._stop_calibration_polling()

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
        """Handle curve point change."""
        if self._updating:
            return

    def apply_curve(self):
        """Apply joystick response curve."""
        if not self.keyboard:
            return

        curve = [
            (x_spin.value(), y_spin.value()) for x_spin, y_spin in self.curve_points
        ]
        if self.keyboard.set_keychron_analog_curve(curve):
            _show_info(
                self.tabs,
                tr("AnalogMatrix", "Applied"),
                tr("AnalogMatrix", "Curve settings applied."),
            )
        else:
            _show_warning(
                self.tabs,
                tr("AnalogMatrix", "Error"),
                tr("AnalogMatrix", "Failed to apply curve settings."),
            )

    def deactivate(self):
        """Called when tab is deactivated."""
        if self.realtime_timer:
            self.realtime_timer.stop()
            self.realtime_timer = None
            self.btn_start_realtime.setText(tr("AnalogMatrix", "Start Monitoring"))
        self._stop_calibration_polling()
