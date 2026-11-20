# Metric Provenance and Warehouse Queries

Use the offline `warehouse-queries` command when the user asks where a core IRF metric comes from or wants to inspect the warehouse SQL behind it. No reader connection or `GROWTHOS_READER_DSN` is required.

```bash
.venv/bin/python -m growthos warehouse-queries
.venv/bin/python -m growthos warehouse-queries --metric installs
.venv/bin/python -m growthos warehouse-queries --id ftu-monthly
```

The response contains `resource`, `query_count`, and `queries`. Each query includes `id`, `metrics`, `checksum`, and `sql`.

## Coverage and registry reference

`growthos/metric_registry.json` carries one top-level `warehouse_queries` block pointing to `sql/warehouse_queries.sql` for all **four monthly volume queries**:

- `installs-monthly` -> `installs`
- `registrations-monthly` -> `registrations`
- `ftu-monthly` -> `platform_ftu`, `platform_link_ftu`, `platform_auto_ftu`, `platform_cab_ftu`
- `funnel-monthly` -> `fe_sessions`, `rr`, `net`, `bike_rr`, `bike_net`, `auto_rr`, `auto_net`, `cab_rr`, `cab_net`

Ratio metrics (`i2r`, `r2n`, service shares, `fe2rr`, `rr2net`, and service `*_rr2net`) are derived from their volume operands and have no standalone warehouse query.

The registry validator fails closed if the reference block, volume coverage, or SQL headers disagree. `status`, `compare`, and `verify` expose only the compact reference block; full SQL comes only from `warehouse-queries`.

## Source of truth

The authoritative extraction queries live in `gwos-data/gwosdata/warehouse_client.py`. The analyst package holds a verbatim copy for offline inspection, guarded by `gwos-data/tests/test_query_parity.py`. Edit the operator query first, then update the packaged copy together.

`gwos-data/quries/raw.sql` is an older shortened sample and is not authoritative.

## Boundaries

- Returned SQL is source-query text, not a standalone business definition; pair it with the registry's business meaning, units, caveats, and missing-data rules.
- Never run warehouse SQL from the analyst repo, connect to the warehouse, or hand-reconstruct metrics.
- Never treat table or column names as proof of source-data completeness.
