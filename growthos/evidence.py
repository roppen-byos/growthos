"""Current-read provenance and shared bounded status response formatting."""

import calendar
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .classification import is_unattributed
from .contracts import BUSINESS_TIMEZONE, MAX_FACTS, MonthlyData, calendar_day
from .errors import reject
from .metrics import TargetActualObservation, definitions

CURRENT_DATA_WARNING = "Current monthly data may be replaced by a later fetch; no historical versions are retained."
SOURCE_READ_WARNING = "Source-read timestamps describe query execution, not authoritative business-data completeness."
INDEPENDENT_READ_WARNING = "Independent warehouse reads are not a transactional warehouse view; retained months are fetched independently."
MISSING_TOTALS_WARNING = "Missing city observations or operands prevent complete totals; known_subtotal is not a total."
TIER_RECLASSIFIED_WARNING = "City tier assignments changed across compared months; cohorts use each city's latest observed tier in the pair."
UNATTRIBUTED_SCOPE_WARNING = (
    "city_not_found is unattributed geography (location missing, so it could belong to any tier), "
    "not a city or a T3 city; it is included only in national/overall totals and excluded from tier, "
    "city-level, and New launch pool results."
)
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
    "Some funnel stages or services show an isolated effect larger than the dip (over 100 percent); "
    "other factors partly offset the dip."
)
FUNNEL_NATIONAL_MATCHED_WARNING = (
    "National funnel diagnosis rolls up matched cities observed in both months per stage/metric so newly observed cities "
    "do not distort MoM movement; excluded cities are disclosed in coverage."
)
FUNNEL_UNDETERMINED_WARNING = (
    "Unavailable or nonpositive operands make some scopes, stages or services undetermined; null counts are never treated as zero."
)
CITY_PAGE_SIZE = 25


LOW_COVERAGE_DAYS = 7
MTD_BASIS_TEXT = {
    "raw": ("counts and count deltas here are raw month-to-date observations, not full-month totals; ratios and "
            "shares are month-to-date values. Period commands report CLI linear whole-month estimates unless run "
            "with --raw-month-to-date."),
    "estimate": ("counts are CLI linear whole-month estimates (month-to-date count × {scale}, unrounded; round only "
                 "for display) and every delta, growth, ranking and threshold uses them; the scale cancels in ratios "
                 "and shares, so those equal month-to-date values. Installs and Registrations are monthly distinct "
                 "counts, so their estimates overstate. Results are estimates, not final."),
    "daily_rate": ("funnel comparisons treat its {covered} covered days as the month length in the calendar "
                   "adjustment, an exact daily run-rate comparison; scaled counts and changes (`*_scaled`, "
                   "`ftu_change`) are in `reference_days` units, not whole-month counts, so cite percentages, shares "
                   "of the dip and contributions. Conversion rates use raw month-to-date counts. Results are "
                   "estimates, not final."),
}


def month_to_date(month_metadata: list[dict], *, now: datetime | None = None) -> list[dict]:
    """Describe stored months that end at a landed cutoff instead of the calendar month end."""
    zone = ZoneInfo(BUSINESS_TIMEZONE)
    current_month = (now or datetime.now(timezone.utc)).astimezone(zone).date().replace(day=1)
    result = []
    for meta in month_metadata:
        if "cutoff_date" not in meta:
            continue
        cutoff = calendar_day(meta["cutoff_date"])
        days_in_month = calendar.monthrange(cutoff.year, cutoff.month)[1]
        result.append({
            "month": meta["month"], "cutoff_date": meta["cutoff_date"],
            "days_covered": cutoff.day, "days_in_month": days_in_month,
            "linear_scale": f"{days_in_month}/{cutoff.day}",
            "linear_scale_factor": round(days_in_month / cutoff.day, 6),
            "month_ended": cutoff.replace(day=1) < current_month,
        })
    return result


def month_to_date_warnings(partial: list[dict], basis: str = "raw") -> list[str]:
    """One warning per month-to-date month, worded for how this output used its counts."""
    warnings = []
    for item in partial:
        detail = MTD_BASIS_TEXT[basis].format(scale=item["linear_scale"], covered=item["days_covered"])
        warnings.append(f"{item['month']} is month-to-date through {item['cutoff_date']} ({item['days_covered']} of "
                        f"{item['days_in_month']} days): {detail}")
        if item["days_covered"] < LOW_COVERAGE_DAYS:
            warnings.append(f"{item['month']} covers only {item['days_covered']} days; its linear scale "
                            f"{item['linear_scale']} makes estimates highly uncertain.")
        if item["month_ended"]:
            warnings.append(f"{item['month']} has ended but is still stored month-to-date; refresh it as a complete "
                            "month for final figures.")
    return warnings


def apply_month_to_date_basis(result: dict, partial: list[dict], basis: str, stored_checksum: str) -> dict:
    """Reword month-to-date warnings for the basis used and keep the stored-data checksum."""
    if not partial:
        return result
    prefixes = tuple(f"{item['month']} " for item in partial)
    kept = [w for w in result.get("warnings", []) if not (w.startswith(prefixes) and (
        "is month-to-date through" in w or " covers only " in w or "still stored month-to-date" in w))]
    result["warnings"] = kept + month_to_date_warnings(partial, basis)
    result["month_to_date"] = [{**item, "basis": basis} for item in partial]
    result["result_checksum"] = stored_checksum
    return result


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
    partial = month_to_date(parsed["month_metadata"], now=current)
    result = {
        "nonproduction": parsed["nonproduction"],
        "result_checksum": dataset.result_checksum,
        "read_at": current.isoformat(),
        "months": parsed["months"],
        "month_metadata": parsed["month_metadata"],
        "warnings": (["NONPRODUCTION_SYNTHETIC_DATA"] if parsed["nonproduction"] else []) + [
            CURRENT_DATA_WARNING, SOURCE_READ_WARNING, INDEPENDENT_READ_WARNING,
        ] + month_to_date_warnings(partial),
    }
    if partial:
        result["month_to_date"] = partial
    return result


def validate_city_offset(city_offset: int) -> None:
    if type(city_offset) is not int or not 0 <= city_offset <= 2**31 - 1:
        reject("BOUNDS", "City offset must be a nonnegative bounded integer.")


def status_result(*, months: list[str], month_metadata: list[dict], fact_count: int,
                  cities: list[dict], city_count: int, tiers: list[str],
                  city_offset: int = 0, nonproduction: bool = False) -> dict:
    """Format an already-bounded catalog page; never fetch or retain all facts."""
    validate_city_offset(city_offset)
    partial = month_to_date(month_metadata)
    return {
        **({"month_to_date": [{**item, "basis": "raw"} for item in partial]} if partial else {}),
        "operation": "status", "nonproduction": nonproduction,
        "available_months": months, "month_metadata": month_metadata, "fact_count": fact_count,
        "metrics": definitions(), "tiers": tiers, "city_count": city_count, "cities": cities,
        "city_offset": city_offset,
        "next_city_offset": city_offset + CITY_PAGE_SIZE if city_offset + CITY_PAGE_SIZE < city_count else None,
        "omitted_city_count": max(0, city_count - len(cities)),
        "narrowing": "Read another city catalog page with status --city-offset OFFSET.",
        "warnings": [CURRENT_DATA_WARNING, SOURCE_READ_WARNING, *month_to_date_warnings(partial)],
    }


def catalog(dataset: MonthlyData, city_offset: int = 0) -> dict:
    """Compatibility interface for an in-memory catalog, not the reader CLI path."""
    if type(city_offset) is not int or not 0 <= city_offset <= MAX_FACTS:
        reject("BOUNDS", "City catalog offset must be between zero and the fact limit.")
    data = dataset.data
    cities = {
        r["city_key"]: (r["city"], r["tier"])
        for r in data["facts"]
        if not is_unattributed(r["city_key"])
    }
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
