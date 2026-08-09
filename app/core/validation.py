"""
Validation rules for:
  1. Raw source JSON (before it's turned into a Project) — section 2 of spec.
  2. A fully constructed Project, run right before Excel export — section 16.

Both entry points return a ValidationResult so the UI can show every error
at once instead of failing one at a time.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .models import ColumnKey, Project


@dataclass
class ValidationResult:
    errors: list[str] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return len(self.errors) == 0

    def add(self, message: str) -> None:
        self.errors.append(message)

    def merge(self, other: "ValidationResult") -> None:
        self.errors.extend(other.errors)


# ----------------------------------------------------------------------
# 1. Raw source JSON validation — PARTS ONLY.
#    Regions/variants are user input inside the app and are never read
#    from the imported JSON, so there is nothing to validate for them here.
# ----------------------------------------------------------------------
def validate_import_parts(raw: dict) -> ValidationResult:
    result = ValidationResult()

    if not isinstance(raw, dict):
        result.add("Top-level JSON must be an object with a 'parts' list.")
        return result

    _validate_parts_block(raw.get("parts"), result)

    return result


def _validate_parts_block(parts, result: ValidationResult) -> None:
    if not parts:
        result.add("At least one part is required ('parts' is missing or empty).")
        return

    if not isinstance(parts, list):
        result.add("'parts' must be a list.")
        return

    seen_ids: set[str] = set()

    for i, part in enumerate(parts):
        label = f"parts[{i}]"

        if not isinstance(part, dict):
            result.add(f"{label} must be an object.")
            continue

        part_id = part.get("id")
        name = part.get("name")

        if not part_id or not isinstance(part_id, str):
            result.add(f"{label} is missing a valid 'id'.")
        else:
            if part_id in seen_ids:
                result.add(f"Duplicate part id: '{part_id}'.")
            seen_ids.add(part_id)

        if not name or not isinstance(name, str):
            result.add(f"Part '{part_id or label}' is missing a valid 'name'.")


# ----------------------------------------------------------------------
# 2. Fully constructed Project validation (pre-export)
# ----------------------------------------------------------------------
def validate_project(project: Project) -> ValidationResult:
    result = ValidationResult()

    if not project.regions:
        result.add("Project has no regions.")
    if not project.parts:
        result.add("Project has no parts.")

    # Duplicate checks (defense in depth, in case a project.json was hand-edited)
    seen_regions: set[str] = set()
    for region in project.regions:
        if region.name in seen_regions:
            result.add(f"Duplicate region name: '{region.name}'.")
        seen_regions.add(region.name)

        if not region.variants:
            result.add(f"Region '{region.name}' has no variants.")

        seen_variants: set[str] = set()
        for v in region.variants:
            if v in seen_variants:
                result.add(f"Region '{region.name}' has a duplicate variant: '{v}'.")
            seen_variants.add(v)

    seen_part_ids: set[str] = set()
    for part in project.parts:
        if part.id in seen_part_ids:
            result.add(f"Duplicate part id: '{part.id}'.")
        seen_part_ids.add(part.id)

    # Assignments must reference only things that still exist. This matters
    # if a project was reloaded against an updated source JSON where a part,
    # region, or variant was removed.
    valid_columns: set[ColumnKey] = set(project.all_columns())
    valid_part_ids: set[str] = set(project.part_ids())

    for part_id, cols in project.assignments.items():
        if part_id not in valid_part_ids:
            result.add(f"Assignment references unknown part id: '{part_id}'.")
            continue
        for col in cols:
            if col not in valid_columns:
                result.add(
                    f"Part '{part_id}' has an assignment to an unknown "
                    f"region/variant: '{col.region} / {col.variant}'."
                )

    return result
