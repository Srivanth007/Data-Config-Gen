"""
Core data models for the Vehicle Variant Configuration Generator.

This module has NO Qt dependency on purpose — it must be usable and
testable completely independently of the UI layer.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable


@dataclass(frozen=True, eq=True)
class ColumnKey:
    """
    Uniquely identifies one matrix column, i.e. one (region, variant) pair.

    Using a combined key (rather than the variant name alone) avoids bugs
    where the same variant name (e.g. "Premium") exists in multiple regions.
    """
    region: str
    variant: str

    def __str__(self) -> str:  # pragma: no cover - convenience only
        return f"{self.region} / {self.variant}"


@dataclass
class Region:
    name: str
    variants: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        # Defensive copy so callers can't accidentally alias a shared list.
        self.variants = list(self.variants)


@dataclass
class Part:
    id: str
    name: str


@dataclass
class Project:
    """
    The single source of truth for the whole application.

    - `regions` and `parts` are the master data (originally imported from
      the source JSON).
    - `assignments` maps part_id -> set of ColumnKey that part is available
      for. This is the *only* place assignment state lives; the UI never
      keeps a parallel copy.
    """
    regions: list[Region] = field(default_factory=list)
    parts: list[Part] = field(default_factory=list)
    assignments: dict[str, set[ColumnKey]] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Column / lookup helpers
    # ------------------------------------------------------------------
    def all_columns(self) -> list[ColumnKey]:
        """
        Flattened, ordered list of (region, variant) columns.

        This order is used consistently everywhere: the Qt matrix model,
        the Excel export, and the project save file. Never re-derive this
        order differently in more than one place.
        """
        return [
            ColumnKey(region.name, variant)
            for region in self.regions
            for variant in region.variants
        ]

    def region_names(self) -> list[str]:
        return [r.name for r in self.regions]

    def get_region(self, name: str) -> Region | None:
        for r in self.regions:
            if r.name == name:
                return r
        return None

    def part_ids(self) -> list[str]:
        return [p.id for p in self.parts]

    def get_part(self, part_id: str) -> Part | None:
        for p in self.parts:
            if p.id == part_id:
                return p
        return None

    # ------------------------------------------------------------------
    # Assignment helpers — the ONLY mutation path for the matrix.
    # ------------------------------------------------------------------
    def is_assigned(self, part_id: str, col: ColumnKey) -> bool:
        return col in self.assignments.get(part_id, set())

    def set_assigned(self, part_id: str, col: ColumnKey, value: bool) -> None:
        cols = self.assignments.setdefault(part_id, set())
        if value:
            cols.add(col)
        else:
            cols.discard(col)
            if not cols:
                # keep the dict tidy; not required, but avoids empty-set clutter
                del self.assignments[part_id]

    def toggle_assigned(self, part_id: str, col: ColumnKey) -> bool:
        """Flip the assignment and return the new state."""
        new_value = not self.is_assigned(part_id, col)
        self.set_assigned(part_id, col, new_value)
        return new_value

    def assign_many(self, part_ids: Iterable[str], col: ColumnKey, value: bool = True) -> None:
        for pid in part_ids:
            self.set_assigned(pid, col, value)

    def assigned_columns_for_part(self, part_id: str) -> set[ColumnKey]:
        return set(self.assignments.get(part_id, set()))

    # ------------------------------------------------------------------
    # Region / variant management — these are now USER INPUT within the
    # app (added, renamed, removed via the Regions & Variants panel),
    # NOT sourced from the imported JSON. The JSON import only ever
    # supplies `parts`. All of these raise ValueError with a clear
    # message on invalid input; the UI layer catches that and shows it.
    # ------------------------------------------------------------------
    def add_region(self, name: str) -> Region:
        name = (name or "").strip()
        if not name:
            raise ValueError("Region name cannot be empty.")
        if self.get_region(name) is not None:
            raise ValueError(f"Region '{name}' already exists.")
        region = Region(name=name, variants=[])
        self.regions.append(region)
        return region

    def rename_region(self, old_name: str, new_name: str) -> None:
        new_name = (new_name or "").strip()
        if not new_name:
            raise ValueError("Region name cannot be empty.")
        region = self.get_region(old_name)
        if region is None:
            raise ValueError(f"Region '{old_name}' does not exist.")
        if new_name != old_name and self.get_region(new_name) is not None:
            raise ValueError(f"Region '{new_name}' already exists.")

        region.name = new_name
        # Every assignment ColumnKey carrying the old region name must be
        # rewritten, or those assignments would silently become "unknown
        # region" and get flagged/dropped by validation.
        for part_id, cols in list(self.assignments.items()):
            updated = {
                ColumnKey(new_name, c.variant) if c.region == old_name else c
                for c in cols
            }
            self.assignments[part_id] = updated

    def remove_region(self, name: str) -> None:
        region = self.get_region(name)
        if region is None:
            raise ValueError(f"Region '{name}' does not exist.")
        self.regions.remove(region)
        # Prune every assignment that pointed at this region — those
        # columns no longer exist.
        for part_id, cols in list(self.assignments.items()):
            remaining = {c for c in cols if c.region != name}
            if remaining:
                self.assignments[part_id] = remaining
            else:
                del self.assignments[part_id]

    def add_variant(self, region_name: str, variant_name: str) -> None:
        variant_name = (variant_name or "").strip()
        if not variant_name:
            raise ValueError("Variant name cannot be empty.")
        region = self.get_region(region_name)
        if region is None:
            raise ValueError(f"Region '{region_name}' does not exist.")
        if variant_name in region.variants:
            raise ValueError(f"Region '{region_name}' already has a variant named '{variant_name}'.")
        region.variants.append(variant_name)

    def rename_variant(self, region_name: str, old_variant: str, new_variant: str) -> None:
        new_variant = (new_variant or "").strip()
        if not new_variant:
            raise ValueError("Variant name cannot be empty.")
        region = self.get_region(region_name)
        if region is None:
            raise ValueError(f"Region '{region_name}' does not exist.")
        if old_variant not in region.variants:
            raise ValueError(f"Region '{region_name}' has no variant named '{old_variant}'.")
        if new_variant != old_variant and new_variant in region.variants:
            raise ValueError(f"Region '{region_name}' already has a variant named '{new_variant}'.")

        idx = region.variants.index(old_variant)
        region.variants[idx] = new_variant

        old_key = ColumnKey(region_name, old_variant)
        new_key = ColumnKey(region_name, new_variant)
        for part_id, cols in list(self.assignments.items()):
            if old_key in cols:
                cols.discard(old_key)
                cols.add(new_key)

    def remove_variant(self, region_name: str, variant_name: str) -> None:
        region = self.get_region(region_name)
        if region is None:
            raise ValueError(f"Region '{region_name}' does not exist.")
        if variant_name not in region.variants:
            raise ValueError(f"Region '{region_name}' has no variant named '{variant_name}'.")

        region.variants.remove(variant_name)
        # Prune assignments pointing at this now-deleted column.
        col = ColumnKey(region_name, variant_name)
        for part_id, cols in list(self.assignments.items()):
            if col in cols:
                cols.discard(col)
                if not cols:
                    del self.assignments[part_id]

    def replace_parts(self, new_parts: list[Part]) -> None:
        """
        Used when (re-)importing the parts JSON. Replaces the parts list
        wholesale and prunes any assignments referencing part ids that no
        longer exist, while leaving regions/variants (user-defined) and
        assignments for still-existing parts untouched.
        """
        self.parts = list(new_parts)
        valid_ids = {p.id for p in self.parts}
        for part_id in list(self.assignments.keys()):
            if part_id not in valid_ids:
                del self.assignments[part_id]
