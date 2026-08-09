"""
RegionsPanel: lets the engineer build up regions and their variants by hand,
inside the app. This is user input — the imported JSON supplies parts only.

A QTreeWidget shows Region -> Variant as a two-level tree. Buttons operate
on whatever is currently selected: a Region item or a Variant item.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.core.models import Project
from app.ui import dialogs

REGION_ITEM_TYPE = QTreeWidgetItem.UserType + 1
VARIANT_ITEM_TYPE = QTreeWidgetItem.UserType + 2


class RegionsPanel(QWidget):
    """
    Emits `regionsChanged` after any successful add/rename/remove so
    MainWindow can refresh the matrix model's columns and the Excel
    export input.
    """
    regionsChanged = Signal()

    def __init__(self, project: Project, parent=None):
        super().__init__(parent)
        self.project = project

        self.tree = QTreeWidget(self)
        self.tree.setHeaderLabels(["Regions / Variants"])
        self.tree.setSelectionMode(QTreeWidget.SingleSelection)

        self.btn_add_region = QPushButton("Add Region\u2026", self)
        self.btn_rename = QPushButton("Rename\u2026", self)
        self.btn_add_variant = QPushButton("Add Variant\u2026", self)
        self.btn_remove = QPushButton("Remove", self)

        self.btn_add_region.clicked.connect(self._on_add_region)
        self.btn_rename.clicked.connect(self._on_rename)
        self.btn_add_variant.clicked.connect(self._on_add_variant)
        self.btn_remove.clicked.connect(self._on_remove)

        btn_row1 = QHBoxLayout()
        btn_row1.addWidget(self.btn_add_region)
        btn_row1.addWidget(self.btn_add_variant)

        btn_row2 = QHBoxLayout()
        btn_row2.addWidget(self.btn_rename)
        btn_row2.addWidget(self.btn_remove)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Regions & Variants", self))
        layout.addWidget(self.tree)
        layout.addLayout(btn_row1)
        layout.addLayout(btn_row2)

        self.refresh()

    # ------------------------------------------------------------------
    def set_project(self, project: Project) -> None:
        self.project = project
        self.refresh()

    def refresh(self) -> None:
        self.tree.clear()
        for region in self.project.regions:
            region_item = QTreeWidgetItem([region.name], type=REGION_ITEM_TYPE)
            region_item.setData(0, Qt.UserRole, region.name)
            self.tree.addTopLevelItem(region_item)
            for variant in region.variants:
                variant_item = QTreeWidgetItem([variant], type=VARIANT_ITEM_TYPE)
                variant_item.setData(0, Qt.UserRole, (region.name, variant))
                region_item.addChild(variant_item)
            region_item.setExpanded(True)

    # ------------------------------------------------------------------
    def _selected_item(self) -> QTreeWidgetItem | None:
        items = self.tree.selectedItems()
        return items[0] if items else None

    def _on_add_region(self) -> None:
        name, ok = QInputDialog.getText(self, "Add Region", "Region name:")
        if not ok:
            return
        try:
            self.project.add_region(name)
        except ValueError as exc:
            dialogs.show_error(self, "Could not add region", str(exc))
            return
        self.refresh()
        self.regionsChanged.emit()

    def _on_add_variant(self) -> None:
        item = self._selected_item()
        region_name = self._region_name_for(item)
        if region_name is None:
            dialogs.show_error(self, "Add Variant", "Select a region first (or a variant within it).")
            return

        variant_name, ok = QInputDialog.getText(self, "Add Variant", f"Variant name for '{region_name}':")
        if not ok:
            return
        try:
            self.project.add_variant(region_name, variant_name)
        except ValueError as exc:
            dialogs.show_error(self, "Could not add variant", str(exc))
            return
        self.refresh()
        self.regionsChanged.emit()

    def _on_rename(self) -> None:
        item = self._selected_item()
        if item is None:
            dialogs.show_error(self, "Rename", "Select a region or variant first.")
            return

        if item.type() == REGION_ITEM_TYPE:
            old_name = item.data(0, Qt.UserRole)
            new_name, ok = QInputDialog.getText(self, "Rename Region", "New name:", text=old_name)
            if not ok:
                return
            try:
                self.project.rename_region(old_name, new_name)
            except ValueError as exc:
                dialogs.show_error(self, "Could not rename region", str(exc))
                return
        elif item.type() == VARIANT_ITEM_TYPE:
            region_name, old_variant = item.data(0, Qt.UserRole)
            new_variant, ok = QInputDialog.getText(self, "Rename Variant", "New name:", text=old_variant)
            if not ok:
                return
            try:
                self.project.rename_variant(region_name, old_variant, new_variant)
            except ValueError as exc:
                dialogs.show_error(self, "Could not rename variant", str(exc))
                return
        else:
            return

        self.refresh()
        self.regionsChanged.emit()

    def _on_remove(self) -> None:
        item = self._selected_item()
        if item is None:
            dialogs.show_error(self, "Remove", "Select a region or variant first.")
            return

        if item.type() == REGION_ITEM_TYPE:
            region_name = item.data(0, Qt.UserRole)
            if not dialogs.confirm(
                self, "Remove Region",
                f"Remove region '{region_name}' and all its variants?\n"
                "Any part assignments for this region will also be removed.",
            ):
                return
            try:
                self.project.remove_region(region_name)
            except ValueError as exc:
                dialogs.show_error(self, "Could not remove region", str(exc))
                return
        elif item.type() == VARIANT_ITEM_TYPE:
            region_name, variant_name = item.data(0, Qt.UserRole)
            if not dialogs.confirm(
                self, "Remove Variant",
                f"Remove variant '{variant_name}' from '{region_name}'?\n"
                "Any part assignments for this column will also be removed.",
            ):
                return
            try:
                self.project.remove_variant(region_name, variant_name)
            except ValueError as exc:
                dialogs.show_error(self, "Could not remove variant", str(exc))
                return
        else:
            return

        self.refresh()
        self.regionsChanged.emit()

    def _region_name_for(self, item: QTreeWidgetItem | None) -> str | None:
        if item is None:
            return None
        if item.type() == REGION_ITEM_TYPE:
            return item.data(0, Qt.UserRole)
        if item.type() == VARIANT_ITEM_TYPE:
            region_name, _variant = item.data(0, Qt.UserRole)
            return region_name
        return None
