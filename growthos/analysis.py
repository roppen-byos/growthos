"""Reviewed IRF comparisons, target facts and policy-based selections over detached data."""

from __future__ import annotations

from decimal import Decimal
from fractions import Fraction

from .classification import CANONICAL_CITIES, is_unattributed
from .contracts import FUNNEL_VOLUMES, PUBLIC_VOLUMES, RATIOS, TIERS, VOLUMES, MonthlyData, canonical_json, previous_month
from .errors import reject
# Preserve existing public imports; new callers use the owning modules.
from .evidence import MISSING_TOTALS_WARNING, TIER_RECLASSIFIED_WARNING, catalog, provenance
from .evidence import UNATTRIBUTED_SCOPE_WARNING
from .evidence import TARGET_FACT_OPERANDS_WARNING, TARGET_OPERANDS_WARNING
from .evidence import month_to_date, target_actual_warning
from .metrics import ALL_METRICS, aggregate, definitions, group_metrics, number, pair_metrics, quotient
from .metrics import (
    GroupRatioComparison, GroupVolumeComparison, aggregate_observations,
    compare_groups, compare_pair, group_share_gap, metrics_output, share_gap,
)
from .selection import normalize_tier, paired_cohort, select_pair, validate_top
from .selection import prepare_comparison
from .selection import validate_target_request
from .selection import select_target_facts, validate_target_facts_request
from .targets import TargetMonth
from .metrics import observed_target_actuals, target_attainment

SERVICE_MAP = {
    "link": ("platform_link_ftu", "link_share"),
    "bike": ("platform_link_ftu", "link_share"),
    "auto": ("platform_auto_ftu", "auto_share"),
    "cab": ("platform_cab_ftu", "cab_share"),
    "all": ("platform_ftu", "link_share"),
}


PERSISTENCE_CRITERIA = (
    "current variance strictly < -5% of the city's own positive target, an evaluable prior month, "
    "and current_variance_pct - previous_variance_pct < -5 percentage points; "
    "status is persistent when the prior variance was also strictly < -5%, otherwise new_miss"
)


def _target_evidence(dataset: MonthlyData, targets: TargetMonth, period: str, *,
                     tier: str | None = None, operand_warning: str = TARGET_OPERANDS_WARNING,
                     include_cumulative_unit: bool = True) -> tuple[dict, list[dict], dict[str, int]]:
    """One join/calculation seam for unfiltered facts and policy-based selection."""
    data, plan = dataset.data, targets.data
    if period not in data["months"] or plan["month"] != period:
        reject("COVERAGE", "Actuals and targets must describe the requested month; no substitute was selected.")
    month_facts = [r for r in data["facts"] if r["month"] == period]
    unattributed_fact = next((r for r in month_facts if is_unattributed(r["city_key"])), None)
    actuals = {r["city_key"]: r for r in month_facts if not is_unattributed(r["city_key"])}
    explicit = {r["city_key"]: r for r in plan["rows"] if r["kind"] == "city"}
    fallback = next(r for r in plan["rows"] if r["kind"] == "new_launch")
    overall = next(r for r in plan["rows"] if r["kind"] == "overall")
    if tier:
        actuals = {key: row for key, row in actuals.items() if row["tier"] == tier}
        if not actuals:
            reject("COVERAGE", "No stored cities belong to the requested tier in this month; no substitute cohort was selected.")
        explicit = {key: row for key, row in explicit.items() if key in actuals}
    matched_keys = [key for key in sorted(actuals) if key in explicit]
    fallback_keys = [key for key in sorted(actuals) if key not in explicit]
    observed_rows = [actuals[key] for key in matched_keys] if tier else month_facts
    observed = observed_target_actuals(observed_rows)
    rows = []
    for key in sorted(set(actuals) if tier else set(actuals) | set(explicit)):
        row = actuals.get(key)
        source = explicit.get(key, fallback)
        actual = row["platform_ftu"] if row else None
        pooled = source is fallback
        target = None if pooled else source["platform_ftu_target"]
        gap = target_attainment(target, actual)
        rows.append({
            "city_key": key, "city": row["city"] if row else source["city"], "target": target, "actual": actual,
            "variance": gap.variance, "variance_pct": gap.variance_pct,
            "target_source": "new_launch" if pooled else "city",
            "attainment": gap.attainment, "qualifies_for_shortlist": gap.qualifies,
            "actual_unavailable_reason": "missing_city_observation" if row is None else "missing_platform_ftu" if actual is None else None,
            "target_unavailable_reason": "pooled_new_launch_target" if pooled else "missing_target" if target is None else None,
            "variance_pct_unavailable_reason": gap.reason,
        })
    def complete_sum(values):
        return None if any(v is None for v in values) else sum(values)
    city_sum = complete_sum([r["platform_ftu_target"] for r in explicit.values()])
    pool_rows = [actuals[key] for key in fallback_keys]
    pool = observed_target_actuals(pool_rows)
    fallback_gap = target_attainment(fallback["platform_ftu_target"], pool.value)
    if fallback["platform_ftu_target"] is None:
        fallback_reason = "missing_new_launch_target"
    elif pool.value is None:
        fallback_reason = "no_observed_platform_ftu"
    else:
        fallback_reason = fallback_gap.reason
    fallback_summary = {
        "target": fallback["platform_ftu_target"], "actual": pool.value,
        "variance": fallback_gap.variance, "variance_pct": fallback_gap.variance_pct,
        "qualifies": fallback_gap.qualifies, "city_count": len(fallback_keys),
        "observed_city_count": pool.observed_city_count,
        "skipped_null_city_count": pool.skipped_null_ftu_city_count,
        "unavailable_reason": fallback_reason,
        "disclosure": (
            "Unplanned cities share one New launch fallback target compared with their combined observed "
            "Platform FTU; it is not an allocation, per-city target, shared budget or launch evidence."
        ),
    }
    matched_actual_target_sum = complete_sum([explicit[key]["platform_ftu_target"] for key in matched_keys])
    if fallback_keys:
        effective_target_sum = (
            None if matched_actual_target_sum is None or fallback["platform_ftu_target"] is None
            else matched_actual_target_sum + fallback["platform_ftu_target"]
        )
    else:
        effective_target_sum = matched_actual_target_sum
    if tier:
        total = complete_sum([explicit[key]["platform_ftu_target"] for key in matched_keys]) if matched_keys else None
        target_source = "tier_city_target_sum"
        missing_target_reason = "missing_tier_city_target" if matched_keys else "no_plan_matched_cities"
    else:
        total = overall["platform_ftu_target"]
        target_source = "Grand Total"
        missing_target_reason = "missing_overall_target"
    x_reason = missing_target_reason if total is None else observed.reason
    x = None if x_reason else max(total - observed.value, 0)
    overall_variance = None if x_reason else observed.value - total
    difference = None if tier or city_sum is None or total is None else city_sum - total
    reconciliation = {
        "explicit_city_target_sum": city_sum, "city_targets_minus_grand_total": difference,
        "new_launch_target": fallback["platform_ftu_target"],
        "effective_actual_city_target_sum": effective_target_sum,
        "overall_target_policy": "Grand Total is independent; the pooled New launch fallback never changes it",
    }
    if tier:
        reconciliation = {
            "tier_city_target_sum": total, "explicit_city_target_sum": city_sum,
            "national_grand_total": overall["platform_ftu_target"], "new_launch_target": fallback["platform_ftu_target"],
            "plan_matched_city_target_sum": total,
            "effective_actual_city_target_sum": effective_target_sum,
            "overall_target_policy": "Tier target sums assigned targets of its plan-matched stored monthly city cohort; unplanned cities are outside that calculation and the national Grand Total is not used or allocated",
        }
    result = {
        **provenance(dataset, data=data), "month": period, "metric": "platform_ftu",
        "units": {"target": "customers", "actual": "customers", "variance": "customers",
                  "variance_pct": "percent",
                  **({"cumulative_shortfall": "customers"} if include_cumulative_unit else {})},
        "overall": {"target": total, "target_source": target_source,
                    "actual": observed.value, "actual_coverage": observed.to_dict(),
                    "variance": overall_variance, "shortfall": x, "unavailable_reason": x_reason},
        "reconciliation": reconciliation,
        "fallback": fallback_summary,
    }
    if tier:
        result["scope"] = {"tier": tier, "city_cohort": "selected_month_stored_tier",
                           "gap_basis": "plan_matched_tier_own_gap"}
        result["warnings"].append("Tier target, actual and gap use only plan-matched cohort cities; unplanned cities are pooled into the separate New launch fallback comparison outside this tier calculation.")
        result["warnings"].append("Tier targets sum assigned city targets, not an allocation of Grand Total; tier totals need not reconcile to the national target.")
    if difference not in (None, 0):
        result["warnings"].append("Resolved city targets do not reconcile to Grand Total; neither has been adjusted.")
    if fallback_keys:
        result["warnings"].append("Unplanned actual cities share one New launch fallback target compared with their combined observed Platform FTU; they have no individual target and the fallback is not an allocation, shared budget or launch evidence.")
    skipped_warning = target_actual_warning(observed)
    if unattributed_fact is not None:
        result["warnings"].append(UNATTRIBUTED_SCOPE_WARNING)
    if skipped_warning:
        result["warnings"].append(skipped_warning)
    if any(r["actual"] is None for r in rows) or any(
            r["target"] is None for r in rows if r["target_source"] == "city") or x_reason:
        result["warnings"].append(operand_warning)
    counts = {"actual_city_count": len(actuals), "target_city_count": len(explicit),
              "fallback_city_count": len(fallback_keys),
              "plan_matched_actual_city_count": len(matched_keys)}
    return result, rows, counts


TARGET_ROW_FIELDS = ("city_key", "city", "target", "actual", "variance", "variance_pct",
                     "target_source")


def _target_row_reason(row: dict) -> str | None:
    """Why a candidate city cannot enter a target shortlist; eligibility is exact."""
    if row["actual"] is None:
        return "missing_actual"
    if row["target"] is None:
        return "missing_target"
    if row["target"] <= 0:
        return "nonpositive_target"
    return None if row["qualifies_for_shortlist"] else "not_above_5_percent"


def _rank_target_rows(rows: list[dict]) -> tuple[list[dict], dict[str, int]]:
    excluded = {"missing_actual": 0, "missing_target": 0, "nonpositive_target": 0, "not_above_5_percent": 0}
    eligible = []
    for row in rows:
        reason = _target_row_reason(row)
        if reason:
            excluded[reason] += 1
        else:
            eligible.append({k: row[k] for k in TARGET_ROW_FIELDS})
    eligible.sort(key=lambda r: (r["variance"], r["city"], r["city_key"]))
    return eligible, excluded


def _persistent_failure(
    page_rows: list[dict], dataset: MonthlyData, previous_targets: TargetMonth | None,
    period: str, *, tier: str | None = None, queue: str = "matched",
) -> dict:
    """Annotate a page with material misses whose variance worsened against the prior month."""
    prior = previous_month(period)
    excluded = {
        "missing_prior_city_observation": 0, "missing_prior_target": 0,
        "nonpositive_prior_target": 0, "missing_prior_actual": 0,
        "variance_pct_delta_not_below_minus_5": 0,
    }
    result = {
        "available": False, "previous_month": prior, "criteria": PERSISTENCE_CRITERIA,
        "rows": [], "page_city_count": len(page_rows), "checked_city_count": 0,
        "not_currently_qualifying_city_count": 0, "city_count": 0,
        "persistent_city_count": 0, "new_miss_city_count": 0,
        "undetermined_city_count": 0, "excluded_city_counts": excluded, "warnings": [],
    }
    if queue == "fallback":
        result["reason"] = "pooled_fallback_has_no_city_targets"
        result["warnings"].append(
            "Pooled New launch fallback cities have no individual target, so no per-city prior-month comparison is available.")
        return result
    if previous_targets is None:
        result["reason"] = "no_previous_month_with_targets_and_actuals"
        result["warnings"].append(
            "No preceding stored month has both Platform FTU actuals and targets, so the prior-month comparison cannot be evaluated.")
        return result
    if previous_targets.data["month"] != prior:
        reject("COVERAGE", "Prior targets must describe the immediately previous calendar month.")
    data = dataset.data
    if prior not in data["months"]:
        result["reason"] = "no_previous_month_with_targets_and_actuals"
        result["warnings"].append(
            "The prior month's actuals were not read with this request, so the prior-month comparison cannot be evaluated.")
        return result
    prior_facts = {r["city_key"]: r for r in data["facts"] if r["month"] == prior}
    prior_explicit = {r["city_key"]: r for r in previous_targets.data["rows"] if r["kind"] == "city"}
    rows, checked = [], 0
    for row in page_rows:
        target, actual = row["target"], row["actual"]
        current_variance = None if target is None or actual is None else actual - target
        if current_variance is None or target <= 0 or 20 * current_variance >= -target:
            continue
        checked += 1
        key = row["city_key"]
        fact = prior_facts.get(key)
        target_row = prior_explicit.get(key)
        prior_target = target_row["platform_ftu_target"] if target_row else None
        if fact is None:
            reason = "missing_prior_city_observation"
        elif prior_target is None:
            reason = "missing_prior_target"
        elif prior_target <= 0:
            reason = "nonpositive_prior_target"
        elif fact["platform_ftu"] is None:
            reason = "missing_prior_actual"
        else:
            prior_variance = fact["platform_ftu"] - prior_target
            if 20 * current_variance * prior_target - 20 * prior_variance * target >= -target * prior_target:
                reason = "variance_pct_delta_not_below_minus_5"
            else:
                reason = None
        if reason is None:
            current_pct = 100 * current_variance / target
            prior_pct = 100 * prior_variance / prior_target
            persistent = 20 * prior_variance < -prior_target
            rows.append({
                "city_key": key, "city": row["city"], "status": "persistent" if persistent else "new_miss",
                "current_variance_pct": current_pct,
                "previous_variance_pct": prior_pct,
                "variance_pct_delta": current_pct - prior_pct,
                "failing_since": prior if persistent else period,
            })
        else:
            excluded[reason] += 1
    undetermined = sum(excluded[k] for k in (
        "missing_prior_city_observation", "missing_prior_target",
        "nonpositive_prior_target", "missing_prior_actual"))
    persistent_count = sum(r["status"] == "persistent" for r in rows)
    result.update({
        "available": True, "rows": rows, "checked_city_count": checked,
        "not_currently_qualifying_city_count": len(page_rows) - checked,
        "city_count": len(rows), "persistent_city_count": persistent_count,
        "new_miss_city_count": len(rows) - persistent_count,
        "undetermined_city_count": undetermined,
    })
    result["warnings"].append(
        "Listed rows are material current misses whose variance percentage worsened by more than "
        "5 percentage points (< -5pp); status marks prior-month persistence. A city that missed both months "
        "but improved is not listed.")
    if result["new_miss_city_count"]:
        result["warnings"].append(
            f"{result['new_miss_city_count']} listed cities were not a material miss in {prior}; "
            "this is a widening-miss table, not only persistent misses.")
    if undetermined:
        result["warnings"].append(
            f"{undetermined} returned cities could not be compared with {prior} because an explicit prior target or actual was unavailable.")
    if tier:
        result["warnings"].append(
            "Prior-month evaluation is per city; the prior month's stored tier is not required to match the selected month's tier.")
    return result


def linear_full_month_estimate(dataset: MonthlyData, period: str | None = None) -> tuple[MonthlyData, list[dict]]:
    """Scale month-to-date counts to linear whole-month estimates (one `period`, or every MTD month).

    Estimates stay unrounded (the nearest float to count × days_in_month / days_covered), so the common scale
    cancels exactly in ratios and shares and service splits still sum to their totals. The returned dataset
    is an in-memory analysis view, not stored data; callers report the stored checksum.
    """
    partials = [m for m in month_to_date(dataset.data["month_metadata"]) if period is None or m["month"] == period]
    if not partials:
        return dataset, []
    factors = {m["month"]: Fraction(m["days_in_month"], m["days_covered"]) for m in partials}

    def scale(row):
        factor = factors[row["month"]]
        return {**row, **{k: None if row[k] is None else float(row[k] * factor)
                          for k in (*VOLUMES, *FUNNEL_VOLUMES)}}

    data = dataset.data
    view = {**data, "facts": [scale(r) if r["month"] in factors else r for r in data["facts"]]}
    return MonthlyData(canonical_json(view), dataset.result_checksum), partials


def _is_month_to_date(dataset: MonthlyData, period: str) -> bool:
    return any(m["month"] == period for m in month_to_date(dataset.data["month_metadata"]))


def target_cities(dataset: MonthlyData, targets: TargetMonth, period: str, *,
                  mode: str = "top", offset: int = 0, limit: int = 25,
                  tier: str | None = None, queue: str = "matched",
                  previous_targets: TargetMonth | None = None) -> dict:
    """Rank and select one queue: plan-matched city targets, or the New launch fallback."""
    validate_target_request(period, mode, offset, limit, allow_current=_is_month_to_date(dataset, period))
    if queue not in ("matched", "fallback"):
        reject("ARGUMENTS", "Target analysis queue must be matched or fallback.")
    tier_filter = normalize_tier(tier)
    result, rows, counts = _target_evidence(dataset, targets, period, tier=tier_filter)
    x = result["overall"]["shortfall"]
    matched_rows = [r for r in rows if r["target_source"] == "city"]
    fallback_rows = [r for r in rows if r["target_source"] == "new_launch"]
    matched_eligible, matched_excluded = _rank_target_rows(matched_rows)
    if queue == "fallback":
        items = [
            {"city_key": r["city_key"], "city": r["city"], "actual": r["actual"],
             "target_source": r["target_source"], "unavailable_reason": r["actual_unavailable_reason"]}
            for r in fallback_rows
        ]
        items.sort(key=lambda r: (r["city"], r["city_key"]))
        coverage = {
            "requested_shortfall": None, "selected_shortfall": None,
            "covered_amount": None, "uncovered_amount": None, "overshoot": None,
            "eligible_variance": result["fallback"]["variance"], **counts,
            "selected_city_count": len(items), "excluded_city_counts": {},
            "selection_status": "pooled_new_launch",
        }
    else:
        selected, cumulative = [], 0
        for row in matched_eligible:
            if mode == "top" and (x is None or cumulative >= x):
                break
            cumulative += -row["variance"]
            selected.append({**row, "cumulative_shortfall": cumulative})
        items = selected
        coverage = {
            "requested_shortfall": x, "selected_shortfall": cumulative,
            "covered_amount": None if x is None else min(x, cumulative),
            "uncovered_amount": None if x is None else max(x - cumulative, 0),
            "overshoot": None if x is None else max(cumulative - x, 0),
            "eligible_shortfall": sum(-r["variance"] for r in matched_eligible), **counts,
            "eligible_city_count": len(matched_eligible), "selected_city_count": len(selected),
            "excluded_city_counts": matched_excluded,
            "selection_status": "overall_shortfall_unavailable" if mode == "top" and x is None else "complete",
        }
    page = items[offset:offset + limit]
    result.update({
        "operation": "target-analysis", "mode": mode, "queue": queue,
        "eligibility": "variance < -5% of own positive target; missing operands excluded",
        "coverage": {**coverage, "queue": queue},
        "rows": page, "row_count": len(page), "offset": offset, "limit": limit,
        "next_offset": offset + limit if offset + limit < len(items) else None,
        "omitted_row_count": len(items) - len(page),
    })
    result["warnings"].append("Shortfall coverage is arithmetic, not guaranteed recoverable FTU or a causal recommendation.")
    result["persistent_failure"] = _persistent_failure(
        page, dataset, previous_targets, period, tier=tier_filter, queue=queue)
    return result


def target_facts(dataset: MonthlyData, targets: TargetMonth, period: str, *,
                 cities: list[str] | None = None, attainment: str = "all",
                 sort_by: str = "city", descending: bool = False,
                 offset: int = 0, limit: int = 25, tier: str | None = None,
                 previous_targets: TargetMonth | None = None) -> dict:
    """General target observations; no implicit 5% filter or overall-gap stop."""
    validate_target_facts_request(period, attainment, sort_by, descending, offset, limit,
                                  allow_current=_is_month_to_date(dataset, period))
    tier_filter = normalize_tier(tier)
    result, rows, counts = _target_evidence(
        dataset, targets, period, tier=tier_filter,
        operand_warning=TARGET_FACT_OPERANDS_WARNING, include_cumulative_unit=False)
    selected = select_target_facts(rows, cities=cities, attainment=attainment,
                                   sort_by=sort_by, descending=descending)
    page = selected[offset:offset + limit]
    result.update({
        "operation": "target-facts",
        "filters": {"cities": sorted(cities) if cities else "all", "attainment": attainment},
        "sort": {"by": sort_by, "direction": "descending" if descending else "ascending", "unavailable": "last"},
        "coverage": {
            **counts, "total_city_count": len(rows), "matching_city_count": len(selected),
            "attainment_counts": {state: sum(r["attainment"] == state for r in selected)
                                  for state in ("above", "at", "below", "unavailable")},
            "unavailable_counts": {field: sum(r[field] is None for r in selected)
                                   for field in ("actual", "target", "variance_pct")},
        },
        "rows": page, "row_count": len(page), "offset": offset, "limit": limit,
        "next_offset": offset + limit if offset + limit < len(selected) else None,
        "omitted_row_count": len(selected) - len(page),
    })
    if tier_filter:
        result["filters"]["tier"] = tier_filter
        result["warnings"].append("City filters and attainment predicates do not redefine the selected tier's month-wide totals; facts have no implicit 5% filter.")
    else:
        result["warnings"].append("City filters and attainment predicates do not redefine month-wide overall totals; facts have no implicit 5% filter.")
    result["persistent_failure"] = _persistent_failure(
        page, dataset, previous_targets, period, tier=tier_filter)
    return result


def target_city_review(dataset: MonthlyData, targets: TargetMonth, city_key: str) -> dict:
    """One-time import review, not another routine analyst workflow."""
    data, plan = dataset.data, targets.data
    period = plan["month"]
    target = next((r for r in plan["rows"] if r["kind"] == "city" and r["city_key"] == city_key), None)
    actual = next((r for r in data["facts"] if r["month"] == period and r["city_key"] == city_key), None)
    target_value = target["platform_ftu_target"] if target else None
    actual_value = actual["platform_ftu"] if actual else None
    gap = target_attainment(target_value, actual_value)
    return {
        **provenance(dataset, data=data), "month": period, "city_key": city_key,
        "target": target_value, "actual": actual_value, "variance": gap.variance,
        "variance_pct": gap.variance_pct, "above_5_percent": gap.qualifies,
        "unavailable_reason": gap.reason, "target_checksum": targets.result_checksum,
        "interpretation": "One-time merged-city review; separate source-city actuals cannot be reconstructed.",
    }


def compare_overall(
    dataset: MonthlyData,
    previous: str | None = None,
    latest: str | None = None,
    *,
    group_by: str = "overall",
    tier: str | None = None,
    cities: list[str] | None = None,
    top: int = 25,
) -> dict:
    """Compare overall, tier, or city observations; never turn a missing city-month into zero."""
    if group_by not in ("overall", "tier", "city"):
        reject("ARGUMENTS", "group_by must be overall, tier, or city.")
    tier_filter = normalize_tier(tier)
    if group_by == "overall" and (tier_filter or cities):
        reject("ARGUMENTS", "Use --group-by tier or --group-by city when filtering by --tier or --city.")
    if group_by == "tier" and cities:
        reject("ARGUMENTS", "Use --group-by city when filtering by --city.")
    top_limit = validate_top(top)

    comparison = prepare_comparison(dataset, previous, latest, tier_filter=tier_filter, cities=cities)
    prev, curr, pairs = comparison.previous, comparison.latest, comparison.pairs

    out_rows = []
    omitted_row_count = 0
    if group_by == "overall":
        expected = {meta["city_key"] for meta, _, _ in pairs}
        before = aggregate_observations([prev_r for _, prev_r, _ in pairs if prev_r is not None], expected)
        after = aggregate_observations([curr_r for _, _, curr_r in pairs if curr_r is not None], expected)
        out_rows.append({"group": "overall", **metrics_output(*compare_groups(before, after))})
        operation = "compare-overall"
    elif group_by == "tier":
        named = comparison.named_pairs
        tiers_to_show = [tier_filter] if tier_filter else [t for t in TIERS if any(m["tier"] == t for m, _, _ in named)]
        for t in tiers_to_show:
            t_pairs = [p for p in named if p[0]["tier"] == t]
            expected = {m["city_key"] for m, _, _ in t_pairs}
            before = aggregate_observations([prev_r for _, prev_r, _ in t_pairs if prev_r is not None], expected)
            after = aggregate_observations([curr_r for _, _, curr_r in t_pairs if curr_r is not None], expected)
            out_rows.append({
                "group": t, "tier": t, "city_count": len(expected),
                **metrics_output(*compare_groups(before, after)),
            })
        operation = "compare-tier"
    else:
        ordered_pairs = sorted(
            comparison.named_pairs,
            key=lambda pair: (
                -((pair[2] or {}).get("platform_ftu") or 0),
                -((pair[1] or {}).get("platform_ftu") or 0),
                -((pair[2] or {}).get("registrations") or 0),
                pair[0]["city_key"],
            ),
        )
        selected_pairs = ordered_pairs if cities else ordered_pairs[:top_limit]
        omitted_row_count = len(ordered_pairs) - len(selected_pairs)
        for meta, prev_row, curr_row in selected_pairs:
            key = meta["city_key"]
            before = aggregate_observations([prev_row] if prev_row is not None else [], {key})
            after = aggregate_observations([curr_row] if curr_row is not None else [], {key})
            out_rows.append({
                "group": key, "city_key": key, "city": meta["city"], "tier": meta["tier"],
                "source_cities": meta["source_cities"], **metrics_output(*compare_groups(before, after)),
            })
        operation = "compare-city"

    result = {
        **provenance(dataset, data=comparison.data),
        "operation": operation,
        "group_by": group_by,
        "requested_periods": {"previous": previous, "latest": latest},
        "effective_periods": {"previous": prev, "latest": curr},
        "comparison_label": comparison.label,
        "filters": {
            "cities": sorted(cities) if cities else "all",
            "tiers": tier_filter or "all",
        },
        "metric_definitions": definitions(),
        "rows": out_rows,
        "row_count": len(out_rows),
        "omitted_row_count": omitted_row_count,
        "narrowing": "Use compare --group-by overall|tier|city, rank, funnel, or share-drivers to drill down.",
    }
    unmapped_rows = [r for r in comparison.period_rows
                     if r["tier"] == "T3" and r["city_key"] not in CANONICAL_CITIES
                     and not is_unattributed(r["city_key"])]
    unattributed_rows = [r for r in comparison.period_rows if is_unattributed(r["city_key"])]
    result["mapping_impact"] = {
        "unmapped_city_count": len({r["city_key"] for r in unmapped_rows}),
        "fallback_tier": "T3",
        "unattributed_included_in_overall": bool(unattributed_rows) and group_by == "overall",
        "known_volumes_by_month": {
            m: {metric: sum(r[metric] for r in unmapped_rows if r["month"] == m and r[metric] is not None) for metric in PUBLIC_VOLUMES}
            for m in (prev, curr)
        },
        **({"unattributed_volumes_by_month": {
            m: {
                metric: (
                    sum(vals)
                    if (vals := [r[metric] for r in unattributed_rows if r["month"] == m and r[metric] is not None])
                    else None
                )
                for metric in PUBLIC_VOLUMES
            }
            for m in (prev, curr)
        }} if unattributed_rows and group_by == "overall" else {}),
    }
    if unmapped_rows:
        result["warnings"].append("Unmapped cities included in T3 and overall totals; see mapping_impact.")
    if unattributed_rows:
        result["warnings"].append(UNATTRIBUTED_SCOPE_WARNING)
    if group_by != "overall" and comparison.reclassified:
        result["warnings"].append(TIER_RECLASSIFIED_WARNING)
    if any(v["absolute_delta"] is None for row in out_rows for v in row["volumes"].values()):
        result["warnings"].append(MISSING_TOTALS_WARNING)
    return result


def rank_cities(
    dataset: MonthlyData,
    metric: str = "platform_ftu",
    top: int = 20,
    *,
    tier: str | None = None,
    ascending: bool = False,
    basis: str = "delta",
    previous: str | None = None,
    latest: str | None = None,
) -> dict:
    """Rank cities by volume or ratio movement with deterministic tie-breaking."""
    if metric not in ALL_METRICS:
        reject("ARGUMENTS", f"Unsupported metric; choose one of {', '.join(ALL_METRICS)}.")
    if basis not in ("delta", "growth"):
        reject("ARGUMENTS", "Ranking basis must be delta or growth.")
    if metric in RATIOS and basis == "growth":
        reject("ARGUMENTS", "Ratio and share metrics must be ranked by delta (percentage points).")
    top_limit = validate_top(top)
    tier_filter = normalize_tier(tier)
    comparison = prepare_comparison(dataset, previous, latest, tier_filter=tier_filter)
    prev, curr = comparison.previous, comparison.latest
    rankable = []
    excluded_unavailable = 0
    excluded_unattributed = sum(is_unattributed(m["city_key"]) for m, _, _ in comparison.all_pairs)
    for meta, prev_row, curr_row in comparison.named_pairs:
        key, display, c_tier = meta["city_key"], meta["city"], meta["tier"]
        volumes, ratios = compare_pair(prev_row, curr_row)
        if metric in PUBLIC_VOLUMES:
            volume = volumes[metric]
            movement = volume.absolute_delta if basis == "delta" else volume.growth_fraction
            latest_val = volume.latest
            entry = {
                "city_key": key, "city": display, "tier": c_tier, "metric": metric,
                "previous": volume.previous, "latest": volume.latest,
                "absolute_delta": volume.absolute_delta, "growth_fraction": volume.growth_fraction,
                "platform_ftu_latest": volumes["platform_ftu"].latest,
                "registrations_latest": volumes["registrations"].latest,
            }
        else:
            ratio = ratios[metric]
            num_col, den_col = RATIOS[metric]
            movement = ratio.delta_pp
            latest_val = ratio.latest
            entry = {
                "city_key": key, "city": display, "tier": c_tier, "metric": metric,
                "previous": ratio.previous, "latest": ratio.latest, "delta_pp": ratio.delta_pp,
                "numerator_metric": num_col,
                "numerator_previous": volumes[num_col].previous,
                "numerator_latest": volumes[num_col].latest,
                "numerator_delta": volumes[num_col].absolute_delta,
                "denominator_metric": den_col,
                "denominator_previous": volumes[den_col].previous,
                "denominator_latest": volumes[den_col].latest,
                "denominator_delta": volumes[den_col].absolute_delta,
            }
        if movement is None:
            excluded_unavailable += 1
            continue
        sort_primary = movement if ascending else -movement
        sort_secondary = (latest_val or 0) if ascending else -(latest_val or 0)
        rankable.append((sort_primary, sort_secondary, key, entry))

    rankable.sort(key=lambda item: (item[0], item[1], item[2]))
    selected = []
    for idx, (_, _, _, entry) in enumerate(rankable[:top_limit], start=1):
        selected.append({"rank": idx, **entry})

    result = {
        **provenance(dataset, data=comparison.data),
        "operation": "rank",
        "requested_periods": {"previous": previous, "latest": latest},
        "effective_periods": {"previous": prev, "latest": curr},
        "comparison_label": comparison.label,
        "filters": {
            "metric": metric,
            "tier": tier_filter or "all",
            "direction": "ascending_declines" if ascending else "descending_gains",
            "basis": "delta_pp" if metric in RATIOS else basis,
            "top": top_limit,
        },
        "rows": selected,
        "row_count": len(selected),
        "total_rankable_city_count": len(rankable),
        "excluded_unavailable_city_count": excluded_unavailable,
        "excluded_unattributed_city_count": excluded_unattributed,
        "omitted_row_count": max(0, len(rankable) - len(selected)),
        "narrowing": "Pass --tier T1|T2|T3, --top N, or --ascending to refine rankings, or run funnel --city CITY for funnel drivers.",
    }
    if comparison.reclassified:
        result["warnings"].append(TIER_RECLASSIFIED_WARNING)
    if comparison.has_unattributed:
        result["warnings"].append(UNATTRIBUTED_SCOPE_WARNING)
    return result


# Each column has one (group, metric, field, unit) definition. Sort aliases below
# use the same fields; legacy ratio projections are fractional, not pp movements.
_FUNNEL_FIELDS = {
    **{name: (None, None, name, "text") for name in ("city", "city_key", "tier")},
    **{f"{metric}{suffix}": ("volumes", metric, field, unit)
       for metric in PUBLIC_VOLUMES for suffix, field, unit in (
           ("", "latest", "count"), ("_previous", "previous", "count"),
           ("_mom_abs", "absolute_delta", "count"), ("_mom_pct", "growth_fraction", "fraction"),
       )},
    **{f"{metric}{suffix}": ("ratios", metric, field, unit)
       for metric in RATIOS for suffix, field, unit in (
           ("", "latest", "fraction"), ("_previous", "previous", "fraction"),
           ("_delta_pp", "delta_pp", "percentage_points"),
           ("_mom_abs", "fraction_difference", "fraction"), ("_mom_pct", "relative_growth", "fraction"),
       )},
}
FUNNEL_COLUMNS = tuple(_FUNNEL_FIELDS)
_FUNNEL_COLUMN_ALIASES = {
    **{c: c for c in FUNNEL_COLUMNS},
    **{f"{m}_latest": m for m in ALL_METRICS},
    **{f"{m}_delta": f"{m}_mom_abs" for m in PUBLIC_VOLUMES},
    **{f"{m}_growth": f"{m}_mom_pct" for m in PUBLIC_VOLUMES},
    **{f"{m}_delta": f"{m}_delta_pp" for m in RATIOS},
}
_SORT_SUFFIXES = {
    "latest": ("", "_latest"), "previous": ("_previous",),
    "absolute_delta": ("_delta",), "growth_fraction": ("_growth",), "delta_pp": ("_delta", "_delta_pp"),
}
_FUNNEL_SORT_FIELDS = {
    f"{metric}{suffix}": (group, metric, field)
    for group, metric, field, _ in _FUNNEL_FIELDS.values()
    for suffix in _SORT_SUFFIXES.get(field, ())
}


def _funnel_sort_field(sort_by: str) -> tuple[str, str, str]:
    normalized = sort_by.strip().lower().replace("_mom_abs", "_delta").replace("_mom_pct", "_growth")
    if normalized not in _FUNNEL_SORT_FIELDS:
        reject("ARGUMENTS", "Unsupported funnel sort key; run 'funnel --help' for reviewed keys. Input values withheld.")
    return _FUNNEL_SORT_FIELDS[normalized]


def _resolve_funnel_sort_key(row: dict, sort_field: tuple[str, str, str]) -> float | int | None:
    group, metric, field = sort_field
    return getattr(row[group][metric], field)


def _funnel_column(row: dict, column: str):
    group, metric, field, _ = _FUNNEL_FIELDS[column]
    if group is None:
        return row[field]
    values = row[group][metric]
    if field in ("fraction_difference", "relative_growth"):
        delta = None if values.delta_pp is None else Decimal(str(values.delta_pp)) / 100
        return number(delta if field == "fraction_difference" else quotient(delta, values.previous))
    return getattr(values, field)


def city_funnels(
    dataset: MonthlyData,
    *,
    top: int = 10,
    tier: str | None = None,
    cities: list[str] | None = None,
    sort_by: str = "platform_ftu_delta",
    ascending: bool = False,
    previous: str | None = None,
    latest: str | None = None,
    columns: list[str] | None = None,
) -> dict:
    """Retrieve city-level funnel conversion and service-share metrics for top or selected cities."""
    top_limit = validate_top(top)
    if columns is not None:
        if not columns or len(columns) > len(_FUNNEL_COLUMN_ALIASES):
            reject("ARGUMENTS", "Unknown or empty funnel columns; use funnel --help for allowed columns.")
        normalized_cols = []
        for raw_col in columns:
            canonical = _FUNNEL_COLUMN_ALIASES.get((raw_col or "").strip().lower())
            if canonical is None:
                reject("ARGUMENTS", "Unknown or empty funnel columns; use funnel --help for allowed columns.")
            normalized_cols.append(canonical)
        columns = list(dict.fromkeys(normalized_cols))
    tier_filter = normalize_tier(tier)
    comparison = prepare_comparison(dataset, previous, latest, tier_filter=tier_filter, cities=cities)
    prev, curr = comparison.previous, comparison.latest
    sort_field = _funnel_sort_field(sort_by)

    built = []
    for meta, prev_row, curr_row in comparison.named_pairs:
        key = meta["city_key"]
        volumes, ratios = compare_pair(prev_row, curr_row)
        item = {
            "city_key": key, "city": meta["city"], "tier": meta["tier"],
            "source_cities": meta["source_cities"], "volumes": volumes, "ratios": ratios,
        }
        val = _resolve_funnel_sort_key(item, sort_field)
        built.append((val is None, (val if ascending else -(val or 0)) if val is not None else 0, key, item))

    built.sort(key=lambda x: (x[0], x[1], x[2]))
    limit = len(built) if cities else top_limit
    selected = [item for _, _, _, item in built[:limit]]
    if columns:
        selected = [{column: _funnel_column(item, column) for column in columns} for item in selected]
    else:
        selected = [{**item, **metrics_output(item["volumes"], item["ratios"])} for item in selected]

    result = {
        **provenance(dataset, data=comparison.data),
        "operation": "funnel",
        "requested_periods": {"previous": previous, "latest": latest},
        "effective_periods": {"previous": prev, "latest": curr},
        "comparison_label": comparison.label,
        "filters": {
            "cities": sorted(cities) if cities else "top_by_sort",
            "tier": tier_filter or "all",
            "sort_by": sort_by,
            "direction": "ascending" if ascending else "descending",
            "top": limit,
        },
        "rows": selected,
        "columns": columns,
        "column_units": None if columns is None else {c: _FUNNEL_FIELDS[c][3] for c in columns},
        "row_count": len(selected),
        "omitted_row_count": max(0, len(built) - len(selected)),
        "narrowing": "Pass --city CITY (repeatable), --tier T1|T2|T3, --sort METRIC_delta, or --top N.",
    }
    if comparison.reclassified:
        result["warnings"].append(TIER_RECLASSIFIED_WARNING)
    if comparison.has_unattributed:
        result["warnings"].append(UNATTRIBUTED_SCOPE_WARNING)
    return result


def _service_mix_summary(
    pairs: list[tuple[dict, dict | None, dict | None]],
) -> tuple[dict[str, GroupVolumeComparison], dict[str, GroupRatioComparison]]:
    expected = {meta["city_key"] for meta, _, _ in pairs}
    before = aggregate_observations([prev_r for _, prev_r, _ in pairs if prev_r is not None], expected)
    after = aggregate_observations([curr_r for _, _, curr_r in pairs if curr_r is not None], expected)
    all_volumes, all_ratios = compare_groups(before, after)
    volumes = {m: all_volumes[m] for m in ("platform_ftu", "platform_link_ftu", "platform_auto_ftu", "platform_cab_ftu")}
    shares = {m: all_ratios[m] for m in ("link_share", "auto_share", "cab_share")}
    shares["link_vs_auto_share_gap"] = group_share_gap(shares["link_share"], shares["auto_share"])
    return volumes, shares


def _service_mix_output(volumes: dict[str, GroupVolumeComparison], shares: dict[str, GroupRatioComparison]) -> dict:
    output = metrics_output(volumes, shares)
    return {"volumes": output["volumes"], "shares": output["ratios"]}


def share_drivers(
    dataset: MonthlyData,
    *,
    service: str = "all",
    top: int = 15,
    tier: str | None = None,
    previous: str | None = None,
    latest: str | None = None,
) -> dict:
    """Decompose Bike/Link, Auto, and Cab FTU share movements across tiers and top city contributors."""
    svc_key = service.strip().lower()
    if svc_key not in SERVICE_MAP:
        reject("ARGUMENTS", "Service must be one of link, bike, auto, cab, or all.")
    target_vol, _ = SERVICE_MAP[svc_key]
    top_limit = validate_top(top)
    tier_filter = normalize_tier(tier)
    comparison = prepare_comparison(dataset, previous, latest, tier_filter=tier_filter)
    prev, curr, all_pairs = comparison.previous, comparison.latest, comparison.all_pairs
    overall_volumes, overall_shares = _service_mix_summary(all_pairs)
    total_ftu_latest = overall_volumes["platform_ftu"].latest.value
    total_target_delta = overall_volumes[target_vol].absolute_delta
    missing_totals = any(v.absolute_delta is None for v in overall_volumes.values())

    tier_rows = []
    for t in TIERS:
        if tier_filter and t != tier_filter:
            continue
        t_pairs = [p for p in all_pairs if p[0]["tier"] == t and not is_unattributed(p[0]["city_key"])]
        if not t_pairs:
            continue
        t_volumes, t_shares = _service_mix_summary(t_pairs)
        t_ftu_latest = t_volumes["platform_ftu"].latest.value
        t_target_delta = t_volumes[target_vol].absolute_delta
        missing_totals = missing_totals or any(v.absolute_delta is None for v in t_volumes.values())
        tier_rows.append({
            "tier": t,
            "city_count": len(t_pairs),
            "latest_platform_ftu_mix": number(quotient(t_ftu_latest, total_ftu_latest)),
            "target_delta_to_overall_delta_ratio": number(quotient(t_target_delta, total_target_delta)),
            **_service_mix_output(t_volumes, t_shares),
        })

    city_entries = []
    for meta, prev_row, curr_row in comparison.named_pairs:
        key, display, c_tier = meta["city_key"], meta["city"], meta["tier"]
        volumes, ratios = compare_pair(prev_row, curr_row)
        t_delta = volumes[target_vol].absolute_delta
        if t_delta is None:
            continue
        gap = share_gap(ratios["link_share"], ratios["auto_share"])
        entry = {
            "city_key": key,
            "city": display,
            "tier": c_tier,
            "target_service": "link" if svc_key == "bike" else svc_key,
            "target_volume_metric": target_vol,
            "target_ftu_previous": volumes[target_vol].previous,
            "target_ftu_latest": volumes[target_vol].latest,
            "target_ftu_delta": t_delta,
            "target_ftu_growth_fraction": volumes[target_vol].growth_fraction,
            "platform_ftu_previous": volumes["platform_ftu"].previous,
            "platform_ftu_latest": volumes["platform_ftu"].latest,
            "platform_ftu_delta": volumes["platform_ftu"].absolute_delta,
            "link_ftu_delta": volumes["platform_link_ftu"].absolute_delta,
            "auto_ftu_delta": volumes["platform_auto_ftu"].absolute_delta,
            "cab_ftu_delta": volumes["platform_cab_ftu"].absolute_delta,
            "link_share_latest": ratios["link_share"].latest,
            "link_share_delta_pp": ratios["link_share"].delta_pp,
            "auto_share_latest": ratios["auto_share"].latest,
            "auto_share_delta_pp": ratios["auto_share"].delta_pp,
            "cab_share_latest": ratios["cab_share"].latest,
            "cab_share_delta_pp": ratios["cab_share"].delta_pp,
            "link_vs_auto_share_gap_delta_pp": gap.delta_pp,
            "latest_platform_ftu_mix": number(quotient(volumes["platform_ftu"].latest, total_ftu_latest)),
            "target_delta_to_overall_delta_ratio": number(quotient(t_delta, total_target_delta)),
        }
        city_entries.append(entry)

    top_growth = sorted(
        city_entries,
        key=lambda e: (-e["target_ftu_delta"], -(e["platform_ftu_latest"] or 0), e["city_key"]),
    )[:top_limit]
    top_decline = sorted(
        city_entries,
        key=lambda e: (e["target_ftu_delta"], (e["platform_ftu_latest"] or 0), e["city_key"]),
    )[:top_limit]

    result = {
        **provenance(dataset, data=comparison.data),
        "operation": "share-drivers",
        "requested_periods": {"previous": previous, "latest": latest},
        "effective_periods": {"previous": prev, "latest": curr},
        "comparison_label": comparison.label,
        "filters": {
            "service": "link" if svc_key == "bike" else svc_key,
            "tier": tier_filter or "all",
            "top": top_limit,
        },
        "overall_service_mix": _service_mix_output(overall_volumes, overall_shares),
        "tier_service_mix": tier_rows,
        "top_growth_cities": top_growth,
        "top_decline_cities": top_decline,
        "rankable_city_count": len(city_entries),
        "omitted_city_count": max(0, len(city_entries) - top_limit),
        "narrowing": "Arithmetic decomposition only; does not establish marketing, pricing, or supply causality.",
    }
    if comparison.reclassified:
        result["warnings"].append(TIER_RECLASSIFIED_WARNING)
    if comparison.has_unattributed:
        result["warnings"].append(UNATTRIBUTED_SCOPE_WARNING)
    if missing_totals:
        result["warnings"].append(MISSING_TOTALS_WARNING)
    return result
