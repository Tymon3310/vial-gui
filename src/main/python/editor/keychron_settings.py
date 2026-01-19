# SPDX-License-Identifier: GPL-2.0-or-later
"""
Keychron Settings Editor - General keyboard settings tab.

This editor handles:
- Dynamic debounce settings
- NKRO toggle
- USB report rate
- Wireless power management settings
"""

from PyQt5 import QtCore
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
    QMessageBox,
)

from editor.basic_editor import BasicEditor
from protocol.keychron import (
    DEBOUNCE_TYPE_NAMES,
    REPORT_RATE_NAMES,
    REPORT_RATE_8000HZ,
    REPORT_RATE_125HZ,
)
from util import tr
from vial_device import VialKeyboard


class KeychronSettings(BasicEditor):
    """Editor for Keychron keyboard general settings."""

    def __init__(self):
        super().__init__()
        self.keyboard = None

        # Main container
        self.container = QWidget()
        self.container.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Maximum)
        container_layout = QVBoxLayout()
        self.container.setLayout(container_layout)

        # Debounce group
        self.debounce_group = QGroupBox(tr("KeychronSettings", "Debounce"))
        debounce_layout = QGridLayout()

        debounce_layout.addWidget(QLabel(tr("KeychronSettings", "Algorithm:")), 0, 0)
        self.debounce_type = QComboBox()
        for type_id, name in sorted(DEBOUNCE_TYPE_NAMES.items()):
            self.debounce_type.addItem(name, type_id)
        self.debounce_type.currentIndexChanged.connect(self.on_debounce_changed)
        debounce_layout.addWidget(self.debounce_type, 0, 1)

        debounce_layout.addWidget(QLabel(tr("KeychronSettings", "Time (ms):")), 1, 0)
        self.debounce_time = QSpinBox()
        self.debounce_time.setMinimum(0)
        self.debounce_time.setMaximum(50)
        self.debounce_time.valueChanged.connect(self.on_debounce_changed)
        debounce_layout.addWidget(self.debounce_time, 1, 1)

        self.debounce_group.setLayout(debounce_layout)
        container_layout.addWidget(self.debounce_group)

        # NKRO group
        self.nkro_group = QGroupBox(tr("KeychronSettings", "N-Key Rollover"))
        nkro_layout = QHBoxLayout()

        self.nkro_enabled = QCheckBox(tr("KeychronSettings", "Enable NKRO"))
        self.nkro_enabled.stateChanged.connect(self.on_nkro_changed)
        nkro_layout.addWidget(self.nkro_enabled)

        self.nkro_status_label = QLabel()
        nkro_layout.addWidget(self.nkro_status_label)
        nkro_layout.addStretch()

        self.nkro_group.setLayout(nkro_layout)
        container_layout.addWidget(self.nkro_group)

        # Report Rate group
        self.report_rate_group = QGroupBox(tr("KeychronSettings", "USB Report Rate"))
        report_rate_layout = QHBoxLayout()

        report_rate_layout.addWidget(QLabel(tr("KeychronSettings", "Polling Rate:")))
        self.report_rate = QComboBox()
        self.report_rate.currentIndexChanged.connect(self.on_report_rate_changed)
        report_rate_layout.addWidget(self.report_rate)
        report_rate_layout.addStretch()

        self.report_rate_group.setLayout(report_rate_layout)
        container_layout.addWidget(self.report_rate_group)

        # Wireless group
        self.wireless_group = QGroupBox(
            tr("KeychronSettings", "Wireless Power Management")
        )
        wireless_layout = QGridLayout()

        wireless_layout.addWidget(
            QLabel(tr("KeychronSettings", "Backlight off after (seconds):")), 0, 0
        )
        self.wireless_backlit_time = QSpinBox()
        self.wireless_backlit_time.setMinimum(5)
        self.wireless_backlit_time.setMaximum(3600)
        self.wireless_backlit_time.valueChanged.connect(self.on_wireless_changed)
        wireless_layout.addWidget(self.wireless_backlit_time, 0, 1)

        wireless_layout.addWidget(
            QLabel(tr("KeychronSettings", "Sleep after idle (seconds):")), 1, 0
        )
        self.wireless_idle_time = QSpinBox()
        self.wireless_idle_time.setMinimum(60)
        self.wireless_idle_time.setMaximum(7200)
        self.wireless_idle_time.valueChanged.connect(self.on_wireless_changed)
        wireless_layout.addWidget(self.wireless_idle_time, 1, 1)

        self.wireless_group.setLayout(wireless_layout)
        container_layout.addWidget(self.wireless_group)

        # Firmware info label
        self.firmware_label = QLabel()
        container_layout.addWidget(self.firmware_label)

        container_layout.addStretch()

        # Center the container
        main_layout = QVBoxLayout()
        main_layout.addWidget(self.container)
        main_layout.setAlignment(self.container, QtCore.Qt.AlignHCenter)

        w = QWidget()
        w.setLayout(main_layout)
        self.addWidget(w)

        self._updating = False

    def valid(self):
        """Check if this tab should be shown."""
        if not isinstance(self.device, VialKeyboard):
            return False
        kb = self.device.keyboard
        if not hasattr(kb, "has_keychron_features"):
            return False
        # Show if any of our managed features are available
        return (
            kb.has_keychron_debounce()
            or kb.has_keychron_nkro()
            or kb.has_keychron_report_rate()
            or kb.has_keychron_wireless()
        )

    def rebuild(self, device):
        super().rebuild(device)
        if not self.valid():
            return

        self.keyboard = device.keyboard
        self._updating = True

        # Show/hide groups based on feature support
        self.debounce_group.setVisible(self.keyboard.has_keychron_debounce())
        self.nkro_group.setVisible(self.keyboard.has_keychron_nkro())
        self.report_rate_group.setVisible(self.keyboard.has_keychron_report_rate())
        self.wireless_group.setVisible(self.keyboard.has_keychron_wireless())

        # Update debounce UI
        if self.keyboard.has_keychron_debounce():
            idx = self.debounce_type.findData(self.keyboard.keychron_debounce_type)
            if idx >= 0:
                self.debounce_type.setCurrentIndex(idx)
            self.debounce_time.setValue(self.keyboard.keychron_debounce_time)

        # Update NKRO UI
        if self.keyboard.has_keychron_nkro():
            self.nkro_enabled.setChecked(self.keyboard.keychron_nkro_enabled)
            if self.keyboard.keychron_nkro_adaptive:
                # Adaptive NKRO: setting is controlled automatically by firmware
                self.nkro_enabled.setEnabled(False)
                self.nkro_status_label.setText(
                    tr("KeychronSettings", "(Adaptive - controlled by firmware)")
                )
            elif self.keyboard.keychron_nkro_supported:
                self.nkro_enabled.setEnabled(True)
                self.nkro_status_label.setText(
                    tr("KeychronSettings", "(NKRO supported)")
                )
            else:
                self.nkro_enabled.setEnabled(False)
                self.nkro_status_label.setText(
                    tr("KeychronSettings", "(Not supported)")
                )

        # Update report rate UI
        if self.keyboard.has_keychron_report_rate():
            self.report_rate.clear()
            for rate_id in range(REPORT_RATE_8000HZ, REPORT_RATE_125HZ + 1):
                # Check if this rate is supported
                if self.keyboard.keychron_report_rate_mask & (1 << rate_id):
                    self.report_rate.addItem(
                        REPORT_RATE_NAMES.get(rate_id, f"{rate_id}"), rate_id
                    )
            idx = self.report_rate.findData(self.keyboard.keychron_report_rate)
            if idx >= 0:
                self.report_rate.setCurrentIndex(idx)

        # Update wireless UI
        if self.keyboard.has_keychron_wireless():
            self.wireless_backlit_time.setValue(
                self.keyboard.keychron_wireless_backlit_time
            )
            self.wireless_idle_time.setValue(self.keyboard.keychron_wireless_idle_time)

        # Update firmware label
        if self.keyboard.keychron_firmware_version:
            self.firmware_label.setText(
                tr("KeychronSettings", "Firmware: {}").format(
                    self.keyboard.keychron_firmware_version
                )
            )
            self.firmware_label.setVisible(True)
        else:
            self.firmware_label.setVisible(False)

        self._updating = False

    def on_debounce_changed(self):
        if self._updating or not self.keyboard:
            return
        debounce_type = self.debounce_type.currentData()
        debounce_time = self.debounce_time.value()
        self.keyboard.set_keychron_debounce(debounce_type, debounce_time)

    def on_nkro_changed(self):
        if self._updating or not self.keyboard:
            return
        self.keyboard.set_keychron_nkro(self.nkro_enabled.isChecked())

    def on_report_rate_changed(self):
        if self._updating or not self.keyboard:
            return
        rate = self.report_rate.currentData()
        if rate is not None:
            self.keyboard.set_keychron_report_rate(rate)

    def on_wireless_changed(self):
        if self._updating or not self.keyboard:
            return
        self.keyboard.set_keychron_wireless_lpm(
            self.wireless_backlit_time.value(), self.wireless_idle_time.value()
        )
