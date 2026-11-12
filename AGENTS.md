# GrowthOS IRF Analyst Guide

Codex runs here and answers IRF questions by running the reviewed `growthos` CLI tools against current reporting data in read-only Neon db.
Analytical reports are delivered in chat.

Approved scope: IRF only — Installs, Registrations, Platform FTU and Bike/Link,
Auto, and Cab breakdowns — across all cities and completed calendar months.

## Task routing

For IRF questions, data verification, load [the IRF analysis skill](.agents/skills/growthos-irf-analysis/SKILL.md).
It owns command routing, the evidence workflow, interpretation and analytical formatting;
follow its conditional references for report investigations.

For guided installation, connection setup or setup failures, load
[the setup skill](.agents/skills/growthos-setup/SKILL.md).
For manual setup or test commands, use [README.md](README.md).

Run reviewed read-only `growthos` CLI commands outside the sandbox through the host's approval/escalation mechanism; if approval is unavailable or denied, stop and report the blocker. All credential and read-only restrictions still apply.

## Credentials

- Routine analysis uses only the read-only reporting connection (`GROWTHOS_READER_DSN`), which the CLI reads automatically from the environment or this folder's local `.env`.
- During setup, you may check whether `.env` exists and copy `.env.example` to it only when absent, using the setup skill's no-clobber command. Preserve existing files and symlinks. Never read, display, edit or source `.env`, and never ask users to paste tokens or passwords in chat.
- If `status` fails (for example `CONFIGURATION` or `DEPENDENCY`), follow the setup skill's recovery workflow and report only the CLI's sanitized error rather than inventing data.
