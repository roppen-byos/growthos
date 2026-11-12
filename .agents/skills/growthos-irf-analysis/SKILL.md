---
name: growthos-irf-analysis
description: Answer GrowthOS IRF questions: master reports, ratio investigations, quarterly monthly trends, city rankings/funnels, service-share drivers, and current-data verification.
---

# GrowthOS IRF Analysis

## Triggers

Use this skill when the user asks for:

- IRF master reports or completed-month trends, including quarterly views shown month by month.
- Installs, Registrations, Platform FTU, I2R or R2N comparisons across all cities, tiers or selected cities.
- City growth/decline rankings, funnel investigations, or Bike/Link, Auto and Cab service-share drivers.
- Available reporting months, metric definitions, city coverage, or verification of current stored IRF data.

## Contract

- Every reported number, ratio, ranking, and warning comes from the reviewed `growthos` CLI; the LLM model selects commands, analysis data and explains evidence rather than computing metrics.
- Database access is strictly read-only over current stored ended calendar months. Analytical reports are delivered in chat.
- Every answer states the selected period, month metadata/source-read times, unavailable values, and applicable coverage or mapping warnings.

## Workflow

1. **Check boundaries and interpreter.** Apply repository-root [AGENTS.md](../../../AGENTS.md) for scope and credentials; consult [README.md](../../../README.md) for local setup. Run commands from the repository root with `.venv/bin/python`.
2. **Inspect current availability.** Run `.venv/bin/python -m growthos status`. Done when available months, the packaged business-first metric catalogue (`metrics`, derived from `growthos/metric_registry.json` — including PFTU vs glossary-only SVN and R2N vs RR2Net distinctions), warnings, month metadata, and source-read times are recorded.
3. **Select periods and collect evidence.** Choose explicit months using the rules below, run the matching command(s) from the router, and reuse returned JSON across follow-ups. Before interpreting a master report, any highlighted I2R/R2N movement (even without a drilldown request), or a quarterly/three-month analysis, complete the matching branch in [report-playbook.md](report-playbook.md).
4. **Deliver the report in chat.** Done when requested evidence, the output-format requirements below, provenance/source-read caveats, and any applicable playbook completion checks are satisfied.

## Tools and command routing

Commands follow `.venv/bin/python -m growthos`. Use `<command> --help` for exact flags, defaults, and allowed values. Analytical comparisons and `verify` default to the latest two stored months.

| User asks | Command |
| --- | --- |
| Stored months, metrics, cities | `status [--city-offset N]` |
| Overall MoM / period comparison (`i2r`, `r2n`, shares) | `compare [--previous YYYY-MM --latest YYYY-MM]` |
| Tier breakdown (`T1`, `T2`, `T3`) | `compare --group-by tier [--tier T1\|T2\|T3]` |
| Key / selected city MoM comparison | `compare --group-by city [--city City ...] [--top 25]` |
| Top N cities by metric growth or decline | `rank --metric platform_ftu --top 20 [--tier T3] [--ascending]` |
| Funnel drilldown for top or selected cities | `funnel --top 10 [--tier T2] [--city City ...] [--sort platform_ftu_delta]` |
| Auto / Cab / Bike(Link) share drivers | `share-drivers --service auto\|cab\|link\|all [--top 15] [--tier T1]` |
| Verify current data and stored checksums | `verify [--start YYYY-MM --end YYYY-MM]` |

For projected funnel columns or units, read [funnel-columns.md](funnel-columns.md).

## Interpretation and gotchas

- Pass explicit month pairs across follow-up queries. Each command reads consistently, but refreshes overwrite stored months in place — there is no snapshot pinning or time travel. If checksums or month metadata change mid-investigation, rerun affected commands. Honor requested months; label nonadjacent pairs a `period comparison`, not `MoM`.
- Cities absent from the canonical classification are reported under `T3` and included in overall totals; `mapping_impact` reports the count and stored volumes those unmapped cities contributed. Paired comparisons group cities by their latest observed tier within the pair and warn on cross-month tier changes. Missing operands or zero denominators stay `null`, never `0`.
- Report source-read times, unavailable observations, and warnings. stored ended months are immediately reportable without certification labels, and retained months are fetched independently without cross-month pinning. Distinguish IRF arithmetic from causal hypotheses.

## Output format

Before composing any analytical answer, read [output-format.md](output-format.md)
for answer structure, question-sized layouts, tables, numeric display and evidence notes.

## When it fails

CLI failures return `{"error": {"code": "...", "message": "..."}}` with sanitized messages. Recover by code class:

- `CONFIGURATION`, `DEPENDENCY`, `DATABASE_UNAVAILABLE`: report only the CLI's sanitized error and point to setup steps in [README.md](../../../README.md); never read/modify `.env`, request secrets in chat, or invent numbers.
- `ARGUMENTS`: inspect `<subcommand> --help`, fix the flag or allowed enum value, and rerun.
- `COVERAGE`, `PERIOD`, `MONTH`, `INCOMPLETE_MONTH`, `CITY`: state the exact missing or invalid month/city from `status`, keep the user's requested scope explicit, and offer available stored periods without silent substitution.
- `CREDENTIAL_BOUNDARY`, `READ_ONLY_ROLE`, `TLS`, `STORE_INTEGRITY`, `SCHEMA`, `PROVENANCE`: stop the analysis immediately and surface the sanitized blocker; never attempt to bypass environment, role, TLS, or checksum guards.
- `OUTPUT_BOUND`, `BOUNDS`: narrow the month window or `--top`/`--tier`/`--city` filter and disclose the narrower scope.
- Partial coverage or `null` metrics in valid JSON: continue with observed evidence, cite `known_subtotal*` and unavailable counts where provided, and render missing values as `N/A`.

## Anti-patterns

- **Ad-hoc math (`python3 -c`, pandas, hand-computed ratios/totals)** -> run the reviewed `growthos` subcommand so rollup before division, completeness checks, and latest-tier pairing hold.
- **Averaging city ratios, summing `pp` deltas, or summing monthly distincts into a quarter** -> take aggregate ratios from `compare` and keep three-month views as side-by-side monthly observations ([report-playbook.md](report-playbook.md)).
- **Treating `null` or missing city-months as `0`, or `known_subtotal` as complete** -> keep `null` as `N/A` and label partial subtotals with their unavailable counts.
- **Asserting marketing, CRM, or marketplace causes from funnel arithmetic** -> report measured count/ratio movements and label operational explanations as unverified hypotheses.
- **Silent period substitution** -> fail closed when requested months are absent and deliver reports in chat.
