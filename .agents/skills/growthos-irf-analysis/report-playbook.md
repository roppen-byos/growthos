# IRF Report Playbook

## Common setup and completion

Follow [SKILL.md](SKILL.md) for evidence collection, period selection, interpretation, and error recovery, and [output-format.md](output-format.md) for presentation; repository-root [AGENTS.md](../../../AGENTS.md) governs scope, credentials, and CLI command approval. Pass the same explicit month pair across all commands in a report and verify `result_checksum` and month metadata match across calls. Finish with applicable coverage limitations and the branch's completion check.

## Master performance report

Use this structure for a full/default IRF master report. For narrower questions, run only the relevant sections.

### A. Funnel master — overall and tiers

- Run `compare` and `compare --group-by tier` for the same explicit `--previous`/`--latest` pair.
- Show Installs, Registrations, and Platform FTU (previous, latest, absolute change, % growth) for `T1`–`T3` and overall.
- Show I2R and R2N (previous, latest, `pp` change) in a separate compact panel. Use complete totals where available; label known subtotals and unavailable counts explicitly.
- Run the **Ratio investigation** branch below for every I2R/R2N movement highlighted in the narrative.

### B. Service performance matrix

- Use the tier comparison's service metrics for a `Tier × Bike/Link PFTU, Auto PFTU, Cab PFTU` volume panel (latest count, absolute change, % growth).
- Add service shares and `pp` movements in a second panel. Service shares need not sum to 100%; describe a service-mix shift only when observed shares support it.

### C. City deep dives

- For each represented tier, run `rank --tier Tn --metric platform_ftu --top 5` and again with `--ascending`. Preserve signed deltas; a least-positive gain is not a decliner, and ties follow the tool's declared order.
- Run `funnel --city CITY ...` for the cities discussed (use `--column` per [funnel-columns.md](funnel-columns.md) when a compact projection helps). Show Installs, Registrations, and Platform FTU absolute changes alongside I2R and R2N `pp` changes.
- For Bike/Link, Auto, and Cab, run `share-drivers --service SERVICE --top 5` (add `--tier` when explaining a specific tier). Separate gains from declines, and describe flat movement as flat rather than statistically significant stagnation.
- **Completion check:** every reported service trend is backed by city evidence or an explicit coverage limitation. City volume movements explain arithmetic concentration, not causation or a formal additive share decomposition.

## Ratio investigation — automatic follow-through

Run this check before interpreting any highlighted I2R or R2N movement as conversion improvement or deterioration. Report measured `pp` movement without assigning severity or significance thresholds.

1. Inspect unavailable counts, known subtotals, and `mapping_impact` from `compare`. A ratio built from independently partial numerator/denominator subtotals can mislead — label it partial and stop short of a full-population conversion claim.
2. Read both operands' absolute and relative movements from `compare`: Registrations and Installs for I2R; Platform FTU and Registrations for R2N.
3. Run `compare --group-by tier` to localize the shift, including named unmapped cities in `T3` (the unattributed `city_not_found` bucket is national-only and excluded from tiers).
4. In affected tiers, rank both numerator and denominator in both directions (`rank --metric METRIC --tier Tn --top 5 [--ascending]`), then run `funnel --city CITY ...` on the union of driver cities (reusing master-report JSON when it already covers them).
5. Synthesize operand and city-mix movement: a falling denominator lifts a ratio even without numerator growth. Separate coverage gaps or unmapped-city volume changes from observed count changes. Never average city ratios, sum city `pp` deltas, or infer operational causes from IRF counts.

**Completion check:** the explanation names both operands, their signed movements, the localizing tier/city evidence, and relevant coverage caveats.

## Quarterly / three-month deep dive

Present a three-month **monthly trend report**, never a deduplicated quarterly total. Honor an explicit calendar quarter (`Jan–Mar`, `Apr–Jun`, `Jul–Sep`, `Oct–Dec`) or three-month window; otherwise use the latest three consecutive stored months and label it a **rolling three-month view**, not a calendar quarter. If fewer than three consecutive months are stored, name the missing month(s) and offer the available two-month comparison (fetching more months is an operator action).

1. Verify all three months in `status` without substituting periods.
2. Run overall and tier `compare` for `M1→M2` and `M2→M3`. Run `M1→M3` when an endpoint comparison adds clarity, explicitly labeled a **period comparison** (not MoM).
3. Present `M1`, `M2`, and `M3` counts and ratios side by side with both adjacent deltas/growth rates and `pp` changes. Apply master sections A/B/C to the latest MoM pair and note whether `M2→M3` strengthens or reverses `M1→M2` without inventing a numeric momentum score.
4. Run the **Ratio investigation** branch for highlighted ratio shifts, and rank city/service drivers across both adjacent pairs when explaining momentum changes.

Never sum monthly unique counts into quarterly distinct totals or average monthly ratios; quarterly distinct counts require separately approved warehouse grains.

**Completion check:** three actual stored monthly observations, both adjacent comparisons, and latest-month drivers are shown — or the exact missing-month blocker is disclosed.
