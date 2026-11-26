# GrowthOS IRF Analyst

IRF (Installs, Registrations, FTU) analyst CLI and Codex workspace.

All reporting commands read current monthly data (completed months plus the current month to date) from Neon using a read-only connection. There is no offline data fallback.

## Setup

Open this folder in Codex and say **"Set me up"** for guided installation and connection checks. Codex runs the setup commands; you enter credentials locally.

### Manual installation

From `gwos-analyst/`:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
```

Use Python 3.11 or newer. Installation reads `pyproject.toml` and installs the required dependencies into `.venv`.

### Local connection configuration

Obtain a dedicated read-only reporting connection from your administrator through an approved private channel. During guided setup, Codex copies the blank template to `.env` only when that file is absent. For manual setup, run this from `gwos-analyst/`:

```bash
cp .env.example .env
```

Open the project directory (`gwos-analyst`) in your own local editor and locate `.env` (enable hidden files if needed). Paste your read-only database connection URL after `GROWTHOS_READER_DSN=`. Save the file locally, then tell Codex you are ready for the connection check without sharing the link. Never paste its contents into chat or commit it.

An already configured `GROWTHOS_READER_DSN` environment variable takes precedence over `.env`. The CLI loads the setting itself; no shell sourcing is needed.

### Check installation and connection

```bash
.venv/bin/python -m pip check
.venv/bin/python -m growthos --help
.venv/bin/python -m growthos status
```

For guided recovery, say **"Setup failed"** and share only the sanitized CLI error, without credentials. A successful `status` checks the reporting connection; it does not certify source completeness or freshness.

## Capabilities and CLI overview

All analytical answers in Codex come from reviewed `growthos` subcommands (run `.venv/bin/python -m growthos <command> --help` for exact flags, defaults, and allowed values):

- **Core IRF & funnel reporting** (`status`, `compare`, `rank`, `funnel`, `share-drivers`, `verify`): monthly trends (including month-to-date estimates), overall/tier/city comparisons, master reports, ratio investigations (`I2R`, `R2N`, `FE2RR`, `RR2Net`, and service shares/fulfillment), service-share drivers, and checksum verification. See [the IRF analysis skill](.agents/skills/growthos-irf-analysis/SKILL.md).
- **Assessment Layer — Platform FTU targets (observe/shortlist)** (`target-facts`, `target-analysis`): city target/actual lookups (ended months, or month-to-date estimates against whole-month targets), attainment filtering/sorting, coverage-based (`>5%`) shortlists, pooled New launch comparisons, and prior-month widening-miss (`persistent` / `new_miss`) tables. See [the target analysis guide](.agents/skills/growthos-irf-analysis/target-analysis.md).
- **Investigation Layer — FTU dip & target-miss investigation** (`investigate`, `investigate-funnel`): starts from the Assessment Layer's shortlisted FTU target-miss cities (`top` or `all` as requested) — or any explicitly requested city, tier, group, national scope, or history — and runs the sequential decision tree: Registration Target Shortfall Check (`registration_issue` stops); Month-on-Month Dip and Persistence Check (`persistent` stops citing the prior month; non-dipping/undetermined stops and discloses); Funnel Decomposition for `new_miss` cities across `FE sessions`, `FE2RR`, and `RR2Net` (material stages ranked until cumulative contribution passes the configured share, with automatic calendar-adjusted MoM Registration check when `FE` is primary and Bike/Auto/Cab service breakdowns when `FE2RR` or `RR2Net` is primary). See [the FTU investigation skill](.agents/skills/growthos-ftu-investigation/SKILL.md).

## Metric source queries

Inspect the packaged warehouse SQL behind all four monthly volume queries (`installs-monthly`, `registrations-monthly`, `ftu-monthly`, `funnel-monthly`) without a database connection:

```bash
.venv/bin/python -m growthos warehouse-queries
.venv/bin/python -m growthos warehouse-queries --metric fe_sessions
.venv/bin/python -m growthos warehouse-queries --id funnel-monthly
```

The metric registry references these queries under `warehouse_queries`; full SQL is returned only by this command (derived ratios such as `i2r`, `r2n`, `fe2rr`, and `rr2net` have no standalone warehouse query). The packaged copy is guarded against drift by the operator suite in `gwos-data`.

## Tests

```bash
.venv/bin/python -m unittest discover -s tests -v
```

All analyst tests run offline using test-only data and scripted readers.
