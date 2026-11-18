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
from .contracts import month_range, validate_data
from .errors import DiagnosticStage, MAX_OUTPUT_BYTES, SafeParser, diagnostic_stage, execute_cli, reject
from .evidence import provenance
from .metrics import ALL_METRICS
from .postgres import connect_reader
from .selection import normalize_tier, validate_target_request
from .selection import TARGET_ATTAINMENTS, TARGET_FACT_SORTS, validate_target_facts_request


def _add_period_args(command: argparse.ArgumentParser) -> None:
    command.add_argument("--previous", help="YYYY-MM; requires --latest")
    command.add_argument("--latest", help="YYYY-MM; requires --previous")


TARGET_COMMANDS = ("target-analysis", "target-facts")
PERIOD_COMMANDS = ("compare", "rank", "funnel", "share-drivers")
COMMANDS = ("status", *PERIOD_COMMANDS, "verify", *TARGET_COMMANDS)


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
                            help="matched city targets (default) or the separate New launch fallback queue")

    facts_cmd = commands.add_parser("target-facts", command_name="target-facts")
    facts_cmd.add_argument("--month", required=True, help="explicit ended month, YYYY-MM")
    facts_cmd.add_argument("--city", action="append", default=[], help="repeatable canonical city/name filter; overall totals stay month-wide")
    facts_cmd.add_argument("--attainment", choices=TARGET_ATTAINMENTS, default="all", help="exact actual-versus-target predicate; no implicit 5%% filter")
    facts_cmd.add_argument("--sort", choices=TARGET_FACT_SORTS, default="city", help="sort field; unavailable values always last")
    facts_cmd.add_argument("--descending", action="store_true", help="reverse primary sort; numeric ties keep city-name order")
    facts_cmd.add_argument("--offset", type=int, default=0, help="page offset after filtering and sorting")
    facts_cmd.add_argument("--limit", type=int, default=25, help="page size (1..100); follow next_offset for remaining facts")
    facts_cmd.add_argument("--tier", help="stored monthly tier T1/T2/T3; totals use assigned targets for its city cohort")

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


def run(args) -> dict:
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
                dataset = store.load(requested_months(args))
                if args.command in TARGET_COMMANDS:
                    targets = store.load_targets(args.month)
            with diagnostic_stage(DiagnosticStage.ANALYSIS):
                if args.command == "target-analysis":
                    return target_cities(dataset, targets, args.month, mode=args.mode,
                                         offset=args.offset, limit=args.limit, tier=args.tier,
                                         queue=args.queue)
                if args.command == "target-facts":
                    return target_facts(dataset, targets, args.month, cities=args.city or None,
                                        attainment=args.attainment, sort_by=args.sort,
                                        descending=args.descending, offset=args.offset, limit=args.limit, tier=args.tier)
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
