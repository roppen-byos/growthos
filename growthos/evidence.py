"""Current-read provenance and shared bounded status response formatting."""

from datetime import datetime, timezone

from .contracts import MAX_FACTS, MonthlyData
from .errors import reject
from .metrics import definitions

CURRENT_DATA_WARNING = "Current monthly data may be replaced by a later fetch; no historical versions are retained."
SOURCE_READ_WARNING = "Source-read timestamps describe query execution, not authoritative business-data completeness."
INDEPENDENT_READ_WARNING = "Independent warehouse reads are not a transactional warehouse view; retained months are fetched independently."
MISSING_TOTALS_WARNING = "Missing city observations or operands prevent complete totals; known_subtotal is not a total."
TIER_RECLASSIFIED_WARNING = "City tier assignments changed across compared months; cohorts use each city's latest observed tier in the pair."


def provenance(dataset: MonthlyData, *, now: datetime | None = None, data: dict | None = None) -> dict:
    parsed = dataset.data if data is None else data
    current = now or datetime.now(timezone.utc)
    return {
        "nonproduction": parsed["nonproduction"],
        "result_checksum": dataset.result_checksum,
        "read_at": current.isoformat(),
        "months": parsed["months"],
        "month_metadata": parsed["month_metadata"],
        "warnings": (["NONPRODUCTION_SYNTHETIC_DATA"] if parsed["nonproduction"] else []) + [
            CURRENT_DATA_WARNING, SOURCE_READ_WARNING, INDEPENDENT_READ_WARNING,
        ],
    }


def validate_city_offset(city_offset: int) -> None:
    if type(city_offset) is not int or not 0 <= city_offset <= 2**31 - 1:
        reject("BOUNDS", "City offset must be a nonnegative bounded integer.")


def status_result(*, months: list[str], month_metadata: list[dict], fact_count: int,
                  cities: list[dict], city_count: int, tiers: list[str],
                  city_offset: int = 0, nonproduction: bool = False) -> dict:
    """Format an already-bounded catalog page; never fetch or retain all facts."""
    validate_city_offset(city_offset)
    return {
        "operation": "status", "nonproduction": nonproduction,
        "available_months": months, "month_metadata": month_metadata, "fact_count": fact_count,
        "metrics": definitions(), "tiers": tiers, "city_count": city_count, "cities": cities,
        "city_offset": city_offset,
        "next_city_offset": city_offset + 25 if city_offset + 25 < city_count else None,
        "omitted_city_count": max(0, city_count - len(cities)),
        "narrowing": "Read another city catalog page with status --city-offset OFFSET.",
        "warnings": [CURRENT_DATA_WARNING, SOURCE_READ_WARNING],
    }


def catalog(dataset: MonthlyData, city_offset: int = 0) -> dict:
    """Compatibility interface for an in-memory catalog, not the reader CLI path."""
    if type(city_offset) is not int or not 0 <= city_offset <= MAX_FACTS:
        reject("BOUNDS", "City catalog offset must be between zero and the fact limit.")
    data = dataset.data
    cities = {r["city_key"]: (r["city"], r["tier"]) for r in data["facts"]}
    page = sorted(cities.items())[city_offset:city_offset + 25]
    result = status_result(
        months=data["months"], month_metadata=data["month_metadata"], fact_count=len(data["facts"]),
        cities=[{"city_key": key, "city": city, "tier": tier} for key, (city, tier) in page],
        city_count=len(cities), tiers=sorted({tier for _, tier in cities.values()}),
        city_offset=city_offset, nonproduction=data["nonproduction"],
    )
    del result["fact_count"]
    return {
        **result, **provenance(dataset),
        "supported_operations": ["status", "compare", "rank", "funnel", "share-drivers", "verify"],
    }
