# IRF Answer Format

The active skill (`growthos-irf-analysis` or `growthos-ftu-investigation`) governs tool use and interpretation; [report-playbook.md](report-playbook.md) governs required report investigations. Formatting never replaces those checks.

## Answer first, then evidence

1. **Bottom line:** lead with a 1–2 sentence direct answer naming the selected period and scope. Place any limitation that invalidates or materially qualifies the headline right beside it, not only at the end.
2. **Evidence:** present compact native Markdown tables when helpful. Lead with volumes and signed absolute change (`absolute_delta`, or clearly labeled **known subtotal** change for partial coverage), then growth, ratios, and shares.
3. **Explanation:** synthesize 2–3 high-signal findings supported by tier/city evidence (or more for full master reports). Connect *what moved* to *where it concentrated* (tier/city share of change, numerator vs denominator drivers, persistent vs new misses) rather than narrating rows already visible in the table.
4. **Limitations & data note:** consolidate relevant unavailable observations, coverage/mapping warnings, and month metadata/source-read times once at the end. Identify which months and sources each timestamp covers; never invent a single shared cutoff for distinct reads, and never equate read times with source completeness.
5. **Bounded next step (optional):** when natural in-scope follow-ups exist (remaining paged rows, opt-in New launch fallback breakdown, direct Registration check for a `no_historical_ftu_dip` city), close with one specific offer rather than auto-running unrequested calls or making operational recommendations.

Size the response to the question: a single-city lookup needs a crisp bottom line, compact table or bullet facts, and a brief data note — not a full report template.

## Match the question

| Request | Presentation |
| --- | --- |
| Two-month comparison | Bottom line, volume table, separate ratio/share table, synthesis, and data note |
| City ranking | Ranked table; state metric, delta/growth basis, direction, tier/filter, and top-N scope |
| Target lookup / attainment / shortlist | Overall (or tier) target/actual/variance (plus gap to cover `X`), requested city evidence, separate pooled New launch fallback line, and first-page-then-ask continuation per [target-analysis.md](target-analysis.md) |
| Widening target misses | Main target table first; then a separate "Widening target misses vs `<previous_month>` (`< -5pp`)" table showing status (`persistent` / `new_miss`), current/prior variance %, pp change, and failing-since month |
| Ratio investigation | Ratio movement, both numerator and denominator movements, tier/city drivers, and coverage caveats |
| Master report | Overall funnel, tier/service matrix, city drivers, limitations; follow [report-playbook.md](report-playbook.md) |
| Three-month view | Side-by-side monthly observations and adjacent changes; label endpoint comparisons as period comparisons |
| Registration target investigation (`investigate`) | Same-month target table (`FTU Target \| FTU Actual \| FTU Gap \| Reg Target \| Reg Actual \| Reg Variance % \| Reg Contribution % \| Verdict`), stating the strict `> 70%` Registration contribution rule once; follow [registration-contribution.md](../growthos-ftu-investigation/registration-contribution.md) |
| FTU target-miss / MoM funnel investigation (`investigate` + `investigate-funnel`, Investigation Layer) | Starts from the Assessment Layer's shortlisted cities (`top` or `all` as requested, or explicit scopes) and runs the sequential decision tree: Registration Target Shortfall Check (`City \| FTU Target \| FTU Actual \| FTU Variance % \| Reg Variance % \| Reg Contribution % \| Outcome`, listing only `registration_issue` cities with Outcome `Registration issue` — continuing cities are omitted), Month-on-Month Dip and Persistence Check (`persistent` → cites `<prior_month>`; non-dipping / undetermined → disclose; omits `new_miss` cities, which appear only in Funnel Decomposition), Funnel Decomposition for `new_miss` cities (`City \| Cal.-Adj. MoM FTU Dip \| Primary Issues \| FE Share of Dip \| FE2RR Share of Dip \| RR2Net Share of Dip`, bold primary issues, inline `FE (reg. issue)` / `FE (reg. N/A)` when `FE` is primary and `FE2RR (Bike, Cab)` / `RR2Net (Cab, Auto)` / `<Stage> (service N/A)` for `> 30%` Bike/Auto/Cab service drivers when `FE2RR`/`RR2Net` is primary, with a concise footnote citing triggered MoM `Registrations %`, service shares, and `RR2Net` mix shift when relevant; a line directly above the table (`+` means the metric contributed to FTU improvement, cushioning the fall; `−` means the metric pulled FTU down and deepened the fall; percentages are shares of the dip, not metric growth rates), `*(also material)*` for extra material stages, `0.00%` on exact zero), plus one consolidated proxy/scaling/coverage note; follow [growthos-ftu-investigation/SKILL.md](../growthos-ftu-investigation/SKILL.md) |

Question size controls presentation length, never required evidence collection.

## Tables

- Render native Markdown pipe tables directly in the answer, outside code fences; use prose or bullets for non-tabular findings.
- Keep tables compact (5–6 columns). Split wider evidence into labeled panels sharing the same city/tier keys and periods so rows align across panels.
- Standard column headers (replace placeholders with actual month/year labels):
  - Volumes: `Metric | <previous> | <latest> | Change | Growth`
  - Ratios / shares: `Metric | <previous> | <latest> | Change (pp)`
  - Targets: `City | Target | Actual | Variance | Variance %` (add `Cum. Shortfall Covered` for selection shortlists)
- Left-align text labels and right-align numeric columns.
- Use business names from the metric catalogue (e.g. `Platform FTU`, `Bike/Link PFTU`, `FE sessions`) and readable verdict labels rather than raw JSON keys: lowercase for funnel-stage verdicts (`material contributor`, `below threshold`, `same or better`, `no historical dip`, `dip within 5%`, `undetermined`), sentence case for target verdicts (`Registration issue`). Keep internal governance IDs (`DEC-*`, `ADR-*`) and physical table/column names (`irf.facts`, `fe_sessions`) out of business answers unless technical provenance is requested.
- State ranking scope accurately: e.g. "largest absolute Platform FTU declines," not "worst cities." A least-positive gain is not a decline. Preserve exact CLI ordering, ties, signed movements, and omitted-row disclosures.

## Numbers and unavailable values

- Format integer counts exactly with thousands separators (`1,234,567`); avoid K/M abbreviations unless requested, and label any requested unit explicitly.
- Display fractional ratios, shares, and growth as percentages; display ratio/share movement as percentage points (`pp`), not relative growth. Respect CLI units: `delta_pp`, `variance_pct`, `rate_delta_pp`, `relative_change_pct`, and `contribution_pct` are already scaled in percentage/pp units — convert raw fractions (`0..1`) only once.
- Default to two decimal places for percentages and `pp`, consistent within a column. Add decimals when needed so a nonzero movement never rounds to `0.00%` / `0.00pp`. Display rounding never alters CLI thresholds or ordering.
- Always show explicit signs: prefix positive deltas/variances with `+` and retain `-` for negatives (`variance = actual - target`, so misses are `-` and beats are `+`; widening misses are `-pp`). Show observed zero as `0`, never as `N/A`.
- Render unavailable values as `N/A` with their returned reason beside the table or in the data note. Label `known_subtotal*` values explicitly as **known subtotals** with unavailable city counts. Never cap period ratios at 100% or clip `over_explained` (`> 100%`) contributions (mark them as partial offsets).

## Commentary and evidence

- Distinguish **Observed** facts, arithmetic **Interpretation**, unverified **Hypotheses**, and **Limitations** whenever the boundary matters (without forcing four rigid headings onto every short answer).
- Never equate a rising ratio with volume growth or conversion improvement until both numerator and denominator have been inspected.
- Describe measured direction and magnitude instead of unsupported qualitative labels ("critical," "healthy," "statistically significant"). Formatting introduces no severity thresholds or causal claims.
- Keep CLI JSON internal; reproduce raw JSON only when explicitly requested. Share requested checksums/provenance honestly without implying historical snapshot reproducibility.
