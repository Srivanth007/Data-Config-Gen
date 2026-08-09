"""
JSON I/O.

IMPORTANT: the imported source JSON now supplies PARTS ONLY.
Regions and variants are user input, entered and edited inside the app
(see app.ui.regions_panel / Project.add_region etc. in models.py) — they
are never read from the imported JSON.

  - load_source_json / build_parts_from_source / import_parts_file:
      import the parts list (read-only source file, never modified).
  - save_project / load_project:
      the app's own project.json format, which stores the user-authored
      regions/variants, the parts (as last imported), and the assignments
      matrix — everything needed to reconstruct the project exactly.
"""
from __future__ import annotations

import json
from pathlib import Path

from .models import ColumnKey, Part, Project, Region
from .validation import validate_import_parts, ValidationResult

SCHEMA_VERSION = 2  # bumped: regions are no longer part of the source JSON


class SourceJsonError(Exception):
    """Raised when the source (parts) JSON is malformed or fails validation."""

    def __init__(self, message: str, validation: ValidationResult | None = None):
        super().__init__(message)
        self.validation = validation


class ProjectFileError(Exception):
    """Raised when a project.json file is malformed or unreadable."""


# ----------------------------------------------------------------------
# Source JSON (parts only) — "json is only for showing parts"
# ----------------------------------------------------------------------
def load_source_json(path: str | Path) -> dict:
    """Read and JSON-parse the source file. Raises SourceJsonError on bad JSON."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SourceJsonError(f"Could not read file: {exc}") from exc

    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise SourceJsonError(
            f"Invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from exc


def build_parts_from_source(raw: dict) -> list[Part]:
    """
    Validate `raw` and return the list of Part objects it describes.
    Raises SourceJsonError (carrying the ValidationResult) if invalid.
    """
    result = validate_import_parts(raw)
    if not result.is_valid:
        raise SourceJsonError("Source JSON failed validation.", validation=result)

    return [Part(id=p["id"], name=p["name"]) for p in raw["parts"]]


def import_parts_file(path: str | Path) -> list[Part]:
    """Convenience: load + validate + build parts in one call."""
    raw = load_source_json(path)
    return build_parts_from_source(raw)


# ----------------------------------------------------------------------
# Project save file — regions/variants (user-authored), parts (last
# imported), and assignments, all together.
# ----------------------------------------------------------------------
def save_project(project: Project, path: str | Path) -> None:
    data = {
        "schema_version": SCHEMA_VERSION,
        "regions": [
            {"name": r.name, "variants": list(r.variants)} for r in project.regions
        ],
        "parts": [{"id": p.id, "name": p.name} for p in project.parts],
        "assignments": {
            part_id: [
                {"region": col.region, "variant": col.variant}
                for col in sorted(cols, key=lambda c: (c.region, c.variant))
            ]
            for part_id, cols in project.assignments.items()
            if cols  # skip empty entries
        },
    }
    path = Path(path)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def load_project(path: str | Path) -> Project:
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
        data = json.loads(text)
    except OSError as exc:
        raise ProjectFileError(f"Could not read file: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ProjectFileError(f"Invalid JSON at line {exc.lineno}: {exc.msg}") from exc

    if not isinstance(data, dict):
        raise ProjectFileError("File does not look like a valid project.json.")

    # Accept both the current key names and the old schema_version==1 names
    # ("source_regions"/"source_parts") so previously saved projects still load.
    regions_raw = data.get("regions", data.get("source_regions"))
    parts_raw = data.get("parts", data.get("source_parts"))

    if regions_raw is None or parts_raw is None:
        raise ProjectFileError("File does not look like a valid project.json (missing regions/parts).")

    try:
        regions = [
            Region(name=r["name"], variants=list(r["variants"]))
            for r in regions_raw
        ]
        parts = [Part(id=p["id"], name=p["name"]) for p in parts_raw]
    except (KeyError, TypeError) as exc:
        raise ProjectFileError(f"Malformed regions/parts data in project file: {exc}") from exc

    assignments: dict[str, set[ColumnKey]] = {}
    for part_id, cols in data.get("assignments", {}).items():
        col_set = set()
        for c in cols:
            try:
                col_set.add(ColumnKey(region=c["region"], variant=c["variant"]))
            except (KeyError, TypeError) as exc:
                raise ProjectFileError(
                    f"Malformed assignment entry for part '{part_id}': {exc}"
                ) from exc
        assignments[part_id] = col_set

    return Project(regions=regions, parts=parts, assignments=assignments)
