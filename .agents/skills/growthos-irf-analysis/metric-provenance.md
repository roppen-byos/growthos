# Metric provenance and warehouse queries

Use the offline `warehouse-queries` command when the user asks where a metric comes from or
wants to see the warehouse SQL behind it. No reader connection or `GROWTHOS_READER_DSN`
is required.

```bash
.venv/bin/python -m growthos warehouse-queries
.venv/bin/python -m growthos warehouse-queries --metric installs
.venv/bin/python -m growthos warehouse-queries --id ftu-monthly
```

The response contains `resource`, `query_count` and `queries`. Each query has `id`,
`metrics`, `checksum` and `sql`.

## Registry reference

`growthos/metric_registry.json` has one top-level `warehouse_queries` block:

- `resource` is the packaged SQL file `sql/warehouse_queries.sql`.
- `queries` maps each query ID to the volume metrics it produces.
- Ratio metrics are derived from their volume operands and have no warehouse query.

The registry validator fails closed if the block, the volume coverage or the SQL file
headers disagree. `status`, `compare` and `verify` expose only this small reference
block; full SQL comes only from `warehouse-queries`.

## Source of truth

The authoritative queries are the `Operation` constants in
`gwos-data/gwosdata/warehouse_client.py`. The analyst package keeps a verbatim copy
for offline use, and an operator-side test compares SQL text and checksums. Do not
edit the packaged file independently; change the operator query first, then refresh
the copy.

`gwos-data/quries/raw.sql` is an older shortened sample and is not authoritative.

## Boundaries

- Returned SQL is source-query text, not a metric definition. Keep business meaning,
  units, caveats and missing-data rules from the registry definitions.
- Do not run warehouse SQL, connect to the warehouse, or reconstruct metrics by hand.
- Do not treat table or column names as evidence of source-data completeness.
