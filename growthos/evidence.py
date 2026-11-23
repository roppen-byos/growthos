"""Current-read provenance and shared bounded status response formatting."""

from datetime import datetime, timezone

from .contracts import MAX_FACTS, MonthlyData
from .errors import reject
from .metrics import TargetActualObservation, definitions

CURRENT_DATA_WARNING = "Current monthly data may be replaced by a later fetch; no historical versions are retained."
SOURCE_READ_WARNING = "Source-read timestamps describe query execution, not authoritative business-data completeness."
INDEPENDENT_READ_WARNING = "Independent warehouse reads are not a transactional warehouse view; retained months are fetched independently."
MISSING_TOTALS_WARNING = "Missing city observations or operands prevent complete totals; known_subtotal is not a total."
TIER_RECLASSIFIED_WARNING = "City tier assignments changed across compared months; cohorts use each city's latest observed tier in the pair."
TARGET_OPERANDS_WARNING = "Some target or actual operands are unavailable; cities with missing operands cannot qualify. Overall recorded FTU excludes null city FTU values."
TARGET_FACT_OPERANDS_WARNING = "Some target or actual operands are unavailable; missing city observations remain visible in facts. Overall recorded FTU excludes null city FTU values."
OVER_EXPLAINED_WARNING = (
    "Some scopes have a registration effect larger than the FTU miss (over 100 percent); "
    "better-than-target FTU relative to registrations partly offsets the miss and the remainder is positive."
)
NATIONAL_COVERAGE_WARNING = (
    "National diagnosis uses the supplied Grand Total target pair with observed non-null actuals for both "
    "metrics; observed FTU and registration city coverage can differ and need not reconcile with summed city effects."
)
POOLED_WARNING = (
    "Unplanned cities share one New launch target pair compared with their combined observed actuals; "
    "the pooled target is not an allocation, per-city target, shared budget or launch evidence."
)
TIER_EXCLUDED_WARNING = (
    "Tier contribution uses only cities with an explicit target whose stored tier matches the selected month; "
    "unplanned tier cities are excluded from the aggregate and are covered only by the separate pooled New launch block."
)
INVESTIGATION_UNDETERMINED_WARNING = (
    "Unavailable operands make some scopes undetermined; null targets and null actuals are never treated as zero."
)
FUNNEL_PROXY_WARNING = (
    "FE sessions, RR and Net in irf.facts cover the FTU-eligible cohort (activity on or before each customer's "
    "lifetime first completed platform ride plus Zero-Transaction customers with no completed ride yet, including "
    "same-day activity on the first-ride day); stage contributions apply FTU-eligible session/ride relative changes "
    "as hold-two-constant counterfactual proxies against the calendar-adjusted MoM Platform FTU dip. Counterfactual "
    "effects drop interaction terms, so stage percentages need not sum to 100%; the > 70% primary cutoff ranks "
    "> 20% contributors rather than forming an additive partition."
)
FUNNEL_CALENDAR_SCALING_WARNING = (
    "Volume counts for the longer month in a MoM pair are proportionally scaled to the shorter month's calendar length "
    "(average daily run-rate adjustment, not literal removal of the extra calendar day); conversion rates are unaffected."
)
FUNNEL_OVER_EXPLAINED_WARNING = (
    "Some funnel stages show an isolated counterfactual effect larger than the calendar-adjusted MoM FTU dip (over 100 percent); "
    "other factors partly offset the dip."
)
FUNNEL_NATIONAL_MATCHED_WARNING = (
    "National funnel diagnosis rolls up matched cities observed in both months per stage/metric so newly observed cities "
    "do not distort MoM movement; excluded cities are disclosed in coverage."
)
FUNNEL_UNDETERMINED_WARNING = (
    "Unavailable or nonpositive operands make some scopes or stages undetermined; null counts are never treated as zero."
)
CITY_PAGE_SIZE = 25


def target_actual_warning(actual: TargetActualObservation) -> str | None:
    count = actual.skipped_null_ftu_city_count
    if not count:
        return None
    cities = "1 city" if count == 1 else f"{count} cities"
    ir_only = actual.install_or_registration_only_city_count
    if count == ir_only:
        return (f"{cities} had null FTU data and only installs or registrations recorded; "
                "skipped from overall actual FTU, not treated as zero.")
    return (f"{cities} had null FTU data and were skipped from overall actual FTU, not treated as zero; "
            f"{ir_only} had only installs or registrations recorded and {count - ir_only} had other or no non-null metrics.")


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
        "next_city_offset": city_offset + CITY_PAGE_SIZE if city_offset + CITY_PAGE_SIZE < city_count else None,
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
    page = sorted(cities.items())[city_offset:city_offset + CITY_PAGE_SIZE]
    result = status_result(
        months=data["months"], month_metadata=data["month_metadata"], fact_count=len(data["facts"]),
        cities=[{"city_key": key, "city": city, "tier": tier} for key, (city, tier) in page],
        city_count=len(cities), tiers=sorted({tier for _, tier in cities.values()}),
        city_offset=city_offset, nonproduction=data["nonproduction"],
    )
    del result["fact_count"]
    return {
        **result, **provenance(dataset),
        "supported_operations": ["status", "compare", "rank", "funnel", "share-drivers", "warehouse-queries", "verify", "target-analysis", "target-facts", "investigate", "investigate-funnel"],
    }
