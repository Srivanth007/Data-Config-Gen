"""
GroupedHeaderView: paints a second header row above the normal QHeaderView,
merging consecutive columns that belong to the same region into one band.

QHeaderView has no native concept of "grouped" columns, so this widget owns
an extra strip of screen real estate above the table's regular horizontal
header and paints the region bands itself, using MatrixModel.column_at() to
know which columns share a region.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QHeaderView, QWidget

from app.ui.matrix_model import MatrixModel

REGION_BAND_HEIGHT = 26
REGION_BG = QColor("#1F3B78")
REGION_FG = QColor("#FFFFFF")
REGION_BORDER = QColor("#020303")


class RegionBandHeader(QWidget):
    

    def __init__(self, section_header: QHeaderView, model: MatrixModel, parent=None):
        super().__init__(parent)
        self._header = section_header
        self._model = None
        self.setFixedHeight(REGION_BAND_HEIGHT)
        self._header.sectionResized.connect(lambda *_: self.update())
        self.set_model(model)

    def set_model(self, model: MatrixModel) -> None:
        if self._model is not None:
            try:
                self._model.modelReset.disconnect(self.update)
                self._model.headerDataChanged.disconnect(self._on_header_data_changed)
                self._model.layoutChanged.disconnect(self.update)
            except (RuntimeError, TypeError):
                pass  # already disconnected / never connected

        self._model = model
        model.modelReset.connect(self.update)
        model.headerDataChanged.connect(self._on_header_data_changed)
        model.layoutChanged.connect(self.update)
        self.update()

    def _on_header_data_changed(self, *_args) -> None:
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt override)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)

        col_count = self._model.columnCount()
        if col_count == 0:
            painter.end()
            return

        # Group consecutive columns by region, computing each band's pixel span.
        band_start_col = 0
        current_region = self._model.column_at(0).region

        def draw_band(start_col: int, end_col: int, region_name: str) -> None:
            x_start = self._header.sectionViewportPosition(start_col)
            x_end = (
                self._header.sectionViewportPosition(end_col)
                + self._header.sectionSize(end_col)
            )
            rect = self.rect()
            band_rect = rect.__class__(x_start, 0, max(x_end - x_start, 1), REGION_BAND_HEIGHT)

            painter.fillRect(band_rect, REGION_BG)
            painter.setPen(REGION_BORDER)
            painter.drawRect(band_rect.adjusted(0, 0, -1, -1))
            painter.setPen(REGION_FG)
            painter.drawText(band_rect, Qt.AlignCenter, region_name)

        for col in range(1, col_count):
            region = self._model.column_at(col).region
            if region != current_region:
                draw_band(band_start_col, col - 1, current_region)
                band_start_col = col
                current_region = region
        draw_band(band_start_col, col_count - 1, current_region)

        painter.end()
