# Registration Contribution Rules (`investigate`)

Use this reference whenever interpreting `.venv/bin/python -m growthos investigate` output — either as **Step 1 — Registration Target Shortfall Check** of the sequential Investigation Layer FTU investigation flow on shortlisted FTU target-miss cities, or for direct same-month Registration target questions. For calendar-adjusted MoM funnel contribution (`investigate-funnel`), see [funnel-contribution.md](funnel-contribution.md).

## Command examples

```bash
.venv/bin/python -m growthos investigate --month 2026-10 --national
.venv/bin/python -m growthos investigate --month 2026-10 --city Mumbai --city Delhi
.venv/bin/python -m growthos investigate --month 2026-10 --tier T2
.venv/bin/python -m growthos investigate --month 2026-10 --group "South:Trivandrum,Kochi,Chennai"
.venv/bin/python -m growthos investigate --month 2026-10 --city Mumbai --start 2026-06 --end 2026-10
```

## Method and exact boundaries

- **Lens:** evaluates how much of a **same-month Platform FTU target miss** (`ftu_gap < 0`) is accounted for by a same-month **Registration target shortfall**, using exact target-accounting arithmetic (`expected_ftu`, `registration_effect`, `unexplained_gap`, `contribution_pct`). This is target-accounting evidence — not causal proof and not a month-on-month comparison.
- **Arithmetic per scope (exact rational arithmetic; floats are display only):**
  - `ftu_gap = platform_ftu_actual - platform_ftu_target` (negative on a miss)
  - `reg_gap = registrations_actual - registrations_target` (negative on a miss); `reg_gap_pct = 100 * reg_gap / registrations_target`
  - `target_ratio = platform_ftu_target / registrations_target`
  - `expected_ftu = registrations_actual * target_ratio`
  - `registration_effect = expected_ftu - platform_ftu_target` (negative when registrations miss target)
  - `unexplained_gap = ftu_gap - registration_effect`
  - `contribution = registration_effect / ftu_gap` (when `ftu_gap < 0`)
- **Strict `> 70%` Registration contribution rule:** when `ftu_gap < 0`, a scope flags `registration_issue` iff `contribution > 70%` (exact rational test `contribution > Fraction(7, 10)`). Exact `70.00%` and lower positive contributions return `below_threshold` (`contribution_at_or_below_70_percent`); meeting or exceeding the Registration target (`reg_gap >= 0`) returns `no_registration_shortfall`. Display rounding never changes a verdict.
- **Investigation Layer routing:** in the sequential FTU investigation flow, `registration_issue` (`> 70%`) **stops** further investigation for that city; `below_threshold`, `no_registration_shortfall`, and `undetermined` (disclosed as `N/A`) continue to Step 2 — Month-on-Month Dip and Persistence Check.
- **Nulls and zeros:** null targets and null actuals are never treated as zero; missing or nonpositive targets keep the verdict `undetermined` while leaving valid observed facts visible.

## Verdicts

| Verdict | Meaning |
| --- | --- |
| `registration_issue` | `ftu_gap < 0` and Registration contribution is strictly above `70%` (`contribution > 70%`); flag `over_explained` when `contribution_pct > 100%` (positive `unexplained_gap`: actual FTU above expected partly offset the miss). **Stops the Investigation Layer for that city.** |
| `below_threshold` | `ftu_gap < 0`, `reg_gap < 0`, and `0% < contribution_pct <= 70%` (including exact `70.00%`; reason `contribution_at_or_below_70_percent`). Continues to Step 2 (Month-on-Month Dip and Persistence Check). |
| `no_registration_shortfall` | `ftu_gap < 0` and `reg_gap >= 0`; Registrations met or exceeded target. Continues to Step 2 (Month-on-Month Dip and Persistence Check). |
| `not_applicable` | `ftu_gap >= 0` (`ftu_target_met`); Platform FTU met or exceeded target (factual counts remain visible) |
| `undetermined` | Required month, target import, city observation, group member, or positive operand unavailable (`no_stored_month`, `targets_not_imported`, `registration_targets_not_imported`, `incomplete_group_member`, `missing_*`, `nonpositive_*`, `pooled_new_launch_target_has_no_per_city_value`, etc.). Disclosed as `N/A` and continues to Step 2 (Month-on-Month Dip and Persistence Check). |

## Scopes and history

- **Default:** with no selector (`--city`, `--tier`, `--group`), runs the `--national` Grand Total block.
- **Named cities (`--city`):** each named city is one independent block; never sum separate cities unless `--tier` or `--group` was explicitly requested.
- **`--tier` and `--group`:** sum member `registration_effect` and `ftu_gap` across all members, and stay `undetermined` (`incomplete_group_member`) unless **every** member is calculable — always cite `blocking_city_keys` and never fall back to a partial subset. For `--tier`, only cities with an explicit target whose stored tier matches the selected month join the tier aggregate; unplanned tier cities are excluded and covered only by the separate pooled New launch block. The unattributed `city_not_found` bucket is not a tier member and never joins a tier aggregate.
- **Pooled New launch block:** compares the single stored New launch target pair against combined observed actuals for both metrics at cohort level only; disclose `coverage_mismatch` when observed FTU and Registration city sets differ.
- **National (`--national`):** uses the supplied Grand Total target pair with observed non-null actuals for both metrics, discloses per-metric city coverage, and need not reconcile with summed city effects.
- **Month history (`--start` / `--end`):** evaluates each month independently and reports `no_stored_month`, `targets_not_imported`, or `registration_targets_not_imported` per month instead of failing the whole range (`--month` must fall inside `--start..--end`).
- **`shortlist_eligible`:** mirrors the `>5%` Platform FTU shortlist rule for cities (`null` for aggregate scopes); running `investigate` never changes shortlist selection.

## Wording and reporting guardrails

- Always show the returned **verdict** beside `reg_gap_pct` and `contribution_pct` in every row and state the strict `> 70%` Registration contribution rule once per answer.
- Retain contributions above `100%` (`over_explained`) and explain them as **partial offsets** (better-than-target FTU relative to registrations), never as errors.
- State what the registration shortfall **accounts for** in target accounting; never say registrations *caused* the FTU gap, never label the unexplained remainder a "conversion problem," and use **"target"** rather than **"plan"** in new output.
- Stop upstream at **Registrations**; do not extend into Installs or Paid/Organic attribution.
