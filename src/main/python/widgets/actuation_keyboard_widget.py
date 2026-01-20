# SPDX-License-Identifier: GPL-2.0-or-later
"""
Actuation Keyboard Widget - A keyboard visualization for per-key actuation editing.

This widget displays the keyboard layout with actuation values overlaid on each key,
allowing users to click keys to select them for actuation editing.
"""

from PyQt5.QtGui import QPainter, QColor, QBrush, QPen, QPalette, QFont
from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import Qt, pyqtSignal

from widgets.keyboard_widget import KeyboardWidget, KeyWidget, EncoderWidget


class ActuationKeyboardWidget(KeyboardWidget):
    """
    A keyboard widget that displays per-key actuation values.

    This extends the standard KeyboardWidget to show actuation point values
    on each key and support multi-selection for bulk editing.
    """

    key_selected = pyqtSignal(object)  # Emits the selected key widget
    key_deselected = pyqtSignal()

    def __init__(self, layout_editor):
        super().__init__(layout_editor)
        # Dict mapping (row, col) -> actuation settings dict
        # Each dict contains: mode, actuation_point, sensitivity, release_sensitivity
        self.key_settings = {}
        # Set of selected key widgets for multi-select
        self.selected_keys = set()
        # Whether we're in multi-select mode (Ctrl/Shift held)
        self.multi_select = False
        # Color gradient for actuation visualization
        self.min_actuation = 1  # 0.1mm
        self.max_actuation = 40  # 4.0mm

    def set_key_settings(self, key_settings):
        """
        Set the per-key actuation settings.

        Args:
            key_settings: Dict mapping (row, col) -> settings dict
        """
        self.key_settings = key_settings or {}
        self.update()

    def set_key_setting(
        self, row, col, mode, actuation_point, sensitivity, release_sensitivity
    ):
        """Set actuation settings for a single key."""
        self.key_settings[(row, col)] = {
            "mode": mode,
            "actuation_point": actuation_point,
            "sensitivity": sensitivity,
            "release_sensitivity": release_sensitivity,
        }
        self.update()

    def get_key_setting(self, row, col):
        """Get actuation settings for a key, or None if not set."""
        return self.key_settings.get((row, col))

    def get_selected_keys(self):
        """Get list of (row, col) tuples for all selected keys."""
        keys = []
        for key in self.selected_keys:
            if key.desc.row is not None and key.desc.col is not None:
                keys.append((key.desc.row, key.desc.col))
        return keys

    def select_all_keys(self):
        """Select all keys."""
        self.selected_keys.clear()
        for key in self.widgets:
            if key.desc.row is not None and key.desc.col is not None:
                self.selected_keys.add(key)
        self.update()

    def deselect_all_keys(self):
        """Deselect all keys."""
        self.selected_keys.clear()
        self.active_key = None
        self.update()
        self.key_deselected.emit()

    def mousePressEvent(self, ev):
        if not self.enabled:
            return

        # Check for multi-select modifier
        self.multi_select = (
            ev.modifiers() & Qt.ControlModifier or ev.modifiers() & Qt.ShiftModifier
        )

        key, _ = self.hit_test(ev.pos())

        if key is not None:
            if key.desc.row is not None and key.desc.col is not None:
                if self.multi_select:
                    # Toggle selection
                    if key in self.selected_keys:
                        self.selected_keys.discard(key)
                    else:
                        self.selected_keys.add(key)
                else:
                    # Single selection - clear others
                    self.selected_keys.clear()
                    self.selected_keys.add(key)

                self.active_key = key
                self.key_selected.emit(key)
        else:
            if not self.multi_select:
                self.deselect_all_keys()

        self.update()

    def _get_actuation_color(self, actuation_point):
        """
        Get a color based on actuation point value.
        Lower actuation = more red (sensitive), Higher = more blue (less sensitive)
        """
        if actuation_point is None:
            return QColor(128, 128, 128)  # Gray for unset

        # Normalize to 0-1 range
        normalized = (actuation_point - self.min_actuation) / (
            self.max_actuation - self.min_actuation
        )
        normalized = max(0, min(1, normalized))

        # Red (sensitive) to Blue (less sensitive) gradient
        # Low actuation (sensitive) = red/orange
        # High actuation = blue/purple
        if normalized < 0.5:
            # Red to Yellow
            r = 255
            g = int(255 * (normalized * 2))
            b = 0
        else:
            # Yellow to Blue
            r = int(255 * (1 - (normalized - 0.5) * 2))
            g = int(255 * (1 - (normalized - 0.5) * 2))
            b = int(255 * ((normalized - 0.5) * 2))

        return QColor(r, g, b)

    def paintEvent(self, event):
        """Override paint to show actuation values on keys."""
        qp = QPainter()
        qp.begin(self)
        qp.setRenderHint(QPainter.Antialiasing)

        # Pens
        regular_pen = qp.pen()
        regular_pen.setColor(QApplication.palette().color(QPalette.ButtonText))
        qp.setPen(regular_pen)

        # For selected keys
        selected_pen = QPen()
        selected_pen.setColor(QColor(255, 255, 0))  # Yellow highlight
        selected_pen.setWidthF(3.0)

        # Default brushes (for keys without settings)
        default_background = QBrush()
        default_background.setColor(
            QApplication.palette().color(QPalette.Button).darker(120)
        )
        default_background.setStyle(Qt.SolidPattern)

        default_foreground = QBrush()
        default_foreground.setColor(
            QApplication.palette().color(QPalette.Button).darker(100)
        )
        default_foreground.setStyle(Qt.SolidPattern)

        # Font for actuation values
        font = QFont()
        font.setPointSize(8)
        font.setBold(True)

        for key in self.widgets:
            qp.save()

            qp.scale(self.scale, self.scale)
            qp.translate(key.shift_x, key.shift_y)
            qp.translate(key.rotation_x, key.rotation_y)
            qp.rotate(key.rotation_angle)
            qp.translate(-key.rotation_x, -key.rotation_y)

            row, col = key.desc.row, key.desc.col
            is_selected = key in self.selected_keys
            settings = (
                self.key_settings.get((row, col))
                if row is not None and col is not None
                else None
            )

            if settings:
                actuation_point = settings.get("actuation_point", 20)
                actuation_color = self._get_actuation_color(actuation_point)

                # Create brushes based on actuation color
                bg_brush = QBrush()
                bg_brush.setColor(actuation_color.darker(130))
                bg_brush.setStyle(Qt.SolidPattern)

                fg_brush = QBrush()
                fg_brush.setColor(actuation_color)
                fg_brush.setStyle(Qt.SolidPattern)
            else:
                # No settings - use default colors
                bg_brush = default_background
                fg_brush = default_foreground

            # Draw background
            qp.setPen(selected_pen if is_selected else Qt.NoPen)
            qp.setBrush(bg_brush)
            qp.drawPath(key.background_draw_path)

            # Draw foreground
            qp.setPen(Qt.NoPen)
            qp.setBrush(fg_brush)
            qp.drawPath(key.foreground_draw_path)

            # Draw actuation value text
            if row is not None and col is not None:
                qp.setFont(font)
                if settings:
                    actuation_point = settings.get("actuation_point", 20)
                    mode = settings.get("mode", 0)
                    # Format as mm value
                    mm_value = actuation_point / 10.0
                    text = f"{mm_value:.1f}"

                    # Add mode indicator
                    mode_char = ""
                    if mode == 1:  # Regular
                        mode_char = ""
                    elif mode == 2:  # Rapid Trigger
                        mode_char = "R"
                    elif mode == 3:  # DKS
                        mode_char = "D"

                    if mode_char:
                        text = f"{mode_char}\n{text}"

                    # Determine text color based on brightness
                    actuation_color = self._get_actuation_color(actuation_point)
                    # Calculate luminance
                    r, g, b, _ = actuation_color.getRgb()
                    luminance = 0.299 * r + 0.587 * g + 0.114 * b
                    if luminance < 128:
                        qp.setPen(Qt.white)
                    else:
                        qp.setPen(Qt.black)
                else:
                    text = "-"
                    qp.setPen(regular_pen)

                qp.drawText(key.text_rect, Qt.AlignCenter, text)

            # Draw extra path (encoder arrows)
            if hasattr(key, "extra_draw_path"):
                qp.setPen(regular_pen)
                qp.drawPath(key.extra_draw_path)

            qp.restore()

        qp.end()
