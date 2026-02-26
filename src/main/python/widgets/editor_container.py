from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import QVBoxLayout, QScrollArea, QWidget


class EditorContainer(QWidget):
    clicked = pyqtSignal()

    def __init__(self, editor):
        super().__init__()

        self.editor = editor

        # Wrap editor (a QVBoxLayout) in a scrollable widget so that the tab
        # content is never clipped when the window is too small vertically.
        inner = QWidget()
        inner.setLayout(editor)

        scroll = QScrollArea()
        scroll.setWidget(inner)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        outer = QVBoxLayout()
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        self.setLayout(outer)

        self.clicked.connect(editor.on_container_clicked)

    def mousePressEvent(self, ev):
        self.clicked.emit()
