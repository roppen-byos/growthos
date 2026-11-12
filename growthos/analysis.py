"""Reviewed compare, rank, funnel and share-driver operations over detached data."""

from __future__ import annotations

from decimal import Decimal, localcontext

from .classification import CANONICAL_CITIES
from .contracts import RATIOS, TIERS, VOLUMES, MonthlyData, month, next_month
from .errors import reject
# Preserve existing public imports; new callers use the owning modules.
from .evidence import MISSING_TOTALS_WARNING, TIER_RECLASSIFIED_WARNING, catalog, provenance
from .metrics import ALL_METRICS, aggregate, definitions, group_metrics, number, pair_metrics, quotient
from .selection import normalize_tier, paired_cohort, select_pair, validate_top

SERVICE_MAP = {
    "link": ("platform_link_ftu", "link_share"),
    "bike": ("platform_link_ftu", "link_share"),
    "auto": ("platform_auto_ftu", "auto_share"),
    "cab": ("platform_cab_ftu", "cab_share"),
    "all": ("platform_ftu", "link_share"),
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

    data = dataset.data
    prev, curr = select_pair(dataset, previous, latest, data=data)
    period_rows = [r for r in data["facts"] if r["month"] in (prev, curr)]
    pairs, reclassified = paired_cohort(period_rows, prev, curr, tier_filter=tier_filter, cities=cities)

    out_rows = []
    omitted_row_count = 0
    if group_by == "overall":
        expected = {meta["city_key"] for meta, _, _ in pairs}
        before = aggregate([prev_r for _, prev_r, _ in pairs if prev_r is not None], expected)
        after = aggregate([curr_r for _, _, curr_r in pairs if curr_r is not None], expected)
        volumes, ratios = group_metrics(before, after)
        out_rows.append({"group": "overall", "volumes": volumes, "ratios": ratios})
        operation = "compare-overall"
    elif group_by == "tier":
        tiers_to_show = [tier_filter] if tier_filter else [t for t in TIERS if any(m["tier"] == t for m, _, _ in pairs)]
        for t in tiers_to_show:
            t_pairs = [p for p in pairs if p[0]["tier"] == t]
            expected = {m["city_key"] for m, _, _ in t_pairs}
            before = aggregate([prev_r for _, prev_r, _ in t_pairs if prev_r is not None], expected)
            after = aggregate([curr_r for _, _, curr_r in t_pairs if curr_r is not None], expected)
            volumes, ratios = group_metrics(before, after)
            out_rows.append({"group": t, "tier": t, "city_count": len(expected), "volumes": volumes, "ratios": ratios})
        operation = "compare-tier"
    else:
        ordered_pairs = sorted(
            pairs,
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
            before = aggregate([prev_row] if prev_row is not None else [], {key})
            after = aggregate([curr_row] if curr_row is not None else [], {key})
            volumes, ratios = group_metrics(before, after)
            out_rows.append({
                "group": key, "city_key": key, "city": meta["city"], "tier": meta["tier"],
                "source_cities": meta["source_cities"], "volumes": volumes, "ratios": ratios,
            })
        operation = "compare-city"

    result = {
        **provenance(dataset, data=data),
        "operation": operation,
        "group_by": group_by,
        "requested_periods": {"previous": previous, "latest": latest},
        "effective_periods": {"previous": prev, "latest": curr},
        "comparison_label": "MoM" if next_month(month(prev)) == month(curr) else "period comparison",
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
    unmapped_rows = [r for r in period_rows if r["tier"] == "T3" and r["city_key"] not in CANONICAL_CITIES]
    result["mapping_impact"] = {
        "unmapped_city_count": len({r["city_key"] for r in unmapped_rows}),
        "fallback_tier": "T3",
        "known_volumes_by_month": {
            m: {metric: sum(r[metric] for r in unmapped_rows if r["month"] == m and r[metric] is not None) for metric in VOLUMES}
            for m in (prev, curr)
        },
    }
    if unmapped_rows:
        result["warnings"].append("Unmapped cities included in T3 and overall totals; see mapping_impact.")
    if group_by != "overall" and reclassified:
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
    data = dataset.data
    prev, curr = select_pair(dataset, previous, latest, data=data)

    period_rows = [r for r in data["facts"] if r["month"] in (prev, curr)]
    pairs, reclassified = paired_cohort(period_rows, prev, curr, tier_filter=tier_filter)
    rankable = []
    excluded_unavailable = 0
    for meta, prev_row, curr_row in pairs:
        key, display, c_tier = meta["city_key"], meta["city"], meta["tier"]
        volumes, ratios = pair_metrics(prev_row, curr_row)
        if metric in VOLUMES:
            m_data = volumes[metric]
            movement = m_data["absolute_delta"] if basis == "delta" else m_data["growth_fraction"]
            latest_val = m_data["latest"]
            entry = {
                "city_key": key, "city": display, "tier": c_tier, "metric": metric,
                "previous": m_data["previous"], "latest": m_data["latest"],
                "absolute_delta": m_data["absolute_delta"], "growth_fraction": m_data["growth_fraction"],
                "platform_ftu_latest": volumes["platform_ftu"]["latest"],
                "registrations_latest": volumes["registrations"]["latest"],
            }
        else:
            m_data = ratios[metric]
            num_col, den_col = RATIOS[metric]
            movement = m_data["delta_pp"]
            latest_val = m_data["latest"]
            entry = {
                "city_key": key, "city": display, "tier": c_tier, "metric": metric,
                "previous": m_data["previous"], "latest": m_data["latest"], "delta_pp": m_data["delta_pp"],
                "numerator_metric": num_col,
                "numerator_previous": volumes[num_col]["previous"],
                "numerator_latest": volumes[num_col]["latest"],
                "numerator_delta": volumes[num_col]["absolute_delta"],
                "denominator_metric": den_col,
                "denominator_previous": volumes[den_col]["previous"],
                "denominator_latest": volumes[den_col]["latest"],
                "denominator_delta": volumes[den_col]["absolute_delta"],
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
        **provenance(dataset, data=data),
        "operation": "rank",
        "requested_periods": {"previous": previous, "latest": latest},
        "effective_periods": {"previous": prev, "latest": curr},
        "comparison_label": "MoM" if next_month(month(prev)) == month(curr) else "period comparison",
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
        "omitted_row_count": max(0, len(rankable) - len(selected)),
        "narrowing": "Pass --tier T1|T2|T3, --top N, or --ascending to refine rankings, or run funnel --city CITY for funnel drivers.",
    }
    if reclassified:
        result["warnings"].append(TIER_RECLASSIFIED_WARNING)
    return result


# Each column has one (group, metric, field, unit) definition. Sort aliases below
# use the same fields; legacy ratio projections are fractional, not pp movements.
_FUNNEL_FIELDS = {
    **{name: (None, None, name, "text") for name in ("city", "city_key", "tier")},
    **{f"{metric}{suffix}": ("volumes", metric, field, unit)
       for metric in VOLUMES for suffix, field, unit in (
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
    **{f"{m}_delta": f"{m}_mom_abs" for m in VOLUMES},
    **{f"{m}_growth": f"{m}_mom_pct" for m in VOLUMES},
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


def _resolve_funnel_sort_key(row: dict, sort_by: str) -> float | int | None:
    normalized = sort_by.strip().lower().replace("_mom_abs", "_delta").replace("_mom_pct", "_growth")
    if normalized not in _FUNNEL_SORT_FIELDS:
        reject("ARGUMENTS", f"Unsupported funnel sort key '{sort_by}'.")
    group, metric, field = _FUNNEL_SORT_FIELDS[normalized]
    return row[group][metric][field]


def _funnel_column(row: dict, column: str):
    group, metric, field, _ = _FUNNEL_FIELDS[column]
    if group is None:
        return row[field]
    values = row[group][metric]
    if field in ("fraction_difference", "relative_growth"):
        delta = None if values["delta_pp"] is None else Decimal(str(values["delta_pp"])) / 100
        return number(delta if field == "fraction_difference" else quotient(delta, values["previous"]))
    return values[field]


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
    data = dataset.data
    prev, curr = select_pair(dataset, previous, latest, data=data)

    period_rows = [r for r in data["facts"] if r["month"] in (prev, curr)]
    pairs, reclassified = paired_cohort(period_rows, prev, curr, tier_filter=tier_filter, cities=cities)

    built = []
    for meta, prev_row, curr_row in pairs:
        key = meta["city_key"]
        volumes, ratios = pair_metrics(prev_row, curr_row)
        item = {
            "city_key": key, "city": meta["city"], "tier": meta["tier"],
            "source_cities": meta["source_cities"], "volumes": volumes, "ratios": ratios,
        }
        val = _resolve_funnel_sort_key(item, sort_by)
        built.append((val is None, (val if ascending else -(val or 0)) if val is not None else 0, key, item))

    built.sort(key=lambda x: (x[0], x[1], x[2]))
    limit = len(built) if cities else top_limit
    selected = [item for _, _, _, item in built[:limit]]
    if columns:
        selected = [{column: _funnel_column(item, column) for column in columns} for item in selected]

    result = {
        **provenance(dataset, data=data),
        "operation": "funnel",
        "requested_periods": {"previous": previous, "latest": latest},
        "effective_periods": {"previous": prev, "latest": curr},
        "comparison_label": "MoM" if next_month(month(prev)) == month(curr) else "period comparison",
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
    if reclassified:
        result["warnings"].append(TIER_RECLASSIFIED_WARNING)
    return result


def _service_mix_summary(pairs: list[tuple[dict, dict | None, dict | None]]) -> dict:
    expected = {meta["city_key"] for meta, _, _ in pairs}
    before = aggregate([prev_r for _, prev_r, _ in pairs if prev_r is not None], expected)
    after = aggregate([curr_r for _, _, curr_r in pairs if curr_r is not None], expected)
    all_volumes, all_ratios = group_metrics(before, after)
    volumes = {m: all_volumes[m] for m in ("platform_ftu", "platform_link_ftu", "platform_auto_ftu", "platform_cab_ftu")}
    shares = {m: all_ratios[m] for m in ("link_share", "auto_share", "cab_share")}
    with localcontext() as context:
        context.prec = 40
        def _gap(link_val: float | None, auto_val: float | None) -> Decimal | None:
            if link_val is None or auto_val is None:
                return None
            return Decimal(str(link_val)) - Decimal(str(auto_val))

        gap_prev = _gap(shares["link_share"]["previous"], shares["auto_share"]["previous"])
        gap_curr = _gap(shares["link_share"]["latest"], shares["auto_share"]["latest"])
        sub_prev = _gap(shares["link_share"]["known_subtotal_previous"], shares["auto_share"]["known_subtotal_previous"])
        sub_curr = _gap(shares["link_share"]["known_subtotal_latest"], shares["auto_share"]["known_subtotal_latest"])
        shares["link_vs_auto_share_gap"] = {
            "previous": number(gap_prev),
            "latest": number(gap_curr),
            "delta_pp": number(None if gap_prev is None or gap_curr is None else 100 * (gap_curr - gap_prev)),
            "previous_unavailable_reason": (
                shares["link_share"]["previous_unavailable_reason"]
                or shares["auto_share"]["previous_unavailable_reason"]
            ),
            "latest_unavailable_reason": (
                shares["link_share"]["latest_unavailable_reason"]
                or shares["auto_share"]["latest_unavailable_reason"]
            ),
            "known_subtotal_previous": number(sub_prev),
            "known_subtotal_latest": number(sub_curr),
            "known_subtotal_delta_pp": number(None if sub_prev is None or sub_curr is None else 100 * (sub_curr - sub_prev)),
        }
    return {"volumes": volumes, "shares": shares}


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
    data = dataset.data
    prev, curr = select_pair(dataset, previous, latest, data=data)

    all_period_rows = [r for r in data["facts"] if r["month"] in (prev, curr)]
    all_pairs, all_reclassified = paired_cohort(all_period_rows, prev, curr)
    overall_summary = _service_mix_summary(all_pairs)
    total_ftu_latest = overall_summary["volumes"]["platform_ftu"]["latest"]["value"]
    total_target_delta = overall_summary["volumes"][target_vol]["absolute_delta"]

    tier_rows = []
    for t in TIERS:
        if tier_filter and t != tier_filter:
            continue
        t_pairs = [p for p in all_pairs if p[0]["tier"] == t]
        if not t_pairs:
            continue
        t_mix = _service_mix_summary(t_pairs)
        t_ftu_latest = t_mix["volumes"]["platform_ftu"]["latest"]["value"]
        t_target_delta = t_mix["volumes"][target_vol]["absolute_delta"]
        tier_rows.append({
            "tier": t,
            "city_count": len(t_pairs),
            "latest_platform_ftu_mix": number(quotient(t_ftu_latest, total_ftu_latest)),
            "target_delta_to_overall_delta_ratio": number(quotient(t_target_delta, total_target_delta)),
            **t_mix,
        })

    filtered_pairs = [p for p in all_pairs if tier_filter is None or p[0]["tier"] == tier_filter]
    reclassified = all_reclassified if tier_filter is None else any(
        prev_r is not None and curr_r is not None and prev_r["tier"] != curr_r["tier"]
        for _, prev_r, curr_r in filtered_pairs
    )
    city_entries = []
    for meta, prev_row, curr_row in filtered_pairs:
        key, display, c_tier = meta["city_key"], meta["city"], meta["tier"]
        volumes, ratios = pair_metrics(prev_row, curr_row)
        t_delta = volumes[target_vol]["absolute_delta"]
        if t_delta is None:
            continue
        gap_prev = (
            None if ratios["link_share"]["previous"] is None or ratios["auto_share"]["previous"] is None
            else ratios["link_share"]["previous"] - ratios["auto_share"]["previous"]
        )
        gap_curr = (
            None if ratios["link_share"]["latest"] is None or ratios["auto_share"]["latest"] is None
            else ratios["link_share"]["latest"] - ratios["auto_share"]["latest"]
        )
        gap_delta_pp = None if gap_prev is None or gap_curr is None else 100 * (gap_curr - gap_prev)
        entry = {
            "city_key": key,
            "city": display,
            "tier": c_tier,
            "target_service": "link" if svc_key == "bike" else svc_key,
            "target_volume_metric": target_vol,
            "target_ftu_previous": volumes[target_vol]["previous"],
            "target_ftu_latest": volumes[target_vol]["latest"],
            "target_ftu_delta": t_delta,
            "target_ftu_growth_fraction": volumes[target_vol]["growth_fraction"],
            "platform_ftu_previous": volumes["platform_ftu"]["previous"],
            "platform_ftu_latest": volumes["platform_ftu"]["latest"],
            "platform_ftu_delta": volumes["platform_ftu"]["absolute_delta"],
            "link_ftu_delta": volumes["platform_link_ftu"]["absolute_delta"],
            "auto_ftu_delta": volumes["platform_auto_ftu"]["absolute_delta"],
            "cab_ftu_delta": volumes["platform_cab_ftu"]["absolute_delta"],
            "link_share_latest": ratios["link_share"]["latest"],
            "link_share_delta_pp": ratios["link_share"]["delta_pp"],
            "auto_share_latest": ratios["auto_share"]["latest"],
            "auto_share_delta_pp": ratios["auto_share"]["delta_pp"],
            "cab_share_latest": ratios["cab_share"]["latest"],
            "cab_share_delta_pp": ratios["cab_share"]["delta_pp"],
            "link_vs_auto_share_gap_delta_pp": gap_delta_pp,
            "latest_platform_ftu_mix": number(quotient(volumes["platform_ftu"]["latest"], total_ftu_latest)),
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
        **provenance(dataset, data=data),
        "operation": "share-drivers",
        "requested_periods": {"previous": previous, "latest": latest},
        "effective_periods": {"previous": prev, "latest": curr},
        "comparison_label": "MoM" if next_month(month(prev)) == month(curr) else "period comparison",
        "filters": {
            "service": "link" if svc_key == "bike" else svc_key,
            "tier": tier_filter or "all",
            "top": top_limit,
        },
        "overall_service_mix": overall_summary,
        "tier_service_mix": tier_rows,
        "top_growth_cities": top_growth,
        "top_decline_cities": top_decline,
        "rankable_city_count": len(city_entries),
        "omitted_city_count": max(0, len(city_entries) - top_limit),
        "narrowing": "Arithmetic decomposition only; does not establish marketing, pricing, or supply causality.",
    }
    if reclassified:
        result["warnings"].append(TIER_RECLASSIFIED_WARNING)
    if any(v["absolute_delta"] is None for s in (overall_summary, *tier_rows) for v in s["volumes"].values()):
        result["warnings"].append(MISSING_TOTALS_WARNING)
    return result
