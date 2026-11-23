# GrowthOS IRF Analyst Guide

Codex runs here and answers IRF questions by running the reviewed `growthos` CLI against current reporting data in read-only Neon Postgres. Deliver analytical reports in chat using the loaded skill's output format.

Approved scope: IRF and FTU-eligible funnel reporting — Installs, Registrations, Platform FTU (and Bike/Link, Auto, Cab breakdowns), plus FTU-eligible FE sessions, RR, Net rides, FE2RR, RR2Net, and Bike/Auto/Cab RR/Net/RR2Net — across all cities and completed calendar months. Ended-month Platform FTU target facts/shortlists, same-month Registration target contribution (`investigate`), and calendar-adjusted MoM funnel contribution (`investigate-funnel`) are also supported.

## Task routing

- **Assessment Layer — IRF reporting, targets & verification:** load [the IRF analysis skill](.agents/skills/growthos-irf-analysis/SKILL.md) for factual IRF lookups, MoM/period comparisons, master reports, ratio investigations, monthly trends, city rankings/funnels, service-share drivers, PFTU target facts/shortlists + widening-miss (`persistent` / `new_miss`) tables, warehouse SQL provenance, and data verification.
- **Investigation Layer — FTU target-miss & dip investigation:** load [the FTU investigation skill](.agents/skills/growthos-ftu-investigation/SKILL.md) when asked to investigate *why* Platform FTU missed target or dipped MoM (starts from the Assessment Layer's shortlisted `top`/`all` cities and runs the sequential flow: Registration Target Shortfall Check → stop as registration issue when Registrations explain the gap; otherwise Month-on-Month Dip and Persistence Check → `persistent` stops citing prior month, `new_miss` runs Funnel Decomposition across `FE`/`FE2RR`/`RR2Net` counterfactuals on Platform FTU, selecting stages above the floor until cumulative contribution passes the configured share, checking calendar-adjusted MoM Registrations when `FE` is primary, and breaking down Bike/Auto/Cab drivers when `FE2RR` or `RR2Net` is primary) or to answer granular `FE`, `FE2RR`, `RR2Net`, or `Registration` questions.
- **Setup & connection recovery:** load [the setup skill](.agents/skills/growthos-setup/SKILL.md) for guided installation, local `.env` setup, or `status` failures. Use [README.md](README.md) for manual setup and offline test commands.

## Operating rules

- Use [GLOSSARY.md](GLOSSARY.md) as the normative vocabulary for names and meanings. Do not invent definitions or aliases.
- Use the CLI's output for every number. Do not recalculate formulas, thresholds, or eligibility rules from memory, and do not present a number the CLI did not return.
- Run reviewed read-only `growthos` commands strictly outside sandbox as we need internet connection for data access, run it through the host's approval/escalation mechanism; if approval is unavailable or denied, stop and report the blocker.
- Keep reports chat-only. Do not save local report files or export historical CSVs.

## Credentials

- Routine analysis uses only the read-only reporting connection (`GROWTHOS_READER_DSN`), which the CLI loads automatically from the environment or this folder's local `.env`.
- During setup, you may check whether `.env` exists and copy `.env.example` to it only when absent, using the setup skill's no-clobber command. Preserve existing files and symlinks.
- Never read, display, or source `.env`, and never ask users to paste tokens or passwords in chat. If user pastes a token accept it and update the .env for user.
- If `status` fails (for example `CONFIGURATION` or `DEPENDENCY`), follow the setup skill's recovery workflow and report only the CLI's sanitized error rather than inventing data.
