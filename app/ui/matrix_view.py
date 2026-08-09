"""
MatrixView: the QTableView showing the Part x (Region/Variant) matrix.

Responsibilities:
  - Accept drops of part IDs (dragged from PartsPanel) onto a column and
    assign them (spec section 5 & 13).
  - Toggle a cell's assignment on click (single click, not double).
  - Host the RegionBandHeader strip above its own horizontal header and
    keep it in sync during horizontal scrolling.
"""
from __future__ import annotations

from PySide6.QtCore import QModelIndex, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from app.ui.header_view import RegionBandHeader
from app.ui.matrix_model import PART_MIME_TYPE, MatrixModel


class _InnerTableView(QTableView):
    """The actual QTableView; wrapped by MatrixViewContainer below so we can
    stack a RegionBandHeader above it without fighting QTableView's own
    layout of its corner/header widgets."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setSelectionBehavior(QAbstractItemView.SelectItems)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DropOnly)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)  # we handle clicks manually
        self.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.horizontalHeader().setDefaultSectionSize(90)
        self.verticalHeader().setDefaultSectionSize(24)
        self.setAlternatingRowColors(True)
        self.clicked.connect(self._on_cell_clicked)

    def matrix_model(self) -> MatrixModel | None:
        m = self.model()
        return m if isinstance(m, MatrixModel) else None

    # ------------------------------------------------------------------
    # Click-to-toggle (spec section 6: click checked -> remove, click empty -> add)
    # ------------------------------------------------------------------
    def _on_cell_clicked(self, index: QModelIndex) -> None:
        model = self.matrix_model()
        if model is None or not index.isValid():
            return
        model.setData(index, None, Qt.EditRole)  # setData toggles internally

    # ------------------------------------------------------------------
    # Drag-and-drop target: parts dropped from PartsPanel
    # ------------------------------------------------------------------
    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasFormat(PART_MIME_TYPE):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        if not event.mimeData().hasFormat(PART_MIME_TYPE):
            event.ignore()
            return
        index = self.indexAt(event.position().toPoint()) if hasattr(event, "position") else self.indexAt(event.pos())
        if index.isValid():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:
        model = self.matrix_model()
        if model is None or not event.mimeData().hasFormat(PART_MIME_TYPE):
            event.ignore()
            return

        pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
        index = self.indexAt(pos)
        if not index.isValid():
            event.ignore()
            return

        raw = bytes(event.mimeData().data(PART_MIME_TYPE)).decode("utf-8")
        part_ids = [pid for pid in raw.split("\n") if pid]
        if not part_ids:
            event.ignore()
            return

        model.assign_parts_to_column(part_ids, index.column())
        event.acceptProposedAction()


class MatrixViewContainer(QWidget):
    """
    Public widget used by MainWindow: stacks a RegionBandHeader strip on top
    of the real QTableView and keeps them horizontally synchronized.
    """

    def __init__(self, model: MatrixModel, parent=None):
        super().__init__(parent)
        self.table = _InnerTableView(self)
        self.table.setModel(model)

        self.band_header = RegionBandHeader(self.table.horizontalHeader(), model, self)

        # Keep the band aligned with the header as columns are resized and
        # as the user scrolls horizontally.
        self.table.horizontalScrollBar().valueChanged.connect(lambda *_: self.band_header.update())
        self.table.horizontalHeader().sectionResized.connect(lambda *_: self.band_header.update())

        # The band strip must start where the table's DATA columns start, not
        # at x=0 — otherwise it would overlap the vertical header (row-label)
        # column. A fixed-width spacer, kept in sync with the vertical
        # header's width, achieves that offset.
        self._row_header_spacer = QWidget(self)
        self._sync_row_header_spacer_width()

        band_row = QWidget(self)
        band_row_layout = QHBoxLayout(band_row)
        band_row_layout.setContentsMargins(0, 0, 0, 0)
        band_row_layout.setSpacing(0)
        band_row_layout.addWidget(self._row_header_spacer)
        band_row_layout.addWidget(self.band_header)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(band_row)
        layout.addWidget(self.table)

    def _sync_row_header_spacer_width(self) -> None:
        width = self.table.verticalHeader().width()
        self._row_header_spacer.setFixedWidth(width)

    def set_model(self, model: MatrixModel) -> None:
        self.table.setModel(model)
        self.band_header.set_model(model)
        self._sync_row_header_spacer_width()
        self.band_header.update()

    def select_column(self, col: int) -> None:
        """Highlight a whole column, e.g. when its header is clicked."""
        model = self.table.model()
        if model is None:
            return
        self.table.selectColumn(col)
