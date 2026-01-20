# SPDX-License-Identifier: GPL-2.0-or-later
"""
Snap Click Editor - SOCD (Simultaneous Opposite Cardinal Directions) for regular keyboards.

This editor allows configuring key pairs that should use SOCD handling,
similar to what fighting game controllers or Hall Effect keyboards offer,
but for standard mechanical switches.
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
    QPushButton,
    QScrollArea,
    QFrame,
    QMessageBox,
)

from editor.basic_editor import BasicEditor
from protocol.keychron import SNAP_CLICK_TYPE_NAMES, SNAP_CLICK_TYPE_NONE
from keycodes.keycodes import Keycode
from widgets.key_widget import KeyWidget
from tabbed_keycodes import keycode_filter_masked
from util import tr
from vial_device import VialKeyboard


class SnapClickEntry(QFrame):
    """Widget for a single Snap Click entry (key pair + type)."""

    def __init__(self, index, parent_editor):
        super().__init__()
        self.index = index
        self.parent_editor = parent_editor
        self.setFrameStyle(QFrame.StyledPanel | QFrame.Raised)

        layout = QHBoxLayout()
        layout.setContentsMargins(5, 5, 5, 5)

        # Index label
        self.index_label = QLabel(f"#{index + 1}")
        self.index_label.setFixedWidth(30)
        layout.addWidget(self.index_label)

        # Key 1 widget - uses KeyWidget for proper keycode selection
        layout.addWidget(QLabel(tr("SnapClick", "Key 1:")))
        self.key1_widget = KeyWidget(keycode_filter=keycode_filter_masked)
        self.key1_widget.changed.connect(lambda: self.on_key_changed(1))
        layout.addWidget(self.key1_widget)

        # Key 2 widget
        layout.addWidget(QLabel(tr("SnapClick", "Key 2:")))
        self.key2_widget = KeyWidget(keycode_filter=keycode_filter_masked)
        self.key2_widget.changed.connect(lambda: self.on_key_changed(2))
        layout.addWidget(self.key2_widget)

        # SOCD Type dropdown
        layout.addWidget(QLabel(tr("SnapClick", "Mode:")))
        self.type_combo = QComboBox()
        for type_id, name in SNAP_CLICK_TYPE_NAMES.items():
            self.type_combo.addItem(name, type_id)
        self.type_combo.currentIndexChanged.connect(self.on_type_changed)
        layout.addWidget(self.type_combo)

        layout.addStretch()

        self.setLayout(layout)
        self._updating = False

    def set_entry(self, entry):
        """Update UI from entry data."""
        self._updating = True
        # Convert raw keycodes to QMK keycode strings
        key1 = entry.get("key1", 0)
        key2 = entry.get("key2", 0)
        self.key1_widget.set_keycode(self._keycode_to_qmk(key1))
        self.key2_widget.set_keycode(self._keycode_to_qmk(key2))
        idx = self.type_combo.findData(entry.get("type", SNAP_CLICK_TYPE_NONE))
        if idx >= 0:
            self.type_combo.setCurrentIndex(idx)
        self._updating = False

    def _keycode_to_qmk(self, keycode):
        """Convert raw keycode to QMK string."""
        if keycode == 0:
            return "KC_NO"
        # Use Keycode.serialize to convert integer to QMK string
        try:
            qmk_str = Keycode.serialize(keycode)
            if qmk_str:
                return qmk_str
        except Exception:
            pass
        # Return as hex if we can't find a name
        return f"0x{keycode:02X}"

    def _qmk_to_keycode(self, qmk_id):
        """Convert QMK string to raw keycode."""
        if qmk_id == "KC_NO" or not qmk_id:
            return 0
        try:
            return Keycode.deserialize(qmk_id)
        except Exception:
            pass
        # Try to parse hex
        if qmk_id.startswith("0x"):
            try:
                return int(qmk_id, 16)
            except ValueError:
                pass
        return 0

    def on_key_changed(self, key_num):
        """Handle key widget change."""
        if self._updating:
            return
        self.parent_editor.update_entry_key(self.index, key_num, self._get_key(key_num))

    def _get_key(self, key_num):
        """Get raw keycode for key 1 or 2."""
        if key_num == 1:
            return self._qmk_to_keycode(self.key1_widget.keycode)
        else:
            return self._qmk_to_keycode(self.key2_widget.keycode)

    def on_type_changed(self):
        """Handle SOCD type change."""
        if self._updating:
            return
        self.parent_editor.update_entry_type(self.index, self.type_combo.currentData())


class SnapClickEditor(BasicEditor):
    """Editor for Snap Click (SOCD) settings."""

    def __init__(self):
        super().__init__()
        self.keyboard = None
        self.entries = []
        self.entry_widgets = []
        self.pending_key_selection = None  # (entry_index, key_num)

        # Main container
        main_widget = QWidget()
        main_layout = QVBoxLayout()
        main_widget.setLayout(main_layout)

        # Description
        desc = QLabel(
            tr(
                "SnapClick",
                "Snap Click allows you to configure SOCD (Simultaneous Opposite Cardinal Directions) "
                "handling for key pairs. When both keys in a pair are pressed simultaneously, "
                "the selected resolution mode determines which key takes priority.",
            )
        )
        desc.setWordWrap(True)
        main_layout.addWidget(desc)

        # Scroll area for entries
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)

        self.entries_container = QWidget()
        self.entries_layout = QVBoxLayout()
        self.entries_layout.setSpacing(5)
        self.entries_container.setLayout(self.entries_layout)
        scroll.setWidget(self.entries_container)

        main_layout.addWidget(scroll, 1)

        # Buttons
        buttons_layout = QHBoxLayout()
        buttons_layout.addStretch()

        self.btn_save = QPushButton(tr("SnapClick", "Save to Keyboard"))
        self.btn_save.clicked.connect(self.save_to_keyboard)
        buttons_layout.addWidget(self.btn_save)

        main_layout.addLayout(buttons_layout)

        # Center layout
        outer_layout = QVBoxLayout()
        outer_layout.addWidget(main_widget)

        w = QWidget()
        w.setLayout(outer_layout)
        self.addWidget(w)

    def valid(self):
        """Check if this tab should be shown."""
        if not isinstance(self.device, VialKeyboard):
            return False
        kb = self.device.keyboard
        if not hasattr(kb, "has_keychron_snap_click"):
            return False
        return kb.has_keychron_snap_click() and kb.keychron_snap_click_count > 0

    def rebuild(self, device):
        super().rebuild(device)
        if not self.valid():
            return

        self.keyboard = device.keyboard
        self._rebuild_entries()

    def _rebuild_entries(self):
        """Rebuild the entry widgets."""
        # Clear existing widgets
        for widget in self.entry_widgets:
            widget.setParent(None)
            widget.deleteLater()
        self.entry_widgets.clear()

        # Create new widgets
        for i, entry in enumerate(self.keyboard.keychron_snap_click_entries):
            widget = SnapClickEntry(i, self)
            widget.set_entry(entry)
            self.entries_layout.addWidget(widget)
            self.entry_widgets.append(widget)

        # Add stretch at the end
        self.entries_layout.addStretch()

    def update_entry_key(self, entry_index, key_num, keycode):
        """Update a key in a Snap Click entry."""
        if not self.keyboard or entry_index >= len(
            self.keyboard.keychron_snap_click_entries
        ):
            return

        entry = self.keyboard.keychron_snap_click_entries[entry_index]
        key1 = entry.get("key1", 0)
        key2 = entry.get("key2", 0)
        if key_num == 1:
            key1 = keycode
        else:
            key2 = keycode
        self.keyboard.set_keychron_snap_click(
            entry_index, entry.get("type", 0), key1, key2
        )

    def update_entry_type(self, entry_index, snap_type):
        """Update the SOCD type for an entry."""
        if not self.keyboard or entry_index >= len(
            self.keyboard.keychron_snap_click_entries
        ):
            return

        entry = self.keyboard.keychron_snap_click_entries[entry_index]
        self.keyboard.set_keychron_snap_click(
            entry_index, snap_type, entry.get("key1", 0), entry.get("key2", 0)
        )

    def save_to_keyboard(self):
        """Save all Snap Click settings to EEPROM."""
        if not self.keyboard:
            return

        if self.keyboard.save_keychron_snap_click():
            QMessageBox.information(
                self.widget(),
                tr("SnapClick", "Saved"),
                tr("SnapClick", "Snap Click settings saved to keyboard."),
            )
        else:
            QMessageBox.warning(
                self.widget(),
                tr("SnapClick", "Error"),
                tr("SnapClick", "Failed to save Snap Click settings."),
            )
