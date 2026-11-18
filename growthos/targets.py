"""Detached PFTU plan contract and integrity checks, independent of actual months."""

from dataclasses import dataclass
from datetime import datetime, timezone
from importlib.resources import files
import json
import re

from .classification import normalize_city_key
from .contracts import MAX_FACTS, _digest, canonical_json, exact_fields, month, reference, text, timestamp
from .errors import reject

TARGET_FIELDS = {"month", "kind", "city_key", "city", "target", "source_targets"}
TARGET_DB_FIELDS = tuple(sorted(TARGET_FIELDS | {
    "source_checksum", "imported_at", "month_checksum", "month_row_count",
}))
SPECIAL_LABELS = {"overall": "Grand Total", "new_launch": "New launch"}


def target_schema_sql() -> str:
    return files("growthos").joinpath("sql/002_pftu_targets.sql").read_text(encoding="utf-8")


def target_count(value: object) -> bool:
    return value is None or (type(value) is int and 0 <= value <= 2**63 - 1)


@dataclass(frozen=True)
class TargetMonth:
    payload: bytes
    result_checksum: str

    @property
    def data(self) -> dict:
        return json.loads(self.payload)


def validate_targets(raw: object, *, now: datetime | None = None) -> TargetMonth:
    exact_fields(raw, {"month", "rows", "source_checksum", "imported_at"})
    month(raw["month"])  # Future plan months are permitted; actuals are ended-month only.
    digest = raw["source_checksum"]
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        reject("TARGETS", "Targets require a valid source checksum.")
    imported = timestamp(raw["imported_at"])
    if imported > (now or datetime.now(timezone.utc)):
        reject("TIMESTAMP", "Target import time must not be in the future.")
    rows = raw["rows"]
    if not isinstance(rows, list) or not 3 <= len(rows) <= MAX_FACTS:
        reject("BOUNDS", "Targets require bounded city, fallback and overall records.")
    seen, specials = set(), set()
    for row in rows:
        exact_fields(row, TARGET_FIELDS)
        kind, key = row["kind"], row["city_key"]
        if row["month"] != raw["month"] or kind not in ("city", *SPECIAL_LABELS):
            reject("TARGETS", "Invalid target month or record kind.")
        if not text(row["city"]) or not target_count(row["target"]):
            reject("TARGETS", "Targets must have bounded labels and nonnegative 64-bit counts or null.")
        if kind == "city":
            if not reference(key) or normalize_city_key(key) != key:
                reject("TARGETS", "Invalid canonical target city.")
        elif key != "" or row["city"] != SPECIAL_LABELS[kind]:
            reject("TARGETS", "Overall and fallback targets must be distinct from city targets.")
        else:
            specials.add(kind)
        grain = kind, key
        if grain in seen:
            reject("DUPLICATE_GRAIN", "Duplicate canonical target; resolve the source rows explicitly.")
        seen.add(grain)
        parts = row["source_targets"]
        if not isinstance(parts, list) or not 1 <= len(parts) <= 100:
            reject("TARGETS", "Targets require bounded original target components.")
        sources = set()
        for part in parts:
            exact_fields(part, {"city", "target"})
            if not text(part["city"]) or not target_count(part["target"]):
                reject("TARGETS", "Invalid source target component.")
            source = part["city"].casefold()
            if source in sources:
                reject("TARGETS", "Duplicate source target component.")
            sources.add(source)
            if kind == "city" and normalize_city_key(part["city"]) != key:
                reject("TARGETS", "Source target does not belong to its canonical city.")
            if kind != "city" and part["city"] != row["city"]:
                reject("TARGETS", "Invalid overall or fallback source component.")
        combined = None if any(p["target"] is None for p in parts) else sum(p["target"] for p in parts)
        if row["target"] != combined:
            reject("TARGETS", "Combined target differs from its source components.")
    if specials != set(SPECIAL_LABELS) or not any(r["kind"] == "city" for r in rows):
        reject("TARGETS", "Each target month needs cities, one New launch and one Grand Total.")
    normalized = sorted(
        ({**r, "source_targets": sorted(r["source_targets"], key=lambda p: p["city"])} for r in rows),
        key=lambda r: (r["kind"], r["city_key"]),
    )
    return TargetMonth(canonical_json({**raw, "rows": normalized, "imported_at": imported.isoformat()}),
                       _digest(normalized))


def targets_from_records(records: list[dict]) -> TargetMonth:
    """Validate one bounded SQL result, including redundant per-month integrity evidence."""
    if not records:
        reject("TARGETS_UNAVAILABLE", "Targets are unavailable for the requested month; no substitute was selected.")
    if len(records) > MAX_FACTS:
        reject("BOUNDS", "Too many target records for one month.")
    for row in records:
        exact_fields(row, set(TARGET_DB_FIELDS))
    first = records[0]
    metadata = ("month", "source_checksum", "imported_at", "month_checksum", "month_row_count")
    if any(any(row[k] != first[k] for k in metadata) for row in records):
        reject("STORE_INTEGRITY", "Target records disagree on their month integrity evidence.")
    if type(first["month_row_count"]) is not int or len(records) != first["month_row_count"]:
        reject("STORE_INTEGRITY", "Stored target month count is invalid.")
    result = validate_targets({
        "month": first["month"], "source_checksum": first["source_checksum"],
        "imported_at": first["imported_at"],
        "rows": [{k: row[k] for k in TARGET_FIELDS} for row in records],
    })
    if result.result_checksum != first["month_checksum"]:
        reject("STORE_INTEGRITY", "Stored target month count or checksum is invalid.")
    return result
