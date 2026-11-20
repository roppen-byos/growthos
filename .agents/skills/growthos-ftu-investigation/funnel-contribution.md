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
- **Scale-invariant stage counterfactuals:**
  - `ftu_change = current_ftu_scaled - prior_ftu_scaled` (negative indicates an MoM dip).
  - `stage_effect = prior_ftu_scaled * (current_stage / prior_stage - 1)` for each of `fe`, `fe2rr`, `rr2net`.
  - `contribution = stage_effect / ftu_change` (computed when `ftu_change < 0`; positive when the stage deteriorated during an FTU dip).
- **Parallel independent evaluation:** all requested stages evaluate independently against the full `ftu_change`. Never stop early and never sum `FE + FE2RR + RR2Net` percentages.
- **Strict `> 20%` materiality rule:** strictly above `20%` flags `material_contributor`; exact `20.00%` and below return `below_threshold`. Exact rational arithmetic governs verdicts; display rounding never changes a verdict.

## Stage and block verdicts

### Table formatting

Render the historical funnel panel with these exact six columns:

`City | Cal.-Adj. MoM FTU Dip | Material Drivers (>20%) | FE Share of Dip | FE2RR Share of Dip | RR2Net Share of Dip`

Add one concise sign note beside the table: `+` means the stage worsened and contributed to the FTU dip (bold when `> 20%` material); `−` means the stage stayed flat or improved (helped offset the dip). Primary stage shares are non-additive and must not be summed.

Stage verdicts and compact cell formatting:

| Stage verdict | Cell format | Meaning |
| --- | --- | --- |
| `material_contributor` | **`+87.81%`** (add `*(>100% offset)*` if `over_explained`) | `contribution_pct` strictly `> 20%`; listed in `Material Drivers (>20%)` |
| `below_threshold` | `+5.23%` | Stage deteriorated (`0% < contribution_pct <= 20%`) |
| `no_stage_deterioration` | `−3.02% *(improved)*` (`0.00% *(flat)*` if zero) | Stage stayed flat or improved (`contribution_pct <= 0%`); negative percentages are counter-effects, not causes |
| `not_applicable` | `—` | Scaled Platform FTU did not decline (`ftu_change >= 0`); factual rates stay visible in prose/details if relevant |
| `undetermined` | `N/A *(reason)*` | Missing or nonpositive required operand (`missing_*`, `nonpositive_*`, `incomplete_group_member`, `no_prior_month`, etc.) |

Block verdicts and `Material Drivers (>20%)` summary values:

| Block verdict | `Material Drivers (>20%)` cell | Meaning |
| --- | --- | --- |
| `contributors_identified` | **`FE`** / **`FE, RR2Net`** (bold comma-separated material stages) | `ftu_change < 0`, all requested stages evaluated, at least one `> 20%` |
| `partial_contributors_identified` | **`FE`** *(partial)* | At least one requested stage `> 20%`, while another stage was `undetermined` |
| `no_material_funnel_contributor` | `None` | `ftu_change < 0`, all requested stages evaluated, none `> 20%` |
| `partial_no_material_contributor` | `None *(partial)*` | `ftu_change < 0`, evaluated stages `<= 20%`, at least one stage `undetermined` |
| `no_historical_ftu_dip` | `No MoM dip` | Scaled `ftu_change >= 0`; no MoM dip to decompose |
| `undetermined` | `Undetermined *(reason)*` | Month, prior month, city, FTU, or all requested stages unavailable |

## Scope boundaries (independent lenses)

- Same-month Registration target checks (`investigate`, strictly `< -5%` vs Registration target) run **independently** of `FE > 20%` on the widening-miss (`persistent` / `new_miss`) cities in the default Layer 2 investigation flow (no FE gate). Stop upstream at **Registrations** (no Installs or Paid/Organic attribution).
- Platform `RR2Net` is the final downstream stage in this workflow; Bike/Auto/Cab `RR2Net` drilldowns are intentionally deferred.

## Aggregate scopes

- **Named cities:** each `--city` returns one independent block; never sum separate cities unless an explicit `--group` or `--tier` was requested.
- **`--tier` and `--group`:** roll up counts before computing ratios. `--tier` uses the selected month's stored tier cohort. Member completeness is strict per stage: if one city lacks `Net`, only `rr2net` returns `undetermined` (`incomplete_group_member`) with `blocking_city_keys`, while `fe` and `fe2rr` still evaluate.
- **`--national`:** default when no selector is supplied; rolls up matched cities observed in both months per metric/stage (`fe2rr` matches cities with both `FE sessions` and `RR` in both months; `rr2net` matches cities with both `RR` and `Net` in both months). Always report `matched_city_count` and `excluded_city_count` from `coverage`.

## Wording guardrails

- State that `FE`, `FE2RR`, and `RR2Net` contributions are **city-wide (all-customer) funnel proxies** applied to scaled Platform FTU — not FTU-eligible-only losses and not causal proof.
- When a block returns `no_material_funnel_contributor`, show all three measured stage percentages and note that either combined small dips or FTU-specific behavior diverging from city-wide funnel rates may be at play, without asserting an unmeasured cause.
- Label the stage columns `FE Share of Dip`, `FE2RR Share of Dip`, and `RR2Net Share of Dip` alongside `Material Drivers (>20%)` (never bare `FE %`), keeping cells concise (`+87.81%` bolded when material, `+5.23%` when below threshold, `−3.02% *(improved)*` when counter-effect).
- Keep **Target lens** (`variance = actual - target`, Registration target miss `< -5%`) clearly distinct from **Historical funnel lens** (calendar-adjusted MoM FTU change, `> 20%`).
