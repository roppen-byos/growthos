# GrowthOS IRF Analyst

IRF(Installs, Registrations, FTU) analyst CLI and Codex workspace.

All reporting commands read current monthly data from Neon using a read-only
connection. There is no offline data fallback.

## Setup

Open this folder in Codex and say **"Set me up"** for guided installation and
connection checks. Codex runs the setup commands; you enter credentials locally.

### Manual installation

From `gwos-analyst/`:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
```

Use Python 3.11 or newer. Installation reads `pyproject.toml` and installs the
required dependencies into `.venv`.

### Local connection configuration

Obtain a dedicated read-only reporting connection from your administrator through
an approved private channel. During guided setup, Codex copies the blank template
to `.env` only when that file is absent.
For manual setup, run this from `gwos-analyst/`:

```bash
cp .env.example .env
```

Open the project directory (`gwos-analyst`) in your own local editor and locate
`.env` (enable hidden files if needed). Paste your read-only database connection
URL after `GROWTHOS_READER_DSN=`. Save the file locally, then tell Codex you are
ready for the connection check without sharing the link.
never paste its contents into chat or commit it.
Codex may create the blank file as above, but must not read or edit its contents.

An already configured `GROWTHOS_READER_DSN` environment variable takes precedence
over `.env`. The CLI loads the setting itself; no shell sourcing is needed.

### Check installation and connection

```bash
.venv/bin/python -m pip check
.venv/bin/python -m growthos --help
.venv/bin/python -m growthos status
```

For guided recovery, say **"Setup failed"** and share only the sanitized CLI
error, without credentials. A successful `status` checks the reporting connection;
it does not certify source completeness or freshness.

## Platform FTU targets

Ask Codex about city targets, target misses or tier-specific shortlists.
See the [target analysis guide](.agents/skills/growthos-irf-analysis/target-analysis.md) for rules and command examples.

## Tests

```bash
.venv/bin/python -m unittest discover -s tests -v
```

All analyst tests run offline using test-only data and scripted readers.

Use each command's `--help` for exact flags, defaults and allowed values.
