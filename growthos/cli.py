"""Bounded IRF analysis CLI."""

from __future__ import annotations

import argparse
import sys

from .analysis import (
    city_funnels,
    compare_overall,
    rank_cities,
    share_drivers,
    target_cities,
    target_facts,
)
from .contracts import month_range, previous_month, validate_data
from .errors import DiagnosticStage, MAX_OUTPUT_BYTES, SafeParser, diagnostic_stage, execute_cli, reject
from .evidence import provenance
from .funnel_investigation import investigate_funnel
from .investigation import investigate
from .metrics import ALL_METRICS
from .postgres import connect_reader
from .queries import warehouse_output, warehouse_query_ids
from .selection import (
    FUNNEL_STAGES,
    INVESTIGATION_MAX_BLOCKS,
    TARGET_ATTAINMENTS,
    TARGET_FACT_SORTS,
    funnel_investigation_block_count,
    investigation_block_count,
    normalize_tier,
    parse_investigation_group,
    validate_funnel_investigation_request,
    validate_investigation_request,
    validate_target_facts_request,
    validate_target_request,
)


def _add_period_args(command: argparse.ArgumentParser) -> None:
    command.add_argument("--previous", help="YYYY-MM; requires --latest")
    command.add_argument("--latest", help="YYYY-MM; requires --previous")


TARGET_COMMANDS = ("target-analysis", "target-facts")
PERIOD_COMMANDS = ("compare", "rank", "funnel", "share-drivers")
COMMANDS = ("status", *PERIOD_COMMANDS, "warehouse-queries", "verify", *TARGET_COMMANDS, "investigate", "investigate-funnel")


def parser() -> argparse.ArgumentParser:
    root = SafeParser(description="GrowthOS IRF — analytical CLI.", allowed_commands=COMMANDS)
    commands = root.add_subparsers(dest="command", required=True, parser_class=SafeParser)

    status_cmd = commands.add_parser("status", command_name="status")
    status_cmd.add_argument("--city-offset", type=int, default=0, help="bounded city catalog page offset")

    compare_cmd = commands.add_parser("compare", command_name="compare")
    _add_period_args(compare_cmd)
    compare_cmd.add_argument("--group-by", choices=("overall", "tier", "city"), default="overall", help="grouping grain")
    compare_cmd.add_argument("--tier", help="filter to T1, T2, or T3 (includes unmapped cities)")
    compare_cmd.add_argument("--city", action="append", default=[], help="repeatable city filter (with --group-by city)")
    compare_cmd.add_argument("--top", type=int, default=25, help="bounded city count when --group-by city")

    rank_cmd = commands.add_parser("rank", command_name="rank")
    _add_period_args(rank_cmd)
    rank_cmd.add_argument("--metric", choices=ALL_METRICS, default="platform_ftu", help="metric to rank by")
    rank_cmd.add_argument("--top", type=int, default=20, help="top N cities (1..100)")
    rank_cmd.add_argument("--tier", help="optional tier filter (T1, T2, T3)")
    rank_cmd.add_argument("--ascending", action="store_true", help="show largest declines first")
    rank_cmd.add_argument("--basis", choices=("delta", "growth"), default="delta", help="sort by absolute/pp delta or fractional growth")

    funnel_cmd = commands.add_parser("funnel", command_name="funnel")
    _add_period_args(funnel_cmd)
    funnel_cmd.add_argument("--top", type=int, default=10, help="top N cities (1..100)")
    funnel_cmd.add_argument("--tier", help="optional tier filter (T1, T2, T3)")
    funnel_cmd.add_argument("--city", action="append", default=[], help="repeatable city filter")
    funnel_cmd.add_argument("--sort", default="platform_ftu_delta", help="sort key, e.g. platform_ftu_delta")
    funnel_cmd.add_argument("--ascending", action="store_true", help="show lowest values first")
    funnel_cmd.add_argument(
        "--column", action="append",
        help="repeat to select flat columns: city, city_key, tier, or <metric>[_previous|_mom_abs|_mom_pct|_delta_pp] (aliases: _latest, _delta, _growth)",
    )

    share_cmd = commands.add_parser("share-drivers", command_name="share-drivers")
    _add_period_args(share_cmd)
    share_cmd.add_argument("--service", choices=("all", "link", "bike", "auto", "cab"), default="all", help="service share to decompose")
    share_cmd.add_argument("--top", type=int, default=15, help="top N growth and decline cities (1..100)")
    share_cmd.add_argument("--tier", help="optional tier filter (T1, T2, T3)")

    queries_cmd = commands.add_parser("warehouse-queries", command_name="warehouse-queries")
    queries_cmd.add_argument("--metric", choices=ALL_METRICS, help="only queries that produce this metric")
    queries_cmd.add_argument("--id", dest="query_id", choices=warehouse_query_ids(), help="one packaged warehouse query")

    verify_cmd = commands.add_parser("verify", command_name="verify")
    verify_cmd.add_argument("--start", help="first stored month; requires --end; defaults to latest two")
    verify_cmd.add_argument("--end", help="last stored month; requires --start")

    target_cmd = commands.add_parser("target-analysis", command_name="target-analysis")
    target_cmd.add_argument("--month", required=True, help="explicit ended month, YYYY-MM")
    target_cmd.add_argument("--mode", choices=("top", "all"), default="top", help="overall-shortfall coverage or every qualifying city")
    target_cmd.add_argument("--offset", type=int, default=0, help="page offset after complete selection")
    target_cmd.add_argument("--limit", type=int, default=25, help="page size (1..100); not a selection cap")
    target_cmd.add_argument("--tier", help="stored monthly tier T1/T2/T3; cover that tier's own plan-matched city-target gap")
    target_cmd.add_argument("--queue", choices=("matched", "fallback"), default="matched",
                            help="matched city targets (default) or the pooled New launch fallback comparison")

    facts_cmd = commands.add_parser("target-facts", command_name="target-facts")
    facts_cmd.add_argument("--month", required=True, help="explicit ended month, YYYY-MM")
    facts_cmd.add_argument("--city", action="append", default=[], help="repeatable canonical city/name filter; overall totals stay month-wide")
    facts_cmd.add_argument("--attainment", choices=TARGET_ATTAINMENTS, default="all", help="exact actual-versus-target predicate; no implicit 5%% filter")
    facts_cmd.add_argument("--sort", choices=TARGET_FACT_SORTS, default="city", help="sort field; unavailable values always last")
    facts_cmd.add_argument("--descending", action="store_true", help="reverse primary sort; numeric ties keep city-name order")
    facts_cmd.add_argument("--offset", type=int, default=0, help="page offset after filtering and sorting")
    facts_cmd.add_argument("--limit", type=int, default=25, help="page size (1..100); follow next_offset for remaining facts")
    facts_cmd.add_argument("--tier", help="stored monthly tier T1/T2/T3; totals use assigned targets for its city cohort")

    investigate_cmd = commands.add_parser("investigate", command_name="investigate")
    investigate_cmd.add_argument("--month", required=True, help="explicit ended month, YYYY-MM")
    investigate_cmd.add_argument("--city", action="append", default=[], help="repeatable city; each named city is one independent block")
    investigate_cmd.add_argument("--group", action="append", default=[], help='explicit group as "Label:City,City"; never inferred')
    investigate_cmd.add_argument("--tier", help="stored monthly tier T1/T2/T3 aggregate")
    investigate_cmd.add_argument("--national", action="store_true", help="Grand Total target pair with observed actuals")
    investigate_cmd.add_argument("--start", help="first history month YYYY-MM; requires --end")
    investigate_cmd.add_argument("--end", help="last history month YYYY-MM; requires --start")

    funnel_inv_cmd = commands.add_parser("investigate-funnel", command_name="investigate-funnel")
    funnel_inv_cmd.add_argument("--month", required=True, help="explicit ended month, YYYY-MM")
    funnel_inv_cmd.add_argument("--stage", action="append", default=[], choices=FUNNEL_STAGES,
                                help="repeatable funnel stage (fe, fe2rr, rr2net); defaults to all three")
    funnel_inv_cmd.add_argument("--city", action="append", default=[], help="repeatable city; each named city is one independent block")
    funnel_inv_cmd.add_argument("--group", action="append", default=[], help='explicit group as "Label:City,City"; never inferred')
    funnel_inv_cmd.add_argument("--tier", help="stored selected-month tier T1/T2/T3 aggregate")
    funnel_inv_cmd.add_argument("--national", action="store_true", help="matched-city national MoM funnel aggregate")
    funnel_inv_cmd.add_argument("--start", help="first history month YYYY-MM; requires --end")
    funnel_inv_cmd.add_argument("--end", help="last history month YYYY-MM; requires --start")

    return root


def analyze(args, dataset):
    if args.command == "compare":
        return compare_overall(
            dataset,
            args.previous,
            args.latest,
            group_by=args.group_by,
            tier=args.tier,
            cities=args.city or None,
            top=args.top,
        )
    if args.command == "rank":
        return rank_cities(
            dataset,
            metric=args.metric,
            top=args.top,
            tier=args.tier,
            ascending=args.ascending,
            basis=args.basis,
            previous=args.previous,
            latest=args.latest,
        )
    if args.command == "funnel":
        return city_funnels(
            dataset,
            top=args.top,
            tier=args.tier,
            cities=args.city or None,
            sort_by=args.sort,
            ascending=args.ascending,
            previous=args.previous,
            latest=args.latest,
            columns=args.column,
        )
    if args.command == "share-drivers":
        return share_drivers(
            dataset,
            service=args.service,
            top=args.top,
            tier=args.tier,
            previous=args.previous,
            latest=args.latest,
        )
    checked = validate_data(dataset.data)
    checked_data = checked.data
    return {
        **provenance(checked, data=checked_data), "operation": "verify", "valid": True,
        "fact_count": len(checked_data["facts"]),
        "verification_scope": "current aggregate schema, grain, counts, per-month read provenance and stored checksums; not source completeness",
    }


def load_target_context(store, period: str):
    """Read the selected month and, when evaluable, the immediately previous month."""
    targets = store.load_targets(period)
    prior = previous_month(period)
    evaluable = prior in store.target_months() and prior in store.available_months()
    if not evaluable:
        return store.load([period]), targets, None
    return store.load([prior, period]), targets, store.load_targets(prior)


def run(args) -> dict:
    if args.command == "warehouse-queries":
        return warehouse_output(args.metric, args.query_id)
    investigation = None
    if args.command == "investigate":
        with diagnostic_stage(DiagnosticStage.ARGUMENTS):
            groups = [parse_investigation_group(item) for item in args.group]
            tier = normalize_tier(args.tier)
            months = validate_investigation_request(args.month, args.city, groups, args.start, args.end)
            national = args.national or not (args.city or groups or tier)
            if investigation_block_count(months, args.city, groups, tier, national) > INVESTIGATION_MAX_BLOCKS:
                reject("BOUNDS", "Investigation exceeds the bounded block limit; narrow the months or selectors.")
            investigation = {"months": months, "groups": groups, "tier": tier, "national": national}
    if args.command == "investigate-funnel":
        with diagnostic_stage(DiagnosticStage.ARGUMENTS):
            groups = [parse_investigation_group(item) for item in args.group]
            tier = normalize_tier(args.tier)
            months, required_months, stages = validate_funnel_investigation_request(
                args.month, args.city, groups, args.start, args.end, args.stage,
            )
            national = args.national or not (args.city or groups or tier)
            if funnel_investigation_block_count(months, args.city, groups, tier, national) > INVESTIGATION_MAX_BLOCKS:
                reject("BOUNDS", "Funnel investigation exceeds the bounded block limit; narrow the months or selectors.")
            investigation = {
                "months": months,
                "required_months": required_months,
                "stages": stages,
                "groups": groups,
                "tier": tier,
                "national": national,
            }
    if args.command in TARGET_COMMANDS:
        with diagnostic_stage(DiagnosticStage.ARGUMENTS):
            normalize_tier(args.tier)
            if args.command == "target-analysis":
                validate_target_request(args.month, args.mode, args.offset, args.limit)
            else:
                validate_target_facts_request(args.month, args.attainment, args.sort,
                                               args.descending, args.offset, args.limit)
    with diagnostic_stage(DiagnosticStage.READER):
        with connect_reader() as store:
            if args.command == "status":
                with diagnostic_stage(DiagnosticStage.CATALOG):
                    return store.catalog(args.city_offset)
            with diagnostic_stage(DiagnosticStage.LOAD):
                if args.command == "investigate":
                    available = set(store.available_months())
                    ftu_months = set(store.target_months("platform_ftu"))
                    registrations_months = set(store.target_months("registrations"))
                    loaded = [value for value in investigation["months"] if value in available]
                    if not loaded:
                        reject("COVERAGE", "None of the requested months have stored actuals; no substitute was selected.")
                    dataset = store.load(loaded)
                    targets = {value: store.load_targets(value) for value in loaded if value in ftu_months}
                elif args.command == "investigate-funnel":
                    available = set(store.available_months())
                    if not any(value in available for value in investigation["months"]):
                        reject("COVERAGE", "None of the requested months have stored actuals; no substitute was selected.")
                    loaded = [value for value in investigation["required_months"] if value in available]
                    dataset = store.load(loaded)
                elif args.command in TARGET_COMMANDS:
                    dataset, targets, previous_targets = load_target_context(store, args.month)
                else:
                    dataset = store.load(requested_months(args))
            with diagnostic_stage(DiagnosticStage.ANALYSIS):
                if args.command == "investigate":
                    return investigate(
                        dataset, targets, requested_months=investigation["months"],
                        available_months=available, ftu_months=ftu_months,
                        registrations_months=registrations_months, cities=args.city,
                        groups=investigation["groups"], tier=investigation["tier"],
                        national=investigation["national"],
                    )
                if args.command == "investigate-funnel":
                    return investigate_funnel(
                        dataset,
                        requested_months=investigation["months"],
                        available_months=available,
                        cities=args.city,
                        groups=investigation["groups"],
                        tier=investigation["tier"],
                        national=investigation["national"],
                        stages=investigation["stages"],
                    )
                if args.command == "target-analysis":
                    return target_cities(dataset, targets, args.month, mode=args.mode,
                                         offset=args.offset, limit=args.limit, tier=args.tier,
                                         queue=args.queue, previous_targets=previous_targets)
                if args.command == "target-facts":
                    return target_facts(dataset, targets, args.month, cities=args.city or None,
                                        attainment=args.attainment, sort_by=args.sort,
                                        descending=args.descending, offset=args.offset, limit=args.limit, tier=args.tier,
                                        previous_targets=previous_targets)
                return analyze(args, dataset)


def requested_months(args):
    if args.command in TARGET_COMMANDS:
        return [args.month]
    if args.command == "verify":
        if (args.start is None) != (args.end is None):
            reject("PERIOD", "Supply both --start and --end, or neither.")
        return month_range(args.start, args.end) if args.start is not None else None
    if args.command in PERIOD_COMMANDS:
        if (args.previous is None) != (args.latest is None):
            reject("PERIOD", "Supply both --previous and --latest, or neither.")
        return sorted(set((args.previous, args.latest))) if args.previous is not None else None
    return None


def main(argv: list[str] | None = None) -> int:
    def invoke():
        with diagnostic_stage(DiagnosticStage.ARGUMENTS):
            args = parser().parse_args(argv)
        return run(args)

    return execute_cli(invoke, max_bytes=MAX_OUTPUT_BYTES, diagnostics=True)


if __name__ == "__main__":
    sys.exit(main())
