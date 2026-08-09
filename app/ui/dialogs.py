"""
Small reusable dialogs: showing a list of validation errors, and file pickers.
Kept separate from main_window.py so that file grows less and stays focused
on wiring, not dialog boilerplate.
"""
from __future__ import annotations

from PySide6.QtWidgets import QFileDialog, QMessageBox, QWidget

from app.core.validation import ValidationResult


def show_validation_errors(parent: QWidget, title: str, result: ValidationResult) -> None:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Warning)
    box.setWindowTitle(title)
    box.setText(f"{len(result.errors)} issue(s) found:")
    box.setDetailedText("\n".join(f"- {e}" for e in result.errors))
    box.setStandardButtons(QMessageBox.Ok)
    box.exec()


def show_info(parent: QWidget, title: str, message: str) -> None:
    QMessageBox.information(parent, title, message)


def show_error(parent: QWidget, title: str, message: str) -> None:
    QMessageBox.critical(parent, title, message)


def confirm(parent: QWidget, title: str, message: str) -> bool:
    result = QMessageBox.question(
        parent, title, message, QMessageBox.Yes | QMessageBox.No, QMessageBox.No
    )
    return result == QMessageBox.Yes


def pick_open_json(parent: QWidget, caption: str) -> str | None:
    path, _ = QFileDialog.getOpenFileName(parent, caption, "", "JSON files (*.json)")
    return path or None


def pick_save_json(parent: QWidget, caption: str, default_name: str = "project.json") -> str | None:
    path, _ = QFileDialog.getSaveFileName(parent, caption, default_name, "JSON files (*.json)")
    return path or None


def pick_save_xlsx(parent: QWidget, caption: str, default_name: str = "variant_configuration.xlsx") -> str | None:
    path, _ = QFileDialog.getSaveFileName(parent, caption, default_name, "Excel files (*.xlsx)")
    return path or None
