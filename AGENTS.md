# GrowthOS IRF Analyst Guide

Codex runs here and answers IRF questions by running the reviewed `growthos` CLI against current reporting data in read-only Neon Postgres. Analytical reports are delivered in chat.

Approved scope: IRF and city-wide funnel reporting — Installs, Registrations, Platform FTU (and Bike/Link, Auto, Cab breakdowns), plus all-customer FE sessions, RR, Net rides, FE2RR, RR2Net, and Bike/Auto/Cab RR/Net/RR2Net — across all cities and completed calendar months. Ended-month Platform FTU target facts/shortlists, same-month Registration target contribution (`investigate`), and calendar-adjusted MoM funnel contribution (`investigate-funnel`) are also supported.

## Task routing

- **Layer 1 — IRF reporting, targets & verification:** load [the IRF analysis skill](.agents/skills/growthos-irf-analysis/SKILL.md) for factual IRF lookups, MoM/period comparisons, master reports, ratio investigations, monthly trends, city rankings/funnels, service-share drivers, PFTU target facts/shortlists + widening-miss (`persistent` / `new_miss`) tables, warehouse SQL provenance, and data verification.
- **Layer 2 — FTU target-miss & dip investigation:** load [the FTU investigation skill](.agents/skills/growthos-ftu-investigation/SKILL.md) when asked to investigate *why* Platform FTU missed target or dipped MoM (takes the widening-miss `persistent`/`new_miss` table cities by default and runs independent same-month Registration target-miss checks + parallel `FE`/`FE2RR`/`RR2Net` funnel contribution) or to answer granular `FE`, `FE2RR`, `RR2Net`, or `Registration` questions.
- **Setup & connection recovery:** load [the setup skill](.agents/skills/growthos-setup/SKILL.md) for guided installation, local `.env` setup, or `status` failures. Use [README.md](README.md) for manual setup and offline test commands.

Run reviewed read-only `growthos` CLI commands outside the sandbox through the host's approval/escalation mechanism; if approval is unavailable or denied, stop and report the blocker. All credential and read-only restrictions still apply.

## Credentials

- Routine analysis uses only the read-only reporting connection (`GROWTHOS_READER_DSN`), which the CLI loads automatically from the environment or this folder's local `.env`.
- During setup, you may check whether `.env` exists and copy `.env.example` to it only when absent, using the setup skill's no-clobber command. Preserve existing files and symlinks. Never read, display, or source `.env`, and never ask users to paste tokens or passwords in chat.
- If `status` fails (for example `CONFIGURATION` or `DEPENDENCY`), follow the setup skill's recovery workflow and report only the CLI's sanitized error rather than inventing data.
