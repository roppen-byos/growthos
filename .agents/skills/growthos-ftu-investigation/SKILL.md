---
name: growthos-ftu-investigation
description: "Layer 2 FTU investigation skill: take cities from the widening-miss (persistent/new_miss) table — or explicitly requested cities, tiers, groups, national scope, or history — and run independent same-month Registration target-miss checks (< -5% vs target) alongside parallel calendar-adjusted MoM funnel contributions across FE sessions, FE2RR, and RR2Net (>20%)."
---

# GrowthOS FTU Investigation

Use this skill to investigate **why** Platform FTU missed target or dipped MoM, or to answer granular `FE`, `FE2RR`, `RR2Net`, or `Registration` contribution questions. Route factual lookups, general MoM comparisons, master reports, standalone target tables, and warehouse SQL to [the IRF analysis skill](../growthos-irf-analysis/SKILL.md).

## Contract

- **CLI-only arithmetic:** every number, calendar-adjusted count, ratio, contribution %, verdict, and warning comes from reviewed `growthos` CLI commands. Apply repository-root [AGENTS.md](../../../AGENTS.md) for scope, credentials, and CLI approval.
- **Keep the two analytical lenses separate:**
  - **Target lens (`target-analysis`, `investigate`):** compares same-month actuals against stored Platform FTU and Registration targets (signed `variance = actual - target`; material Registration miss is strictly `< -5%` of Registration target).
  - **Historical funnel lens (`investigate-funnel`):** decomposes a calendar-adjusted MoM Platform FTU dip across city-wide (all-customer) `FE sessions`, `FE2RR`, and `RR2Net` (strictly `> 20%`, parallel independent).
- **Hard scope stops:** stop upstream at **Registrations** (no Installs or Paid/Organic attribution) and stop downstream at **Platform `RR2Net`** (no Bike/Auto/Cab `RR2Net` drilldown).
- **Analytical honesty:** never sum primary funnel contributions, treat `null` as `0`, assert causation from accounting or proxy splits, or silently substitute unavailable months.

## Command router

Prefix every command with `.venv/bin/python -m growthos`. Use `<command> --help` for exact flags and bounds.

| User intent | Command & reference |
| --- | --- |
| Full FTU target-miss investigation (broad prompt, e.g. *"investigate October FTU misses"*) | Run the **Full investigation flow** below |
| Granular `FE`, `FE2RR`, or `RR2Net` contribution (city, tier, group, national, history) | `investigate-funnel --month YYYY-MM [--stage fe\|fe2rr\|rr2net ...] [--city CITY ...] [--tier T1\|T2\|T3] [--group "Label:City,City"] [--national] [--start YYYY-MM --end YYYY-MM]`; read [funnel-contribution.md](funnel-contribution.md) |
| Same-month Registration target-miss check & accounting context (city, tier, group, national, history) | `investigate --month YYYY-MM [--city CITY ...] [--tier T1\|T2\|T3] [--group "Label:City,City"] [--national] [--start YYYY-MM --end YYYY-MM]`; read [registration-contribution.md](registration-contribution.md) |
| Shortlist target-miss cities & widening (`persistent` / `new_miss`) annotations | `target-analysis --month YYYY-MM --mode top [--tier T1\|T2\|T3]`; read [target-analysis.md](../growthos-irf-analysis/target-analysis.md) |

## Full investigation flow (Layer 2)

GrowthOS separates **Layer 1 (Observe / Shortlist)** from **Layer 2 (Investigate)**:
- **Layer 1 (`growthos-irf-analysis` -> `target-analysis`):** Answers *"which cities are failing to achieve targets?"* by showing the FTU target-miss shortlist and the widening-miss (`persistent` / `new_miss`) table — without auto-investigating.
- **Layer 2 (`growthos-ftu-investigation`):** Runs when the user asks to investigate (either as a follow-up to Layer 1 or in a broad *"investigate October FTU target misses"* prompt), taking the cities from the **`persistent` / `new_miss` widening-miss table (`persistent_failure.rows`)** as the default investigation cohort.

Steps for a broad investigation request (optionally scoped to a tier) without explicitly named cities:

1. **Resolve one explicit ended month.** Ask if omitted; check `status` if month availability is not yet known in the conversation.
2. **Get the widening-miss (`persistent` / `new_miss`) cohort.** Reuse the current conversation's `target-analysis --month YYYY-MM --mode top [--tier T1|T2|T3]` output if already fetched for the same month/checksum, or run it now (load [target-analysis.md](../growthos-irf-analysis/target-analysis.md) for coverage, skipped-null, and widening-miss rules).
   - Keep default first-page delivery (`--limit 25`, which also matches `investigate` / `investigate-funnel`'s 25-city cap per call); never auto-paginate remaining pages unless requested, and disclose `returned / selected_city_count` if `next_offset` is non-null.
   - Select the candidate cities from **`persistent_failure.rows`**, ordered with **`persistent`** cities first, followed by **`new_miss`** cities.
   - If `persistent_failure.available` is `false` (for example no prior-month targets/actuals) or `persistent_failure.city_count == 0` (no returned shortlist city widened by `< -5pp`), report that explicitly (including the overall/tier variance, shortlist summary, and pooled `fallback`) and **offer** to investigate the top shortlisted cities directly rather than silently switching cohorts.
   - Disclose the separate pooled `fallback` comparison without mixing unplanned cities into city-level investigation.
3. **Run same-month Registration target checks (Target lens, independent of FE).** Load [registration-contribution.md](registration-contribution.md) and call `investigate --month YYYY-MM --city <C1> --city <C2> ...` on the `persistent` / `new_miss` candidate cities (up to 25 cities per call) to check whether Registrations materially missed its own same-month target (strictly `< -5%`), regardless of whether `FE` crossed `> 20%` or whether MoM FTU dipped.
   - Stop upstream at Registrations (no Installs or Paid/Organic analysis).
   - If `investigate` returns `targets_not_imported`, `registration_targets_not_imported`, or `missing_registration_target`, keep the city visible as `N/A` with that exact reason.
4. **Run parallel funnel contribution (`FE`, `FE2RR`, `RR2Net`, Historical funnel lens).** Load [funnel-contribution.md](funnel-contribution.md) and call `investigate-funnel --month YYYY-MM --city <C1> --city <C2> ...` on the same `persistent` / `new_miss` candidate cities.
5. **Verify provenance consistency.** Confirm `result_checksum` and month metadata match across all commands in the investigation; if a refresh occurred mid-run, rerun affected commands before answering.

## Granular question flow

When the user asks a targeted contribution question (for example *"what was FE2RR contribution in Mumbai for October?"*, *"check RR2Net in T2"*, or *"did Registrations explain Delhi's target miss?"*):

- Run only the matching command directly (`investigate-funnel` with `--stage` if specific stages were requested, or `investigate` for Registrations) on the requested city, tier, group, national, or history scope — never force a shortlist first.
- Load the matching reference ([funnel-contribution.md](funnel-contribution.md) and/or [registration-contribution.md](registration-contribution.md)) before interpreting verdicts.
- If a user asks about a city's FTU target miss or FE dip without specifying one lens, you may offer the companion lens (`investigate` or `investigate-funnel`) as a follow-up.

## Output format & completion check

Before composing the response, read [output-format.md](../growthos-irf-analysis/output-format.md) and satisfy every item below:

1. **Bottom line:** state the selected month, scope, how many widening-miss (`persistent` vs `new_miss`) cities were investigated out of the returned FTU shortlist page, and synthesize the dominant driver patterns across both lenses (e.g. Registration target miss + FE dip, Registration target miss without MoM dip, conversion-led `FE2RR`/`RR2Net`, or target miss without funnel regression) rather than just restating table cells.
2. **Separated dual-lens tables:**
   - **Target lens panel:** `City | Status | FTU Target | FTU Actual | FTU Variance % | Reg Variance % | Reg Contribution % | Reg Verdict` (stating the strict `< -5%` Registration target-miss rule once).
   - **Historical funnel lens panel:** `City | Cal.-Adj. MoM FTU Dip | Material Drivers (>20%) | FE Share of Dip | FE2RR Share of Dip | RR2Net Share of Dip` (stating the strict `> 20%` rule and sign convention once: `+` = stage worsened and contributed to the dip; `−` = stage stayed flat or improved and helped offset the dip).
   - Format each stage cell per [funnel-contribution.md](funnel-contribution.md#table-formatting):
     - `material_contributor` (`> 20%`): **`+87.81%`** (append `*(>100% offset)*` if `over_explained`).
     - `below_threshold` (`0%..20%`): `+5.23%` (plain number; no verbose inline label).
     - `no_stage_deterioration` (`<= 0%`): `-3.02% *(improved)*` (or `0.00% *(flat)*` when exact zero; never drop the minus sign or present it as a cause).
     - `not_applicable` (`no_historical_ftu_dip`): `—` (with `No MoM dip` in `Material Drivers (>20%)`).
     - `undetermined`: `N/A *(reason)*`.
   - Use readable business labels and avoid leaking internal `DEC-*` IDs or DB table names in routine answers.
3. **Consolidated data & method note (once per answer):**
   - **All-customer proxy:** `FE sessions`, `RR`, and `Net` cover all customers (not an FTU-eligible-only cohort); stage percentages apply city-wide relative changes to scaled Platform FTU and are non-additive (never summed).
   - **Proportional calendar scaling:** when prior and current months differ in calendar length (e.g. Sep 30d vs Oct 31d), volume counts in the longer month are proportionally scaled to the shorter month's length (average daily run-rate adjustment, not literal removal of the extra day); conversion rates are unaffected.
   - **Coverage & provenance:** disclose unavailable reasons, skipped null-FTU counts, pooled New launch fallback summary, aggregate `blocking_city_keys`, national `matched_city_count` / `excluded_city_count`, and month source-read times.
4. **Bounded follow-up offer (when applicable):** offer next page (`next_offset`) or opt-in `--queue fallback` breakdown without auto-running them.

## When it fails

Follow the error-class recovery rules in [growthos-irf-analysis/SKILL.md](../growthos-irf-analysis/SKILL.md#when-it-fails). In addition:

- `BOUNDS` on multi-month `--start/--end` funnel history (limit is 12 stored months including each month's prior month, i.e. at most 11 consecutive requested months) or >25 `--city` flags: narrow the window or batch cities and disclose the narrower scope.
- `no_prior_month`, `no_prior_city_observation`, `targets_not_imported`, or `registration_targets_not_imported` in valid JSON: keep the block visible as `N/A` with its exact reason without substituting an older month.
