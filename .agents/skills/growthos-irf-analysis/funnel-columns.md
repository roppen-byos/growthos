# Custom funnel columns

Repeat `funnel --column NAME` to select flat output columns; otherwise, the command
returns the full nested result. Use `funnel --help` for allowed names.

- `city`, `city_key`, `tier`; an unsuffixed metric (or `METRIC_latest` alias) is its latest value.
- Volumes: `METRIC_previous`, `METRIC_mom_abs` (alias `METRIC_delta`), `METRIC_mom_pct` (alias `METRIC_growth`).
- Ratios/shares: `METRIC_previous`, `METRIC_delta_pp` (alias `METRIC_delta`); `METRIC_mom_abs`
  is a **fractional** difference, and `METRIC_mom_pct` is relative ratio growth.
- Aliases normalize to canonical column names in `columns`, `rows`, and `column_units`.
- Fractions are not preformatted percentages. `column_units` identifies units;
  missing/zero-denominator results stay `null`. Sorting uses full metrics before
  projecting columns; selecting columns does not change city selection.

Example from the repository root:

```bash
.venv/bin/python -m growthos funnel --top 10 --column city --column platform_ftu --column platform_ftu_mom_abs --column i2r --column i2r_delta_pp
```
