"""Bounded IRF analysis CLI."""

from __future__ import annotations

import argparse
import sys

from .analysis import (
    city_funnels,
    compare_overall,
    rank_cities,
    share_drivers,
)
from .contracts import month_range, validate_data
from .errors import DiagnosticStage, MAX_OUTPUT_BYTES, SafeParser, diagnostic_stage, execute_cli, reject
from .evidence import provenance
from .metrics import ALL_METRICS
from .postgres import connect_reader


def _add_period_args(command: argparse.ArgumentParser) -> None:
    command.add_argument("--previous", help="YYYY-MM; requires --latest")
    command.add_argument("--latest", help="YYYY-MM; requires --previous")


COMMANDS = ("status", "compare", "rank", "funnel", "share-drivers", "verify")


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
    with diagnostic_stage(DiagnosticStage.READER):
        with connect_reader() as store:
            if args.command == "status":
                with diagnostic_stage(DiagnosticStage.CATALOG):
                    return store.catalog(args.city_offset)
            with diagnostic_stage(DiagnosticStage.LOAD):
                dataset = store.load(requested_months(args))
            with diagnostic_stage(DiagnosticStage.ANALYSIS):
                return analyze(args, dataset)


def requested_months(args):
    if hasattr(args, "previous"):
        if (args.previous is None) != (args.latest is None):
            reject("PERIOD", "Supply both --previous and --latest, or neither.")
        return sorted(set((args.previous, args.latest))) if args.previous is not None else None
    if hasattr(args, "start"):
        if (args.start is None) != (args.end is None):
            reject("PERIOD", "Supply both --start and --end, or neither.")
        if args.start is not None:
            return month_range(args.start, args.end)
    return None


def main(argv: list[str] | None = None) -> int:
    def invoke():
        with diagnostic_stage(DiagnosticStage.ARGUMENTS):
            args = parser().parse_args(argv)
        return run(args)

    return execute_cli(invoke, max_bytes=MAX_OUTPUT_BYTES, diagnostics=True)


if __name__ == "__main__":
    sys.exit(main())
