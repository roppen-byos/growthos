"""Granular calendar-adjusted MoM funnel contribution investigation (DEC-032, DEC-037, DEC-039).

Calculation-only: no database, filesystem or CLI access. Evaluates FE sessions,
FE2RR and RR2Net independently against a material (> 5%) calendar-adjusted MoM
Platform FTU dip via hold-two-constant counterfactual proxies using exact rational
arithmetic, selects > 20% stages descending until cumulative contribution
exceeds > 70%, checks calendar-adjusted MoM Registrations (> 5% dip) when FE is
primary, and breaks down Bike/Auto/Cab drivers (> 30%, no cumulative cap) when
FE2RR (additive) or RR2Net (weighted rate + mix vs prior platform RR2Net) is
primary; floats are presentation only.
"""

from __future__ import annotations

import calendar
from fractions import Fraction

from .classification import is_unattributed, normalize_city_key
from .contracts import MonthlyData, month, previous_month
from .errors import reject
from .evidence import month_to_date
from .evidence import (
    FUNNEL_CALENDAR_SCALING_WARNING,
    FUNNEL_NATIONAL_MATCHED_WARNING,
    FUNNEL_OVER_EXPLAINED_WARNING,
    FUNNEL_PROXY_WARNING,
    FUNNEL_UNDETERMINED_WARNING,
    UNATTRIBUTED_SCOPE_WARNING,
    provenance,
)
from .selection import FUNNEL_STAGES, reject_if_unattributed, validate_funnel_stages

MATERIAL_THRESHOLD = Fraction(1, 5)
CUMULATIVE_ISSUE_THRESHOLD = Fraction(7, 10)
FTU_DIP_THRESHOLD = Fraction(-1, 20)
REG_DIP_THRESHOLD = Fraction(-1, 20)
SERVICE_MATERIAL_THRESHOLD = Fraction(3, 10)
STAGE_RANK = {"fe": 0, "fe2rr": 1, "rr2net": 2}
SERVICES = ("bike", "auto", "cab")
SERVICE_RANK = {"bike": 0, "auto": 1, "cab": 2}
SERVICE_FIELDS = {
    "bike": ("bike_rr", "bike_net"),
    "auto": ("auto_rr", "auto_net"),
    "cab": ("cab_rr", "cab_net"),
}
MEMBER_PAGE_SIZE = 25

METHOD = {
    "baseline": "immediately_previous_calendar_month",
    "calendar_adjustment": (
        "Proportional shorter-month scaling: volume counts (Platform FTU, Registrations, FE sessions, RR, Net) "
        "in the longer month are scaled by min(prior_days, current_days) / month_days. "
        "Conversion rates FE2RR, RR2Net, and service-level breakdowns are unaffected."
    ),
    "ftu_change": "current_ftu_scaled - prior_ftu_scaled (negative indicates an FTU dip)",
    "ftu_dip_rule": "ftu_change / prior_ftu_scaled < -5% (strict > 5% calendar-adjusted MoM FTU dip; exact rational test)",
    "stage_formulas": {
        "fe": "prior_ftu_scaled * (current_fe_scaled / prior_fe_scaled - 1) / ftu_change",
        "fe2rr": "prior_ftu_scaled * (current_fe2rr / prior_fe2rr - 1) / ftu_change",
        "rr2net": "prior_ftu_scaled * (current_rr2net / prior_rr2net - 1) / ftu_change",
    },
    "evaluation": "independent_counterfactual_hold_two_constant_on_platform_ftu",
    "material_threshold_pct": 20,
    "cumulative_issue_threshold_pct": 70,
    "issue_selection_rule": (
        "Filter stages strictly > 20%, sort descending by exact contribution, and select stages "
        "until cumulative contribution is strictly > 70% (if all > 20% stages sum <= 70%, keep "
        "all > 20% stages and mark primary_coverage_reached_70_pct: false)."
    ),
    "registration_check": (
        "Triggered only when fe is in primary_stages: checks whether calendar-adjusted MoM "
        "Registrations fell strictly > 5% ((current_reg_scaled - prior_reg_scaled) / prior_reg_scaled < -5%, "
        "exact rational test). Independent of Step 1's same-month Registration target check; "
        "missing or nonpositive registrations affect only registration_check and never block funnel stages."
    ),
    "service_breakdown": {
        "trigger": "Triggered independently when fe2rr or rr2net is in primary_stages.",
        "services": list(SERVICES),
        "material_threshold_pct": 30,
        "cumulative_cap": None,
        "selection_rule": (
            "Select every service strictly > 30% (no 70% cumulative cap so offsetting service "
            "gains cannot hide a second declining service), sorted descending by exact contribution."
        ),
        "fe2rr_formula": (
            "Additive over shared fe_sessions: service_fe2rr = service_rr / fe_sessions; "
            "contribution = (current_service_fe2rr - prior_service_fe2rr) / (current_fe2rr - prior_fe2rr). "
            "Residual non-Bike/Auto/Cab RR (e.g. c2c/delivery) is disclosed in other_services_contribution_pct."
        ),
        "rr2net_formula": (
            "Weighted MoM rate + mix effect benchmarked against prior Platform RR2Net: "
            "w_s = service_rr / rr; r_s = service_net / service_rr; R_prior = prior_net / prior_rr; "
            "weighted_rate_delta_s = w_curr * (r_curr - r_prior); "
            "mix_shift_delta_s = (w_curr - w_prior) * (r_prior - R_prior); "
            "weighted_total_delta_s = w_curr * (r_curr - R_prior) - w_prior * (r_prior - R_prior) "
            "= weighted_rate_delta_s + mix_shift_delta_s; "
            "contribution_s = weighted_total_delta_s / (current_rr2net - prior_rr2net). "
            "Per-service rate and mix components are exposed alongside aggregate mix_shift_contribution_pct "
            "and non-Bike/Auto/Cab other_services_contribution_pct (which sum with Bike/Auto/Cab to 100%)."
        ),
        "inactive_service_handling": (
            "Zero service RR in both months returns not_applicable (no_service_volume). One-sided zero RR "
            "(launch or shutdown) is scored directly in both FE2RR and RR2Net: in RR2Net, launches score "
            "w_curr * (r_curr - R_prior) and shutdowns score -w_prior * (r_prior - R_prior) as pure mix-shift "
            "effects (with rate_delta_pp and weighted_rate_delta_pp null). Missing counts are strictly isolated per service."
        ),
    },
    "threshold_comparison": "strictly_greater_than",
    "arithmetic": "exact rational; float fields are presentation only",
    "population_scope": (
        "FTU-eligible city funnel counts from irf.facts (FE sessions, RR, Net, and Bike/Auto/Cab RR & Net "
        "for days on or before a customer's first completed platform ride plus Zero-Transaction customers), "
        "applied as scale-invariant hold-two-constant relative-change counterfactuals to scaled Platform FTU."
    ),
    "interpretation": (
        "Independent counterfactual decomposition of a material (> 5%) calendar-adjusted MoM Platform FTU dip. "
        "Counterfactual effects omit cross-stage interaction terms so the three percentages need not sum "
        "to 100%; do not claim causation, and note that irf.facts funnel columns count FTU-eligible daily "
        "sessions/rides (including same-day rides on the first-ride day) rather than distinct FTU users."
    ),
}

THRESHOLDS = {
    "material_contributor_above_pct": 20,
    "cumulative_issue_above_pct": 70,
    "ftu_dip_below_pct": -5,
    "ftu_dip": "ftu_change / prior_ftu_scaled < -5% (strict)",
    "registration_dip_below_pct": -5,
    "registration_dip": "reg_change / prior_reg_scaled < -5% (strict; triggered only when fe is in primary_stages)",
    "service_contributor_above_pct": 30,
    "service_drilldown": "triggered_when_fe2rr_or_rr2net_in_primary_stages_above_30_pct_no_cumulative_cap",
    "registration_gate": "registration_target_shortfall_check_above_70_stops_before_funnel",
    "persistence_gate": "month_on_month_dip_persistence_check_persistent_prior_month_stops_before_funnel",
}


def _month_days(period: str) -> int:
    parsed = month(period)
    return calendar.monthrange(parsed.year, parsed.month)[1]


def _calendar_pair(prior: str, current: str, covered: dict[str, int] | None = None) -> tuple[dict, Fraction, Fraction]:
    """Month-to-date months count only their covered days, so the pair compares daily run-rates exactly."""
    covered = covered or {}
    prior_days = covered.get(prior, _month_days(prior))
    current_days = covered.get(current, _month_days(current))
    ref_days = min(prior_days, current_days)
    prior_scale = Fraction(ref_days, prior_days)
    current_scale = Fraction(ref_days, current_days)
    meta = {
        "prior_month": prior,
        "prior_days": prior_days,
        "current_month": current,
        "current_days": current_days,
        "reference_days": ref_days,
        "prior_scale": float(prior_scale),
        "current_scale": float(current_scale),
        "adjusted": prior_days != current_days,
        "month_to_date_months": sorted(m for m in (prior, current) if m in covered),
    }
    return meta, prior_scale, current_scale


def _scale(value: int | None, factor: Fraction) -> Fraction | None:
    return None if value is None else Fraction(value) * factor


def _rate(numerator: int | None, denominator: int | None) -> Fraction | None:
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return Fraction(numerator, denominator)


def _facts_by_key(facts: list[dict]) -> dict[str, dict]:
    return {row["city_key"]: row for row in facts}


def _resolve(name: str, catalog: dict[str, str]) -> str:
    raw = (name or "").strip()
    if not raw:
        reject("CITY", "City names must be nonempty.")
    reject_if_unattributed(raw)
    folded, norm = raw.casefold(), normalize_city_key(raw)
    for key, label in catalog.items():
        if key == folded or label.casefold() == folded:
            return key
    for key in catalog:
        if normalize_city_key(key) == norm:
            return key
    reject("CITY", "One or more requested cities were not found in the selected months.")


def _resolve_names(catalog: dict[str, str], names: list[str]) -> list[tuple[str, str]]:
    resolved, seen = [], set()
    for name in names:
        key = _resolve(name, catalog)
        folded = key.casefold()
        if folded in seen:
            reject("ARGUMENTS", "Requested cities resolve to the same canonical city.")
        seen.add(folded)
        resolved.append((key, catalog[key]))
    return resolved


def _resolve_groups(catalog: dict[str, str], groups: list[tuple[str, list[str]]]) -> list[tuple[str, list[str]]]:
    resolved = []
    for label, names in groups:
        keys = []
        for name in names:
            key = _resolve(name, catalog)
            if key in keys:
                reject("ARGUMENTS", "A group cannot repeat the same canonical city.")
            keys.append(key)
        resolved.append((label, keys))
    return resolved


def _stage_outcome(
    *,
    stage: str,
    prior_value: Fraction | None,
    current_value: Fraction | None,
    raw_operands: dict,
    prior_ftu: Fraction | None,
    ftu_change: Fraction | None,
    block_reason: str | None,
    stage_reason: str | None,
    blocking_keys: list[str] | None = None,
) -> dict:
    is_rate = stage in ("fe2rr", "rr2net")
    relative_change = (
        None
        if prior_value is None or current_value is None or prior_value <= 0
        else (current_value - prior_value) / prior_value
    )
    rate_delta_pp = (
        None
        if not is_rate or prior_value is None or current_value is None
        else 100 * float(current_value - prior_value)
    )
    stage_effect = (
        None
        if relative_change is None or prior_ftu is None or prior_ftu <= 0
        else prior_ftu * relative_change
    )
    counterfactual_ftu = (
        None
        if stage_effect is None or prior_ftu is None
        else prior_ftu + stage_effect
    )
    material_dip = (
        prior_ftu is not None
        and prior_ftu > 0
        and ftu_change is not None
        and ftu_change / prior_ftu < FTU_DIP_THRESHOLD
    )
    contribution = (
        None
        if block_reason is not None or stage_reason is not None or stage_effect is None or not material_dip
        else stage_effect / ftu_change
    )

    flags: list[str] = []
    if block_reason is not None:
        verdict, reason, material = "undetermined", block_reason, None
    elif ftu_change is None or prior_ftu is None or prior_ftu <= 0:
        verdict, reason, material = "undetermined", "ftu_change_unavailable", None
    elif ftu_change >= 0:
        verdict, reason, material = "not_applicable", "no_historical_ftu_dip", False
    elif not material_dip:
        verdict, reason, material = "not_applicable", "no_material_ftu_dip", False
    elif stage_reason is not None:
        verdict, reason, material = "undetermined", stage_reason, None
    elif contribution is None:
        verdict, reason, material = "undetermined", "contribution_unavailable", None
    elif contribution <= 0:
        verdict, reason, material = "no_stage_deterioration", "stage_not_deteriorating", False
    else:
        if contribution > 1:
            flags.append("over_explained")
        if contribution > MATERIAL_THRESHOLD:
            verdict, reason, material = "material_contributor", None, True
        else:
            verdict, reason, material = "below_threshold", "contribution_at_or_below_threshold", False

    return {
        "stage": stage,
        "unit": "rate" if is_rate else "scaled_sessions",
        **raw_operands,
        "prior_value": None if prior_value is None else float(prior_value),
        "current_value": None if current_value is None else float(current_value),
        "relative_change_pct": None if relative_change is None else 100 * float(relative_change),
        "rate_delta_pp": rate_delta_pp,
        "counterfactual_ftu": None if counterfactual_ftu is None else float(counterfactual_ftu),
        "stage_effect": None if stage_effect is None else float(stage_effect),
        "contribution_pct": None if contribution is None else 100 * float(contribution),
        "material": material,
        "selected_as_primary": None if verdict == "undetermined" else False,
        "verdict": verdict,
        "reason": reason,
        "flags": flags,
        "blocking_city_keys": list(blocking_keys or []),
        "_contribution": contribution,
    }


def _evaluate_fe(
    prior_fe: int | None,
    curr_fe: int | None,
    prior_scale: Fraction,
    curr_scale: Fraction,
    prior_ftu: Fraction | None,
    ftu_change: Fraction | None,
    block_reason: str | None,
    stage_override_reason: str | None = None,
    blocking_keys: list[str] | None = None,
) -> dict:
    p_scaled = _scale(prior_fe, prior_scale)
    c_scaled = _scale(curr_fe, curr_scale)
    reason = stage_override_reason
    if reason is None:
        if prior_fe is None:
            reason = "missing_prior_fe_sessions"
        elif curr_fe is None:
            reason = "missing_current_fe_sessions"
        elif prior_fe <= 0:
            reason = "nonpositive_prior_fe_sessions"
    return _stage_outcome(
        stage="fe",
        prior_value=p_scaled,
        current_value=c_scaled,
        raw_operands={"prior_fe_raw": prior_fe, "current_fe_raw": curr_fe},
        prior_ftu=prior_ftu,
        ftu_change=ftu_change,
        block_reason=block_reason,
        stage_reason=reason,
        blocking_keys=blocking_keys,
    )


def _evaluate_fe2rr(
    prior_fe: int | None,
    curr_fe: int | None,
    prior_rr: int | None,
    curr_rr: int | None,
    prior_ftu: Fraction | None,
    ftu_change: Fraction | None,
    block_reason: str | None,
    stage_override_reason: str | None = None,
    blocking_keys: list[str] | None = None,
) -> dict:
    p_rate = _rate(prior_rr, prior_fe)
    c_rate = _rate(curr_rr, curr_fe)
    reason = stage_override_reason
    if reason is None:
        if prior_fe is None:
            reason = "missing_prior_fe_sessions"
        elif curr_fe is None:
            reason = "missing_current_fe_sessions"
        elif prior_rr is None:
            reason = "missing_prior_rr"
        elif curr_rr is None:
            reason = "missing_current_rr"
        elif prior_fe <= 0:
            reason = "nonpositive_prior_fe_sessions"
        elif curr_fe <= 0:
            reason = "nonpositive_current_fe_sessions"
        elif prior_rr <= 0:
            reason = "nonpositive_prior_rr"
    return _stage_outcome(
        stage="fe2rr",
        prior_value=p_rate,
        current_value=c_rate,
        raw_operands={
            "prior_fe_raw": prior_fe,
            "current_fe_raw": curr_fe,
            "prior_rr_raw": prior_rr,
            "current_rr_raw": curr_rr,
        },
        prior_ftu=prior_ftu,
        ftu_change=ftu_change,
        block_reason=block_reason,
        stage_reason=reason,
        blocking_keys=blocking_keys,
    )


def _evaluate_rr2net(
    prior_rr: int | None,
    curr_rr: int | None,
    prior_net: int | None,
    curr_net: int | None,
    prior_ftu: Fraction | None,
    ftu_change: Fraction | None,
    block_reason: str | None,
    stage_override_reason: str | None = None,
    blocking_keys: list[str] | None = None,
) -> dict:
    p_rate = _rate(prior_net, prior_rr)
    c_rate = _rate(curr_net, curr_rr)
    reason = stage_override_reason
    if reason is None:
        if prior_rr is None:
            reason = "missing_prior_rr"
        elif curr_rr is None:
            reason = "missing_current_rr"
        elif prior_net is None:
            reason = "missing_prior_net"
        elif curr_net is None:
            reason = "missing_current_net"
        elif prior_rr <= 0:
            reason = "nonpositive_prior_rr"
        elif curr_rr <= 0:
            reason = "nonpositive_current_rr"
        elif prior_net <= 0:
            reason = "nonpositive_prior_net"
    return _stage_outcome(
        stage="rr2net",
        prior_value=p_rate,
        current_value=c_rate,
        raw_operands={
            "prior_rr_raw": prior_rr,
            "current_rr_raw": curr_rr,
            "prior_net_raw": prior_net,
            "current_net_raw": curr_net,
        },
        prior_ftu=prior_ftu,
        ftu_change=ftu_change,
        block_reason=block_reason,
        stage_reason=reason,
        blocking_keys=blocking_keys,
    )


def _ftu_summary(
    prior_ftu_raw: int | None,
    curr_ftu_raw: int | None,
    prior_scale: Fraction,
    curr_scale: Fraction,
    prereq_reason: str | None,
) -> tuple[dict, Fraction | None, Fraction | None, str | None]:
    p_scaled = _scale(prior_ftu_raw, prior_scale)
    c_scaled = _scale(curr_ftu_raw, curr_scale)
    ftu_change = None if p_scaled is None or c_scaled is None else c_scaled - p_scaled
    ftu_change_pct = (
        None if ftu_change is None or p_scaled is None or p_scaled <= 0
        else 100 * float(ftu_change / p_scaled)
    )
    reason = prereq_reason
    if reason is None:
        if prior_ftu_raw is None:
            reason = "missing_prior_ftu"
        elif curr_ftu_raw is None:
            reason = "missing_current_ftu"
        elif prior_ftu_raw <= 0:
            reason = "nonpositive_prior_ftu"
    summary = {
        "prior_ftu_raw": prior_ftu_raw,
        "current_ftu_raw": curr_ftu_raw,
        "prior_ftu_scaled": None if p_scaled is None else float(p_scaled),
        "current_ftu_scaled": None if c_scaled is None else float(c_scaled),
        "ftu_change": None if ftu_change is None else float(ftu_change),
        "ftu_change_pct": ftu_change_pct,
    }
    return summary, p_scaled, ftu_change, reason


def _public_stage(item: dict, *, selected_as_primary: bool | None) -> dict:
    cleaned = {key: value for key, value in item.items() if not key.startswith("_")}
    cleaned["selected_as_primary"] = selected_as_primary
    return cleaned


def _evaluate_registration_check(
    *,
    triggered: bool,
    prior_reg_raw: int | None,
    curr_reg_raw: int | None,
    prior_scale: Fraction,
    curr_scale: Fraction,
    override_reason: str | None = None,
    blocking_keys: list[str] | None = None,
) -> dict:
    if not triggered:
        return {
            "triggered": False,
            "prior_registrations_raw": None,
            "current_registrations_raw": None,
            "prior_registrations_scaled": None,
            "current_registrations_scaled": None,
            "registrations_change": None,
            "registrations_change_pct": None,
            "verdict": "not_triggered",
            "reason": "fe_not_primary",
            "blocking_city_keys": [],
        }

    p_scaled = _scale(prior_reg_raw, prior_scale)
    c_scaled = _scale(curr_reg_raw, curr_scale)
    reg_change = None if p_scaled is None or c_scaled is None else c_scaled - p_scaled
    reg_change_frac = (
        None if reg_change is None or p_scaled is None or p_scaled <= 0
        else reg_change / p_scaled
    )

    if override_reason is not None:
        verdict, reason = "undetermined", override_reason
    elif prior_reg_raw is None:
        verdict, reason = "undetermined", "missing_prior_registrations"
    elif curr_reg_raw is None:
        verdict, reason = "undetermined", "missing_current_registrations"
    elif prior_reg_raw <= 0:
        verdict, reason = "undetermined", "nonpositive_prior_registrations"
    elif reg_change_frac is None:
        verdict, reason = "undetermined", "registrations_change_unavailable"
    elif reg_change >= 0:
        verdict, reason = "no_registration_dip", "registrations_not_declining_vs_prior_month"
    elif reg_change_frac < REG_DIP_THRESHOLD:
        verdict, reason = "registration_dip", None
    else:
        verdict, reason = "below_threshold", "registration_dip_within_5_percent"

    return {
        "triggered": True,
        "prior_registrations_raw": prior_reg_raw,
        "current_registrations_raw": curr_reg_raw,
        "prior_registrations_scaled": None if p_scaled is None else float(p_scaled),
        "current_registrations_scaled": None if c_scaled is None else float(c_scaled),
        "registrations_change": None if reg_change is None else float(reg_change),
        "registrations_change_pct": None if reg_change_frac is None else 100 * float(reg_change_frac),
        "verdict": verdict,
        "reason": reason,
        "blocking_city_keys": list(blocking_keys or []),
    }


def _classify_service_contribution(contribution: Fraction) -> tuple[str, str | None, bool, list[str]]:
    if contribution <= 0:
        return "no_service_deterioration", "service_not_deteriorating", False, []
    flags = ["over_explained"] if contribution > 1 else []
    if contribution > SERVICE_MATERIAL_THRESHOLD:
        return "material_contributor", None, True, flags
    return "below_threshold", "contribution_at_or_below_threshold", False, flags


def _service_breakdown_verdict(
    service_results: dict[str, dict],
) -> tuple[str, str | None, list[str], list[str], list[str]]:
    material_services = sorted(
        (svc for svc in SERVICES if service_results[svc]["verdict"] == "material_contributor"),
        key=lambda svc: (-service_results[svc]["_contribution"], SERVICE_RANK[svc]),
    )
    undetermined_services = [
        svc for svc in SERVICES if service_results[svc]["verdict"] == "undetermined"
    ]
    flags = sorted({flag for item in service_results.values() for flag in item["flags"]})
    blocking = sorted({key for item in service_results.values() for key in item["blocking_city_keys"]})
    if material_services:
        verdict = "services_identified" if not undetermined_services else "partial_services_identified"
        reason = None
    elif len(undetermined_services) == len(SERVICES):
        verdict, reason = "undetermined", "all_services_undetermined"
    elif undetermined_services:
        verdict, reason = "partial_no_material_service", None
    else:
        verdict, reason = "no_material_service_contributor", None
    return verdict, reason, material_services, flags, blocking


def _public_service(item: dict) -> dict:
    return {key: value for key, value in item.items() if not key.startswith("_")}


def _evaluate_fe2rr_services(
    *,
    triggered: bool,
    prior_fe: int | None,
    curr_fe: int | None,
    prior_rr: int | None,
    curr_rr: int | None,
    service_inputs: dict[str, dict],
    same_cohort: bool,
) -> dict:
    if not triggered:
        return {
            "triggered": False,
            "method": "additive_fe2rr_delta",
            "verdict": "not_triggered",
            "reason": "fe2rr_not_primary",
            "material_services": [],
            "flags": [],
            "platform_rate_delta_pp": None,
            "other_services_contribution_pct": None,
            "services": {},
            "blocking_city_keys": [],
        }

    p_platform = _rate(prior_rr, prior_fe)
    c_platform = _rate(curr_rr, curr_fe)
    platform_delta = None if p_platform is None or c_platform is None else c_platform - p_platform

    results: dict[str, dict] = {}
    for svc in SERVICES:
        inp = service_inputs[svc]
        s_prior_fe, s_curr_fe = inp["prior_fe"], inp["curr_fe"]
        s_prior_rr, s_curr_rr = inp["prior_rr"], inp["curr_rr"]
        override_reason = inp["override_reason"]
        blocking_keys = list(inp["blocking_keys"])

        p_rate = _rate(s_prior_rr, s_prior_fe)
        c_rate = _rate(s_curr_rr, s_curr_fe)
        rate_delta = None if p_rate is None or c_rate is None else c_rate - p_rate
        contribution: Fraction | None = None
        flags: list[str] = []

        if override_reason is not None:
            verdict, reason, material = "undetermined", override_reason, None
        elif s_prior_fe is None:
            verdict, reason, material = "undetermined", "missing_prior_fe_sessions", None
        elif s_curr_fe is None:
            verdict, reason, material = "undetermined", "missing_current_fe_sessions", None
        elif s_prior_rr is None:
            verdict, reason, material = "undetermined", f"missing_prior_{svc}_rr", None
        elif s_curr_rr is None:
            verdict, reason, material = "undetermined", f"missing_current_{svc}_rr", None
        elif s_prior_fe <= 0:
            verdict, reason, material = "undetermined", "nonpositive_prior_fe_sessions", None
        elif s_curr_fe <= 0:
            verdict, reason, material = "undetermined", "nonpositive_current_fe_sessions", None
        elif s_prior_rr < 0:
            verdict, reason, material = "undetermined", f"negative_prior_{svc}_rr", None
        elif s_curr_rr < 0:
            verdict, reason, material = "undetermined", f"negative_current_{svc}_rr", None
        elif s_prior_rr == 0 and s_curr_rr == 0:
            verdict, reason, material = "not_applicable", "no_service_volume", False
        elif platform_delta is None or platform_delta >= 0:
            verdict, reason, material = "not_applicable", "no_fe2rr_dip", False
        elif rate_delta is None:
            verdict, reason, material = "undetermined", "contribution_unavailable", None
        else:
            contribution = rate_delta / platform_delta
            verdict, reason, material, flags = _classify_service_contribution(contribution)

        results[svc] = {
            "service": svc,
            "prior_rr_raw": s_prior_rr,
            "current_rr_raw": s_curr_rr,
            "prior_value": None if p_rate is None else float(p_rate),
            "current_value": None if c_rate is None else float(c_rate),
            "rate_delta_pp": None if rate_delta is None else 100 * float(rate_delta),
            "contribution_pct": None if contribution is None else 100 * float(contribution),
            "material": material,
            "verdict": verdict,
            "reason": reason,
            "flags": flags,
            "blocking_city_keys": blocking_keys,
            "_contribution": contribution,
        }

    other_pct: float | None = None
    if (
        same_cohort
        and platform_delta is not None
        and platform_delta < 0
        and prior_fe is not None
        and curr_fe is not None
        and prior_fe > 0
        and curr_fe > 0
        and prior_rr is not None
        and curr_rr is not None
        and all(results[svc]["verdict"] != "undetermined" for svc in SERVICES)
    ):
        prior_other_rr = prior_rr - sum(service_inputs[svc]["prior_rr"] for svc in SERVICES)
        curr_other_rr = curr_rr - sum(service_inputs[svc]["curr_rr"] for svc in SERVICES)
        if prior_other_rr >= 0 and curr_other_rr >= 0:
            other_delta = Fraction(curr_other_rr, curr_fe) - Fraction(prior_other_rr, prior_fe)
            other_pct = 100 * float(other_delta / platform_delta)

    verdict, reason, material_services, flags, blocking = _service_breakdown_verdict(results)
    return {
        "triggered": True,
        "method": "additive_fe2rr_delta",
        "verdict": verdict,
        "reason": reason,
        "material_services": material_services,
        "flags": flags,
        "platform_rate_delta_pp": None if platform_delta is None else 100 * float(platform_delta),
        "other_services_contribution_pct": other_pct,
        "services": {svc: _public_service(results[svc]) for svc in SERVICES},
        "blocking_city_keys": blocking,
    }


def _evaluate_rr2net_services(
    *,
    triggered: bool,
    prior_rr: int | None,
    curr_rr: int | None,
    prior_net: int | None,
    curr_net: int | None,
    service_inputs: dict[str, dict],
    same_cohort: bool,
) -> dict:
    if not triggered:
        return {
            "triggered": False,
            "method": "weighted_rr2net_rate_and_mix_delta",
            "verdict": "not_triggered",
            "reason": "rr2net_not_primary",
            "material_services": [],
            "flags": [],
            "platform_rate_delta_pp": None,
            "mix_shift_contribution_pct": None,
            "other_services_contribution_pct": None,
            "services": {},
            "blocking_city_keys": [],
        }

    p_platform = _rate(prior_net, prior_rr)
    c_platform = _rate(curr_net, curr_rr)
    platform_delta = None if p_platform is None or c_platform is None else c_platform - p_platform

    results: dict[str, dict] = {}
    for svc in SERVICES:
        inp = service_inputs[svc]
        p_plat_rr, c_plat_rr = inp["prior_platform_rr"], inp["curr_platform_rr"]
        p_plat_net = inp["prior_platform_net"]
        s_prior_rr, s_curr_rr = inp["prior_rr"], inp["curr_rr"]
        s_prior_net, s_curr_net = inp["prior_net"], inp["curr_net"]
        override_reason = inp["override_reason"]
        blocking_keys = list(inp["blocking_keys"])

        s_p_platform = _rate(p_plat_net, p_plat_rr)
        p_share = _rate(s_prior_rr, p_plat_rr)
        c_share = _rate(s_curr_rr, c_plat_rr)
        p_rate = _rate(s_prior_net, s_prior_rr)
        c_rate = _rate(s_curr_net, s_curr_rr)
        rate_delta = None if p_rate is None or c_rate is None else c_rate - p_rate
        weighted_rate_delta: Fraction | None = None
        mix_shift_delta: Fraction | None = None
        weighted_total_delta: Fraction | None = None
        rate_contribution: Fraction | None = None
        mix_contribution: Fraction | None = None
        contribution: Fraction | None = None
        flags: list[str] = []

        if override_reason is not None:
            verdict, reason, material = "undetermined", override_reason, None
        elif p_plat_rr is None:
            verdict, reason, material = "undetermined", "missing_prior_rr", None
        elif c_plat_rr is None:
            verdict, reason, material = "undetermined", "missing_current_rr", None
        elif p_plat_net is None:
            verdict, reason, material = "undetermined", "missing_prior_net", None
        elif s_prior_rr is None:
            verdict, reason, material = "undetermined", f"missing_prior_{svc}_rr", None
        elif s_curr_rr is None:
            verdict, reason, material = "undetermined", f"missing_current_{svc}_rr", None
        elif s_prior_net is None:
            verdict, reason, material = "undetermined", f"missing_prior_{svc}_net", None
        elif s_curr_net is None:
            verdict, reason, material = "undetermined", f"missing_current_{svc}_net", None
        elif p_plat_rr <= 0:
            verdict, reason, material = "undetermined", "nonpositive_prior_rr", None
        elif c_plat_rr <= 0:
            verdict, reason, material = "undetermined", "nonpositive_current_rr", None
        elif p_plat_net < 0:
            verdict, reason, material = "undetermined", "negative_prior_net", None
        elif s_prior_rr < 0:
            verdict, reason, material = "undetermined", f"negative_prior_{svc}_rr", None
        elif s_curr_rr < 0:
            verdict, reason, material = "undetermined", f"negative_current_{svc}_rr", None
        elif s_prior_net < 0:
            verdict, reason, material = "undetermined", f"negative_prior_{svc}_net", None
        elif s_curr_net < 0:
            verdict, reason, material = "undetermined", f"negative_current_{svc}_net", None
        elif s_prior_rr == 0 and s_curr_rr == 0:
            if s_prior_net == 0 and s_curr_net == 0:
                verdict, reason, material = "not_applicable", "no_service_volume", False
            else:
                verdict, reason, material = "undetermined", "contribution_unavailable", None
        elif platform_delta is None or platform_delta >= 0:
            verdict, reason, material = "not_applicable", "no_rr2net_dip", False
        elif s_p_platform is None or p_share is None or c_share is None:
            verdict, reason, material = "undetermined", "contribution_unavailable", None
        elif s_prior_rr == 0:
            if s_prior_net != 0 or c_rate is None:
                verdict, reason, material = "undetermined", "contribution_unavailable", None
            else:
                mix_shift_delta = c_share * (c_rate - s_p_platform)
                weighted_total_delta = mix_shift_delta
                contribution = weighted_total_delta / platform_delta
                mix_contribution = contribution
                verdict, reason, material, flags = _classify_service_contribution(contribution)
        elif s_curr_rr == 0:
            if s_curr_net != 0 or p_rate is None:
                verdict, reason, material = "undetermined", "contribution_unavailable", None
            else:
                mix_shift_delta = -p_share * (p_rate - s_p_platform)
                weighted_total_delta = mix_shift_delta
                contribution = weighted_total_delta / platform_delta
                mix_contribution = contribution
                verdict, reason, material, flags = _classify_service_contribution(contribution)
        elif rate_delta is None or p_rate is None:
            verdict, reason, material = "undetermined", "contribution_unavailable", None
        else:
            weighted_rate_delta = c_share * rate_delta
            mix_shift_delta = (c_share - p_share) * (p_rate - s_p_platform)
            weighted_total_delta = weighted_rate_delta + mix_shift_delta
            rate_contribution = weighted_rate_delta / platform_delta
            mix_contribution = mix_shift_delta / platform_delta
            contribution = weighted_total_delta / platform_delta
            verdict, reason, material, flags = _classify_service_contribution(contribution)

        results[svc] = {
            "service": svc,
            "prior_rr_raw": s_prior_rr,
            "current_rr_raw": s_curr_rr,
            "prior_net_raw": s_prior_net,
            "current_net_raw": s_curr_net,
            "prior_rr_share": None if p_share is None else float(p_share),
            "current_rr_share": None if c_share is None else float(c_share),
            "prior_value": None if p_rate is None else float(p_rate),
            "current_value": None if c_rate is None else float(c_rate),
            "rate_delta_pp": None if rate_delta is None else 100 * float(rate_delta),
            "weighted_rate_delta_pp": None if weighted_rate_delta is None else 100 * float(weighted_rate_delta),
            "mix_shift_delta_pp": None if mix_shift_delta is None else 100 * float(mix_shift_delta),
            "weighted_total_delta_pp": None if weighted_total_delta is None else 100 * float(weighted_total_delta),
            "rate_contribution_pct": None if rate_contribution is None else 100 * float(rate_contribution),
            "mix_shift_contribution_pct": None if mix_contribution is None else 100 * float(mix_contribution),
            "contribution_pct": None if contribution is None else 100 * float(contribution),
            "material": material,
            "verdict": verdict,
            "reason": reason,
            "flags": flags,
            "blocking_city_keys": blocking_keys,
            "_contribution": contribution,
            "_weighted_rate_delta": weighted_rate_delta,
        }

    mix_pct: float | None = None
    other_pct: float | None = None
    if (
        same_cohort
        and platform_delta is not None
        and platform_delta < 0
        and p_platform is not None
        and prior_rr is not None
        and curr_rr is not None
        and prior_rr > 0
        and curr_rr > 0
        and prior_net is not None
        and curr_net is not None
        and all(results[svc]["verdict"] != "undetermined" for svc in SERVICES)
    ):
        prior_other_rr = prior_rr - sum(service_inputs[svc]["prior_rr"] for svc in SERVICES)
        curr_other_rr = curr_rr - sum(service_inputs[svc]["curr_rr"] for svc in SERVICES)
        prior_other_net = prior_net - sum(service_inputs[svc]["prior_net"] for svc in SERVICES)
        curr_other_net = curr_net - sum(service_inputs[svc]["curr_net"] for svc in SERVICES)
        if (
            prior_other_rr >= 0
            and curr_other_rr >= 0
            and prior_other_net >= 0
            and curr_other_net >= 0
        ):
            sum_commute_rate = sum(
                (results[svc]["_weighted_rate_delta"] for svc in SERVICES if results[svc]["_weighted_rate_delta"] is not None),
                Fraction(0),
            )
            prior_other_share = Fraction(prior_other_rr, prior_rr)
            curr_other_share = Fraction(curr_other_rr, curr_rr)
            other_total_term = (
                (Fraction(curr_other_net, curr_rr) - curr_other_share * p_platform)
                - (Fraction(prior_other_net, prior_rr) - prior_other_share * p_platform)
            )
            if prior_other_rr > 0 and curr_other_rr > 0:
                other_rate_term = curr_other_share * (
                    Fraction(curr_other_net, curr_other_rr) - Fraction(prior_other_net, prior_other_rr)
                )
            else:
                other_rate_term = Fraction(0)
            mix_term = platform_delta - sum_commute_rate - other_rate_term
            other_pct = 100 * float(other_total_term / platform_delta)
            mix_pct = 100 * float(mix_term / platform_delta)

    verdict, reason, material_services, flags, blocking = _service_breakdown_verdict(results)
    return {
        "triggered": True,
        "method": "weighted_rr2net_rate_and_mix_delta",
        "verdict": verdict,
        "reason": reason,
        "material_services": material_services,
        "flags": flags,
        "platform_rate_delta_pp": None if platform_delta is None else 100 * float(platform_delta),
        "mix_shift_contribution_pct": mix_pct,
        "other_services_contribution_pct": other_pct,
        "services": {svc: _public_service(results[svc]) for svc in SERVICES},
        "blocking_city_keys": blocking,
    }


def _summarize_block(
    *,
    scope: dict,
    calendar_meta: dict,
    ftu_summary: dict,
    prior_ftu: Fraction | None,
    ftu_change: Fraction | None,
    block_reason: str | None,
    stage_results: dict[str, dict],
    selected_stages: tuple[str, ...],
    prior_scale: Fraction,
    curr_scale: Fraction,
    reg_inputs: tuple[int | None, int | None, str | None, list[str], dict | None],
    service_inputs: dict,
    coverage: dict | None = None,
    members: list[dict] | None = None,
    omitted_member_count: int = 0,
    blocking_city_keys: list[str] | None = None,
) -> dict:
    material_stages = sorted(
        (name for name in selected_stages if stage_results[name]["verdict"] == "material_contributor"),
        key=lambda name: (-stage_results[name]["_contribution"], STAGE_RANK[name]),
    )
    primary_stages: list[str] = []
    cumulative = Fraction(0)
    for name in material_stages:
        primary_stages.append(name)
        cumulative += stage_results[name]["_contribution"]
        if cumulative > CUMULATIVE_ISSUE_THRESHOLD:
            break
    reached_70 = bool(primary_stages and cumulative > CUMULATIVE_ISSUE_THRESHOLD)
    cumulative_pct = None if not primary_stages else 100 * float(cumulative)

    undetermined_stages = [
        name for name in selected_stages if stage_results[name]["verdict"] == "undetermined"
    ]
    flags = sorted({flag for item in stage_results.values() for flag in item["flags"]})

    if block_reason is not None:
        verdict, reason = "undetermined", block_reason
    elif ftu_change is None or prior_ftu is None or prior_ftu <= 0:
        verdict, reason = "undetermined", "ftu_change_unavailable"
    elif ftu_change >= 0:
        verdict, reason = "no_historical_ftu_dip", "ftu_not_declining_vs_prior_month"
    elif ftu_change / prior_ftu >= FTU_DIP_THRESHOLD:
        verdict, reason = "no_material_ftu_dip", "ftu_dip_within_5_percent"
    elif material_stages:
        verdict = "contributors_identified" if not undetermined_stages else "partial_contributors_identified"
        reason = None
    elif len(undetermined_stages) == len(selected_stages):
        verdict, reason = "undetermined", "all_requested_stages_undetermined"
    elif undetermined_stages:
        verdict, reason = "partial_no_material_contributor", None
    else:
        verdict, reason = "no_material_funnel_contributor", None

    public_stages = {
        name: _public_stage(
            item,
            selected_as_primary=None if item["verdict"] == "undetermined" else name in primary_stages,
        )
        for name, item in stage_results.items()
    }

    reg_triggered = "fe" in primary_stages
    prior_reg_raw, curr_reg_raw, reg_override, reg_blocking, reg_cov = reg_inputs
    registration_check = _evaluate_registration_check(
        triggered=reg_triggered,
        prior_reg_raw=prior_reg_raw,
        curr_reg_raw=curr_reg_raw,
        prior_scale=prior_scale,
        curr_scale=curr_scale,
        override_reason=reg_override,
        blocking_keys=reg_blocking,
    )

    fe2rr_svc_triggered = "fe2rr" in primary_stages
    fe2rr_svc_cfg = service_inputs["fe2rr"]
    fe2rr_services = _evaluate_fe2rr_services(
        triggered=fe2rr_svc_triggered,
        prior_fe=fe2rr_svc_cfg["prior_fe"],
        curr_fe=fe2rr_svc_cfg["curr_fe"],
        prior_rr=fe2rr_svc_cfg["prior_rr"],
        curr_rr=fe2rr_svc_cfg["curr_rr"],
        service_inputs=fe2rr_svc_cfg["services"],
        same_cohort=fe2rr_svc_cfg["same_cohort"],
    )

    rr2net_svc_triggered = "rr2net" in primary_stages
    rr2net_svc_cfg = service_inputs["rr2net"]
    rr2net_services = _evaluate_rr2net_services(
        triggered=rr2net_svc_triggered,
        prior_rr=rr2net_svc_cfg["prior_rr"],
        curr_rr=rr2net_svc_cfg["curr_rr"],
        prior_net=rr2net_svc_cfg["prior_net"],
        curr_net=rr2net_svc_cfg["curr_net"],
        service_inputs=rr2net_svc_cfg["services"],
        same_cohort=rr2net_svc_cfg["same_cohort"],
    )

    public_coverage = None if coverage is None else dict(coverage)
    if public_coverage is not None:
        if reg_triggered:
            if reg_cov is not None:
                public_coverage["registrations"] = reg_cov
            elif "stage_blocked_city_counts" in public_coverage:
                public_coverage["registrations_blocked_city_count"] = len(reg_blocking)
        if fe2rr_svc_triggered:
            if fe2rr_svc_cfg["coverage"] is not None:
                public_coverage["fe2rr_services"] = fe2rr_svc_cfg["coverage"]
            elif "stage_blocked_city_counts" in public_coverage:
                public_coverage["fe2rr_service_blocked_city_counts"] = {
                    svc: len(fe2rr_svc_cfg["services"][svc]["blocking_keys"]) for svc in SERVICES
                }
        if rr2net_svc_triggered:
            if rr2net_svc_cfg["coverage"] is not None:
                public_coverage["rr2net_services"] = rr2net_svc_cfg["coverage"]
            elif "stage_blocked_city_counts" in public_coverage:
                public_coverage["rr2net_service_blocked_city_counts"] = {
                    svc: len(rr2net_svc_cfg["services"][svc]["blocking_keys"]) for svc in SERVICES
                }

    return {
        **scope,
        "calendar_adjustment": calendar_meta,
        **ftu_summary,
        "verdict": verdict,
        "reason": reason,
        "material_stages": material_stages,
        "primary_stages": primary_stages,
        "cumulative_primary_contribution_pct": cumulative_pct,
        "primary_coverage_reached_70_pct": reached_70,
        "flags": flags,
        "stages": public_stages,
        "registration_check": registration_check,
        "service_breakdown": {
            "fe2rr": fe2rr_services,
            "rr2net": rr2net_services,
        },
        "coverage": public_coverage,
        "members": members,
        "omitted_member_count": omitted_member_count,
        "blocking_city_keys": list(blocking_city_keys or []),
    }


def _scope(kind: str, label: str, current: str, prior: str, keys: list[str]) -> dict:
    scope = {
        "kind": kind,
        "label": label,
        "month": current,
        "prior_month": prior,
        "city_keys": keys,
    }
    if kind == "city":
        scope["city"] = label
        scope["city_key"] = keys[0] if keys else None
    return scope


def _month_prereq(context: dict) -> str | None:
    if not context["current_stored"]:
        return "no_stored_month"
    if not context["prior_stored"]:
        return "no_prior_month"
    return None


def _evaluate_selected_stages(
    selected_stages: tuple[str, ...],
    counts: dict,
    prior_scale: Fraction,
    curr_scale: Fraction,
    prior_ftu: Fraction | None,
    ftu_change: Fraction | None,
    block_reason: str | None,
    stage_overrides: dict[str, tuple[str | None, list[str]]] | None = None,
) -> dict[str, dict]:
    overrides = stage_overrides or {}
    results = {}
    for stage in selected_stages:
        override_reason, blocking = overrides.get(stage, (None, []))
        if stage == "fe":
            results["fe"] = _evaluate_fe(
                counts["prior_fe"], counts["curr_fe"], prior_scale, curr_scale,
                prior_ftu, ftu_change, block_reason, override_reason, blocking,
            )
        elif stage == "fe2rr":
            results["fe2rr"] = _evaluate_fe2rr(
                counts["prior_fe2rr_fe"], counts["curr_fe2rr_fe"],
                counts["prior_fe2rr_rr"], counts["curr_fe2rr_rr"],
                prior_ftu, ftu_change, block_reason, override_reason, blocking,
            )
        else:
            results["rr2net"] = _evaluate_rr2net(
                counts["prior_rr2net_rr"], counts["curr_rr2net_rr"],
                counts["prior_rr2net_net"], counts["curr_rr2net_net"],
                prior_ftu, ftu_change, block_reason, override_reason, blocking,
            )
    return results


def _city_block(context: dict, key: str, name: str, selected_stages: tuple[str, ...]) -> dict:
    prior_fact = context["prior_facts"].get(key)
    curr_fact = context["current_facts"].get(key)
    prereq = _month_prereq(context)
    if prereq is None:
        if curr_fact is None:
            prereq = "no_stored_city_observation"
        elif prior_fact is None:
            prereq = "no_prior_city_observation"

    ftu_summary, p_ftu, ftu_change, block_reason = _ftu_summary(
        None if prior_fact is None else prior_fact["platform_ftu"],
        None if curr_fact is None else curr_fact["platform_ftu"],
        context["prior_scale"],
        context["current_scale"],
        prereq,
    )
    counts = {
        "prior_fe": None if prior_fact is None else prior_fact["fe_sessions"],
        "curr_fe": None if curr_fact is None else curr_fact["fe_sessions"],
        "prior_fe2rr_fe": None if prior_fact is None else prior_fact["fe_sessions"],
        "curr_fe2rr_fe": None if curr_fact is None else curr_fact["fe_sessions"],
        "prior_fe2rr_rr": None if prior_fact is None else prior_fact["rr"],
        "curr_fe2rr_rr": None if curr_fact is None else curr_fact["rr"],
        "prior_rr2net_rr": None if prior_fact is None else prior_fact["rr"],
        "curr_rr2net_rr": None if curr_fact is None else curr_fact["rr"],
        "prior_rr2net_net": None if prior_fact is None else prior_fact["net"],
        "curr_rr2net_net": None if curr_fact is None else curr_fact["net"],
    }
    stage_results = _evaluate_selected_stages(
        selected_stages, counts, context["prior_scale"], context["current_scale"],
        p_ftu, ftu_change, block_reason,
    )
    label = (curr_fact or prior_fact or {}).get("city", name)
    reg_inputs = (
        None if prior_fact is None else prior_fact["registrations"],
        None if curr_fact is None else curr_fact["registrations"],
        None,
        [],
        None,
    )
    service_inputs = {
        "fe2rr": {
            "prior_fe": counts["prior_fe2rr_fe"],
            "curr_fe": counts["curr_fe2rr_fe"],
            "prior_rr": counts["prior_fe2rr_rr"],
            "curr_rr": counts["curr_fe2rr_rr"],
            "same_cohort": True,
            "coverage": None,
            "services": {
                svc: {
                    "prior_fe": counts["prior_fe2rr_fe"],
                    "curr_fe": counts["curr_fe2rr_fe"],
                    "prior_rr": None if prior_fact is None else prior_fact[SERVICE_FIELDS[svc][0]],
                    "curr_rr": None if curr_fact is None else curr_fact[SERVICE_FIELDS[svc][0]],
                    "override_reason": None,
                    "blocking_keys": [],
                }
                for svc in SERVICES
            },
        },
        "rr2net": {
            "prior_rr": counts["prior_rr2net_rr"],
            "curr_rr": counts["curr_rr2net_rr"],
            "prior_net": counts["prior_rr2net_net"],
            "curr_net": counts["curr_rr2net_net"],
            "same_cohort": True,
            "coverage": None,
            "services": {
                svc: {
                    "prior_platform_rr": counts["prior_rr2net_rr"],
                    "curr_platform_rr": counts["curr_rr2net_rr"],
                    "prior_platform_net": counts["prior_rr2net_net"],
                    "prior_rr": None if prior_fact is None else prior_fact[SERVICE_FIELDS[svc][0]],
                    "curr_rr": None if curr_fact is None else curr_fact[SERVICE_FIELDS[svc][0]],
                    "prior_net": None if prior_fact is None else prior_fact[SERVICE_FIELDS[svc][1]],
                    "curr_net": None if curr_fact is None else curr_fact[SERVICE_FIELDS[svc][1]],
                    "override_reason": None,
                    "blocking_keys": [],
                }
                for svc in SERVICES
            },
        },
    }
    return _summarize_block(
        scope=_scope("city", label, context["month"], context["prior_month"], [key]),
        calendar_meta=context["calendar_adjustment"],
        ftu_summary=ftu_summary,
        prior_ftu=p_ftu,
        ftu_change=ftu_change,
        block_reason=block_reason,
        stage_results=stage_results,
        selected_stages=selected_stages,
        prior_scale=context["prior_scale"],
        curr_scale=context["current_scale"],
        reg_inputs=reg_inputs,
        service_inputs=service_inputs,
    )


def _compact_member(block: dict) -> dict:
    return {
        "city": block["city"],
        "city_key": block["city_key"],
        "ftu_change": block["ftu_change"],
        "ftu_change_pct": block["ftu_change_pct"],
        "verdict": block["verdict"],
        "material_stages": block["material_stages"],
        "primary_stages": block["primary_stages"],
        "registration_check_verdict": block["registration_check"]["verdict"],
        "fe2rr_service_drivers": block["service_breakdown"]["fe2rr"]["material_services"],
        "rr2net_service_drivers": block["service_breakdown"]["rr2net"]["material_services"],
        "reason": block["reason"],
    }


def _strict_sum(
    keys: list[str],
    prior_facts: dict[str, dict],
    curr_facts: dict[str, dict],
    fields: tuple[str, ...],
) -> tuple[dict[str, int | None], dict[str, int | None], list[str]]:
    blocking = []
    for key in keys:
        p_row, c_row = prior_facts.get(key), curr_facts.get(key)
        if p_row is None or c_row is None:
            blocking.append(key)
            continue
        if any(p_row[field] is None or c_row[field] is None for field in fields):
            blocking.append(key)
    if not keys or blocking:
        empty = {field: None for field in fields}
        return empty, empty, blocking
    prior_totals = {field: sum(prior_facts[key][field] for key in keys) for field in fields}
    curr_totals = {field: sum(curr_facts[key][field] for key in keys) for field in fields}
    return prior_totals, curr_totals, []


def _strict_aggregate_block(
    context: dict,
    kind: str,
    label: str,
    keys: list[str],
    selected_stages: tuple[str, ...],
    empty_reason: str,
) -> dict:
    prereq = _month_prereq(context)
    prior_facts, curr_facts = context["prior_facts"], context["current_facts"]

    if prereq is None and not keys:
        prereq = empty_reason

    p_ftu_map, c_ftu_map, ftu_blocking = _strict_sum(keys, prior_facts, curr_facts, ("platform_ftu",))
    if prereq is None and ftu_blocking:
        prereq = "incomplete_group_member"

    ftu_summary, p_ftu, ftu_change, block_reason = _ftu_summary(
        p_ftu_map["platform_ftu"],
        c_ftu_map["platform_ftu"],
        context["prior_scale"],
        context["current_scale"],
        prereq,
    )

    p_fe, c_fe, fe_block = _strict_sum(keys, prior_facts, curr_facts, ("fe_sessions",))
    p_fe2rr, c_fe2rr, fe2rr_block = _strict_sum(keys, prior_facts, curr_facts, ("fe_sessions", "rr"))
    p_rr2net, c_rr2net, rr2net_block = _strict_sum(keys, prior_facts, curr_facts, ("rr", "net"))
    p_reg, c_reg, reg_block = _strict_sum(keys, prior_facts, curr_facts, ("registrations",))

    counts = {
        "prior_fe": p_fe["fe_sessions"],
        "curr_fe": c_fe["fe_sessions"],
        "prior_fe2rr_fe": p_fe2rr["fe_sessions"],
        "curr_fe2rr_fe": c_fe2rr["fe_sessions"],
        "prior_fe2rr_rr": p_fe2rr["rr"],
        "curr_fe2rr_rr": c_fe2rr["rr"],
        "prior_rr2net_rr": p_rr2net["rr"],
        "curr_rr2net_rr": c_rr2net["rr"],
        "prior_rr2net_net": p_rr2net["net"],
        "curr_rr2net_net": c_rr2net["net"],
    }
    stage_overrides = {
        "fe": ("incomplete_group_member" if fe_block else None, fe_block),
        "fe2rr": ("incomplete_group_member" if fe2rr_block else None, fe2rr_block),
        "rr2net": ("incomplete_group_member" if rr2net_block else None, rr2net_block),
    }
    stage_results = _evaluate_selected_stages(
        selected_stages, counts, context["prior_scale"], context["current_scale"],
        p_ftu, ftu_change, block_reason, stage_overrides,
    )

    member_blocks = [
        _city_block(
            context,
            key,
            (curr_facts.get(key) or prior_facts.get(key) or {}).get("city", key),
            selected_stages,
        )
        for key in keys
    ]
    member_blocks.sort(key=lambda item: (item["ftu_change"] is None, item["ftu_change"] or 0, item["city"], item["city_key"]))

    stage_blocked = {
        stage: len(stage_overrides[stage][1]) for stage in selected_stages
    }
    coverage = {
        "member_city_count": len(keys),
        "ftu_blocked_city_count": len(ftu_blocking),
        "stage_blocked_city_counts": stage_blocked,
    }
    reg_inputs = (
        p_reg["registrations"],
        c_reg["registrations"],
        "incomplete_group_member" if reg_block else None,
        reg_block,
        None,
    )
    fe2rr_svc_map: dict[str, dict] = {}
    rr2net_svc_map: dict[str, dict] = {}
    for svc in SERVICES:
        rr_col, net_col = SERVICE_FIELDS[svc]
        p_s_fe2rr, c_s_fe2rr, s_fe2rr_block = _strict_sum(keys, prior_facts, curr_facts, ("fe_sessions", rr_col))
        fe2rr_svc_map[svc] = {
            "prior_fe": p_s_fe2rr["fe_sessions"],
            "curr_fe": c_s_fe2rr["fe_sessions"],
            "prior_rr": p_s_fe2rr[rr_col],
            "curr_rr": c_s_fe2rr[rr_col],
            "override_reason": "incomplete_group_member" if s_fe2rr_block else None,
            "blocking_keys": s_fe2rr_block,
        }
        p_s_rr2net, c_s_rr2net, s_rr2net_block = _strict_sum(keys, prior_facts, curr_facts, ("rr", "net", rr_col, net_col))
        rr2net_svc_map[svc] = {
            "prior_platform_rr": p_s_rr2net["rr"],
            "curr_platform_rr": c_s_rr2net["rr"],
            "prior_platform_net": p_s_rr2net["net"],
            "prior_rr": p_s_rr2net[rr_col],
            "curr_rr": c_s_rr2net[rr_col],
            "prior_net": p_s_rr2net[net_col],
            "curr_net": c_s_rr2net[net_col],
            "override_reason": "incomplete_group_member" if s_rr2net_block else None,
            "blocking_keys": s_rr2net_block,
        }
    service_inputs = {
        "fe2rr": {
            "prior_fe": counts["prior_fe2rr_fe"],
            "curr_fe": counts["curr_fe2rr_fe"],
            "prior_rr": counts["prior_fe2rr_rr"],
            "curr_rr": counts["curr_fe2rr_rr"],
            "same_cohort": not fe2rr_block and all(not fe2rr_svc_map[svc]["blocking_keys"] for svc in SERVICES),
            "coverage": None,
            "services": fe2rr_svc_map,
        },
        "rr2net": {
            "prior_rr": counts["prior_rr2net_rr"],
            "curr_rr": counts["curr_rr2net_rr"],
            "prior_net": counts["prior_rr2net_net"],
            "curr_net": counts["curr_rr2net_net"],
            "same_cohort": not rr2net_block and all(not rr2net_svc_map[svc]["blocking_keys"] for svc in SERVICES),
            "coverage": None,
            "services": rr2net_svc_map,
        },
    }
    return _summarize_block(
        scope=_scope(kind, label, context["month"], context["prior_month"], keys),
        calendar_meta=context["calendar_adjustment"],
        ftu_summary=ftu_summary,
        prior_ftu=p_ftu,
        ftu_change=ftu_change,
        block_reason=block_reason,
        stage_results=stage_results,
        selected_stages=selected_stages,
        prior_scale=context["prior_scale"],
        curr_scale=context["current_scale"],
        reg_inputs=reg_inputs,
        service_inputs=service_inputs,
        coverage=coverage,
        members=[_compact_member(item) for item in member_blocks[:MEMBER_PAGE_SIZE]],
        omitted_member_count=max(0, len(member_blocks) - MEMBER_PAGE_SIZE),
        blocking_city_keys=ftu_blocking,
    )


def _matched_sum(
    prior_facts: dict[str, dict],
    curr_facts: dict[str, dict],
    fields: tuple[str, ...],
) -> tuple[dict[str, int | None], dict[str, int | None], dict]:
    all_keys = sorted(set(prior_facts) | set(curr_facts))
    matched = [
        key for key in all_keys
        if key in prior_facts and key in curr_facts
        and all(prior_facts[key][f] is not None and curr_facts[key][f] is not None for f in fields)
    ]
    prior_valid = sum(
        1 for key in prior_facts if all(prior_facts[key][f] is not None for f in fields)
    )
    curr_valid = sum(
        1 for key in curr_facts if all(curr_facts[key][f] is not None for f in fields)
    )
    coverage = {
        "matched_city_count": len(matched),
        "excluded_city_count": len(all_keys) - len(matched),
        "prior_observed_city_count": prior_valid,
        "current_observed_city_count": curr_valid,
    }
    if not matched:
        empty = {field: None for field in fields}
        return empty, empty, coverage
    return (
        {field: sum(prior_facts[key][field] for key in matched) for field in fields},
        {field: sum(curr_facts[key][field] for key in matched) for field in fields},
        coverage,
    )


def _national_block(context: dict, selected_stages: tuple[str, ...]) -> dict:
    prereq = _month_prereq(context)
    prior_facts, curr_facts = context["prior_facts"], context["current_facts"]

    p_ftu_map, c_ftu_map, ftu_cov = _matched_sum(prior_facts, curr_facts, ("platform_ftu",))
    if prereq is None and ftu_cov["matched_city_count"] == 0:
        prereq = "no_matched_ftu_cities"

    ftu_summary, p_ftu, ftu_change, block_reason = _ftu_summary(
        p_ftu_map["platform_ftu"],
        c_ftu_map["platform_ftu"],
        context["prior_scale"],
        context["current_scale"],
        prereq,
    )

    p_fe, c_fe, fe_cov = _matched_sum(prior_facts, curr_facts, ("fe_sessions",))
    p_fe2rr, c_fe2rr, fe2rr_cov = _matched_sum(prior_facts, curr_facts, ("fe_sessions", "rr"))
    p_rr2net, c_rr2net, rr2net_cov = _matched_sum(prior_facts, curr_facts, ("rr", "net"))
    p_reg, c_reg, reg_cov = _matched_sum(prior_facts, curr_facts, ("registrations",))

    counts = {
        "prior_fe": p_fe["fe_sessions"],
        "curr_fe": c_fe["fe_sessions"],
        "prior_fe2rr_fe": p_fe2rr["fe_sessions"],
        "curr_fe2rr_fe": c_fe2rr["fe_sessions"],
        "prior_fe2rr_rr": p_fe2rr["rr"],
        "curr_fe2rr_rr": c_fe2rr["rr"],
        "prior_rr2net_rr": p_rr2net["rr"],
        "curr_rr2net_rr": c_rr2net["rr"],
        "prior_rr2net_net": p_rr2net["net"],
        "curr_rr2net_net": c_rr2net["net"],
    }
    stage_overrides = {
        "fe": ("no_matched_stage_cities" if fe_cov["matched_city_count"] == 0 else None, []),
        "fe2rr": ("no_matched_stage_cities" if fe2rr_cov["matched_city_count"] == 0 else None, []),
        "rr2net": ("no_matched_stage_cities" if rr2net_cov["matched_city_count"] == 0 else None, []),
    }
    stage_results = _evaluate_selected_stages(
        selected_stages, counts, context["prior_scale"], context["current_scale"],
        p_ftu, ftu_change, block_reason, stage_overrides,
    )
    coverage = {"platform_ftu": ftu_cov}
    stage_cov_map = {"fe": fe_cov, "fe2rr": fe2rr_cov, "rr2net": rr2net_cov}
    for stage in selected_stages:
        coverage[stage] = stage_cov_map[stage]

    reg_inputs = (
        p_reg["registrations"],
        c_reg["registrations"],
        "no_matched_registration_cities" if reg_cov["matched_city_count"] == 0 else None,
        [],
        reg_cov,
    )
    fe2rr_svc_map: dict[str, dict] = {}
    fe2rr_svc_cov: dict[str, dict] = {}
    rr2net_svc_map: dict[str, dict] = {}
    rr2net_svc_cov: dict[str, dict] = {}
    for svc in SERVICES:
        rr_col, net_col = SERVICE_FIELDS[svc]
        p_s_fe2rr, c_s_fe2rr, s_fe2rr_cov = _matched_sum(prior_facts, curr_facts, ("fe_sessions", rr_col))
        fe2rr_svc_cov[svc] = s_fe2rr_cov
        fe2rr_svc_map[svc] = {
            "prior_fe": p_s_fe2rr["fe_sessions"],
            "curr_fe": c_s_fe2rr["fe_sessions"],
            "prior_rr": p_s_fe2rr[rr_col],
            "curr_rr": c_s_fe2rr[rr_col],
            "override_reason": "no_matched_service_cities" if s_fe2rr_cov["matched_city_count"] == 0 else None,
            "blocking_keys": [],
        }
        p_s_rr2net, c_s_rr2net, s_rr2net_cov = _matched_sum(prior_facts, curr_facts, ("rr", "net", rr_col, net_col))
        rr2net_svc_cov[svc] = s_rr2net_cov
        rr2net_svc_map[svc] = {
            "prior_platform_rr": p_s_rr2net["rr"],
            "curr_platform_rr": c_s_rr2net["rr"],
            "prior_platform_net": p_s_rr2net["net"],
            "prior_rr": p_s_rr2net[rr_col],
            "curr_rr": c_s_rr2net[rr_col],
            "prior_net": p_s_rr2net[net_col],
            "curr_net": c_s_rr2net[net_col],
            "override_reason": "no_matched_service_cities" if s_rr2net_cov["matched_city_count"] == 0 else None,
            "blocking_keys": [],
        }
    _, _, fe2rr_all_cov = _matched_sum(
        prior_facts, curr_facts, ("fe_sessions", "rr", "bike_rr", "auto_rr", "cab_rr"),
    )
    _, _, rr2net_all_cov = _matched_sum(
        prior_facts, curr_facts, ("rr", "net", "bike_rr", "bike_net", "auto_rr", "auto_net", "cab_rr", "cab_net"),
    )
    service_inputs = {
        "fe2rr": {
            "prior_fe": counts["prior_fe2rr_fe"],
            "curr_fe": counts["curr_fe2rr_fe"],
            "prior_rr": counts["prior_fe2rr_rr"],
            "curr_rr": counts["curr_fe2rr_rr"],
            "same_cohort": (
                fe2rr_cov["matched_city_count"] > 0
                and fe2rr_all_cov["matched_city_count"] == fe2rr_cov["matched_city_count"]
            ),
            "coverage": fe2rr_svc_cov,
            "services": fe2rr_svc_map,
        },
        "rr2net": {
            "prior_rr": counts["prior_rr2net_rr"],
            "curr_rr": counts["curr_rr2net_rr"],
            "prior_net": counts["prior_rr2net_net"],
            "curr_net": counts["curr_rr2net_net"],
            "same_cohort": (
                rr2net_cov["matched_city_count"] > 0
                and rr2net_all_cov["matched_city_count"] == rr2net_cov["matched_city_count"]
            ),
            "coverage": rr2net_svc_cov,
            "services": rr2net_svc_map,
        },
    }
    return _summarize_block(
        scope=_scope("national", "National (matched cities)", context["month"], context["prior_month"], []),
        calendar_meta=context["calendar_adjustment"],
        ftu_summary=ftu_summary,
        prior_ftu=p_ftu,
        ftu_change=ftu_change,
        block_reason=block_reason,
        stage_results=stage_results,
        selected_stages=selected_stages,
        prior_scale=context["prior_scale"],
        curr_scale=context["current_scale"],
        reg_inputs=reg_inputs,
        service_inputs=service_inputs,
        coverage=coverage,
    )


def _month_context(dataset: MonthlyData, current: str, available: set[str]) -> dict:
    prior = previous_month(current)
    data = dataset.data
    covered = {m["month"]: m["days_covered"] for m in month_to_date(data["month_metadata"])}
    cal_meta, prior_scale, curr_scale = _calendar_pair(prior, current, covered)
    prior_rows = [row for row in data["facts"] if row["month"] == prior]
    curr_rows = [row for row in data["facts"] if row["month"] == current]
    return {
        "month": current,
        "prior_month": prior,
        "current_stored": current in available,
        "prior_stored": prior in available,
        "prior_facts": _facts_by_key(prior_rows),
        "current_facts": _facts_by_key(curr_rows),
        "calendar_adjustment": cal_meta,
        "prior_scale": prior_scale,
        "current_scale": curr_scale,
    }


def _city_catalog(dataset: MonthlyData) -> dict[str, str]:
    catalog: dict[str, str] = {}
    for row in sorted(dataset.data["facts"], key=lambda item: (item["month"], item["city_key"])):
        if not is_unattributed(row["city_key"]):
            catalog[row["city_key"]] = row["city"]
    return catalog


def investigate_funnel(
    dataset: MonthlyData,
    *,
    requested_months: list[str],
    available_months: set[str] | None = None,
    cities: list[str] | None = None,
    groups: list[tuple[str, list[str]]] | None = None,
    tier: str | None = None,
    national: bool = False,
    stages: list[str] | tuple[str, ...] | None = None,
) -> dict:
    """Evaluate FE, FE2RR and RR2Net independently against >5% calendar-adjusted MoM FTU dips."""
    selected_stages = validate_funnel_stages(stages)
    available = set(dataset.data["months"]) if available_months is None else set(available_months)
    contexts = [_month_context(dataset, value, available) for value in requested_months]
    catalog = _city_catalog(dataset)
    city_keys = _resolve_names(catalog, cities or [])
    group_keys = _resolve_groups(catalog, groups or [])

    blocks = []
    for context in contexts:
        if national:
            blocks.append(_national_block(context, selected_stages))
        if tier is not None:
            tier_keys = sorted(
                key for key, row in context["current_facts"].items()
                if row["tier"] == tier and not is_unattributed(key)
            )
            blocks.append(
                _strict_aggregate_block(context, "tier", tier, tier_keys, selected_stages, "no_tier_cities")
            )
        for label, keys in group_keys:
            blocks.append(
                _strict_aggregate_block(context, "group", label, keys, selected_stages, "no_members")
            )
        for key, name in city_keys:
            blocks.append(_city_block(context, key, name, selected_stages))

    warnings = list(provenance(dataset)["warnings"])
    additions = [FUNNEL_PROXY_WARNING]
    if any(block["calendar_adjustment"]["adjusted"] for block in blocks):
        additions.append(FUNNEL_CALENDAR_SCALING_WARNING)
    if any(
        "over_explained" in block["flags"]
        or any("over_explained" in sb["flags"] for sb in block["service_breakdown"].values())
        for block in blocks
    ):
        additions.append(FUNNEL_OVER_EXPLAINED_WARNING)
    if national:
        additions.append(FUNNEL_NATIONAL_MATCHED_WARNING)
    unattributed = any(
        is_unattributed(key)
        for context in contexts
        for key in set(context["prior_facts"]) | set(context["current_facts"])
    )
    if unattributed and (national or tier is not None):
        additions.append(UNATTRIBUTED_SCOPE_WARNING)
    if any(
        block["verdict"] in ("undetermined", "partial_contributors_identified", "partial_no_material_contributor")
        or any(stage["verdict"] == "undetermined" for stage in block["stages"].values())
        or block["registration_check"]["verdict"] == "undetermined"
        or any(
            sb["verdict"] in ("undetermined", "partial_services_identified", "partial_no_material_service")
            or any(svc["verdict"] == "undetermined" for svc in sb["services"].values())
            for sb in block["service_breakdown"].values()
        )
        for block in blocks
    ):
        additions.append(FUNNEL_UNDETERMINED_WARNING)
    for warning in additions:
        if warning not in warnings:
            warnings.append(warning)

    return {
        "operation": "investigate-funnel",
        "metric": "platform_ftu",
        "month": requested_months[0] if len(requested_months) == 1 else None,
        "requested_months": requested_months,
        "stages": list(selected_stages),
        **provenance(dataset),
        "method": METHOD,
        "thresholds": THRESHOLDS,
        "blocks": blocks,
        "warnings": warnings,
    }
