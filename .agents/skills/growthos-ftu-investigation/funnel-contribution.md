# Funnel Contribution Rules (`investigate-funnel`)

Use this reference whenever interpreting `.venv/bin/python -m growthos investigate-funnel` output. For same-month Registration target contribution (`investigate`), see [registration-contribution.md](registration-contribution.md).

## Command examples

```bash
.venv/bin/python -m growthos investigate-funnel --month 2026-10 --national
.venv/bin/python -m growthos investigate-funnel --month 2026-10 --city Mumbai --city Delhi
.venv/bin/python -m growthos investigate-funnel --month 2026-10 --city Mumbai --stage fe2rr
.venv/bin/python -m growthos investigate-funnel --month 2026-10 --tier T2 --stage fe --stage rr2net
.venv/bin/python -m growthos investigate-funnel --month 2026-10 --group "South:Trivandrum,Kochi,Chennai"
.venv/bin/python -m growthos investigate-funnel --month 2026-10 --city Mumbai --start 2026-07 --end 2026-10
```

## Method and exact boundaries

- **Baseline:** immediately previous calendar month (`prior_month`). Never substitute an older month when `prior_month` is absent.
- **Calendar scaling:** let `d_ref = min(prior_days, current_days)`. Volume counts (Platform FTU, FE sessions, RR, Net) in the longer month are multiplied by `d_ref / month_days` using exact rational arithmetic (e.g. October 31 days vs September 30 days scales October by `30/31`). Rates `FE2RR = RR / FE sessions` and `RR2Net = Net / RR` are invariant under scaling. Always describe this as proportional daily-average scaling, never as literally removing the extra calendar day.
- **Strict `> 5%` calendar-adjusted MoM FTU dip gate:**
  - `ftu_change = current_ftu_scaled - prior_ftu_scaled` (negative indicates an MoM dip).
  - Stage decomposition runs only when the calendar-adjusted MoM FTU drop is strictly greater than `5%` (`ftu_change / prior_ftu_scaled < -5%`, exact rational test).
  - `ftu_change >= 0` returns `no_historical_ftu_dip` (`ftu_not_declining_vs_prior_month`); a small drop within `5%` (`-5% <= ftu_change_pct < 0%`, including exact `-5.00%`) returns `no_material_ftu_dip` (`ftu_dip_within_5_percent`). In both cases stages return `not_applicable` while factual counts and rates remain visible.
- **Hold-two-constant counterfactual proxies on Platform FTU (when MoM FTU dip `> 5%`):**
  - `stage_effect = prior_ftu_scaled × (current_stage / prior_stage − 1)` (`counterfactual_ftu = prior_ftu_scaled + stage_effect`) for each of `fe`, `fe2rr`, `rr2net`.
  - `contribution = stage_effect / ftu_change` (positive when the stage worsened during a material FTU dip). Because holding two factors constant omits cross-stage interaction terms, the three stage percentages need not sum to 100%. Every stage evaluates independently even if `Net` rides grew MoM.
- **Two-threshold issue selection (`> 20%` stage floor + `> 70%` cumulative primary cutoff):**
  - **Stage materiality (`material_stages`):** strictly above `20%` (`> 1/5`) flags `material_contributor`; exact `20.00%` and below return `below_threshold`.
  - **Primary issues (`primary_stages`):** sort `material_stages` (`> 20%`) descending by exact rational contribution (tie-break `fe -> fe2rr -> rr2net`) and take stages until cumulative contribution is strictly **`> 70%`** (`> 7/10`).
    - If one stage alone exceeds `70%`, only that stage is in `primary_stages`.
    - If two stages cross `> 70%` (e.g. `RR2Net 60.98% + FE 36.59% = 97.56%`), those two are `primary_stages` while the third `> 20%` stage (`FE2RR 27.44%`) remains visible with `material: true, selected_as_primary: false`.
    - If all `> 20%` stages combined still sum `<= 70%`, all `> 20%` stages are retained in `primary_stages` with `primary_coverage_reached_70_pct: false`.
  - Exact rational arithmetic governs every threshold and rank; display rounding never changes a verdict or primary selection.

## Stage and block verdicts

### Table formatting

Render the historical funnel panel with these exact six columns:

`City | Cal.-Adj. MoM FTU Dip | Primary Issues (>20%, cum. >70%) | FE Share of Dip | FE2RR Share of Dip | RR2Net Share of Dip`

Add one concise note beside the table: `+` means the stage worsened and contributed to the FTU dip (**bold** = selected primary issue from `> 20%` stages until cumulative `> 70%`); `−` means the stage stayed flat or improved (helped offset the dip). Counterfactual percentages omit interaction terms and need not sum to 100%.

Stage verdicts and compact cell formatting:

| Stage verdict / selection | Cell format | Meaning |
| --- | --- | --- |
| `material_contributor` (`selected_as_primary: true`) | **`+60.98%`** (add `*(>100% offset)*` if `over_explained`) | `contribution_pct > 20%` and selected into `primary_stages` (top descending until cumulative `> 70%`) |
| `material_contributor` (`selected_as_primary: false`) | `+27.44% *(>20%, non-primary)*` | `contribution_pct > 20%`, but earlier stages already exceeded `> 70%` cumulative |
| `below_threshold` | `+5.23%` | Stage deteriorated (`0% < contribution_pct <= 20%`) |
| `no_stage_deterioration` | `−3.02% *(improved)*` (`0.00% *(flat)*` if zero) | Stage stayed flat or improved (`contribution_pct <= 0%`); negative percentages are counter-effects, not causes |
| `not_applicable` | `—` | Scaled Platform FTU did not dip strictly `> 5%` (`no_historical_ftu_dip` or `no_material_ftu_dip`); factual rates stay visible in prose/details if relevant |
| `undetermined` | `N/A *(reason)*` | Missing or nonpositive required operand (`missing_*`, `nonpositive_*`, `incomplete_group_member`, `no_prior_month`, etc.) |

Block verdicts and `Primary Issues (>20%, cum. >70%)` summary values:

| Block verdict | `Primary Issues (>20%, cum. >70%)` cell | Meaning |
| --- | --- | --- |
| `contributors_identified` | **`RR2Net, FE`** (append `*(<70% cum.)*` when `primary_coverage_reached_70_pct: false`) | Cal.-adj. MoM FTU dip `> 5%`, all requested stages evaluated, at least one `> 20%` |
| `partial_contributors_identified` | **`FE`** *(partial)* | Cal.-adj. MoM FTU dip `> 5%`, at least one stage `> 20%`, while another stage was `undetermined` |
| `no_material_funnel_contributor` | `None` | Cal.-adj. MoM FTU dip `> 5%`, all requested stages evaluated, none `> 20%` |
| `partial_no_material_contributor` | `None *(partial)*` | Cal.-adj. MoM FTU dip `> 5%`, evaluated stages `<= 20%`, at least one stage `undetermined` |
| `no_material_ftu_dip` | `Dip <= 5% MoM` | Scaled MoM FTU dipped by `<= 5%` (`-5% <= ftu_change_pct < 0%`); below materiality threshold |
| `no_historical_ftu_dip` | `No MoM dip` | Scaled `ftu_change >= 0`; no MoM dip to decompose |
| `undetermined` | `Undetermined *(reason)*` | Month, prior month, city, FTU, or all requested stages unavailable |

## Scope boundaries (sequential Investigation Layer checks)

- In the default Investigation Layer FTU investigation flow, `investigate-funnel` decomposition is presented only for shortlisted cities that:
  1. Did **not** stop at Step 1 (`investigate` Registration contribution `<= 70%`, `no_registration_shortfall`, or `undetermined`),
  2. Had a material **`> 5%` calendar-adjusted MoM FTU dip**, and
  3. Are **`new_miss`** (did **not** miss prior-month FTU target by `> 5%`; `persistent` cities stop and recommend investigating `<prior_month>`).
- For direct granular user queries naming a city, tier, group, national scope, or history, `investigate-funnel` remains directly callable without requiring the shortlist or earlier Investigation Layer checks (while still enforcing the `> 5%` calendar-adjusted MoM FTU dip gate and `> 20%` / `> 70%` selection internally).
- Platform `RR2Net` is the final downstream stage in this workflow; Bike/Auto/Cab `RR2Net` drilldowns are intentionally deferred.

## Aggregate scopes

- **Named cities:** each `--city` returns one independent block; never sum separate cities unless an explicit `--group` or `--tier` was requested.
- **`--tier` and `--group`:** roll up counts before computing ratios. `--tier` uses the selected month's stored tier cohort. Member completeness is strict per stage: if one city lacks `Net`, only `rr2net` returns `undetermined` (`incomplete_group_member`) with `blocking_city_keys`, while `fe` and `fe2rr` still evaluate.
- **`--national`:** default when no selector is supplied; rolls up matched cities observed in both months per metric/stage (`platform_ftu`, `fe`, `fe2rr`, `rr2net`). Always report `matched_city_count` and `excluded_city_count` from `coverage`.

## Wording guardrails

- State that `FE`, `FE2RR`, and `RR2Net` come from the **FTU-eligible funnel** (days on/before first completed platform ride + Zero-Transaction customers, counting sessions/rides including same-day rides rather than distinct FTU users) and are applied as hold-two-constant counterfactuals on scaled Platform FTU — not causal proof.
- When `primary_coverage_reached_70_pct` is `false` (all `> 20%` stages summed `<= 70%`), still report `primary_stages` and explicitly note the cumulative share was `<= 70%`.
- When a block returns `no_material_funnel_contributor`, show all three measured stage percentages and note that combined small dips or differences between session/ride counts (including same-day repeat rides) and distinct Platform FTU may be at play, without asserting an unmeasured cause.
- Keep **Target lens** (`variance = actual - target`, Registration contribution `> 70%`) clearly distinct from **Historical funnel lens** (calendar-adjusted MoM FTU dip `> 5%`, `> 20%` stage floor, `> 70%` cumulative primary cutoff).
