"""Strict aggregate-only current-month data contract; no approval or revision states."""

from __future__ import annotations

import hashlib
from importlib.resources import files
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from .errors import reject

VOLUMES = (
    "installs", "registrations", "platform_ftu", "platform_link_ftu",
    "platform_auto_ftu", "platform_cab_ftu",
)
RATIOS = {
    "i2r": ("registrations", "installs"),
    "r2n": ("platform_ftu", "registrations"),
    "link_share": ("platform_link_ftu", "platform_ftu"),
    "auto_share": ("platform_auto_ftu", "platform_ftu"),
    "cab_share": ("platform_cab_ftu", "platform_ftu"),
}
TIERS = ("T1", "T2", "T3")
SOURCES = ("installs", "registrations", "ftu")
BUSINESS_TIMEZONE = "Asia/Kolkata"
# Per fetch/read, not database retention limits.
MAX_FACTS = 20_000
MAX_MONTHS = 12
TOP_FIELDS = {"nonproduction", "months", "month_metadata", "facts"}
METADATA_FIELDS = {
    "month", "extracted_at", "business_timezone", "source_reads",
}
FACT_FIELDS = {"month", "city_key", "city", "tier", "source_cities", *VOLUMES}
REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}\Z")


def exact_fields(value: object, fields: set[str]) -> None:
    if not isinstance(value, dict) or set(value) != fields:
        reject("SCHEMA", "Unexpected or missing aggregate fields; raw field names and values withheld.")


def reference(value: object) -> bool:
    return isinstance(value, str) and REF.fullmatch(value) is not None


def text(value: object) -> bool:
    return isinstance(value, str) and 0 < len(value) <= 120 and value == value.strip() and all(c.isprintable() for c in value)


def month(value: object) -> date:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}", value):
        reject("MONTH", "Use an unambiguous calendar month in YYYY-MM format.")
    try:
        return date.fromisoformat(value + "-01")
    except ValueError:
        reject("MONTH", "Invalid calendar month.")


def next_month(value: date) -> date:
    if value.year == 9999 and value.month == 12:
        reject("MONTH", "Calendar month exceeds the supported date range.")
    return date(value.year + (value.month == 12), value.month % 12 + 1, 1)


def previous_month(value: str) -> str:
    """The immediately preceding calendar month, never a skipped or substituted one."""
    current = month(value)
    if current.year == 1 and current.month == 1:
        reject("MONTH", "Calendar month precedes the supported date range.")
    if current.month > 1:
        return f"{current.year:04d}-{current.month - 1:02d}"
    return f"{current.year - 1:04d}-12"


def month_range(start: str, end: str) -> list[str]:
    current, last = month(start), month(end)
    if current > last:
        reject("PERIOD", "The start month must not follow the end month.")
    result = []
    while current <= last:
        result.append(f"{current.year:04d}-{current.month:02d}")
        if len(result) > MAX_MONTHS:
            reject("BOUNDS", "Select at most twelve calendar months per fetch or read.")
        if current == last:
            break
        current = next_month(current)
    return result


def timestamp(value: object) -> datetime:
    if not isinstance(value, str) or len(value) > 40:
        reject("TIMESTAMP", "Expected an ISO timestamp with an explicit UTC offset.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        reject("TIMESTAMP", "Expected an ISO timestamp with an explicit UTC offset.")
    if parsed.tzinfo is None:
        reject("TIMESTAMP", "Naive timestamps are not accepted.")
    return parsed.astimezone(timezone.utc)


def canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()


def _digest(rows: list[dict]) -> str:
    """SHA-256 of rows in the exact order given; callers must pre-sort canonically."""
    return hashlib.sha256(canonical_json(rows)).hexdigest()


def checksum(rows: list[dict], key=lambda r: (r["month"], r["city_key"])) -> str:
    """Canonical SHA-256 of unordered rows, sorted by key first."""
    return _digest(sorted(rows, key=key))


def reporting_schema_sql() -> str:
    return files("growthos").joinpath("sql/001_reporting.sql").read_text(encoding="utf-8")


@dataclass(frozen=True)
class MonthlyData:
    """Detached bounded observations, not a stored or recoverable historical version."""

    payload: bytes
    result_checksum: str

    @property
    def data(self) -> dict:
        return json.loads(self.payload)


def validate_metadata(raw: object, *, now: datetime | None = None) -> None:
    exact_fields(raw, METADATA_FIELDS)
    value = month(raw["month"])
    extracted = timestamp(raw["extracted_at"])
    if extracted > (now or datetime.now(timezone.utc)):
        reject("TIMESTAMP", "Extraction time must not be in the future.")
    if raw["business_timezone"] != BUSINESS_TIMEZONE:
        reject("TIMEZONE", f"Business timezone must be {BUSINESS_TIMEZONE}.")
    zone = ZoneInfo(BUSINESS_TIMEZONE)
    if value >= extracted.astimezone(zone).date().replace(day=1):
        reject("INCOMPLETE_MONTH", "Only ended calendar months may be fetched or reported.")
    reads = raw["source_reads"]
    if not isinstance(reads, list) or len(reads) != len(SOURCES):
        reject("PROVENANCE", "Each month needs Installs, Registrations and FTU read evidence.")
    seen = set()
    for read in reads:
        exact_fields(read, {"source", "month", "started_at", "completed_at"})
        if read["source"] not in SOURCES or read["source"] in seen or read["month"] != raw["month"]:
            reject("PROVENANCE", "Invalid or duplicate source read evidence.")
        seen.add(read["source"])
        started, completed = timestamp(read["started_at"]), timestamp(read["completed_at"])
        if started > completed or completed > extracted:
            reject("PROVENANCE", "Source read times must be ordered and no later than extraction completion.")


def validate_data(raw: object, *, now: datetime | None = None) -> MonthlyData:
    exact_fields(raw, TOP_FIELDS)
    if type(raw["nonproduction"]) is not bool:
        reject("SCHEMA", "Invalid production label.")
    months = raw["months"]
    if not isinstance(months, list) or not 1 <= len(months) <= MAX_MONTHS:
        reject("BOUNDS", "Select one to twelve stored calendar months.")
    for value in months:
        month(value)
    if sorted(set(months)) != months:
        reject("PERIOD", "Months must be ordered and unique.")
    metadata = raw["month_metadata"]
    if not isinstance(metadata, list) or len(metadata) != len(months):
        reject("PROVENANCE", "Each month needs exactly one metadata record.")
    for item in metadata:
        validate_metadata(item, now=now)
    if sorted(item["month"] for item in metadata) != months:
        reject("PROVENANCE", "Metadata must describe each selected month exactly once.")
    facts = raw["facts"]
    if not isinstance(facts, list) or not 1 <= len(facts) <= MAX_FACTS:
        reject("BOUNDS", "Aggregate result is empty or exceeds the per-read fact limit; narrow the range.")
    grain, lineage_owner, observed = set(), {}, set()
    for row in facts:
        exact_fields(row, FACT_FIELDS)
        if row["month"] not in months:
            reject("GRAIN", "Fact month lies outside the selected range.")
        if not reference(row["city_key"]) or not text(row["city"]) or row["tier"] not in TIERS:
            reject("CITY", "Invalid canonical city or tier; unmapped cities must use T3.")
        lineage = row["source_cities"]
        if not isinstance(lineage, list) or not 1 <= len(lineage) <= 100 or not all(text(x) for x in lineage):
            reject("CITY", "Every city requires bounded source-city lineage.")
        if len(set(lineage)) != len(lineage):
            reject("CITY", "Duplicate source-city lineage.")
        key = row["month"], row["city_key"]
        if key in grain:
            reject("DUPLICATE_GRAIN", "Duplicate month/city; do not sum potentially overlapping distinct counts.")
        for source_city in lineage:
            source_key = row["month"], source_city
            if source_key in lineage_owner and lineage_owner[source_key] != row["city_key"]:
                reject("CITY", "A source city cannot map to multiple canonical cities within a month.")
            lineage_owner[source_key] = row["city_key"]
        grain.add(key)
        observed.add(row["month"])
        for metric in VOLUMES:
            value = row[metric]
            if value is not None and (type(value) is not int or not 0 <= value <= 2**63 - 1):
                reject("COUNT", "Facts must be nonnegative 64-bit integer counts or unavailable.")
    if observed != set(months):
        reject("COVERAGE", "Each month needs observations; an empty query is not evidence of zero.")
    norm_facts = sorted(
        ({**r, "source_cities": sorted(r["source_cities"])} for r in facts),
        key=lambda r: (r["month"], r["city_key"]),
    )
    norm_metadata = sorted(
        ({**m, "source_reads": sorted((dict(sr) for sr in m["source_reads"]), key=lambda sr: sr["source"])} for m in metadata),
        key=lambda m: m["month"],
    )
    normalized = {
        "nonproduction": raw["nonproduction"],
        "months": list(months),
        "month_metadata": norm_metadata,
        "facts": norm_facts,
    }
    return MonthlyData(canonical_json(normalized), _digest(norm_facts))
