"""Periods, canonical city matching, comparison cohorts and target fact selection."""

from dataclasses import dataclass
from datetime import datetime, timezone
from fractions import Fraction
from zoneinfo import ZoneInfo

from .classification import normalize_city_key
from .contracts import BUSINESS_TIMEZONE, TIERS, MonthlyData, month, month_range, next_month
from .errors import reject


CityPair = tuple[dict, dict | None, dict | None]


@dataclass(frozen=True)
class PreparedComparison:
    """Detached pair context; all_pairs stays unfiltered for overall evidence."""

    data: dict
    previous: str
    latest: str
    period_rows: list[dict]
    all_pairs: list[CityPair]
    pairs: list[CityPair]

    @property
    def label(self) -> str:
        return "MoM" if next_month(month(self.previous)) == month(self.latest) else "period comparison"

    @property
    def reclassified(self) -> bool:
        return _reclassified(self.pairs)


def prepare_comparison(
    dataset: MonthlyData,
    previous: str | None = None,
    latest: str | None = None,
    *,
    tier_filter: str | None = None,
    cities: list[str] | None = None,
) -> PreparedComparison:
    """Select periods and pair once; tier_filter is already normalized by the caller."""
    data = dataset.data
    prev, curr = select_pair(dataset, previous, latest, data=data)
    rows = [r for r in data["facts"] if r["month"] in (prev, curr)]
    all_pairs = list(city_pairs(rows, prev, curr))
    pairs = _filter_pairs(all_pairs, tier_filter=tier_filter, cities=cities)
    return PreparedComparison(data, prev, curr, rows, all_pairs, pairs)


def select_pair(dataset: MonthlyData, previous: str | None, latest: str | None, *, data: dict | None = None) -> tuple[str, str]:
    if (previous is None) != (latest is None):
        reject("PERIOD", "Supply both previous and latest months, or neither.")
    available = (dataset.data if data is None else data)["months"]
    if previous is None:
        if len(available) < 2:
            reject("COVERAGE", "Two stored months are required; no substitute period is available.")
        return available[-2], available[-1]
    if month(previous) >= month(latest):
        reject("PERIOD", "The previous month must precede the latest month.")
    if any(m not in available for m in (previous, latest)):
        reject("COVERAGE", "Requested months are unavailable; no substitute was selected.")
    return previous, latest


def normalize_tier(tier: str | None) -> str | None:
    if tier is None:
        return None
    cleaned = tier.strip().upper()
    if cleaned not in TIERS:
        reject("ARGUMENTS", "Tier must be one of T1, T2, or T3.")
    return cleaned


def validate_top(top: int, limit: int = 100) -> int:
    if type(top) is not int or not 1 <= top <= limit:
        reject("BOUNDS", f"Top limit must be an integer between 1 and {limit}.")
    return top


def _require_ended(value: str, *, now=None, operation: str = "Target analysis") -> None:
    selected = month(value)
    zone = ZoneInfo(BUSINESS_TIMEZONE)
    current = (now or datetime.now(timezone.utc)).astimezone(zone)
    if selected >= current.date().replace(day=1):
        reject("INCOMPLETE_MONTH", f"{operation} requires an ended calendar month in {BUSINESS_TIMEZONE}.")


def validate_target_request(value: str, mode: str, offset: int, limit: int, *, now=None) -> None:
    _require_ended(value, now=now)
    if mode not in ("top", "all"):
        reject("ARGUMENTS", "Target mode must be top or all.")
    if type(offset) is not int or not 0 <= offset <= 2**31 - 1:
        reject("BOUNDS", "Target page offset must be a nonnegative bounded integer.")
    if type(limit) is not int or not 1 <= limit <= 100:
        reject("BOUNDS", "Target page limit must be between 1 and 100; selection is computed before paging.")


TARGET_ATTAINMENTS = ("all", "above", "at", "below", "unavailable")
TARGET_FACT_SORTS = ("city", "target", "actual", "shortfall", "shortfall-pct")

INVESTIGATION_MAX_CITIES = 25
INVESTIGATION_MAX_GROUPS = 5
INVESTIGATION_MAX_GROUP_CITIES = 25
INVESTIGATION_MAX_BLOCKS = 240


def validate_target_facts_request(value: str, attainment: str, sort_by: str,
                                  descending: bool, offset: int, limit: int) -> None:
    validate_target_request(value, "all", offset, limit)
    if attainment not in TARGET_ATTAINMENTS:
        reject("ARGUMENTS", "Target attainment must be all, above, at, below or unavailable.")
    if sort_by not in TARGET_FACT_SORTS or type(descending) is not bool:
        reject("ARGUMENTS", "Use a reviewed target fact sort and boolean direction; run target-facts --help.")


def parse_investigation_group(value: str) -> tuple[str, list[str]]:
    """Parse one explicit Label:City,City group; membership is never inferred."""
    label, separator, members = (value or "").partition(":")
    label, members = label.strip(), members.strip()
    if not separator or not label or len(label) > 40:
        reject("ARGUMENTS", 'Each --group needs a label and a colon, for example --group "South:Trivandrum,Kochi".')
    names = [item.strip() for item in members.split(",")]
    if not 2 <= len(names) <= INVESTIGATION_MAX_GROUP_CITIES or any(not name for name in names):
        reject("BOUNDS", f"A group needs between 2 and {INVESTIGATION_MAX_GROUP_CITIES} named cities.")
    folded = [name.casefold() for name in names]
    if len(set(folded)) != len(folded):
        reject("ARGUMENTS", "A group cannot repeat a city.")
    return label, names


def validate_investigation_request(value: str, cities: list[str], groups: list[tuple[str, list[str]]],
                                  start: str | None, end: str | None, *, now=None) -> list[str]:
    """Bound the investigation selectors and resolve the requested month list."""
    if (start is None) != (end is None):
        reject("PERIOD", "Supply both --start and --end, or neither.")
    months = [value] if start is None else month_range(start, end)
    if value not in months:
        reject("PERIOD", "--month must lie inside the requested --start/--end range.")
    for item in months:
        _require_ended(item, now=now, operation="Investigation")
    if len(cities) > INVESTIGATION_MAX_CITIES:
        reject("BOUNDS", f"Request at most {INVESTIGATION_MAX_CITIES} cities; each city is reported separately.")
    if len(groups) > INVESTIGATION_MAX_GROUPS:
        reject("BOUNDS", f"Request at most {INVESTIGATION_MAX_GROUPS} explicit groups.")
    folded = [name.strip().casefold() for name in cities]
    if len(set(folded)) != len(folded):
        reject("ARGUMENTS", "Do not repeat a city; each named city is one block.")
    labels = [label.casefold() for label, _ in groups]
    if len(set(labels)) != len(labels):
        reject("ARGUMENTS", "Use a distinct label for each explicit group.")
    return months


def investigation_block_count(months: list[str], cities: list[str], groups: list,
                              tier: str | None, national: bool) -> int:
    per_month = int(bool(national)) + len(cities) + len(groups) + int(tier is not None)
    return per_month * len(months) + len(months)  # one pooled New launch block per month at most


def select_target_facts(rows: list[dict], *, cities: list[str] | None,
                        attainment: str, sort_by: str, descending: bool) -> list[dict]:
    """Filter computed facts and order before paging; missing sort values stay last."""
    wanted = match_cities(rows, cities) if cities else None
    selected = [r for r in rows if (wanted is None or r["city_key"] in wanted)
                and (attainment == "all" or r["attainment"] == attainment)]
    selected.sort(key=lambda r: (r["city"], r["city_key"]))
    if sort_by == "city":
        return list(reversed(selected)) if descending else selected
    field = sort_by.replace("-", "_")
    known = [r for r in selected if r[field] is not None]
    unavailable = [r for r in selected if r[field] is None]
    if sort_by == "shortfall-pct":
        known.sort(key=lambda r: Fraction(r["shortfall"], r["target"]), reverse=descending)
    else:
        known.sort(key=lambda r: r[field], reverse=descending)
    return known + unavailable


def match_cities(rows: list[dict], requested: list[str]) -> set[str]:
    by_key = {r["city_key"].casefold(): r["city_key"] for r in rows}
    by_name = {r["city"].casefold(): r["city_key"] for r in rows}
    by_norm = {normalize_city_key(r["city_key"]): r["city_key"] for r in rows}
    matched = set()
    for item in requested:
        raw = (item or "").strip()
        if not raw:
            continue
        folded = raw.casefold()
        norm = normalize_city_key(raw)
        key = by_key.get(folded) or by_name.get(folded) or by_norm.get(norm)
        if key is None:
            reject("CITY", "One or more requested cities were not found in the selected period.")
        matched.add(key)
    if not matched:
        reject("CITY", "Provide at least one non-empty city filter.")
    return matched


def city_pairs(rows: list[dict], previous: str, latest: str):
    """Pair selected city observations, retaining the last observation's metadata."""
    by_city: dict[str, dict[str, dict]] = {}
    for row in rows:
        by_city.setdefault(row["city_key"], {})[row["month"]] = row
    for month_map in by_city.values():
        prev_row, curr_row = month_map.get(previous), month_map.get(latest)
        metadata = curr_row if curr_row is not None else prev_row
        if metadata is not None:
            yield metadata, prev_row, curr_row


def paired_cohort(
    rows: list[dict],
    previous: str,
    latest: str,
    *,
    tier_filter: str | None = None,
    cities: list[str] | None = None,
) -> tuple[list[CityPair], bool]:
    """Pair cities first so tier filters/cohorts use each city's latest observed tier."""
    pairs = _filter_pairs(list(city_pairs(rows, previous, latest)), tier_filter=tier_filter, cities=cities)
    return pairs, _reclassified(pairs)


def _filter_pairs(
    pairs: list[CityPair], *, tier_filter: str | None = None, cities: list[str] | None = None,
) -> list[CityPair]:
    if tier_filter is not None:
        pairs = [p for p in pairs if p[0]["tier"] == tier_filter]
    if cities:
        cohort_rows = [r for _, prev_r, curr_r in pairs for r in (prev_r, curr_r) if r is not None]
        wanted = match_cities(cohort_rows, cities)
        pairs = [p for p in pairs if p[0]["city_key"] in wanted]
    return pairs


def _reclassified(pairs: list[CityPair]) -> bool:
    return any(
        prev_r is not None and curr_r is not None and prev_r["tier"] != curr_r["tier"]
        for _, prev_r, curr_r in pairs
    )
