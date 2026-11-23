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
- **Calendar scaling:** let `d_ref = min(prior_days, current_days)`. Volume counts (Platform FTU, Registrations, FE sessions, RR, Net) in the longer month are multiplied by `d_ref / month_days` using exact rational arithmetic (e.g. October 31 days vs September 30 days scales October by `30/31`). Rates `FE2RR = RR / FE sessions` and `RR2Net = Net / RR` are invariant under scaling. Always describe this as proportional daily-average scaling, never as literally removing the extra calendar day.
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
- **MoM Registration check when `FE` is primary (`registration_check`):**
  - Triggers **only when `fe` is in `primary_stages`** (completely independent of Step 1's same-month Registration target check; upstream still stops at Registrations). When `fe` is not in `primary_stages` (including `fe` material-but-non-primary, `no_material_ftu_dip`, `no_historical_ftu_dip`, or `--stage` excluding `fe`), `registration_check` returns `triggered: false`, `verdict: "not_triggered"`, `reason: "fe_not_primary"`, and `null` metric fields.
  - Uses the same shorter-month proportional calendar scaling on `registrations` (`reg_change = current_reg_scaled - prior_reg_scaled`, exact rational arithmetic):
    - `reg_change / prior_reg_scaled < -5%` (strict `> 5%` MoM dip) -> **`registration_dip`** (render `FE` as **`FE (reg. issue)`**).
    - `-5% <= reg_change_pct < 0%` (including exact `-5.00%`) -> **`below_threshold`** (`registration_dip_within_5_percent`; render plain **`FE`**).
    - `reg_change >= 0` -> **`no_registration_dip`** (`registrations_not_declining_vs_prior_month`; render plain **`FE`**).
    - Missing/nonpositive `registrations` or incomplete aggregate/matched cohort -> **`undetermined`** (render **`FE (reg. N/A)`**; strictly isolated so it never blocks `fe`, `fe2rr`, or `rr2net`).
- **Bike / Auto / Cab service breakdown when `FE2RR` or `RR2Net` is primary (`service_breakdown`):**
  - Triggers independently **only when `fe2rr` or `rr2net` is in `primary_stages`** (when not primary, returns `triggered: false`, `verdict: "not_triggered"`, `reason: "fe2rr_not_primary"` / `"rr2net_not_primary"`).
  - **Threshold (`> 30%`, no `70%` cumulative cap):** flags every service strictly above **`30%`** (`> 3/10`, exact rational arithmetic, sorted descending by contribution, tie-break `bike -> auto -> cab`). There is no `70%` cumulative cap because an improving service can push two declining services above 100% each.
  - **`FE2RR` formula (additive over shared `fe_sessions`):** `service_fe2rr = service_rr / fe_sessions`, `contribution_s = (current_service_fe2rr - prior_service_fe2rr) / (current_fe2rr - prior_fe2rr)` (invariant to calendar scaling). Non-Bike/Auto/Cab `RR` residual (e.g. `c2c`/`delivery`) is disclosed in `other_services_contribution_pct`.
  - **`RR2Net` formula (weighted MoM rate + mix benchmarked against prior Platform `RR2Net`):** `w_s = service_rr / rr`, `r_s = service_net / service_rr`, `R_prior = prior_net / prior_rr`, `weighted_rate_delta_s = w_curr × (r_curr − r_prior)`, `mix_shift_delta_s = (w_curr − w_prior) × (r_prior − R_prior)`, `weighted_total_delta_s = w_curr × (r_curr − R_prior) − w_prior × (r_prior − R_prior) = weighted_rate_delta_s + mix_shift_delta_s`, and `contribution_s = weighted_total_delta_s / (current_rr2net − prior_rr2net)` (invariant to calendar scaling; drives the `> 30%` material verdict). Per-service `rate_contribution_pct` and `mix_shift_contribution_pct` are exposed alongside block-level `mix_shift_contribution_pct` and `other_services_contribution_pct` (which sum with Bike/Auto/Cab to 100%) when all three services share the parent cohort.
  - **Inactive services & missing isolation:** `0` service `RR` in both months returns `not_applicable` (`no_service_volume`) rather than `undetermined`. One-sided zero `RR` (launch or shutdown) is scored directly in both `FE2RR` and `RR2Net` (in `RR2Net`, launches score `w_curr × (r_curr − R_prior)` and shutdowns score `−w_prior × (r_prior − R_prior)` as pure mix-shift effects with `rate_delta_pp` / `weighted_rate_delta_pp` null). Missing (`null`) service counts are strictly isolated per service and never block sibling services or parent funnel stages.

## Stage and block verdicts

### Table formatting

Render the historical funnel panel with these exact six columns:

`City | Cal.-Adj. MoM FTU Dip | Primary Issues (>20%, cum. >70%) | FE Share of Dip | FE2RR Share of Dip | RR2Net Share of Dip`

Add one concise line directly above the table: `+` means the metric contributed to FTU improvement, cushioning the FTU fall; `−` means the metric pulled FTU down and deepened the fall. Each cell is that stage's **share of the FTU dip** — not the metric's growth rate — so display the reversed sign of the CLI `contribution_pct`: a positive `contribution_pct` (stage declined) renders as `−`, a negative `contribution_pct` (stage improved) renders as `+`, and exact zero renders `0.00%`. **Bold** = selected primary issue from `> 20%` stages until cumulative `> 70%`. Counterfactual percentages omit interaction terms, are not additive, and need not sum to 100%.

When `FE`, `FE2RR`, or `RR2Net` is in `primary_stages`, format each primary stage inline in `Primary Issues` using `registration_check` / `service_breakdown`, and add a concise footnote directly below the table listing each triggered city's exact CLI values (cal.-adj. MoM `Registrations %`, material service shares `> 30%`, `RR2Net` mix-shift or `other_services` share when relevant, or unavailable reasons):

| Follow-up verdict | Inline stage label in `Primary Issues` | Meaning |
| --- | --- | --- |
| `registration_check`: `registration_dip` | **`FE (reg. issue)`** (e.g. **`RR2Net, FE (reg. issue)`**) | Cal.-adj. MoM Registrations fell strictly `> 5%` (`< -5%`) |
| `registration_check`: `below_threshold` / `no_registration_dip` | **`FE`** | Cal.-adj. MoM Registrations dipped `<= 5%`, stayed flat, or grew |
| `registration_check`: `undetermined` | **`FE (reg. N/A)`** | Registrations operand missing/nonpositive or aggregate member incomplete (`reason` disclosed in footnote) |
| `service_breakdown`: `services_identified` / `partial_services_identified` | **`FE2RR (Bike, Cab)`** / **`RR2Net (Cab, Auto)`** | Services strictly `> 30%` (no `70%` cap), descending; note partial coverage in footnote if another service was `undetermined` |
| `service_breakdown`: `no_material_service_contributor` / `partial_no_material_service` | **`FE2RR`** / **`RR2Net`** | No single service exceeded `> 30%` (cite `mix_shift_contribution_pct` / `other_services_contribution_pct` in footnote if material) |
| `service_breakdown`: `undetermined` | **`FE2RR (service N/A)`** / **`RR2Net (service N/A)`** | All three service breakdowns unavailable (`reason` disclosed in footnote) |

Stage verdicts and compact cell formatting:

| Stage verdict / selection | Cell format | Meaning |
| --- | --- | --- |
| `material_contributor` (`selected_as_primary: true`) | **`−60.98%`** (add `*(>100% of dip)*` if `over_explained`) | `contribution_pct > 20%` and selected into `primary_stages` (top descending until cumulative `> 70%`) |
| `material_contributor` (`selected_as_primary: false`) | `−27.44% *(also material)*` | `contribution_pct > 20%`, but earlier stages already exceeded `> 70%` cumulative |
| `below_threshold` | `−5.23%` | Stage deteriorated (`0% < contribution_pct <= 20%`) |
| `no_stage_deterioration` | `+3.02%` (`0.00%` if zero) | Stage stayed flat or improved (`contribution_pct <= 0%`); the stage contributed to FTU improvement, cushioning part of the fall |
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
- Bike/Auto/Cab service breakdown for primary `FE2RR` and `RR2Net` is the final downstream step in this workflow (no surge, pricing, supply, or weather attribution).

## Aggregate scopes

- **Named cities:** each `--city` returns one independent block; never sum separate cities unless an explicit `--group` or `--tier` was requested.
- **`--tier` and `--group`:** roll up counts before computing ratios. `--tier` uses the selected month's stored tier cohort. Member completeness is strict per stage, per `registration_check`, and per service: if one city lacks `Net`, only `rr2net` returns `undetermined`; if one city lacks `registrations` when `fe` is primary, only `registration_check` returns `undetermined`; if one city lacks `cab_net` when `rr2net` is primary, only `cab` returns `undetermined` (`incomplete_group_member`) with `blocking_city_keys`, while `bike`, `auto`, and all funnel stages still evaluate.
- **`--national`:** default when no selector is supplied; rolls up matched cities observed in both months per metric/stage (`platform_ftu`, `fe`, `fe2rr`, `rr2net`, plus `registrations` and per-service `fe2rr_services` / `rr2net_services` when triggered). Always report `matched_city_count` and `excluded_city_count` from `coverage`.

## Wording guardrails

- State that `FE`, `FE2RR`, and `RR2Net` come from the **FTU-eligible funnel** (days on/before first completed platform ride + Zero-Transaction customers, counting sessions/rides including same-day rides rather than distinct FTU users) and are applied as hold-two-constant counterfactuals on scaled Platform FTU — not causal proof.
- Keep Step 3's MoM Registration check (`FE (reg. issue)` — calendar-adjusted MoM `Registrations` dip `> 5%` when `FE` is primary) clearly distinct from Step 1's same-month Registration target shortfall (`investigate` `> 70%`), and stop upstream at Registrations (no Installs or Paid/Organic attribution).
- When `primary_coverage_reached_70_pct` is `false` (all `> 20%` stages summed `<= 70%`), still report `primary_stages` and explicitly note the cumulative share was `<= 70%`.
- When a block returns `no_material_funnel_contributor`, show all three measured stage percentages and note that combined small dips or differences between session/ride counts (including same-day repeat rides) and distinct Platform FTU may be at play, without asserting an unmeasured cause.
- Keep **Target lens** (`variance = actual - target`, Registration contribution `> 70%`) clearly distinct from **Historical funnel lens** (calendar-adjusted MoM FTU dip `> 5%`, `> 20%` stage floor, `> 70%` cumulative primary cutoff).
