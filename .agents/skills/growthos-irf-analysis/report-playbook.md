# IRF report playbook

## Common setup and completion

Follow [SKILL.md](SKILL.md) for evidence collection, period selection,
interpretation and table formatting; repository-root `AGENTS.md` governs
report approval. Use the relevant branch with returned evidence.
Finish with the applicable coverage limitations and the branch's completion check.

## Master performance report

Use this structure for a master/full/default IRF performance report.
For a narrow question, use only the relevant sections.

### A. Funnel master — overall and tiers

- Run `compare` and `compare --group-by tier` for the same explicit pair.
- Show Installs, Registrations and Platform FTU with previous/latest counts,
  absolute change and percentage growth for T1–T3 and overall.
- In a separate compact panel show I2R and R2N, previous/latest and delta in pp.
  Use complete totals where available; label known subtotals and coverage explicitly.
- Follow the ratio-investigation branch for ratio changes described in the report.

### B. Service performance matrix

- Use the tier comparison's service metrics for a tier × Bike/Link PFTU, Auto PFTU,
  Cab PFTU grid: latest count, absolute change and percentage growth.
- Add service shares and pp movement in a second panel. Service shares need not
  total 100%. Describe a service-mix transition only when the observed shares support it.

### C. City deep dives

- For every represented tier, run `rank --tier Tn --metric platform_ftu --top 5`
  and the same with `--ascending`. Keep signed changes visible; a least-positive
  city is not a decliner. Ties follow the tool's declared ordering.
- Get `funnel --city CITY ...` for the cities discussed. Show Installs/Registrations/Platform FTU
  absolute changes, followed by I2R/R2N pp changes in dense rows.
- For Bike/Link, Auto and Cab, use `share-drivers --service SERVICE --top 5`;
  add `--tier` when explaining a particular tier. Identify gains/declines separately,
  and state neutral movement as flat, not statistically significant stagnation.
- Complete when every reported service trend has city evidence or an explicit
  coverage limitation. City volume contributions explain arithmetic, not causality
  or a formal additive decomposition of overall share change.

## Ratio investigation — automatic follow-through

When highlighting an I2R/R2N movement, perform this check before claiming conversion
improvement or deterioration. Report measured pp movement without assigning severity;
no significance threshold is defined.

1. Inspect the comparison's unavailable counts, known subtotals and `mapping_impact`.
   A ratio of independently partial numerator/denominator subtotals can be misleading;
   label it partial and stop short of a full-population conversion claim.
2. For I2R, inspect Registrations (numerator) and Installs (denominator); for R2N,
   inspect Platform FTU and Registrations. Read both absolute and relative movements
   from `compare`, not just the ratio.
3. Run `compare --group-by tier` to localize the movement, including unmapped cities in T3.
4. In the affected tiers, rank both numerator and denominator by delta in both
   directions (`rank --metric METRIC --tier Tn --top 5 [--ascending]`). Then run
   `funnel --city CITY ...` for the union of relevant cities. Reuse master report
   evidence when it covers these cities.
5. Explain the observed numerator/denominator changes and city mix. A falling
   denominator may raise the ratio even without numerator growth. Separate missing
   source coverage or unmapped-city volume changes from observed count changes. Do not
   average city ratios, sum city pp changes, or infer operational causes from IRF.

Completion: the explanation names both operands, their movement, the tier/city
evidence and relevant coverage caveats. This is diagnostic guidance, not a
statistical or causal decomposition metric.

## Quarterly / three-month deep dive

Present a three-month **monthly trend report**, not a deduplicated quarterly total.
Honor an explicit quarter or three-month window. Otherwise, use the latest
three consecutive stored months and call it a rolling three-month view, not a
calendar quarter. If fewer than three are available, state the missing months and
offer the available two-month comparison; fetching more data is an operator action.

1. Check all three requested months in `status`. Never substitute another period.
   Calendar quarters are Jan–Mar, Apr–Jun, Jul–Sep and Oct–Dec.
2. Run overall and tier `compare` for M1→M2 and M2→M3. Use M1→M3 for endpoint
   growth when useful, explicitly labeled a period comparison.
3. Present M1/M2/M3 monthly counts and ratios side by side, with the two adjacent
   deltas/growth rates and ratio pp movements. Reuse master sections A/B/C for the
   latest MoM and explain whether the second movement strengthens or
   reverses the first. Describe momentum without inventing a numerical score.
4. Investigate numerator/denominator shifts using the branch above. Rank city and
   service drivers for both adjacent pairs when discussing changes in momentum.

Do not sum monthly unique users/IDs into quarterly distinct totals or average
monthly ratios. Quarterly unique counts require separately approved, warehouse-
computed counting grains; monthly observations cannot establish them.

Completion: three actual stored monthly observations, adjacent changes and a
latest-month comparison are shown, or the exact coverage blocker is disclosed.
