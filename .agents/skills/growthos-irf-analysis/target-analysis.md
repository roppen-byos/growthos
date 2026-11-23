# Ended-Month Platform FTU Targets

Use this reference for Platform FTU target attainment, lookups, coverage shortlists, pooled New launch comparisons, and widening-miss annotations (`target-facts`, `target-analysis`). Reviewed Python tools own all arithmetic, predicates, ordering, reconciliation, and selection.

For diagnosing **why** a city or tier missed its Platform FTU target — including same-month **Registration target contribution** (`investigate`) or calendar-adjusted MoM **FE / FE2RR / RR2Net** contribution (`investigate-funnel`) — switch to [the FTU investigation skill](../growthos-ftu-investigation/SKILL.md).

## Workflow

1. **Resolve one explicit ended month.** Extract the month from the question or conversation; if omitted, ask. Use `status` to confirm actual availability (the target command validates target availability). Keep unavailable months explicit without substituting another month or inventing targets. Pass `--tier T1|T2|T3` when the user asks about a specific tier.
2. **Choose the tool by intent.**

   | Objective | Command (`.venv/bin/python -m growthos ...`) |
   | --- | --- |
   | Inspect target/actual/variance for named cities or retrieve general facts | `target-facts --month YYYY-MM [--city CITY ...] [--tier T1\|T2\|T3]` |
   | Filter cities above, at, below target, or unavailable | `target-facts --month YYYY-MM --attainment above\|at\|below\|unavailable` |
   | Sort cities factually by variance, variance %, target, actual, or name | `target-facts --month YYYY-MM --sort city\|variance\|variance-pct\|target\|actual [--descending]` |
   | Shortlist material misses (`< -5%`) covering the overall or tier gap `X` | `target-analysis --month YYYY-MM --mode top [--tier T1\|T2\|T3]` |
   | List every city meeting the material miss rule (`< -5%`) | `target-analysis --month YYYY-MM --mode all [--tier T1\|T2\|T3]` |
   | Inspect the pooled New launch fallback comparison + actual-only city breakdown | `target-analysis --month YYYY-MM [--tier T1\|T2\|T3] --queue fallback` |

   - **Intent boundary:** `target-facts` has **no implicit 5% filter** and no coverage stop. Plain *"cities missing targets"* uses `target-analysis --mode all` (`>5%` policy); explicit requests for any miss, small misses (`<=5%`), exact-at-target, or above-target cities use `target-facts`. *"Top cities"* in target-miss prioritization uses `target-analysis --mode top` (coverage-based); a requested fixed-N factual ranking uses `target-facts --sort ... --limit N` and must be labeled as a displayed top-N subset, never as a coverage shortlist.
   - **Combined requests:** when the user asks for both target attainment and MoM/funnel performance, run both reviewed tools and keep their lenses and criteria clearly separated.
3. **Read evidence (one page at a time).**
   - `--limit` controls page size (`1..100`, default `25`), never matching or selection — the CLI computes full selection/sorting before paging.
   - Request the first page and **do not auto-follow `next_offset`**. If a page hits `OUTPUT_BOUND`, lower `--limit` and retry the same `--offset`.
   - When `next_offset` is non-null, report returned vs total counts (`coverage.matching_city_count` for facts or `coverage.selected_city_count` for selection) plus `omitted_row_count`, and ask whether to fetch the next batch. Fetch additional pages only on user request, one batch per request (`--offset next_offset`), verifying `result_checksum` and month metadata remain unchanged across pages (restart collection if a refresh occurred).
4. **Deliver the answer.** Follow [output-format.md](output-format.md) and apply every policy rule below that matches the returned payload. Never describe a partial page as the complete list, and never substitute saved files or exports.

## Policy & interpretation rules

### Variance sign convention and units

- Signed variance is always **`actual - target`**: negative (`-`) for misses, positive (`+`) for beats, and negative `pp` (`-pp`) for widening misses against the prior month. Prefix positive values with `+` and retain `-`.
- `variance_pct` is already in percent — append `%` without multiplying again.
- Unsigned coverage magnitudes (`overall.shortfall` = `X = max(target - actual, 0)`, `cumulative_shortfall`, `covered_amount`, `uncovered_amount`, `overshoot`) remain nonnegative gap amounts alongside signed `overall.variance`.

### National vs tier scope and skipped null-FTU cities

- **National (no `--tier`):** the authoritative overall target is the supplied **Grand Total** (never redefined by summing city targets, merges, or the fallback). Overall actual sums all non-null recorded city `platform_ftu` values, and `X = max(overall target - observed actual, 0)`.
- **Tier (`--tier T1|T2|T3`):** cohort membership uses the selected month's **stored city tiers** (not CSV tags or another month's tier; never infer tiers for planned cities absent from that month). Tier target, actual, and gap use **plan-matched cohort cities only** (retaining assigned targets for null-FTU cohort cities); label these **tier target/actual/variance**, not national totals, and note they need not reconcile to the national Grand Total. A tier with no plan-matched cities or no stored rows keeps totals unavailable (`no_plan_matched_cities` / empty scope), never `0`.
- **Skipped null-FTU disclosure:** when `overall.actual_coverage.skipped_null_ftu_city_count` is nonzero, disclose it beside the headline — those cities were skipped from observed overall FTU, not converted to zero. Report `install_or_registration_only_city_count` for the subset with only Installs or Registrations, and report any other null patterns separately rather than claiming all skipped cities were IR-only. Do not relabel numeric `overall.actual` or `overall.shortfall` as `N/A` solely because some cities had null FTU; only all-null FTU or a missing target keeps actual/`X` unavailable.
- **Approved target merges:** Attingal + Trivandrum targets are already combined under canonical Trivandrum (matching canonical actuals); Ludhiana keeps its T2-source target row while keeping its stored actual tier. Use returned evidence directly.

### Facts vs shortlist selection

- **Facts (`target-facts`):** retains above (`actual > target`), at (`actual == target`), below (`actual < target`), and unavailable rows. City and attainment filters never redefine month-wide or tier-wide totals. Ties break by city name; unavailable sort values stay last. `qualifies_for_shortlist` explains the policy without filtering rows. Zero counts remain visible (`target == 0` makes `variance_pct` unavailable while preserving absolute `variance`).
- **Shortlist (`target-analysis`):** defaults to the **plan-matched queue** (explicit city targets only). A city qualifies only when strictly more than 5% below its own positive target (`variance < -5%`, exact integer check before rounding; `-5.00%` and smaller misses are excluded, and missing operands or nonpositive targets cannot qualify). Ranking orders largest negative variance first, then city name.
  - `--mode top` selects in rank order until cumulative shortfall covers `X` (permitting overshoot; returns empty when `X = 0`; discloses `uncovered_amount` if eligible gaps fall short of `X`). If `X` is unavailable, an empty `top` result is not proof that targets were met.
  - `--mode all` returns every qualifying city even when `X = 0` or `X` is unavailable.
- Arithmetic coverage is not recoverable FTU, forecast likelihood, causal explanation, or an action recommendation.

### Pooled New launch fallback

- Every observed city absent from the target plan (excluding explicit plan entries with missing/zero targets) joins one **New launch fallback pool**, compared as a group against the single monthly New launch target (never added to Grand Total or any tier gap, and never ranked inside the plan-matched queue).
- `target-analysis` always returns this pooled summary (`fallback.*`: target, observed non-null actual, variance, variance %, `qualifies`, `city_count`, `skipped_null_city_count`). Always disclose the pooled comparison alongside plan-matched results, and **ask before running `--queue fallback`** (which adds the paged actual-only per-city breakdown with no per-city targets or variances).
- In `target-facts`, unplanned cities appear with null target and reason `pooled_new_launch_target`, alongside the unchanged pooled summary. The fallback is a planning benchmark for the unplanned group, not proof of launch or a per-city budget.

### Widening target misses vs prior month (`persistent_failure`)

- Returned additively by both `target-analysis` and `target-facts` for **only the currently returned page** of plan-matched qualifying misses (`variance < -5%`). Re-check after paging. Never replace, reorder, or filter the main table because of it.
- Lists returned qualifying cities whose variance percentage worsened by strictly more than 5 percentage points (`variance_pct_delta < -5pp`, exact integer arithmetic) against the immediately previous calendar month (which must have both stored actuals and an explicit positive target for that city).
- Prior-month variance labels each row's `status` without filtering it:
  - **`persistent`:** prior month was also strictly `< -5%` below target (`failing_since` = prior month).
  - **`new_miss`:** prior month was `>= -5%` (beat, at target, or sub-5%/exact-5% miss; `failing_since` = selected month).
- **Presentation:** when `persistent_failure.available` is `true`, render a second table headed **"Widening target misses vs `<previous_month>` (`< -5pp`)"** with `City | Status | Current Variance % | Prior Variance % | Change (pp) | Failing Since`, and report `city_count`, `persistent_city_count`, `new_miss_city_count`, and `undetermined_city_count`. Never call this table "only persistent misses" (cities that missed both months but improved are absent, and `new_miss` cities were not material misses last month). Do **not** auto-run Investigation Layer analysis (`investigate` / `investigate-funnel`) unless the user asks to investigate; you may offer to investigate the shortlisted cities as a follow-up.
- When `available` is `false` (for example no evaluable prior month or `--queue fallback`), state the returned reason and omit the table; when `city_count` is `0`, state that no returned qualifying city widened by more than `5pp`. Missing prior operands stay `undetermined`, never failures. With `--tier`, the selected month's stored tier is the only tier source (prior-month tier need not match).
