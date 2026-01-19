# SPDX-License-Identifier: GPL-2.0-or-later
"""
RGB Keyboard Widget - A keyboard visualization for per-key RGB color editing.

This widget displays the keyboard layout with LED colors overlaid on each key,
allowing users to click keys to select them for color editing.
"""

from PyQt5.QtGui import QPainter, QColor, QBrush, QPen, QPalette
from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import Qt, pyqtSignal

from widgets.keyboard_widget import KeyboardWidget, KeyWidget, EncoderWidget


class RGBKeyboardWidget(KeyboardWidget):
    """
    A keyboard widget that displays per-key RGB colors.

    This extends the standard KeyboardWidget to show LED colors
    on each key and support multi-selection for bulk color editing.
    """

    key_selected = pyqtSignal(object)  # Emits the selected key widget
    key_deselected = pyqtSignal()

    def __init__(self, layout_editor):
        super().__init__(layout_editor)
        # Dict mapping (row, col) -> LED index
        self.led_matrix = {}
        # Dict mapping LED index -> (H, S, V) color tuple
        self.led_colors = {}
        # Set of selected key widgets for multi-select
        self.selected_keys = set()
        # Whether we're in multi-select mode (Ctrl/Shift held)
        self.multi_select = False

    def set_led_matrix(self, led_matrix):
        """
        Set the LED matrix mapping.

        Args:
            led_matrix: Dict mapping (row, col) -> LED index
        """
        self.led_matrix = led_matrix or {}
        self.update()

    def set_led_colors(self, led_colors):
        """
        Set the LED colors.

        Args:
            led_colors: List of (H, S, V) tuples indexed by LED index
        """
        self.led_colors = {}
        for idx, color in enumerate(led_colors):
            self.led_colors[idx] = color
        self.update()

    def set_led_color(self, led_idx, h, s, v):
        """Set color for a single LED."""
        self.led_colors[led_idx] = (h, s, v)
        self.update()

    def get_led_index_for_key(self, key):
        """Get the LED index for a key widget, or None if no LED."""
        if key.desc.row is None or key.desc.col is None:
            return None
        return self.led_matrix.get((key.desc.row, key.desc.col))

    def get_selected_led_indices(self):
        """Get list of LED indices for all selected keys."""
        indices = []
        for key in self.selected_keys:
            led_idx = self.get_led_index_for_key(key)
            if led_idx is not None:
                indices.append(led_idx)
        return indices

    def select_all_keys(self):
        """Select all keys that have LEDs."""
        self.selected_keys.clear()
        for key in self.widgets:
            led_idx = self.get_led_index_for_key(key)
            if led_idx is not None:
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
            led_idx = self.get_led_index_for_key(key)
            if led_idx is not None:
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
                # Key has no LED, ignore
                pass
        else:
            if not self.multi_select:
                self.deselect_all_keys()

        self.update()

    def paintEvent(self, event):
        """Override paint to show LED colors on keys."""
        qp = QPainter()
        qp.begin(self)
        qp.setRenderHint(QPainter.Antialiasing)

        # Pens
        regular_pen = qp.pen()
        regular_pen.setColor(QApplication.palette().color(QPalette.ButtonText))
        qp.setPen(regular_pen)

        # For selected keys
        selected_pen = QPen()
        selected_pen.setColor(QApplication.palette().color(QPalette.Highlight))
        selected_pen.setWidthF(2.0)

        # Default brushes (for keys without LEDs)
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

        for key in self.widgets:
            qp.save()

            qp.scale(self.scale, self.scale)
            qp.translate(key.shift_x, key.shift_y)
            qp.translate(key.rotation_x, key.rotation_y)
            qp.rotate(key.rotation_angle)
            qp.translate(-key.rotation_x, -key.rotation_y)

            # Check if this key has an LED
            led_idx = self.get_led_index_for_key(key)
            is_selected = key in self.selected_keys

            if led_idx is not None and led_idx in self.led_colors:
                # Get the LED color
                h, s, v = self.led_colors[led_idx]
                # Convert HSV (0-255) to QColor HSV (0-359, 0-255, 0-255)
                led_color = QColor.fromHsv(int(h * 359 / 255), s, v)

                # Create brushes based on LED color
                bg_brush = QBrush()
                bg_brush.setColor(led_color.darker(130))
                bg_brush.setStyle(Qt.SolidPattern)

                fg_brush = QBrush()
                fg_brush.setColor(led_color)
                fg_brush.setStyle(Qt.SolidPattern)
            else:
                # No LED - use default darker colors
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

            # Draw LED index text for debugging/reference
            if led_idx is not None:
                # Determine text color based on brightness
                if led_idx in self.led_colors:
                    h, s, v = self.led_colors[led_idx]
                    # Use white text on dark backgrounds, black on light
                    if v < 128:
                        qp.setPen(Qt.white)
                    else:
                        qp.setPen(Qt.black)
                else:
                    qp.setPen(regular_pen)

                # Draw the LED index number
                qp.drawText(key.text_rect, Qt.AlignCenter, str(led_idx))

            # Draw extra path (encoder arrows)
            if hasattr(key, "extra_draw_path"):
                qp.setPen(regular_pen)
                qp.drawPath(key.extra_draw_path)

            qp.restore()

        qp.end()
