# Ended-month Platform FTU targets

Use this branch for target attainment, not month-on-month performance. Choose tools
and parameters by intent; examples are neither exact-phrase rules nor exhaustive.
Reviewed Python tools own all arithmetic, predicates, ordering, reconciliation and coverage selection.

## Workflow

1. **Select the month.** Resolve one explicit ended month from the question or
   conversation; otherwise, ask which month. Use `status` to check actual availability;
   the target command checks target availability.
   An unavailable month stays unavailable, without substitution or invented targets.
   If a tier is requested, pass `--tier T1|T2|T3` to the selected target tool. It
   uses that month's stored city tiers, not CSV tags or a previous month's tier.
2. **Choose the tool by intent.**

   | Objective | Tool |
   | --- | --- |
   | Inspect target/actuals for a city, explain its gap, retrieve general observations | `target-facts`, optionally `--city CITY` (repeatable) |
   | Find cities above, exactly at, below target or with unavailable comparisons | `target-facts --attainment above\|at\|below\|unavailable` |
   | Order observations by absolute/relative gap, target or actual | `target-facts --sort city\|shortfall\|shortfall-pct\|target\|actual`, optionally `--descending` |
   | Select cities whose material gaps cover the overall shortfall | `target-analysis --mode top` |
   | List every city meeting the agreed material target-miss rule | `target-analysis --mode all` |
   | Inspect the pooled New launch fallback comparison and its city breakdown | `target-analysis --queue fallback` |

   General facts have **no implicit 5% filter** or coverage stop. Plain “cities
   missing targets” retains the agreed >5% policy via target-analysis in all mode; an explicit
   request for any miss, small misses, equality or exceeded targets uses facts.
   “Top cities” in the agreed intervention workflow retains coverage-based
   selection. For a factual fixed-N ranking, use facts with the requested sort
   and page size and label the displayed subset/count, not a coverage shortlist.
   Clarify only genuinely ambiguous criteria; do not reinterpret a fixed-N list
   as cumulative coverage. Combine reviewed target and MoM/funnel tools when the
   question requests both, keeping the evidence and criteria separate.
   Target analysis defaults to the **plan-matched queue** (cities with an explicit
   city target). It always reports the **pooled New launch fallback comparison**
   for unplanned cities, separately from that ranking. When `fallback.city_count`
   is nonzero, disclose the pooled target/actual/shortfall and ask whether the user
   wants the actual-only city breakdown before running `--queue fallback`.
3. **Read evidence.** Select the matching command and parameters, for example:
   ```bash
   .venv/bin/python -m growthos target-facts --month YYYY-MM --city Delhi
   .venv/bin/python -m growthos target-facts --month YYYY-MM --attainment above --sort shortfall
   .venv/bin/python -m growthos target-analysis --month YYYY-MM --mode top
   .venv/bin/python -m growthos target-analysis --month YYYY-MM --mode all
   .venv/bin/python -m growthos target-analysis --month YYYY-MM --mode top --tier T2
   .venv/bin/python -m growthos target-analysis --month YYYY-MM --tier T3 --queue fallback
   .venv/bin/python -m growthos target-facts --month YYYY-MM --tier T2 --city Jaipur
   ```
   Run only the needed tools. Follow `next_offset` using
   `--offset OFFSET`; `--limit` controls only page size, not matching/selection.
   If a page hits `OUTPUT_BOUND`, reduce `--limit` and retry the same offset.
   Done when evidence covers the requested scope: for an exhaustive facts query,
   collect `coverage.matching_city_count`; for an exhaustive selection, collect
   `coverage.selected_city_count`. A requested displayed subset must be labeled
   with returned/total counts. Compare actual `result_checksum` and month metadata
   across pages; if they change, restart collection.
4. **Answer.** Follow [output-format.md](output-format.md). Lead with the month,
   overall target/actual/shortfall and the requested city/query scope.
   With `scope.tier` present, label these **tier target/actual/gap**, not national
   totals. Coverage means the selected tier's own gap; report that tier's skipped
   null counts and the tool's target-source/reconciliation caveat.
   Include the tool's city, target, actual, shortfall and shortfall %. For selection,
   also include cumulative shortfall, coverage, uncovered amount and overshoot.
   Preserve exact ordering and disclose missing-value reasons/exclusions,
   reconciliation differences and the recorded actual-month provenance. Report the separate pooled
   fallback target/actual/shortfall (`fallback.*`), `fallback.city_count`,
   `fallback.skipped_null_city_count` and `fallback.qualifies` beside the plan-matched
   result, and ask before listing the actual-only fallback breakdown.
   When `persistent_failure.available` is true, render its `rows` as a second
   table headed “Also failing in <previous_month> — shortfall widened >5pp”, with
   city, current shortfall %, previous shortfall %, change in pp and
   `failing_since`. Report `city_count` and `undetermined_city_count` beside it.
   This is additive: never replace, reorder or filter the main shortlist because
   of it. If `available` is false, state the returned reason and do not show the
   table; if `city_count` is zero, say no returned city also widened by >5pp.
   The object annotates only the returned page, so re-check it after paging.
   When `overall.actual_coverage.skipped_null_ftu_city_count` is nonzero, disclose
   that count beside the headline: these cities were skipped in overall recorded
   Platform FTU, not converted to zero. Use `install_or_registration_only_city_count`
   for the subset with only Installs or Registrations. If other null patterns exist,
   report their counts separately rather than claiming all skipped cities had only Installs or Registrations.
   `shortfall_pct` is already a percentage; append `%` without multiplying again.
   `shortfall = target - actual`: a miss has a positive shortfall; preserve the
   returned sign, not actual-minus-target movement.
   For all-city requests, deliver every selected row, in clearly labeled batches
   if needed. If delivery is incomplete, state returned/total counts and never
   describe it as the complete list. Do not save/export reports as a substitute.

## Interpretation

- Facts retain cities that met/exceeded targets, small/exact-5% misses and missing
  comparisons. `above` means actual > target, `at` means equality, `below` means
  actual < target and `unavailable` means the comparison lacks an operand. Zero
  counts remain visible; a zero target makes its percentage unavailable, not its
  valid absolute gap. These are exact predicates, not severity categories.
- Fact city/attainment filters never redefine the selected scope's target, actual or gap.
  Numeric sort ties use city-name order and unavailable sort values stay last.
  `qualifies_for_shortlist` explains the policy but does not filter facts.
- Shortlist qualification means **strictly more than 5% below the city's own positive
  target**. Exactly 5% and smaller misses are excluded in both modes. Explain
  this when using the user's shorthand “did not reach target”; display rounding
  never changes eligibility.
- Without a tier filter, the overall target is the supplied **Grand Total**. The command calculates
  X = max(overall target - recorded overall actual, 0). Recorded actual is the sum
  of non-null city `platform_ftu` values; null-FTU cities are skipped and disclosed.
  This is the approved observed-data basis, not certified complete national Platform FTU.
  Nonzero skipped/unavailable city counts do not block a numeric X under this
  policy. Use `overall.actual`, `overall.shortfall` and their explicit unavailable
  reason; do not relabel them N/A merely because some city FTU values are null.
  If all actual FTU values are null, actual/X remain unavailable, not zero. City targets,
  deduplication and the pooled fallback do not redefine that overall target.
- With `--tier`, the cohort is the cities in that tier in the selected stored
  month. Tier target and actual use only **plan-matched cohort cities** (those
  with an explicit city target), including approved merges and targets retained
  for null-FTU cities. Unplanned cities are outside that calculation and are
  reported as the separate pooled New launch fallback comparison. The command selects against the
  **tier's own plan-matched gap**. National Grand Total is not allocated or used
  for this calculation; summed tier targets need not match it. A tier whose cohort
  has no plan-matched city keeps target/actual/X unavailable rather than inventing
  zero. Do not infer tiers for planned cities with no stored row that month.
  An empty tier raises a coverage limitation, not invented zero totals.
- Top mode stops when cumulative shortfall covers X, permitting overshoot. It
  returns an empty shortlist when X=0; if eligible gaps cannot cover X, the
  uncovered amount remains explicit. All mode returns all qualifying cities even
  when X=0. A missing overall target or no observed FTU keeps X unavailable; an empty top result in that
  case is not evidence that targets were achieved. All mode can still show known
  qualifying city observations.
- Each observed city absent from the plan joins one **New launch fallback pool**.
  The monthly New launch target is compared against the pool's combined non-null
  Platform FTU, giving one target/actual/shortfall/shortfall %; null pool cities
  are skipped and disclosed, not zeroed. Unplanned cities have no individual
  target or shortfall and are never ranked inside the plan-matched queue.
  `target-analysis` always reports this pooled comparison (never added to the
  national Grand Total or any tier gap); `--queue fallback` returns it with an
  actual-only city breakdown. The fallback is a planning assumption, not proof of
  launch, an allocation or a shared per-city budget. Explicit missing/zero plan
  targets do not join the pool.
- The additive `persistent_failure` object lists currently returned material misses
  that were also strictly >5% below their own positive explicit city target in the
  immediately previous calendar month and whose shortfall percentage widened by
  more than 5 percentage points. It uses exact integer arithmetic before display
  rounding. A city failing both months but improving is not listed. Missing prior
  city/target/actual and nonpositive prior targets are undetermined, not failures.
  Only the returned page is annotated; above/at/unavailable facts and pooled
  fallback cities never appear. With `--tier`, the selected month's stored tier is
  the only tier source; prior-month tier need not match. If no prior month has both
  targets and actuals, `available` is false with an explicit reason. Never describe
  the list as every persistent miss.
- Attingal target + Trivandrum target is the canonical Trivandrum target; actuals
  already use that city. Ludhiana uses the source's T2 target row while its actual
  tier remains unchanged. Use returned evidence rather than redoing these joins.
- Arithmetic shortfall coverage is not recoverable FTU, forecast likelihood,
  causal explanation or an action recommendation. Keep this branch target-only;
  CRM, ingestion, operational actions and the one-time merge sanity review are
  not automatic follow-ups.
