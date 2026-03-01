# SPDX-License-Identifier: GPL-2.0-or-later
"""
Factory Reset Dialog — instructs the user how to trigger a hardware factory
reset on their Keychron keyboard (Fn + J + Z held for 3 seconds).

The dialog mimics the style of the vial-unlock dialog: a progress bar fills
over the 3-second hold period.  There is no USB polling involved because the
factory reset is triggered entirely on-device; the firmware sends a one-shot
notification after the reset completes, but there is no host→device command.
"""

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QPalette
from PyQt5.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QApplication,
)

from util import tr

# Total hold time in milliseconds (matches firmware's 3-second threshold)
_HOLD_MS = 3000
# Timer tick interval in milliseconds
_TICK_MS = 50


class FactoryResetDialog(QDialog):
    """
    Instructional dialog for triggering a Keychron hardware factory reset.

    Shows a progress bar that fills over 3 seconds while the user holds
    Fn + J + Z.  Because the reset happens entirely on-device, the bar is
    a pure countdown — it is not driven by USB state.
    """

    def __init__(self, parent=None):
        super().__init__(parent)

        self.setWindowTitle(tr("FactoryReset", "Factory Reset"))
        self.setWindowFlags(
            Qt.Dialog | Qt.Tool | Qt.WindowTitleHint | Qt.CustomizeWindowHint
        )
        self.setModal(True)

        # Match the unlocker background style
        self.setStyleSheet(
            "background-color: {}".format(
                QApplication.palette().color(QPalette.Button).lighter(130).name()
            )
        )

        layout = QVBoxLayout()
        layout.setSpacing(12)

        # --- Instructions ---
        intro = QLabel(
            tr(
                "FactoryReset",
                "This will restore all keyboard settings to factory defaults.\n"
                "The reset is performed entirely on the keyboard — no USB command is sent.",
            )
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        what_resets = QLabel(
            tr(
                "FactoryReset",
                "The following will be reset:\n"
                "  \u2022  Keymap (all layers restored to firmware defaults)\n"
                "  \u2022  Key actuation profiles (Hall Effect keyboards)\n"
                "  \u2022  RGB lighting settings\n"
                "  \u2022  Debounce, NKRO, and USB report rate settings\n"
                "  \u2022  Wireless pairing information",
            )
        )
        what_resets.setWordWrap(True)
        layout.addWidget(what_resets)

        combo_label = QLabel(
            tr(
                "FactoryReset",
                "Press and hold the following key combination until the bar fills:",
            )
        )
        combo_label.setWordWrap(True)
        layout.addWidget(combo_label)

        # Key combo badge
        badge_layout = QHBoxLayout()
        badge_layout.addStretch()
        for key_text in ("Fn", "J", "Z"):
            key_label = QLabel(key_text)
            key_label.setAlignment(Qt.AlignCenter)
            key_label.setStyleSheet(
                "font-weight: bold; font-size: 14px;"
                "border: 2px solid palette(mid);"
                "border-radius: 6px;"
                "padding: 6px 14px;"
                "background: palette(base);"
            )
            badge_layout.addWidget(key_label)
            if key_text != "Z":
                plus = QLabel("+")
                plus.setAlignment(Qt.AlignCenter)
                badge_layout.addWidget(plus)
        badge_layout.addStretch()
        layout.addLayout(badge_layout)

        # Progress bar (0 → _HOLD_MS ticks)
        self.progress = QProgressBar()
        self.progress.setMinimum(0)
        self.progress.setMaximum(_HOLD_MS)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        layout.addWidget(self.progress)

        # Status label (changes when done)
        self.status_label = QLabel(
            tr("FactoryReset", "Hold the keys above until the bar is full...")
        )
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        # Buttons row
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self.btn_start = QPushButton(tr("FactoryReset", "Start"))
        self.btn_start.setDefault(True)
        self.btn_start.clicked.connect(self._start)
        btn_layout.addWidget(self.btn_start)

        self.btn_cancel = QPushButton(tr("FactoryReset", "Cancel"))
        self.btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(self.btn_cancel)

        layout.addLayout(btn_layout)
        self.setLayout(layout)

        # Internal state
        self._elapsed_ms = 0
        self._timer = QTimer(self)
        self._timer.setInterval(_TICK_MS)
        self._timer.timeout.connect(self._tick)
        self._done = False

        self.setMinimumWidth(420)
        self.setMinimumHeight(350)
        self.adjustSize()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _start(self):
        """Begin the countdown — user must now hold the key combo."""
        self.btn_start.setEnabled(False)
        self.btn_cancel.setText(tr("FactoryReset", "Cancel"))
        self.progress.setValue(0)
        self._elapsed_ms = 0
        self._done = False
        self._timer.start()

    def _tick(self):
        """Called every _TICK_MS ms while the user holds the combo."""
        self._elapsed_ms += _TICK_MS
        self.progress.setValue(min(self._elapsed_ms, _HOLD_MS))

        if self._elapsed_ms >= _HOLD_MS:
            self._timer.stop()
            self._on_complete()

    def _on_complete(self):
        """Bar has filled — the keyboard should be resetting now."""
        self._done = True
        self.progress.setValue(_HOLD_MS)
        self.status_label.setText(
            tr(
                "FactoryReset",
                "Release the keys — the keyboard is resetting.\n"
                "It will flash red three times to confirm.\n\n"
                "Close this dialog and re-open the application to reload settings.",
            )
        )
        self.btn_start.hide()
        self.btn_cancel.setText(tr("FactoryReset", "Close"))
        self.btn_cancel.setDefault(True)

    # ------------------------------------------------------------------
    # Overrides
    # ------------------------------------------------------------------

    def keyPressEvent(self, ev):
        """Ignore Escape while the timer is running so it can't be dismissed."""
        if self._timer.isActive():
            return
        super().keyPressEvent(ev)

    def reject(self):
        """Stop the timer before closing."""
        self._timer.stop()
        super().reject()
