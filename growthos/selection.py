"""Period selection, city matching and paired observations for IRF operations."""

from dataclasses import dataclass

from .classification import normalize_city_key
from .contracts import TIERS, MonthlyData, month, next_month
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
