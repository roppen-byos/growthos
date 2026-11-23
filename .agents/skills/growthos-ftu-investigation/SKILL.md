---
name: growthos-ftu-investigation
description: "Investigation Layer FTU investigation skill: starts from Assessment Layer shortlisted FTU target-miss cities (top or all) — or explicitly requested scopes — and runs a sequential decision tree: (1) Registration Target Shortfall Check -> stop as registration issue when Registrations explain the gap; (2) Month-on-Month Dip and Persistence Check -> persistent stops and cites failing_since month, non-dipping/undetermined stops and discloses; (3) new_miss -> Funnel Decomposition across FE, FE2RR, and RR2Net (material stages ranked descending until cumulative contribution passes the configured share, plus calendar-adjusted MoM Registration dip check when FE is primary)."
---

# GrowthOS FTU Investigation

Use this skill to investigate **why** Platform FTU missed target or dipped MoM, or to answer granular `FE`, `FE2RR`, `RR2Net`, or `Registration` contribution questions. Route factual lookups, general MoM comparisons, master reports, standalone target tables, and warehouse SQL to [the IRF analysis skill](../growthos-irf-analysis/SKILL.md).

## Contract

- **CLI-only arithmetic:** every number, calendar-adjusted count, ratio, contribution %, verdict, and warning comes from reviewed `growthos` CLI commands. Apply repository-root [AGENTS.md](../../../AGENTS.md) for scope, credentials, and CLI approval.
- **Keep the two analytical lenses separate:**
  - **Target lens (`target-analysis`, `target-facts`, `investigate`):** compares same-month actuals against stored Platform FTU and Registration targets (signed `variance = actual - target`; Registration issue gate is strictly `contribution_pct > 70%`).
  - **Historical funnel lens (`investigate-funnel`):** decomposes a material (`> 5%`) calendar-adjusted MoM Platform FTU dip across FTU-eligible `FE sessions`, `FE2RR`, and `RR2Net` via hold-two-constant counterfactuals on Platform FTU. Stages strictly `> 20%` are ranked descending and selected into primary issues until cumulative contribution exceeds **`> 70%`** (if all `> 20%` stages sum `<= 70%`, retain them and note partial coverage). Whenever `FE` is in `primary_stages`, `investigate-funnel` also runs an independent calendar-adjusted MoM **Registrations** check (`> 5%` dip -> `registration_dip`, rendered inline as `FE (reg. issue)`).
- **Hard scope stops:** stop upstream at **Registrations** (no Installs or Paid/Organic attribution) and stop downstream at **Platform `RR2Net`** (no Bike/Auto/Cab `RR2Net` drilldown).
- **Analytical honesty:** counterfactual effects drop cross-stage interaction terms so the three percentages need not sum to 100% (cumulative `> 70%` is a ranking cutoff, not an additive partition). Never treat `null` as `0`, assert causation from accounting or counterfactual splits, or silently substitute unavailable months.

## Command router

Prefix every command with `.venv/bin/python -m growthos`. Use `<command> --help` for exact flags and bounds.

| User intent | Command & reference |
| --- | --- |
| Full FTU target-miss investigation (broad prompt, e.g. *"investigate October FTU misses"*) | Run the **Full investigation flow** below |
| Granular `FE`, `FE2RR`, or `RR2Net` contribution (city, tier, group, national, history) | `investigate-funnel --month YYYY-MM [--stage fe\|fe2rr\|rr2net ...] [--city CITY ...] [--tier T1\|T2\|T3] [--group "Label:City,City"] [--national] [--start YYYY-MM --end YYYY-MM]`; read [funnel-contribution.md](funnel-contribution.md) |
| Registration target shortfall (city, tier, group, national, history) | `investigate --month YYYY-MM [--city CITY ...] [--tier T1\|T2\|T3] [--group "Label:City,City"] [--national] [--start YYYY-MM --end YYYY-MM]`; read [registration-contribution.md](registration-contribution.md) |
| Shortlist target-miss cities & target-based widening (`persistent` / `new_miss`) annotations | `target-analysis --month YYYY-MM --mode top\|all [--tier T1\|T2\|T3]`; read [target-analysis.md](../growthos-irf-analysis/target-analysis.md) |
| Prior-month target attainment lookup for persistence check | `target-facts --month <prior_YYYY-MM> --city CITY ...` |

## Full investigation flow (Investigation Layer)

GrowthOS separates the **Assessment Layer (observe / shortlist)** from the **Investigation Layer (investigate)**:
- **Assessment Layer (`growthos-irf-analysis` -> `target-analysis`):** Answers *"which cities are failing to achieve targets?"* by showing the FTU target-miss shortlist (`variance < -5%`, `--mode top` or `--mode all` as requested) and the target-based widening-miss (`< -5pp`, `persistent` / `new_miss`) table — without auto-investigating.
- **Investigation Layer (`growthos-ftu-investigation`):** Runs when the user asks to investigate (either as a follow-up to the Assessment Layer or in a broad *"investigate October FTU target misses"* prompt). Starts from **the Assessment Layer's shortlisted cities (`rows`, matching whatever mode the user asked for: `--mode top` by default, or `--mode all` if requested)** and routes each city through the three-step sequential decision tree below.

Steps for a broad investigation request (optionally scoped to a tier) without explicitly named cities:

1. **Resolve one explicit ended month.** Ask if omitted; check `status` if month availability is not yet known in the conversation.
2. **Get the FTU target-miss shortlist (`rows`).** Reuse the current conversation's `target-analysis --month YYYY-MM --mode top|all [--tier T1|T2|T3]` output if already fetched for the same month/mode/checksum, or run it now (default `--mode top`, or `--mode all` if the user requested all misses; load [target-analysis.md](../growthos-irf-analysis/target-analysis.md) for coverage and skipped-null rules).
   - Keep default first-page delivery (`--limit 25`, matching `investigate` / `investigate-funnel`'s 25-city cap per call); never auto-paginate remaining pages unless requested, and disclose `returned / selected_city_count` if `next_offset` is non-null.
   - If the shortlist has `0` cities, report that explicitly (including overall/tier variance and pooled `fallback`) and stop.
   - Disclose the separate pooled `fallback` comparison without mixing unplanned cities into city-level investigation.
3. **Step 1 — Registration Target Shortfall Check (`investigate`).** Load [registration-contribution.md](registration-contribution.md) and call `investigate --month YYYY-MM --city <C1> --city <C2> ...` on the shortlisted cities (up to 25 per call).
   - **`registration_issue` (`contribution_pct > 70%`):** **STOP** for this city. Report it as a registration issue (noting `over_explained` if `> 100%`) and do **not** proceed to MoM persistence or funnel analysis for it. Stop upstream at Registrations (no Installs or Paid/Organic analysis).
   - **`below_threshold` (`0% < contribution_pct <= 70%`), `no_registration_shortfall` (`reg_gap >= 0`), or `undetermined` (`N/A`, disclosed with exact reason):** **continue** to Step 2.
4. **Step 2 — Month-on-Month Dip and Persistence Check.** For every remaining city that did not stop at Step 1:
   - Load [funnel-contribution.md](funnel-contribution.md) and call `investigate-funnel --month YYYY-MM --city <R1> --city <R2> ...` on those remaining cities.
   - **Did NOT dip `> 5%` MoM (`no_historical_ftu_dip`, `no_material_ftu_dip` for `<= 5%` dip, or `undetermined` FTU dip):** **STOP** for this city. Disclose its calendar-adjusted MoM FTU change (or unavailable reason) separately and do not decompose funnel stages.
   - **Dipped `> 5%` calendar-adjusted MoM:** check whether the city also missed its **prior-month (`prior_month`) FTU target by `> 5%`** (1-month lookback):
     - If the Assessment Layer's `persistent_failure.available` is `false` (no stored prior month with both FTU targets and actuals), **STOP** for all dipping cities, disclose `prior-month persistence unavailable (no_previous_month_with_targets_and_actuals)`, and offer funnel analysis on request without silently showing un-gated funnel drivers.
     - Otherwise, call `target-facts --month <prior_month> --city <D1> --city <D2> ...` on the dipping cities and inspect each city's `qualifies_for_shortlist` (`variance < -5%` against its explicit positive prior-month FTU target):
       - **`qualifies_for_shortlist == true` → `persistent` → STOP:** state explicitly that the city **has been failing since `<prior_month>` and needs analysis for `<prior_month>` to investigate**. Do **not** present current-month `FE` / `FE2RR` / `RR2Net` decomposition for this city.
       - **`qualifies_for_shortlist == false` with valid positive prior `target` and non-null prior `actual` → `new_miss` → continue to Step 3.**
       - **Missing prior observation, missing/nonpositive prior target, missing prior actual, or `pooled_new_launch_target` → `undetermined` → STOP:** disclose `N/A (reason)` and skip current-month funnel decomposition (offer on request).
5. **Step 3 — Funnel Decomposition (`investigate-funnel`, `new_miss` cities only).** Present the calendar-adjusted MoM Platform FTU dip counterfactual decomposition across `FE`, `FE2RR`, and `RR2Net` (stages above the material floor ranked descending until cumulative contribution passes the configured primary cutoff in `primary_stages`, stopping downstream at Platform `RR2Net`) from Step 2's `investigate-funnel` output for **`new_miss` cities only**. Whenever `fe` is in `primary_stages`, inspect `registration_check` (independent of Step 1's Registration target check; upstream still stops at Registrations) and annotate `FE` inline in `Primary Issues` (`FE (reg. issue)` for `registration_dip`, plain `FE` for `below_threshold` / `no_registration_dip`, `FE (reg. N/A)` for `undetermined`), citing each triggered city's exact calendar-adjusted MoM `Registrations %` in a one-line note right below the Step 3 table.
6. **Verify provenance consistency.** Confirm `result_checksum` and month metadata match across all commands in the investigation; if a refresh occurred mid-run, rerun affected commands before answering.

## Granular question flow

When the user asks a targeted contribution question (for example *"what was FE2RR contribution in Mumbai for October?"*, *"check RR2Net in T2"*, or *"did Registrations explain Delhi's target miss?"*):

- Run only the matching command directly (`investigate-funnel` with `--stage` if specific stages were requested, or `investigate` for Registrations) on the requested city, tier, group, national, or history scope — never force the full shortlist/gating flow first.
- Load the matching reference ([funnel-contribution.md](funnel-contribution.md) and/or [registration-contribution.md](registration-contribution.md)) before interpreting verdicts.
- If a user asks about a city's FTU target miss or FE dip without specifying one lens, you may offer the companion lens (`investigate` or `investigate-funnel`) as a follow-up.

## Output format & completion check

Before composing the response, read [output-format.md](../growthos-irf-analysis/output-format.md) and satisfy every item below:

1. **Bottom line:** state the selected month, scope (`top` or `all` shortlist page), total shortlisted cities investigated, and the funnel-down count across outcomes: how many stopped at **Registration Target Shortfall Check**, how many stopped at **Month-on-Month Dip and Persistence Check (`persistent` since `<prior_month>`, non-dipping, or prior-month undetermined)**, and how many reached **Funnel Decomposition (`new_miss` funnel drivers)** — plus the dominant patterns across groups.
2. **Outcome-grouped, lens-separated tables:**
   - **Registration Target Shortfall Check (Target lens, `registration_issue` cities only):** `City | FTU Target | FTU Actual | FTU Variance % | Reg Variance % | Reg Contribution % | Outcome` (stating the configured Registration-contribution rule once). Include only cities whose verdict is `registration_issue` and show the Outcome as **Registration issue**. Omit cities that continue (`below_threshold`, `no_registration_shortfall`, or `undetermined`) from this table; they appear in the Month-on-Month Dip and Persistence Check table below. If no city stopped here, state that explicitly instead of showing the table.
   - **Month-on-Month Dip and Persistence Check (cities not proceeding to Funnel Decomposition):** `City | Cal.-Adj. MoM FTU % | Prior Month Variance % | Status | Action` — include only cities that stop here:
     - **`persistent`** (*"Failing since `<prior_month>`"*) — do not append "stop" or "stopped" to this cell,
     - **No material MoM dip / prior undetermined** (disclosed with exact reason).
     - Omit `new_miss` cities from this table entirely; they appear only in the Funnel Decomposition table below.
   - **Funnel Decomposition (Historical funnel lens, `new_miss` cities only):** `City | Cal.-Adj. MoM FTU Dip | Primary Issues | FE Share of Dip | FE2RR Share of Dip | RR2Net Share of Dip` (stating the material stage floor, descending primary cutoff, and sign legend directly above the table once: `+` means the metric contributed to FTU improvement, cushioning the fall; `−` means the metric pulled FTU down and deepened the fall; percentages are shares of the dip, not metric growth rates).
     - Format `FE` inside `Primary Issues` using `registration_check` (triggered only when `fe` is in `primary_stages`): **`FE (reg. issue)`** when `verdict == "registration_dip"` (`> 5%` calendar-adjusted MoM Registrations dip), plain **`FE`** when `below_threshold` or `no_registration_dip`, and **`FE (reg. N/A)`** when `undetermined`. Whenever at least one city triggers `registration_check`, add a concise one-line note directly below the Step 3 table listing each triggered city's exact calendar-adjusted MoM `Registrations %` (or unavailable reason) from the CLI.
     - Format each stage cell per [funnel-contribution.md](funnel-contribution.md#table-formatting):
       - Primary issue (`selected_as_primary: true`, `> 20%` and in top-70% set): **`−60.98%`** (append `*(>100% of dip)*` if `over_explained`).
       - Secondary material (`material: true`, `selected_as_primary: false`, `> 20%` after `> 70%` already reached): `−27.44% *(also material)*`.
       - `below_threshold` (`0%..20%`): `−5.23%`.
       - `no_stage_deterioration` (`<= 0%`): `+3.02%` (or `0.00%` when exact zero). Display the reversed sign of `contribution_pct` so decliners show `−` and improvers show `+`.
       - `undetermined`: `N/A *(reason)*`.
   - Use readable business labels and avoid leaking internal `DEC-*` IDs or DB table names in routine answers.
3. **Consolidated data & method note (once per answer):**
   - **FTU-eligible funnel basis:** `FE sessions`, `RR`, and `Net` cover the FTU-eligible cohort (activity on or before each customer's lifetime first completed platform ride plus Zero-Transaction customers with no completed ride yet, including same-day rides on the first-ride day); counterfactuals apply FTU-eligible relative stage changes to scaled Platform FTU (`prior_ftu_scaled × (current_stage / prior_stage − 1) / ftu_change`). Because interaction terms are omitted, the three stage percentages need not sum to 100%; the `> 70%` primary cutoff ranks `> 20%` contributors rather than forming an additive partition.
   - **Proportional calendar scaling:** when prior and current months differ in calendar length (e.g. Sep 30d vs Oct 31d), volume counts in the longer month are proportionally scaled to the shorter month's length (average daily run-rate adjustment, not literal removal of the extra day); conversion rates are unaffected.
   - **Coverage & provenance:** disclose unavailable reasons, skipped null-FTU counts, pooled New launch fallback summary, aggregate `blocking_city_keys`, national `matched_city_count` / `excluded_city_count`, and month source-read times.
4. **Bounded follow-up offer (when applicable):** offer next-month/prior-month investigation for `persistent` cities (e.g. *"Want me to run the investigation for `<prior_month>` on the persistent cities?"*), next page (`next_offset`), or opt-in `--queue fallback` breakdown without auto-running them.

## When it fails

Follow the error-class recovery rules in [growthos-irf-analysis/SKILL.md](../growthos-irf-analysis/SKILL.md#when-it-fails). In addition:

- `BOUNDS` on multi-month `--start/--end` funnel history (limit is 12 stored months including each month's prior month, i.e. at most 11 consecutive requested months) or >25 `--city` flags: narrow the window or batch cities and disclose the narrower scope.
- `no_prior_month`, `no_prior_city_observation`, `targets_not_imported`, or `registration_targets_not_imported` in valid JSON: keep the block visible as `N/A` with its exact reason without substituting an older month.
