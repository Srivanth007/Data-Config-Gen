"""
PartsPanel: the searchable parts list on the left, and the drag SOURCE for
dragging one or more parts onto the matrix (spec section 5 & 13).
"""
from __future__ import annotations

from PySide6.QtCore import QByteArray, QMimeData, Qt
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import (
    QAbstractItemView,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.core.models import Part
from app.ui.matrix_model import PART_MIME_TYPE, MatrixModel


class _PartsListWidget(QListWidget):
    """QListWidget is fine here (not thousands of pixels of custom painting
    needed per item) — the performance-sensitive widget is the matrix, which
    uses QTableView + a model. A plain widget-based list of even a few
    thousand simple text rows is not a problem for Qt."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragOnly)

    def startDrag(self, supportedActions) -> None:  # noqa: N802 (Qt override)
        selected_ids = [
            item.data(Qt.UserRole) for item in self.selectedItems()
        ]
        if not selected_ids:
            return

        mime = QMimeData()
        payload = "\n".join(selected_ids).encode("utf-8")
        mime.setData(PART_MIME_TYPE, QByteArray(payload))

        drag = QDrag(self)
        drag.setMimeData(mime)
        label = selected_ids[0] if len(selected_ids) == 1 else f"{len(selected_ids)} parts"
        drag.setObjectName(label)
        drag.exec(Qt.CopyAction)


class PartsPanel(QWidget):
    def __init__(self, matrix_model: MatrixModel, parent=None):
        super().__init__(parent)
        self.matrix_model = matrix_model
        self._all_parts: list[Part] = []

        self.search_box = QLineEdit(self)
        self.search_box.setPlaceholderText("Search parts (id or name)\u2026")
        self.search_box.textChanged.connect(self._on_search_changed)

        self.count_label = QLabel(self)

        self.list_widget = _PartsListWidget(self)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Parts", self))
        layout.addWidget(self.search_box)
        layout.addWidget(self.list_widget)
        layout.addWidget(self.count_label)

    def set_parts(self, parts: list[Part]) -> None:
        self._all_parts = list(parts)
        self.search_box.clear()
        self._rebuild_list(self._all_parts)

    def _on_search_changed(self, text: str) -> None:
        self.matrix_model.set_part_filter(text)
        text = text.strip().lower()
        if not text:
            filtered = self._all_parts
        else:
            filtered = [
                p for p in self._all_parts
                if text in p.id.lower() or text in p.name.lower()
            ]
        self._rebuild_list(filtered)

    def _rebuild_list(self, parts: list[Part]) -> None:
        self.list_widget.clear()
        for part in parts:
            item = QListWidgetItem(f"{part.id}  \u2014  {part.name}")
            item.setData(Qt.UserRole, part.id)
            self.list_widget.addItem(item)
        self.count_label.setText(f"{len(parts)} of {len(self._all_parts)} parts")
