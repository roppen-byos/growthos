---
name: growthos-irf-analysis
description: "Answer GrowthOS IRF questions: PFTU target/actual lookup and attainment rankings, coverage-based target shortlists, registration-contribution investigation of FTU target gaps, master reports, ratio investigations, monthly trends, city rankings/funnels, service-share drivers, and data verification."
---

# GrowthOS IRF Analysis

## Triggers

Use this skill when the user asks for:

- IRF master reports or completed-month trends, including quarterly views shown month by month.
- Installs, Registrations, Platform FTU, I2R or R2N comparisons across all cities, tiers or selected cities.
- City growth/decline rankings, funnel investigations, or Bike/Link, Auto and Cab service-share drivers.
- Available reporting months, metric definitions, city coverage, or verification of current stored IRF data.
- The warehouse source SQL behind a metric (`warehouse-queries`), with no database connection.
- Ended-month Platform FTU targets versus actuals, city attainment/rankings, coverage shortlists or all material target misses.

## Contract

- Every reported number, ratio, ranking and warning comes from the reviewed `growthos` CLI; the model selects commands and data, then explains evidence without computing metrics.
- Database access is strictly read-only over current stored ended calendar months. Analytical reports are delivered in chat.
- Every answer states the selected period, month metadata/source-read times, unavailable values, and applicable coverage or mapping warnings.

## Workflow

1. **Check boundaries and interpreter.** Apply repository-root [AGENTS.md](../../../AGENTS.md) for scope and credentials; consult [README.md](../../../README.md) for local setup. Run commands from the repository root with `.venv/bin/python`.
2. **Inspect current availability.** Run `.venv/bin/python -m growthos status`. Record available months, the packaged metric catalogue (`metrics`, from `growthos/metric_registry.json`, including PFTU vs glossary-only SVN and R2N vs RR2Net), warnings, month metadata and source-read times.
3. **Select periods and collect evidence.** Choose explicit months using the rules below, run matching commands from the router, and reuse returned JSON across follow-ups. For target questions, first load [target-analysis.md](target-analysis.md): choose facts or policy-based selection by intent, with supported filters, ordering, and first-page delivery with on-request continuation. Before interpreting a master report, highlighted I2R/R2N movement (even without a drilldown request), or quarterly/three-month analysis, complete the matching branch in [report-playbook.md](report-playbook.md).
4. **Deliver the report in chat.** Done when requested evidence, output-format requirements, provenance/source-read caveats and applicable playbook completion checks are satisfied.

## Tools and command routing

Commands follow `.venv/bin/python -m growthos`. Use `<command> --help` for exact flags, defaults, and allowed values. Analytical comparisons and `verify` default to the latest two stored months.

| User asks | Command |
| --- | --- |
| Stored months, metrics, cities | `status [--city-offset N]` |
| Warehouse SQL behind a metric | `warehouse-queries [--metric METRIC] [--id QUERY-ID]`; read [metric-provenance.md](metric-provenance.md) |
| Overall MoM / period comparison (`i2r`, `r2n`, shares) | `compare [--previous YYYY-MM --latest YYYY-MM]` |
| Tier breakdown (`T1`, `T2`, `T3`) | `compare --group-by tier [--tier T1\|T2\|T3]` |
| Key / selected city MoM comparison | `compare --group-by city [--city City ...] [--top 25]` |
| Top N cities by metric growth or decline | `rank --metric platform_ftu --top 20 [--tier T3] [--ascending]` |
| Funnel drilldown for top or selected cities | `funnel --top 10 [--tier T2] [--city City ...] [--sort platform_ftu_delta]` |
| Auto / Cab / Bike/Link share drivers | `share-drivers --service auto\|cab\|link\|all [--top 15] [--tier T1]` |
| Verify current data and stored checksums | `verify [--start YYYY-MM --end YYYY-MM]` |
| Platform FTU target misses: top coverage shortlist or all qualifying cities | `target-analysis --month YYYY-MM --mode top\|all`; first read [target-analysis.md](target-analysis.md) |
| Pooled New launch fallback comparison (separate from matched, ask first) | `target-analysis --month YYYY-MM --tier T1\|T2\|T3 --queue fallback`; first read [target-analysis.md](target-analysis.md) |
| Target/actual lookup, cities above/at/below target, factual attainment ordering | `target-facts --month YYYY-MM [--city CITY ...]`; first read [target-analysis.md](target-analysis.md) |
| Tier-specific target facts or cities covering a tier's own gap | Add `--tier T1\|T2\|T3` to the selected target tool; first read [target-analysis.md](target-analysis.md) |
| Explain a same-month FTU target gap by registration volume (city, tier, group, national, month history) | `investigate --month YYYY-MM [--city CITY ...] [--tier T1\|T2\|T3] [--group "Label:City,City"] [--national] [--start YYYY-MM --end YYYY-MM]`; first read [target-analysis.md](target-analysis.md#registration-contribution-investigation) |
| Cities whose target miss widened >5pp last month (persistent or new) | Returned additively by either target tool; read `persistent_failure`, check `status`, and render a second table per [target-analysis.md](target-analysis.md) |

For projected funnel columns or units, read [funnel-columns.md](funnel-columns.md).

## Interpretation and gotchas

- Pass explicit month pairs across follow-up queries. Each command reads consistently, but refreshes overwrite stored months in place — there is no snapshot pinning or time travel. If checksums or month metadata change mid-investigation, rerun affected commands. Honor requested months; label nonadjacent pairs a `period comparison`, not `MoM`.
- Cities absent from the canonical classification are reported under `T3` and included in overall totals; `mapping_impact` reports the count and stored volumes those unmapped cities contributed. Paired comparisons group cities by their latest observed tier within the pair and warn on cross-month tier changes. Missing operands or zero denominators stay `null`, never `0`.
- Stored ended months are immediately reportable without certification labels; retained months are fetched independently without cross-month pinning. Distinguish IRF arithmetic from causal hypotheses.

## Output format

Before composing any analytical answer, read [output-format.md](output-format.md) for structure, question-sized layouts, tables, numeric display and evidence notes.

## When it fails

CLI failures return `{"error": {"code": "...", "message": "..."}}` with sanitized messages. Recover by code class:

- `CONFIGURATION`, `DEPENDENCY`, `DATABASE_UNAVAILABLE`: report only the CLI's sanitized error and point to setup steps in [README.md](../../../README.md); never read/modify `.env`, request secrets in chat, or invent numbers.
- `ARGUMENTS`: inspect `<subcommand> --help`, fix the flag or allowed enum value, and rerun.
- `COVERAGE`, `PERIOD`, `MONTH`, `INCOMPLETE_MONTH`, `CITY`: state the exact missing or invalid month/city from `status`, keep the user's requested scope explicit, and offer available stored periods without silent substitution.
- `READ_ONLY_ROLE`, `TLS`, `STORE_INTEGRITY`, `SCHEMA`, `PROVENANCE`: stop the analysis immediately and surface the sanitized blocker; never attempt to bypass role, TLS, or checksum guards.
- `OUTPUT_BOUND`, `BOUNDS`: narrow the month window or `--top`/`--tier`/`--city` filter and disclose the narrower scope.
- Partial coverage or `null` metrics in valid JSON: continue with observed evidence, cite `known_subtotal*` and unavailable counts where provided, and render missing values as `N/A`.
- `TARGETS_UNAVAILABLE` or `TARGETS`: report the target-data limitation and requested month; existing MoM analysis remains separate. Table installation/correction is an administrator task, not an analyst action.

## Anti-patterns

- **Ad-hoc math (`python3 -c`, pandas, hand-computed ratios/totals)** -> run the reviewed `growthos` subcommand so rollup before division, completeness checks, and latest-tier pairing hold.
- **Averaging city ratios, summing `pp` deltas, or summing monthly distincts into a quarter** -> take aggregate ratios from `compare` and keep three-month views as side-by-side monthly observations ([report-playbook.md](report-playbook.md)).
- **Treating `null` or missing city-months as `0`, or `known_subtotal` as complete** -> keep `null` as `N/A` and label partial subtotals with their unavailable counts.
- **Asserting marketing, CRM, or marketplace causes from funnel arithmetic** -> report measured count/ratio movements and label operational explanations as unverified hypotheses.
- **Silent period substitution** -> fail closed when requested months are absent.
