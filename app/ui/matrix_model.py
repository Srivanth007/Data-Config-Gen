
from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal

from app.core.models import ColumnKey, Part, Project

PART_MIME_TYPE = "application/x-part-ids"


class MatrixModel(QAbstractTableModel):
    # Emitted whenever an assignment changes, so other UI (e.g. a status bar
    # or an "unsaved changes" indicator) can react without polling.
    assignmentChanged = Signal()

    def __init__(self, project: Project, parent=None):
        super().__init__(parent)
        self.project = project
        self._visible_parts: list[Part] = list(project.parts)
        self._columns: list[ColumnKey] = project.all_columns()
        self._filter_text: str = ""

    # ------------------------------------------------------------------
    # Rebuilding when the underlying project changes wholesale
    # ------------------------------------------------------------------
    def set_project(self, project: Project) -> None:
        self.beginResetModel()
        self.project = project
        self._filter_text = ""
        self._visible_parts = list(project.parts)
        self._columns = project.all_columns()
        self.endResetModel()

    def refresh_columns(self) -> None:
        """Call after regions/variants change (rare, but supported)."""
        self.beginResetModel()
        self._columns = self.project.all_columns()
        self.endResetModel()

    # ------------------------------------------------------------------
    # Search / filter (spec section 6 & 14)
    # ------------------------------------------------------------------
    def set_part_filter(self, text: str) -> None:
        text = (text or "").strip().lower()
        if text == self._filter_text:
            return
        self._filter_text = text
        self.beginResetModel()
        if not text:
            self._visible_parts = list(self.project.parts)
        else:
            self._visible_parts = [
                p for p in self.project.parts
                if text in p.id.lower() or text in p.name.lower()
            ]
        self.endResetModel()

    # ------------------------------------------------------------------
    # Qt model interface
    # ------------------------------------------------------------------
    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._visible_parts)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._columns)

    def part_at_row(self, row: int) -> Part:
        return self._visible_parts[row]

    def column_at(self, col: int) -> ColumnKey:
        return self._columns[col]

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid():
            return None

        part = self._visible_parts[index.row()]
        col = self._columns[index.column()]
        assigned = self.project.is_assigned(part.id, col)

        if role in (Qt.DisplayRole, Qt.EditRole):
            return "\u2713" if assigned else ""
        if role == Qt.TextAlignmentRole:
            return Qt.AlignCenter
        if role == Qt.ToolTipRole:
            return f"{part.id} \u2014 {col.region} / {col.variant}"
        return None

    def setData(self, index: QModelIndex, value, role: int = Qt.EditRole) -> bool:
        if not index.isValid():
            return False
        part = self._visible_parts[index.row()]
        col = self._columns[index.column()]
        self.project.toggle_assigned(part.id, col)
        self.dataChanged.emit(index, index, [Qt.DisplayRole, Qt.EditRole])
        self.assignmentChanged.emit()
        return True

    def flags(self, index: QModelIndex) -> Qt.ItemFlags:
        if not index.isValid():
            return Qt.NoItemFlags
        return (
            Qt.ItemIsEnabled
            | Qt.ItemIsSelectable
            | Qt.ItemIsDropEnabled
        )

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Horizontal:
            if 0 <= section < len(self._columns):
                return self._columns[section].variant
        else:
            if 0 <= section < len(self._visible_parts):
                return self._visible_parts[section].id
        return None

    # ------------------------------------------------------------------
    # Assignment mutation entry points shared by click AND drag-and-drop
    # (single code path, per architecture doc section 5)
    # ------------------------------------------------------------------
    def set_cell_assigned(self, row: int, col: int, value: bool) -> None:
        part = self._visible_parts[row]
        col_key = self._columns[col]
        self.project.set_assigned(part.id, col_key, value)
        idx = self.index(row, col)
        self.dataChanged.emit(idx, idx, [Qt.DisplayRole, Qt.EditRole])
        self.assignmentChanged.emit()

    def assign_parts_to_column(self, part_ids: list[str], col: int) -> None:
        """Used by drag-and-drop: assign every given part id to one column."""
        if not (0 <= col < len(self._columns)):
            return
        col_key = self._columns[col]
        self.project.assign_many(part_ids, col_key, True)

        # Emit dataChanged for whichever visible rows were affected, so only
        # those cells repaint instead of resetting the whole model.
        id_set = set(part_ids)
        for row, part in enumerate(self._visible_parts):
            if part.id in id_set:
                idx = self.index(row, col)
                self.dataChanged.emit(idx, idx, [Qt.DisplayRole, Qt.EditRole])
        self.assignmentChanged.emit()

    def mimeTypes(self) -> list[str]:
        return [PART_MIME_TYPE]
