"""Reviewed metric arithmetic and completeness-aware aggregate comparisons."""

import copy
from decimal import Decimal, localcontext
from importlib.resources import files
import json

from .contracts import RATIOS, VOLUMES, text
from .errors import reject

ALL_METRICS = VOLUMES + tuple(RATIOS)
_VOLUME_FIELDS = {
    "id", "label", "aliases", "kind", "unit", "subject_unit",
    "display_unit", "movement_unit", "definition", "caveats",
}
_RATIO_FIELDS = {
    "id", "label", "aliases", "kind", "unit", "display_unit", "movement_unit",
    "numerator", "denominator", "formula", "definition", "caveats",
}
_MOVEMENT_KEYS = {"absolute_delta", "growth_fraction", "delta_pp", "link_vs_auto_share_gap"}


def _string_list(value: object, *, allow_empty: bool = False, max_len: int = 240) -> bool:
    if not isinstance(value, list) or (not allow_empty and not value):
        return False
    return all(
        isinstance(item, str) and 0 < len(item) <= max_len and item == item.strip() and all(c.isprintable() for c in item)
        for item in value
    )


def _long_text(value: object) -> bool:
    return isinstance(value, str) and 0 < len(value) <= 400 and value == value.strip() and all(c.isprintable() for c in value)


def _load_metric_registry(raw_text: str | None = None) -> dict:
    try:
        payload = json.loads(
            files("growthos").joinpath("metric_registry.json").read_text(encoding="utf-8")
            if raw_text is None else raw_text
        )
    except (OSError, ValueError):
        reject("SCHEMA", "Packaged metric registry is unreadable or invalid JSON.")
    if not isinstance(payload, dict) or set(payload) != {"metrics", "movements", "glossary_distinctions", "rules"}:
        reject("SCHEMA", "Metric registry top-level keys do not match the approved store shape.")
    metrics = payload["metrics"]
    if not isinstance(metrics, dict) or tuple(metrics) != ALL_METRICS:
        reject("SCHEMA", "Metric registry must cover the exact V1 volume and ratio metrics in order.")

    seen_aliases: set[str] = set()
    for metric_id, entry in metrics.items():
        if not isinstance(entry, dict) or entry.get("id") != metric_id:
            reject("SCHEMA", "Metric registry entry ID mismatch.")
        if metric_id in VOLUMES:
            allowed = _VOLUME_FIELDS | ({"service_membership"} if metric_id == "platform_link_ftu" else set())
            if set(entry) != allowed or entry.get("kind") != "volume" or entry.get("unit") != "count":
                reject("SCHEMA", "Volume metric registry entry has unexpected fields or unit.")
            expected_subject = "installations" if metric_id == "installs" else "customers"
            if (
                entry.get("subject_unit") != expected_subject
                or entry.get("display_unit") != expected_subject
                or entry.get("movement_unit") != expected_subject
            ):
                reject("SCHEMA", "Volume metric subject or display unit mismatch.")
            if "service_membership" in entry and not _string_list(entry["service_membership"], max_len=80):
                reject("SCHEMA", "Invalid Bike/Link service_membership list in metric registry.")
        else:
            num_col, den_col = RATIOS[metric_id]
            if (
                set(entry) != _RATIO_FIELDS
                or entry.get("kind") != "ratio"
                or entry.get("unit") != "fraction"
                or entry.get("display_unit") != "percent"
                or entry.get("movement_unit") != "percentage_points"
                or entry.get("numerator") != num_col
                or entry.get("denominator") != den_col
                or entry.get("formula") != f"{num_col} / {den_col}"
            ):
                reject("SCHEMA", "Ratio metric registry entry disagrees with contract operands or units.")
        if (
            not text(entry.get("label"))
            or not _string_list(entry.get("aliases"), allow_empty=True, max_len=80)
            or not _long_text(entry.get("definition"))
            or not _string_list(entry.get("caveats"), max_len=320)
        ):
            reject("SCHEMA", "Metric registry text, aliases, or caveats are invalid.")
        for token in [metric_id, entry["label"], *entry["aliases"]]:
            norm = token.strip().lower()
            if norm in {"svn", "service ftu", "rr2net", "net rides", "net customers"}:
                reject("SCHEMA", "Metric registry alias collides with an excluded or distinct glossary term.")
        for alias in entry["aliases"]:
            norm_alias = alias.strip().lower()
            if norm_alias in seen_aliases:
                reject("SCHEMA", "Duplicate alias across metric registry entries.")
            seen_aliases.add(norm_alias)

    movements = payload["movements"]
    if not isinstance(movements, dict) or set(movements) != _MOVEMENT_KEYS:
        reject("SCHEMA", "Metric registry movements do not match expected V1 movement keys.")
    for move_id, move in movements.items():
        if (
            not isinstance(move, dict)
            or move.get("id") != move_id
            or not text(move.get("label"))
            or not text(move.get("unit"))
            or not text(move.get("display_unit"))
            or not text(move.get("formula"))
            or not _long_text(move.get("definition"))
        ):
            reject("SCHEMA", "Invalid movement definition in metric registry.")
    gap = movements["link_vs_auto_share_gap"]
    if gap.get("operands") != ["link_share", "auto_share"] or gap.get("movement_unit") != "percentage_points":
        reject("SCHEMA", "Invalid link_vs_auto_share_gap definition in metric registry.")

    glossary = payload["glossary_distinctions"]
    if not isinstance(glossary, dict) or set(glossary) != {"svn", "rr2net"}:
        reject("SCHEMA", "Metric registry glossary_distinctions must define svn and rr2net.")
    for item in glossary.values():
        if (
            not isinstance(item, dict)
            or item.get("supported_in_v1") is not False
            or not text(item.get("term"))
            or not _long_text(item.get("definition"))
        ):
            reject("SCHEMA", "Invalid glossary distinction in metric registry.")

    rules = payload["rules"]
    if (
        not isinstance(rules, dict)
        or set(rules) != {"reporting_period", "missing_and_zero_values", "aggregation", "interpretation"}
        or not all(_long_text(v) for v in rules.values())
    ):
        reject("SCHEMA", "Invalid shared rules in metric registry.")
    return payload


_REGISTRY = _load_metric_registry()


def metric_registry() -> dict:
    """Return a detached copy of the packaged business-first V1 IRF metric store."""
    return copy.deepcopy(_REGISTRY)


def quotient(numerator: int | Decimal | None, denominator: int | Decimal | None) -> Decimal | None:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return Decimal(numerator) / Decimal(denominator)


def number(value: Decimal | None) -> float | None:
    # Counts remain integers; only fractional output uses JSON numbers.
    return None if value is None else float(value)


def definitions() -> dict:
    store = metric_registry()
    return {
        **store["metrics"],
        **store["movements"],
        "glossary_distinctions": store["glossary_distinctions"],
        "rules": store["rules"],
    }


def pair_metrics(before: dict | None, after: dict | None) -> tuple[dict, dict]:
    """Calculate scalar movements; callers choose strict values or known subtotals."""
    before, after = before or {}, after or {}
    volumes, ratios = {}, {}
    with localcontext() as context:
        context.prec = 40
        for metric in VOLUMES:
            a, b = before.get(metric), after.get(metric)
            delta = None if a is None or b is None else b - a
            volumes[metric] = {
                "previous": a, "latest": b, "absolute_delta": delta,
                "growth_fraction": number(quotient(delta, a)),
            }
        for metric, (numerator, denominator) in RATIOS.items():
            a = quotient(before.get(numerator), before.get(denominator))
            b = quotient(after.get(numerator), after.get(denominator))
            ratios[metric] = {
                "previous": number(a), "latest": number(b),
                "delta_pp": number(None if a is None or b is None else 100 * (b - a)),
            }
    return volumes, ratios


def aggregate(rows: list[dict], expected_cities: set[str]) -> dict:
    missing_rows = len(expected_cities - {r["city_key"] for r in rows})
    result = {}
    for metric in VOLUMES:
        observed = sum(row[metric] is not None for row in rows)
        unavailable = missing_rows + sum(row[metric] is None for row in rows)
        subtotal = sum(row[metric] for row in rows if row[metric] is not None)
        result[metric] = {
            "value": None if unavailable else subtotal,
            "known_subtotal": subtotal,
            "observed_city_count": observed,
            "unavailable_city_count": unavailable,
            "reason": "missing_city_or_operand" if unavailable else None,
        }
    return result


def group_metrics(before: dict, after: dict) -> tuple[dict, dict]:
    """Compare aggregate values, retaining coverage and known-subtotal evidence."""
    volumes, ratios = pair_metrics(
        {m: before[m]["value"] for m in VOLUMES},
        {m: after[m]["value"] for m in VOLUMES},
    )
    subtotal_volumes, subtotal_ratios = pair_metrics(
        {m: before[m]["known_subtotal"] if before[m]["observed_city_count"] else None for m in VOLUMES},
        {m: after[m]["known_subtotal"] if after[m]["observed_city_count"] else None for m in VOLUMES},
    )
    for metric, values in volumes.items():
        a, delta = values["previous"], values["absolute_delta"]
        values.update({
            "previous": before[metric], "latest": after[metric],
            "growth_unavailable_reason": "missing_operand" if delta is None else "zero_baseline" if a == 0 else None,
            "known_subtotal_delta": subtotal_volumes[metric]["absolute_delta"],
            "known_subtotal_growth_fraction": subtotal_volumes[metric]["growth_fraction"],
        })
    for metric, (numerator, denominator) in RATIOS.items():
        values, subtotal = ratios[metric], subtotal_ratios[metric]
        for side, operands in (("previous", before), ("latest", after)):
            values[f"{side}_unavailable_reason"] = (
                "missing_operand" if operands[numerator]["value"] is None or operands[denominator]["value"] is None
                else "zero_denominator" if values[side] is None else None
            )
        values.update({
            "known_subtotal_previous": subtotal["previous"],
            "known_subtotal_latest": subtotal["latest"],
            "known_subtotal_delta_pp": subtotal["delta_pp"],
        })
    return volumes, ratios
