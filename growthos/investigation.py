"""Granular same-month FTU & Registration target investigation (DEC-028, DEC-037).

Calculation-only: no database, filesystem or CLI access. Every scope uses the same
stored target row and the same month's facts; the Registration contribution verdict
(`> 70%`) and all accounting fields use exact rational arithmetic; floats exist only
for presentation.
"""

from fractions import Fraction

from .classification import is_unattributed, normalize_city_key
from .contracts import MonthlyData
from .errors import reject
from .evidence import (
    INVESTIGATION_UNDETERMINED_WARNING, NATIONAL_COVERAGE_WARNING, OVER_EXPLAINED_WARNING,
    POOLED_WARNING, TIER_EXCLUDED_WARNING, UNATTRIBUTED_SCOPE_WARNING, provenance,
)
from .metrics import target_attainment
from .selection import reject_if_unattributed
from .targets import TargetMonth

PRIORITY = Fraction(7, 10)
MEMBER_PAGE_SIZE = 25

METHOD = {
    "verdict_rule": "registration_effect / ftu_target_gap > 70% (exact rational test: contribution > 7/10)",
    "threshold_comparison": "strictly_greater_than_70_percent",
    "ftu_target_gap": "platform_ftu_actual - platform_ftu_target",
    "reg_target_gap": "registrations_actual - registrations_target",
    "target_ratio": "platform_ftu_target / registrations_target",
    "expected_ftu": "registrations_actual * target_ratio",
    "registration_effect": "expected_ftu - platform_ftu_target",
    "comparison": "registration_effect / ftu_target_gap",
    "arithmetic": "exact rational; float fields are presentation only",
    "interpretation": (
        "Same-month target-accounting split: it does not show that registrations caused "
        "the FTU gap, and the unexplained remainder is not attributed to any other cause. "
        "A registration contribution strictly above 70% stops further MoM investigation "
        "as a registration issue."
    ),
}

THRESHOLDS = {
    "registration_contribution_above_pct": 70,
    "ftu_miss": "ftu_target_gap < 0",
    "shortlist_rule": "unchanged; variance < -5 percent of own positive target",
    "workflow_position": "registration_target_shortfall_check_after_ftu_shortlist_stops_when_above_70_percent",
}


def _fraction(value) -> Fraction | None:
    return None if value is None else Fraction(value)


def _account(ftu_target, ftu_actual, reg_target, reg_actual) -> dict:
    """Shared operands and derived effect fields; every unavailability stays null."""
    ftu_gap = None if ftu_target is None or ftu_actual is None else ftu_actual - ftu_target
    reg_gap = None if reg_target is None or reg_actual is None else reg_actual - reg_target
    ratio = (Fraction(ftu_target, reg_target)
             if ftu_target is not None and reg_target is not None and reg_target > 0 else None)
    expected = None if ratio is None or reg_actual is None else ratio * reg_actual
    effect = None if expected is None or ftu_target is None else expected - _fraction(ftu_target)
    contribution = None if effect is None or ftu_gap is None or ftu_gap >= 0 else effect / _fraction(ftu_gap)
    unexplained = None if effect is None or ftu_gap is None else _fraction(ftu_gap) - effect
    return {
        "ftu_target": ftu_target,
        "ftu_actual": ftu_actual,
        "ftu_gap": ftu_gap,
        "ftu_gap_pct": (None if ftu_gap is None or ftu_target is None or ftu_target <= 0
                        else 100 * ftu_gap / ftu_target),
        "reg_target": reg_target,
        "reg_actual": reg_actual,
        "reg_gap": reg_gap,
        "reg_gap_pct": (None if reg_gap is None or reg_target is None or reg_target <= 0
                        else 100 * reg_gap / reg_target),
        "target_ratio": None if ratio is None else float(ratio),
        "expected_ftu": None if expected is None else float(expected),
        "registration_effect": None if effect is None else float(effect),
        "unexplained_gap": None if unexplained is None else float(unexplained),
        "contribution_pct": None if contribution is None else 100 * float(contribution),
        "_effect": effect,
        "_contribution": contribution,
    }


def _verdict(state: dict) -> tuple[str, str | None, list[str]]:
    """Apply the DEC-037 decision order; the first matching condition wins."""
    if not state["stored"]:
        return "undetermined", "no_stored_month", []
    if not state["ftu_imported"]:
        return "undetermined", "targets_not_imported", []
    if not state["registrations_imported"]:
        return "undetermined", "registration_targets_not_imported", []
    if state.get("pooled_city"):
        return "undetermined", "pooled_new_launch_target_has_no_per_city_value", []
    if state.get("no_observation"):
        return "undetermined", "no_stored_city_observation", []
    if state.get("no_members"):
        return "undetermined", "no_plan_matched_cities", []
    if state.get("national_incomplete"):
        return "undetermined", "national_actual_incomplete", []
    if state.get("empty_cohort"):
        return "undetermined", "no_observed_unplanned_cities", []
    if state.get("incomplete"):
        return "undetermined", "incomplete_group_member", []
    if state["ftu_target"] is None:
        return "undetermined", "missing_ftu_target", []
    if state["ftu_actual"] is None:
        return "undetermined", "missing_ftu_actual", []
    if state["reg_target"] is None:
        return "undetermined", "missing_registration_target", []
    if state["reg_actual"] is None:
        return "undetermined", "missing_registration_actual", []
    if state["ftu_target"] <= 0:
        return "undetermined", "nonpositive_ftu_target", []
    if state["reg_target"] <= 0:
        return "undetermined", "nonpositive_registration_target", []
    if state["ftu_gap"] >= 0:
        return "not_applicable", "ftu_target_met", []
    if state["_contribution"] is None:
        return "undetermined", "contribution_unavailable", []
    flags = ["over_explained"] if state["_contribution"] > 1 else []
    if state["reg_gap"] >= 0:
        return "no_registration_shortfall", "registration_target_met", flags
    if state["_contribution"] > PRIORITY:
        return "registration_issue", None, flags
    return "below_threshold", "contribution_at_or_below_70_percent", flags


def _public(operands: dict) -> dict:
    return {key: value for key, value in operands.items() if not key.startswith("_")}


def _block(scope: dict, operands: dict, state: dict, **extras) -> dict:
    verdict, reason, flags = _verdict({**state, **operands})
    block = {**scope, **_public(operands), "verdict": verdict, "reason": reason,
             "flags": flags, "shortlist_eligible": None,
             "shortlist_eligibility_reason": None, "coverage": None,
             "members": None, "omitted_member_count": 0,
             "blocking_city_keys": [], "cohort": False}
    block.update(extras)
    return block


def _scope(kind: str, label: str, month_value: str, keys: list[str]) -> dict:
    scope = {"kind": kind, "label": label, "month": month_value, "city_keys": keys}
    if kind == "city":
        scope["city"] = label
        scope["city_key"] = keys[0] if keys else None
    return scope


def _facts_by_key(facts: list[dict]) -> dict[str, dict]:
    return {row["city_key"]: row for row in facts}


def _catalog(facts: dict[str, dict], explicit: dict[str, dict]) -> dict[str, str]:
    names = {key: row["city"] for key, row in explicit.items() if not is_unattributed(key)}
    names.update({key: row["city"] for key, row in facts.items() if not is_unattributed(key)})
    return names


def _resolve(name: str, catalogs: list[dict[str, str]]) -> str:
    raw = (name or "").strip()
    if not raw:
        reject("CITY", "City names must be nonempty.")
    reject_if_unattributed(raw)
    folded, norm = raw.casefold(), normalize_city_key(raw)
    for catalog in catalogs:
        for key, label in catalog.items():
            if key == folded or label.casefold() == folded:
                return key
    for catalog in catalogs:
        for key in catalog:
            if normalize_city_key(key) == norm:
                return key
    reject("CITY", "One or more requested cities were not found in the selected months.")


def _observed(rows: list[dict], field: str) -> tuple[int | None, int, int]:
    values = [row[field] for row in rows if row[field] is not None]
    return (sum(values) if values else None, len(values), len(rows) - len(values))


def _coverage(rows: list[dict], fields: tuple[str, str]) -> dict:
    coverage = {}
    for field in fields:
        value, observed, skipped = _observed(rows, field)
        coverage[field] = {"known_subtotal": value, "observed_city_count": observed,
                           "skipped_null_city_count": skipped}
    return coverage


def _compact_member(block: dict) -> dict:
    return {key: block[key] for key in (
        "city", "city_key", "ftu_gap", "reg_gap", "reg_gap_pct",
        "registration_effect", "contribution_pct", "verdict", "reason")}


def _member_operands(fact: dict | None, target_row: dict | None) -> dict:
    return _account(
        None if target_row is None else target_row["platform_ftu_target"],
        None if fact is None else fact["platform_ftu"],
        None if target_row is None else target_row["registrations_target"],
        None if fact is None else fact["registrations"],
    )


def _aggregate(members: list[tuple[str, str, dict]]) -> tuple[dict, list[str]]:
    """Pool member effects and gaps exactly; report members that block a total."""
    blocking = [key for key, _name, operands in members
                if operands["_effect"] is None or operands["ftu_target"] is None
                or operands["ftu_target"] <= 0 or operands["reg_target"] is None
                or operands["reg_target"] <= 0]

    def total(field):
        values = [operands[field] for _key, _name, operands in members]
        if not values or any(value is None for value in values):
            return None
        return sum(values)

    effects = [operands["_effect"] for _key, _name, operands in members]
    gaps = [operands["ftu_gap"] for _key, _name, operands in members]
    effect_sum = (None if not effects or any(value is None for value in effects)
                  else sum(effects, Fraction(0)))
    gap_sum = (None if not gaps or any(value is None for value in gaps)
               else sum(gaps, 0))
    contribution = (None if effect_sum is None or gap_sum is None or gap_sum >= 0
                    else effect_sum / Fraction(gap_sum))
    ftu_target, ftu_actual = total("ftu_target"), total("ftu_actual")
    reg_target, reg_actual = total("reg_target"), total("reg_actual")
    ftu_gap = total("ftu_gap")
    reg_gap = None if reg_target is None or reg_actual is None else reg_actual - reg_target
    operands = {
        "ftu_target": ftu_target, "ftu_actual": ftu_actual, "ftu_gap": ftu_gap,
        "ftu_gap_pct": (None if ftu_gap is None or not ftu_target or ftu_target <= 0
                        else 100 * ftu_gap / ftu_target),
        "reg_target": reg_target, "reg_actual": reg_actual, "reg_gap": reg_gap,
        "reg_gap_pct": (None if reg_gap is None or not reg_target or reg_target <= 0
                        else 100 * reg_gap / reg_target),
        "target_ratio": (None if ftu_target is None or reg_target is None or reg_target <= 0
                         else float(Fraction(ftu_target, reg_target))),
        "expected_ftu": (None if effect_sum is None or ftu_target is None
                         else float(ftu_target + effect_sum)),
        "registration_effect": None if effect_sum is None else float(effect_sum),
        "unexplained_gap": (None if effect_sum is None or ftu_gap is None
                            else float(ftu_gap - effect_sum)),
        "contribution_pct": None if contribution is None else 100 * float(contribution),
        "_effect": effect_sum, "_contribution": contribution,
    }
    return operands, blocking


def _state(context: dict) -> dict:
    return {"stored": context["stored"], "ftu_imported": context["ftu_imported"],
            "registrations_imported": context["registrations_imported"]}


def _month_context(dataset: MonthlyData, value: str, plan: TargetMonth | None,
                   available: set[str], ftu_months: set[str],
                   registrations_months: set[str]) -> dict:
    data = dataset.data
    return {
        "month": value,
        "stored": value in available,
        "facts": [row for row in data["facts"] if row["month"] == value],
        "plan": None if plan is None else plan.data,
        "ftu_imported": value in ftu_months,
        "registrations_imported": value in registrations_months,
    }


def _special(plan: dict | None, kind: str) -> dict | None:
    if plan is None:
        return None
    return next((row for row in plan["rows"] if row["kind"] == kind), None)


def _national_block(context: dict) -> dict:
    facts = context["facts"]
    overall = _special(context["plan"], "overall")
    ftu_target = None if overall is None else overall["platform_ftu_target"]
    reg_target = None if overall is None else overall["registrations_target"]
    ftu_actual, _, _ = _observed(facts, "platform_ftu")
    reg_actual, _, _ = _observed(facts, "registrations")
    operands = _account(ftu_target, ftu_actual, reg_target, reg_actual)
    return _block(
        _scope("national", "Grand Total", context["month"], []), operands,
        {**_state(context), "national_incomplete": ftu_actual is None or reg_actual is None},
        coverage=_coverage(facts, ("platform_ftu", "registrations")),
    )


def _tier_block(context: dict, tier: str) -> tuple[dict, int]:
    explicit = _explicit(context["plan"])
    facts = _facts_by_key(context["facts"])
    tier_facts = {key: row for key, row in facts.items()
                  if row["tier"] == tier and not is_unattributed(key)}
    matched = sorted(key for key in tier_facts if key in explicit)
    excluded = len(tier_facts) - len(matched)
    members = [(key, tier_facts[key]["city"], _member_operands(tier_facts[key], explicit[key]))
               for key in matched]
    operands, blocking = _aggregate(members)
    state = {**_state(context), "no_members": not matched, "incomplete": bool(blocking)}
    ranked = sorted(members, key=lambda item: (
        item[2]["ftu_gap"] is None, item[2]["ftu_gap"] or 0, item[1], item[0]))
    blocks = [
        _block(_scope("city", name, context["month"], [key]), member, _state(context))
        for key, name, member in ranked
    ]
    block = _block(
        _scope("tier", tier, context["month"], matched), operands, state,
        coverage={"member_city_count": len(matched), "blocked_city_count": len(blocking),
                  "unplanned_city_count": excluded},
        members=[_compact_member(item) for item in blocks[:MEMBER_PAGE_SIZE]],
        omitted_member_count=max(0, len(blocks) - MEMBER_PAGE_SIZE),
        blocking_city_keys=blocking,
    )
    return block, excluded


def _group_block(context: dict, label: str, keys: list[str]) -> dict:
    explicit = _explicit(context["plan"])
    facts = _facts_by_key(context["facts"])
    members = [(key, (facts.get(key) or explicit.get(key) or {}).get("city", key),
                _member_operands(facts.get(key), explicit.get(key))) for key in keys]
    operands, blocking = _aggregate(members)
    blocks = [_block(_scope("city", name, context["month"], [key]), member, _state(context))
              for key, name, member in members]
    return _block(
        _scope("group", label, context["month"], keys), operands,
        {**_state(context), "no_members": not keys, "incomplete": bool(blocking)},
        coverage={"member_city_count": len(keys), "blocked_city_count": len(blocking),
                  "unplanned_city_count": 0},
        members=[_compact_member(item) for item in blocks],
        blocking_city_keys=blocking,
    )


def _city_block(context: dict, key: str, name: str) -> tuple[dict, bool]:
    explicit = _explicit(context["plan"])
    facts = _facts_by_key(context["facts"])
    fact, target_row = facts.get(key), explicit.get(key)
    operands = _member_operands(fact, target_row)
    pooled = target_row is None and context["plan"] is not None
    block = _block(
        _scope("city", (fact or target_row or {}).get("city", name), context["month"], [key]),
        operands,
        {**_state(context), "no_observation": fact is None, "pooled_city": pooled},
    )
    attainment = target_attainment(operands["ftu_target"], operands["ftu_actual"])
    block["shortlist_eligible"] = attainment.qualifies
    if attainment.qualifies:
        reason = None
    elif operands["ftu_actual"] is None:
        reason = "missing_actual"
    elif operands["ftu_target"] is None:
        reason = "missing_target"
    elif operands["ftu_target"] <= 0:
        reason = "nonpositive_target"
    else:
        reason = "not_above_5_percent"
    block["shortlist_eligibility_reason"] = reason
    return block, pooled


def _pooled_block(context: dict) -> dict:
    explicit = _explicit(context["plan"])
    fallback = _special(context["plan"], "new_launch")
    pool = [row for row in context["facts"]
            if row["city_key"] not in explicit and not is_unattributed(row["city_key"])]
    ftu_actual, _, _ = _observed(pool, "platform_ftu")
    reg_actual, _, _ = _observed(pool, "registrations")
    ftu_keys = {row["city_key"] for row in pool if row["platform_ftu"] is not None}
    reg_keys = {row["city_key"] for row in pool if row["registrations"] is not None}
    operands = _account(
        None if fallback is None else fallback["platform_ftu_target"], ftu_actual,
        None if fallback is None else fallback["registrations_target"], reg_actual,
    )
    flags = ["coverage_mismatch"] if ftu_keys != reg_keys else []
    block = _block(
        _scope("new_launch", "New launch", context["month"], []), operands,
        {**_state(context), "empty_cohort": not pool}, cohort=True,
        coverage=_coverage(pool, ("platform_ftu", "registrations")),
    )
    block["flags"] = sorted(set(block["flags"]) | set(flags))
    return block


def _explicit(plan: dict | None) -> dict[str, dict]:
    if plan is None:
        return {}
    return {row["city_key"]: row for row in plan["rows"] if row["kind"] == "city"}


def _resolve_names(months: list[dict], names: list[str]) -> list[tuple[str, str]]:
    catalogs = [_catalog(_facts_by_key(context["facts"]), _explicit(context["plan"]))
                for context in months]
    resolved, seen = [], {}
    for name in names:
        key = _resolve(name, catalogs)
        folded = key.casefold()
        if folded in seen:
            reject("ARGUMENTS", "Requested cities resolve to the same canonical city.")
        label = next((catalog[key] for catalog in reversed(catalogs) if key in catalog), name)
        seen[folded] = key
        resolved.append((key, label))
    return resolved


def _resolve_groups(months: list[dict], groups: list[tuple[str, list[str]]]):
    catalogs = [_catalog(_facts_by_key(context["facts"]), _explicit(context["plan"]))
                for context in months]
    resolved = []
    for label, names in groups:
        keys = []
        for name in names:
            key = _resolve(name, catalogs)
            if key in keys:
                reject("ARGUMENTS", "A group cannot repeat the same canonical city.")
            keys.append(key)
        resolved.append((label, keys))
    return resolved


def investigate(
    dataset: MonthlyData,
    targets: dict[str, TargetMonth],
    *,
    requested_months: list[str],
    available_months: set[str] | None = None,
    ftu_months: set[str] | None = None,
    registrations_months: set[str] | None = None,
    cities: list[str] | None = None,
    groups: list[tuple[str, list[str]]] | None = None,
    tier: str | None = None,
    national: bool = False,
) -> dict:
    """Report each requested scope independently; city requests are never summed."""
    available = set(dataset.data["months"]) if available_months is None else available_months
    ftu_imported = set(targets) if ftu_months is None else ftu_months
    registrations_imported = set(targets) if registrations_months is None else registrations_months
    months = [_month_context(dataset, value, targets.get(value), available,
                             ftu_imported, registrations_imported)
              for value in requested_months]
    city_keys = _resolve_names(months, cities or [])
    group_keys = _resolve_groups(months, groups or [])

    blocks, pooled_seen, excluded_total = [], False, 0
    for context in months:
        if national:
            blocks.append(_national_block(context))
        if tier is not None:
            block, excluded = _tier_block(context, tier)
            excluded_total += excluded
            blocks.append(block)
        for label, keys in group_keys:
            blocks.append(_group_block(context, label, keys))
        unplanned_named = False
        for key, name in city_keys:
            block, pooled = _city_block(context, key, name)
            unplanned_named = unplanned_named or pooled
            blocks.append(block)
        if national or unplanned_named:
            blocks.append(_pooled_block(context))
            pooled_seen = True

    warnings = list(provenance(dataset)["warnings"])
    additions = []
    if any("over_explained" in block["flags"] for block in blocks):
        additions.append(OVER_EXPLAINED_WARNING)
    if national:
        additions.append(NATIONAL_COVERAGE_WARNING)
    if pooled_seen:
        additions.append(POOLED_WARNING)
    if tier is not None and excluded_total:
        additions.append(TIER_EXCLUDED_WARNING)
    unattributed = any(is_unattributed(row["city_key"])
                       for context in months for row in context["facts"])
    if unattributed and (national or tier is not None or pooled_seen):
        additions.append(UNATTRIBUTED_SCOPE_WARNING)
    if any(block["verdict"] == "undetermined" for block in blocks):
        additions.append(INVESTIGATION_UNDETERMINED_WARNING)
    for warning in additions:
        if warning not in warnings:
            warnings.append(warning)

    return {
        "operation": "investigate",
        "metric": "platform_ftu",
        "month": requested_months[0] if len(requested_months) == 1 else None,
        "requested_months": requested_months,
        **provenance(dataset),
        "method": METHOD,
        "thresholds": THRESHOLDS,
        "blocks": blocks,
        "warnings": warnings,
    }
