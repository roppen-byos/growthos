---
name: growthos-irf-analysis
description: "Answer GrowthOS IRF questions: master reports, MoM and period comparisons, ratio investigations, monthly trends, city rankings/funnels, Bike/Auto/Cab service-share drivers, ended-month PFTU target/actual lookups and coverage shortlists, offline warehouse SQL provenance, and stored-data verification."
---

# GrowthOS IRF Analysis

## Triggers

Use this skill when the user asks for:

- IRF master reports, MoM/period comparisons, or completed-month trends (including three-month / quarterly views shown month by month).
- Installs, Registrations, Platform FTU, I2R, R2N, FE sessions, RR, Net rides, FE2RR, RR2Net, or service RR/Net/RR2Net comparisons across all cities, tiers (`T1`–`T3`), or selected cities.
- City growth/decline rankings, IRF funnel drilldowns, or Bike/Link, Auto, and Cab service-share drivers.
- Ended-month Platform FTU target/actual lookups, attainment rankings, coverage shortlists, pooled New launch comparisons, or prior-month widening-miss (`persistent` / `new_miss`) annotations.
- Available reporting months, metric definitions, city coverage, stored-data verification, or offline warehouse source SQL (`warehouse-queries`).

For investigating **why** Platform FTU missed target or dipped MoM — including parallel `FE`, `FE2RR`, `RR2Net` funnel contribution and same-month `Registration` target contribution — load [the FTU investigation skill](../growthos-ftu-investigation/SKILL.md).

## Contract

- **CLI-only arithmetic:** every reported number, ratio, delta, ranking, and warning comes from a reviewed `growthos` subcommand. Select commands and explain evidence without hand-calculating metrics.
- **Read-only, chat-only:** database access is strictly read-only over current stored ended calendar months. Deliver all reports in chat.
- **Provenance & honesty:** every answer states the selected period, relevant month metadata/source-read times, unavailable values (`N/A` with reasons), and applicable coverage or mapping warnings.

## Workflow

1. **Check boundaries and interpreter.** Apply repository-root [AGENTS.md](../../../AGENTS.md) for scope, credentials, and CLI approval; use [README.md](../../../README.md) for local setup. Run commands from `gwos-analyst/` with `.venv/bin/python`.
2. **Inspect current availability.** Run `.venv/bin/python -m growthos status` (skip for offline `warehouse-queries`, or when already verified in the current conversation). Note available months, metric definitions (`metrics`, including PFTU vs SFTU, and R2N vs FTU-eligible RR2Net), warnings, and source-read times.
3. **Select explicit periods and collect evidence.** Run the matching command(s) from the router below, passing explicit month flags across follow-up calls so multi-command answers stay aligned, and verify `result_checksum` and month metadata remain consistent across reads (rerun affected commands if a refresh occurred mid-analysis).
   - **Target questions (`target-facts`, `target-analysis`):** load [target-analysis.md](target-analysis.md) first for intent routing, filters, sorting, pooled fallback, widening misses, and first-page-then-ask pagination.
   - **Master reports, highlighted I2R/R2N movements (even without a drilldown ask), or three-month/quarterly views:** complete the matching branch in [report-playbook.md](report-playbook.md) before interpreting.
   - **Custom flat funnel columns or units:** read [funnel-columns.md](funnel-columns.md).
   - **Warehouse SQL provenance:** read [metric-provenance.md](metric-provenance.md).
4. **Format and deliver in chat.** Before composing any analytical answer, read [output-format.md](output-format.md). Done only when every required CLI check, playbook/target rule, formatting requirement, and provenance/caveat disclosure is satisfied.

## Command router

Prefix every command with `.venv/bin/python -m growthos`. Use `<command> --help` for exact flags, defaults, and allowed values. Period commands (`compare`, `rank`, `funnel`, `share-drivers`) and `verify` default to the latest two stored months.

| User asks | Command |
| --- | --- |
| Stored months, metric definitions, cities | `status [--city-offset N]` |
| Warehouse SQL behind stored volume metrics (IRF + funnel) | `warehouse-queries [--metric METRIC] [--id QUERY-ID]`; read [metric-provenance.md](metric-provenance.md) |
| Overall MoM or period comparison (`i2r`, `r2n`, `fe2rr`, `rr2net`, shares) | `compare [--previous YYYY-MM --latest YYYY-MM]` |
| Tier breakdown (`T1`, `T2`, `T3`) | `compare --group-by tier [--tier T1\|T2\|T3]` |
| Selected or top-N city MoM comparison | `compare --group-by city [--city CITY ...] [--top 25]` |
| Top-N cities by metric growth or decline | `rank --metric platform_ftu --top 20 [--tier T3] [--ascending]` |
| IRF funnel drilldown for top or selected cities | `funnel --top 10 [--tier T2] [--city CITY ...] [--sort platform_ftu_delta]` |
| Bike/Link, Auto, or Cab share drivers | `share-drivers --service auto\|cab\|link\|all [--top 15] [--tier T1]` |
| Verify current data and stored checksums | `verify [--start YYYY-MM --end YYYY-MM]` |
| PFTU target misses: coverage shortlist or all qualifying (`>5%`) cities | `target-analysis --month YYYY-MM --mode top\|all [--tier T1\|T2\|T3]`; read [target-analysis.md](target-analysis.md) |
| Pooled New launch fallback breakdown (opt-in; disclose count and ask first) | `target-analysis --month YYYY-MM [--tier T1\|T2\|T3] --queue fallback`; read [target-analysis.md](target-analysis.md) |
| Target/actual lookup, cities above/at/below target, factual attainment sorting | `target-facts --month YYYY-MM [--city CITY ...] [--tier T1\|T2\|T3]`; read [target-analysis.md](target-analysis.md) |
| Widening target misses vs prior month (`< -5pp`, `persistent` / `new_miss`) | Returned additively as `persistent_failure` by `target-analysis` and `target-facts`; render as a second table per [target-analysis.md](target-analysis.md) |
| Investigate why FTU missed target or dipped MoM (`FE`, `FE2RR`, `RR2Net`, or `Registration` contribution) | Load [the FTU investigation skill](../growthos-ftu-investigation/SKILL.md) (`investigate-funnel`, `investigate`) |

## Interpretation and gotchas

- **Explicit periods, no pinning:** pass explicit `--previous`/`--latest` or `--month` flags across follow-ups. Refreshes overwrite stored months in place with no snapshot pinning or time travel. Honor requested months without silent substitution; label nonadjacent pairs a **period comparison**, not **MoM**.
- **Tiers and unmapped cities:** named cities absent from the canonical map are assigned to `T3` and included in overall totals; cite `mapping_impact` for their count and volume contribution. The unattributed `city_not_found` bucket is not a city and not a tier member: exclude it from city and tier answers, include it only in national/overall totals (use `mapping_impact.unattributed_volumes_by_month` in `compare --group-by overall` to disclose its national volume share), and never report a standalone I2R or R2N for it. Paired comparisons group each city by its latest observed tier in the pair and warn when tiers changed across months.
- **Missing data and zeros:** keep `null` observations and zero-denominator ratios as `N/A` with their supplied reason, and label `known_subtotal*` values explicitly as partial subtotals — never complete totals.
- **Availability vs completeness:** stored ended months are immediately reportable without certification labels; source-read timestamps record query execution, not authoritative business-data cutoffs.
- **Guardrails (always prefer the reviewed CLI path):**
  - Use reviewed `growthos` subcommands instead of ad-hoc `python3 -c`, pandas, or hand math so count-rollup-before-division, completeness checks, and latest-tier pairing stay exact.
  - Take aggregate ratios from `compare` and keep multi-month views side by side; never average city ratios, sum `pp` deltas, or sum monthly distinct counts into a quarterly distinct total.
  - Report measured count and ratio movements; frame marketing, CRM, pricing, or supply explanations only as unverified hypotheses.

## When it fails

CLI failures return `{"error": {"code": "...", "message": "..."}}` with sanitized messages. Recover by code class:

- `CONFIGURATION`, `DEPENDENCY`, `DATABASE_UNAVAILABLE`: report only the CLI's sanitized error and direct the user to [the setup skill](../growthos-setup/SKILL.md) or [README.md](../../../README.md); never read/modify `.env`, ask for secrets in chat, or invent numbers.
- `ARGUMENTS`: inspect `<subcommand> --help`, fix the flag or allowed value, and rerun.
- `COVERAGE`, `PERIOD`, `MONTH`, `INCOMPLETE_MONTH`, `CITY`: state the exact missing or invalid month/city from `status`, keep the requested scope explicit, and offer available stored periods without silent substitution.
- `READ_ONLY_ROLE`, `TLS`, `STORE_INTEGRITY`, `SCHEMA`, `PROVENANCE`: stop immediately and surface the sanitized blocker; never bypass role, TLS, or checksum guards.
- `OUTPUT_BOUND`, `BOUNDS`: narrow the month window or `--top`/`--limit`/`--tier`/`--city` filter and disclose the narrower scope.
- `TARGETS_UNAVAILABLE`, `TARGETS`: report the target-data limitation for the requested month; core MoM analysis remains separate, and target installation/correction is an administrator task.
- Partial coverage or `null` metrics in valid JSON: proceed with observed evidence, cite `known_subtotal*` and unavailable counts where provided, and render missing values as `N/A`.
