"""
MainWindow: toolbar + layout + signal wiring only. No business logic lives
here — every action delegates to app.core, so core stays fully testable
without Qt.

Regions/variants are user input (RegionsPanel), NOT read from the imported
JSON — the JSON import only ever supplies parts.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QLabel,
    QMainWindow,
    QSplitter,
    QStatusBar,
    QToolBar,
)

from app.core.excel_export import export_to_excel
from app.core.io_json import (
    ProjectFileError,
    SourceJsonError,
    import_parts_file,
    load_project,
    save_project,
)
from app.core.models import Project
from app.core.validation import validate_project
from app.ui import dialogs
from app.ui.matrix_model import MatrixModel
from app.ui.matrix_view import MatrixViewContainer
from app.ui.parts_panel import PartsPanel
from app.ui.regions_panel import RegionsPanel


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Vehicle Variant Configuration Generator")
        self.resize(1400, 800)

        self.project: Project = Project()
        self.current_project_path: Path | None = None
        self._dirty = False

        self.matrix_model = MatrixModel(self.project)
        self.matrix_model.assignmentChanged.connect(self._mark_dirty)

        self.regions_panel = RegionsPanel(self.project, self)
        self.regions_panel.regionsChanged.connect(self._on_regions_changed)

        self.parts_panel = PartsPanel(self.matrix_model, self)
        self.matrix_view = MatrixViewContainer(self.matrix_model, self)

        splitter = QSplitter(self)
        splitter.addWidget(self.regions_panel)
        splitter.addWidget(self.parts_panel)
        splitter.addWidget(self.matrix_view)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 0)
        splitter.setStretchFactor(2, 1)
        splitter.setSizes([260, 280, 1000])
        self.setCentralWidget(splitter)

        self._build_toolbar()
        self._build_status_bar()
        self._update_status()

    # ------------------------------------------------------------------
    # Toolbar
    # ------------------------------------------------------------------
    def _build_toolbar(self) -> None:
        toolbar = QToolBar("Main", self)
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        toolbar.addAction("New Project", self.action_new_project)
        toolbar.addSeparator()
        toolbar.addAction("Import Parts JSON\u2026", self.action_import_parts)
        toolbar.addAction("Open Project\u2026", self.action_open_project)
        toolbar.addAction("Save Project\u2026", self.action_save_project)
        toolbar.addSeparator()
        toolbar.addAction("Validate", self.action_validate)
        toolbar.addAction("Export Excel\u2026", self.action_export_excel)

    def _build_status_bar(self) -> None:
        self.status_bar = QStatusBar(self)
        self.setStatusBar(self.status_bar)
        self.status_label = QLabel("No project loaded", self)
        self.status_bar.addWidget(self.status_label)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def action_new_project(self) -> None:
        if self._dirty and not dialogs.confirm(
            self, "New Project", "Discard unsaved changes and start a new project?"
        ):
            return
        self._load_new_project(Project(), path=None)

    def action_import_parts(self) -> None:
        """
        Imports PARTS ONLY from a JSON file. Regions/variants are untouched —
        they're user input managed in the Regions & Variants panel. Existing
        assignments for parts that no longer exist after import are dropped;
        assignments for parts that are still present are kept.
        """
        path = dialogs.pick_open_json(self, "Import parts JSON")
        if not path:
            return
        try:
            parts = import_parts_file(path)
        except SourceJsonError as exc:
            if exc.validation is not None and exc.validation.errors:
                dialogs.show_validation_errors(self, "Parts JSON is invalid", exc.validation)
            else:
                dialogs.show_error(self, "Import failed", str(exc))
            return

        self.project.replace_parts(parts)
        self.matrix_model.set_project(self.project)
        self.parts_panel.set_parts(self.project.parts)
        self._mark_dirty()

        dialogs.show_info(self, "Import complete", f"Loaded {len(parts)} part(s).")

    def action_open_project(self) -> None:
        path = dialogs.pick_open_json(self, "Open project")
        if not path:
            return
        try:
            project = load_project(path)
        except ProjectFileError as exc:
            dialogs.show_error(self, "Could not open project", str(exc))
            return

        self._load_new_project(project, path=Path(path))

    def action_save_project(self) -> None:
        if not self.project.parts and not self.project.regions:
            dialogs.show_error(
                self, "Nothing to save",
                "Add at least one region and import some parts first.",
            )
            return

        default_name = self.current_project_path.name if self.current_project_path else "project.json"
        path = dialogs.pick_save_json(self, "Save project", default_name)
        if not path:
            return
        try:
            save_project(self.project, path)
        except OSError as exc:
            dialogs.show_error(self, "Save failed", str(exc))
            return

        self.current_project_path = Path(path)
        self._dirty = False
        self._update_status()
        dialogs.show_info(self, "Saved", f"Project saved to {path}")

    def action_validate(self) -> None:
        result = validate_project(self.project)
        if result.is_valid:
            dialogs.show_info(self, "Validation passed", "No issues found. Ready to export.")
        else:
            dialogs.show_validation_errors(self, "Validation failed", result)

    def action_export_excel(self) -> None:
        result = validate_project(self.project)
        if not result.is_valid:
            dialogs.show_validation_errors(
                self, "Cannot export \u2014 fix these issues first", result
            )
            return

        path = dialogs.pick_save_xlsx(self, "Export Excel")
        if not path:
            return
        try:
            export_to_excel(self.project, path)
        except OSError as exc:
            dialogs.show_error(self, "Export failed", str(exc))
            return

        dialogs.show_info(self, "Export complete", f"Excel file written to {path}")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _load_new_project(self, project: Project, path: Path | None) -> None:
        self.project = project
        self.current_project_path = path
        self._dirty = False

        self.matrix_model.set_project(project)
        self.regions_panel.set_project(project)
        self.parts_panel.set_parts(project.parts)
        self.matrix_view.set_model(self.matrix_model)

        self._update_status()

    def _on_regions_changed(self) -> None:
        """Regions/variants were added/renamed/removed via RegionsPanel."""
        self.matrix_model.refresh_columns()
        self._mark_dirty()

    def _mark_dirty(self) -> None:
        self._dirty = True
        self._update_status()

    def _update_status(self) -> None:
        if not self.project.parts and not self.project.regions:
            self.status_label.setText("No project loaded \u2014 add regions and import parts to begin")
            return

        columns = len(self.project.all_columns())
        parts = len(self.project.parts)
        regions = len(self.project.regions)
        dirty_marker = " \u2014 unsaved changes" if self._dirty else ""
        name = self.current_project_path.name if self.current_project_path else "(unsaved)"
        self.status_label.setText(
            f"{name} \u2014 {parts} parts \u00d7 {columns} variant columns "
            f"across {regions} region(s){dirty_marker}"
        )
