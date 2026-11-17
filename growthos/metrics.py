"""Reviewed metric arithmetic and completeness-aware aggregate comparisons."""

import copy
from dataclasses import dataclass
from decimal import Decimal, localcontext
from importlib.resources import files
import json
from typing import Mapping

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


@dataclass(frozen=True)
class AggregateObservation:
    """A complete count (or unavailable), distinct from its observed subtotal."""

    value: int | None
    known_subtotal: int
    observed_city_count: int
    unavailable_city_count: int
    reason: str | None

    def to_dict(self) -> dict:
        return {
            "value": self.value, "known_subtotal": self.known_subtotal,
            "observed_city_count": self.observed_city_count,
            "unavailable_city_count": self.unavailable_city_count, "reason": self.reason,
        }


@dataclass(frozen=True)
class VolumeComparison:
    """Scalar city counts or explicitly selected aggregate operands."""

    previous: int | None
    latest: int | None
    absolute_delta: int | None
    growth_fraction: float | None

    def to_dict(self) -> dict:
        return {
            "previous": self.previous, "latest": self.latest,
            "absolute_delta": self.absolute_delta, "growth_fraction": self.growth_fraction,
        }


@dataclass(frozen=True)
class RatioComparison:
    """Fractional levels with movement expressed in percentage points."""

    previous: float | None
    latest: float | None
    delta_pp: float | None

    def to_dict(self) -> dict:
        return {"previous": self.previous, "latest": self.latest, "delta_pp": self.delta_pp}


@dataclass(frozen=True)
class GroupVolumeComparison:
    """Coverage-bearing observations, never interchangeable with scalar counts."""

    previous: AggregateObservation
    latest: AggregateObservation
    absolute_delta: int | None
    growth_fraction: float | None
    growth_unavailable_reason: str | None
    known_subtotal_delta: int | None
    known_subtotal_growth_fraction: float | None

    def to_dict(self) -> dict:
        return {
            "previous": self.previous.to_dict(), "latest": self.latest.to_dict(),
            "absolute_delta": self.absolute_delta, "growth_fraction": self.growth_fraction,
            "growth_unavailable_reason": self.growth_unavailable_reason,
            "known_subtotal_delta": self.known_subtotal_delta,
            "known_subtotal_growth_fraction": self.known_subtotal_growth_fraction,
        }


@dataclass(frozen=True)
class GroupRatioComparison:
    """Strict ratio movement alongside explicitly labeled subtotal evidence."""

    previous: float | None
    latest: float | None
    delta_pp: float | None
    previous_unavailable_reason: str | None
    latest_unavailable_reason: str | None
    known_subtotal_previous: float | None
    known_subtotal_latest: float | None
    known_subtotal_delta_pp: float | None

    def to_dict(self) -> dict:
        return {
            "previous": self.previous, "latest": self.latest, "delta_pp": self.delta_pp,
            "previous_unavailable_reason": self.previous_unavailable_reason,
            "latest_unavailable_reason": self.latest_unavailable_reason,
            "known_subtotal_previous": self.known_subtotal_previous,
            "known_subtotal_latest": self.known_subtotal_latest,
            "known_subtotal_delta_pp": self.known_subtotal_delta_pp,
        }


def metrics_output(
    volumes: Mapping[str, VolumeComparison | GroupVolumeComparison],
    ratios: Mapping[str, RatioComparison | GroupRatioComparison],
) -> dict:
    """Format typed results into the existing scalar or coverage-bearing JSON shape."""
    return {
        "volumes": {metric: value.to_dict() for metric, value in volumes.items()},
        "ratios": {metric: value.to_dict() for metric, value in ratios.items()},
    }


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


def compare_pair(
    before: dict | None, after: dict | None,
) -> tuple[dict[str, VolumeComparison], dict[str, RatioComparison]]:
    """Calculate scalar movements; callers choose strict values or known subtotals."""
    before, after = before or {}, after or {}
    volumes: dict[str, VolumeComparison] = {}
    ratios: dict[str, RatioComparison] = {}
    with localcontext() as context:
        context.prec = 40
        for metric in VOLUMES:
            a, b = before.get(metric), after.get(metric)
            delta = None if a is None or b is None else b - a
            volumes[metric] = VolumeComparison(a, b, delta, number(quotient(delta, a)))
        for metric, (numerator, denominator) in RATIOS.items():
            a = quotient(before.get(numerator), before.get(denominator))
            b = quotient(after.get(numerator), after.get(denominator))
            ratios[metric] = RatioComparison(
                number(a), number(b), number(None if a is None or b is None else 100 * (b - a)),
            )
    return volumes, ratios


def aggregate_observations(rows: list[dict], expected_cities: set[str]) -> dict[str, AggregateObservation]:
    missing_rows = len(expected_cities - {r["city_key"] for r in rows})
    result: dict[str, AggregateObservation] = {}
    for metric in VOLUMES:
        observed = sum(row[metric] is not None for row in rows)
        unavailable = missing_rows + sum(row[metric] is None for row in rows)
        subtotal = sum(row[metric] for row in rows if row[metric] is not None)
        result[metric] = AggregateObservation(
            value=None if unavailable else subtotal,
            known_subtotal=subtotal,
            observed_city_count=observed,
            unavailable_city_count=unavailable,
            reason="missing_city_or_operand" if unavailable else None,
        )
    return result


def compare_groups(
    before: Mapping[str, AggregateObservation], after: Mapping[str, AggregateObservation],
) -> tuple[dict[str, GroupVolumeComparison], dict[str, GroupRatioComparison]]:
    """Compare aggregate values, retaining coverage and known-subtotal evidence."""
    scalar_volumes, scalar_ratios = compare_pair(
        {m: before[m].value for m in VOLUMES},
        {m: after[m].value for m in VOLUMES},
    )
    subtotal_volumes, subtotal_ratios = compare_pair(
        {m: before[m].known_subtotal if before[m].observed_city_count else None for m in VOLUMES},
        {m: after[m].known_subtotal if after[m].observed_city_count else None for m in VOLUMES},
    )
    volumes: dict[str, GroupVolumeComparison] = {}
    ratios: dict[str, GroupRatioComparison] = {}
    for metric, values in scalar_volumes.items():
        a, delta = values.previous, values.absolute_delta
        volumes[metric] = GroupVolumeComparison(
            previous=before[metric], latest=after[metric],
            absolute_delta=delta, growth_fraction=values.growth_fraction,
            growth_unavailable_reason="missing_operand" if delta is None else "zero_baseline" if a == 0 else None,
            known_subtotal_delta=subtotal_volumes[metric].absolute_delta,
            known_subtotal_growth_fraction=subtotal_volumes[metric].growth_fraction,
        )
    for metric, (numerator, denominator) in RATIOS.items():
        ratio, subtotal = scalar_ratios[metric], subtotal_ratios[metric]
        ratios[metric] = GroupRatioComparison(
            previous=ratio.previous, latest=ratio.latest, delta_pp=ratio.delta_pp,
            previous_unavailable_reason=_ratio_unavailable_reason(before[numerator], before[denominator], ratio.previous),
            latest_unavailable_reason=_ratio_unavailable_reason(after[numerator], after[denominator], ratio.latest),
            known_subtotal_previous=subtotal.previous,
            known_subtotal_latest=subtotal.latest,
            known_subtotal_delta_pp=subtotal.delta_pp,
        )
    return volumes, ratios


def _ratio_unavailable_reason(
    numerator: AggregateObservation, denominator: AggregateObservation, value: float | None,
) -> str | None:
    if numerator.value is None or denominator.value is None:
        return "missing_operand"
    return "zero_denominator" if value is None else None


def share_gap(link: RatioComparison, auto: RatioComparison) -> RatioComparison:
    """Link-minus-Auto fractional levels and movement in percentage points."""
    return _share_gap(link.previous, auto.previous, link.latest, auto.latest)


def group_share_gap(link: GroupRatioComparison, auto: GroupRatioComparison) -> GroupRatioComparison:
    strict = _share_gap(link.previous, auto.previous, link.latest, auto.latest)
    subtotal = _share_gap(
        link.known_subtotal_previous, auto.known_subtotal_previous,
        link.known_subtotal_latest, auto.known_subtotal_latest,
    )
    return GroupRatioComparison(
        previous=strict.previous, latest=strict.latest, delta_pp=strict.delta_pp,
        previous_unavailable_reason=link.previous_unavailable_reason or auto.previous_unavailable_reason,
        latest_unavailable_reason=link.latest_unavailable_reason or auto.latest_unavailable_reason,
        known_subtotal_previous=subtotal.previous, known_subtotal_latest=subtotal.latest,
        known_subtotal_delta_pp=subtotal.delta_pp,
    )


def _share_gap(
    previous_link: float | None, previous_auto: float | None,
    latest_link: float | None, latest_auto: float | None,
) -> RatioComparison:
    # Keep the existing aggregate policy: operate on decimal representations of
    # fractional ratio levels, not binary-float subtraction or recomputed ratios.
    with localcontext() as context:
        context.prec = 40
        previous = (
            None if previous_link is None or previous_auto is None
            else Decimal(str(previous_link)) - Decimal(str(previous_auto))
        )
        latest = (
            None if latest_link is None or latest_auto is None
            else Decimal(str(latest_link)) - Decimal(str(latest_auto))
        )
        movement = None if previous is None or latest is None else 100 * (latest - previous)
        return RatioComparison(number(previous), number(latest), number(movement))


# Preserve dictionary-returning calculation interfaces for existing callers.
# Analytical operations use the typed functions above and format only their output.
def pair_metrics(before: dict | None, after: dict | None) -> tuple[dict, dict]:
    output = metrics_output(*compare_pair(before, after))
    return output["volumes"], output["ratios"]


def aggregate(rows: list[dict], expected_cities: set[str]) -> dict:
    return {metric: value.to_dict() for metric, value in aggregate_observations(rows, expected_cities).items()}


def group_metrics(before: dict, after: dict) -> tuple[dict, dict]:
    output = metrics_output(*compare_groups(
        {m: AggregateObservation(**value) for m, value in before.items()},
        {m: AggregateObservation(**value) for m, value in after.items()},
    ))
    return output["volumes"], output["ratios"]
