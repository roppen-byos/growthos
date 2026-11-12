# IRF answer format

Use this reference when composing analytical answers. The skill owns tool use and
interpretation; [report-playbook.md](report-playbook.md) owns required investigations
and report-specific evidence. Formatting never replaces those checks.

## Answer first, then evidence

1. **Bottom line:** answer the question in one or two sentences. State the selected
   period and scope. Put any limitation that invalidates or materially qualifies
   the headline beside it, not only at the end.
2. **Evidence:** show a compact comparison or ranking table when it helps. Lead with
   volumes and absolute movement (`absolute_delta`, or clearly labeled
   `known_subtotal_delta` for partial coverage), then growth, ratios and shares.
3. **Explanation:** highlight up to three findings supported by the required
   tier/city evidence. Explain relationships rather than repeating every cell.
   Longer requested reports may need additional findings.
4. **Limitations and data note:** consolidate relevant unavailable observations,
   coverage/mapping warnings and month metadata/source-read times once per answer.
   Identify which months/sources each timestamp describes; do not collapse distinct
   reads into an invented common cutoff. Read times do not certify completeness.

Use only the sections the question needs. A simple factual question can be a direct
answer with a short data note; it does not require an empty table or full report.

## Match the question

| Request | Presentation |
| --- | --- |
| Two-month comparison | Bottom line, volume table, separate ratio/share table, explanation and limitations |
| City ranking | Ranked table; state metric, delta/growth basis, direction, tier/filter and top-N scope |
| Ratio investigation | Ratio movement, both operands, then tier/city explanation and coverage caveats |
| Master report | Overall funnel, tiers/services, city drivers, limitations; follow the playbook |
| Three-month view | Monthly observations and adjacent changes; clearly label endpoint period comparisons |

Keep a narrow question narrow. This controls presentation length, not the evidence
collection required by the playbook.

## Tables

- Use native Markdown pipe tables directly in the answer, outside code fences;
  use prose or bullets for non-tabular findings.
- Prefer five or six columns. Split wider tables into labeled panels, retaining
  the same city/tier keys and periods so panels can be matched.
- For volumes, use `Metric | <previous month> | <latest month> | Change | Growth`.
  For ratios/shares, use `Metric | <previous month> | <latest month> | Change (pp)`.
  Replace month placeholders with actual month/year labels.
- Left-align labels and right-align numeric columns. Use business names from the
  metric catalogue, such as `Platform FTU`, rather than internal JSON keys.
- State ranking scope precisely: e.g. “largest absolute Platform FTU declines,”
  not “worst cities.” A least-positive result is not a decline. Preserve the
  tool's ordering, ties, signed movements and any omitted-result disclosure.

## Numbers and unavailable values

- Show integer counts exactly, with thousands separators; avoid K/M abbreviations
  unless the user requests compact units. Label any requested scale explicitly.
- Display fractional ratios, shares and growth as percentages. Display ratio/share
  movement as percentage points (`pp`), not relative growth. Respect CLI column
  units: `delta_pp` is already in percentage points; convert fractions only once.
- Default to two decimal places for percentages and `pp`, consistently within a
  column. Increase precision when needed to keep a nonzero movement visible;
  never imply flat movement through rounding. Display rounding does not change
  tool calculations or ranking order.
- Prefix positive changes with `+` and retain negative signs. Show actual zero as
  zero, not as a missing-value marker.
- Render unavailable values as `N/A`, with their supplied reason beside the table
  or in a note. Label `known_subtotal*` values as **known subtotals**, with applicable
  unavailable counts; they are not full totals. Use the metric catalogue's caveats
  rather than assuming every percentage is capped at 100%.

## Commentary and evidence

- Separate **Observed** facts, arithmetic **Interpretation**, unverified
  **Hypotheses**, and **Limitations** when the distinction matters; do not force
  four headings onto every answer.
- A higher ratio is not automatically volume growth or conversion improvement.
  Explain both operands using the playbook's investigation before making a claim.
- Use measured direction and magnitude rather than unsupported labels such as
  “critical,” “healthy” or “statistically significant.” No severity thresholds or
  causal conclusions are introduced by this format.
- Keep structured CLI evidence as JSON internally. Present readable findings and
  tables; reproduce raw JSON only when explicitly requested. Include requested
  checksums/provenance details without inventing historical reproducibility.
