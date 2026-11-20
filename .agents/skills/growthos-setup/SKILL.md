---
name: growthos-setup
description: Guide GrowthOS analyst installation, local reporting-connection setup, and recovery from environment or connection failures. Use for requests such as "set me up", "install GrowthOS", or "setup failed".
---

# GrowthOS Setup

## Boundaries

Follow repository-root [AGENTS.md](../../../AGENTS.md) for credentials and CLI command approval. Work entirely inside `gwos-analyst/`. Run installation and verification commands directly; ask the user only for local actions you cannot safely perform.

Credentials stay local: the only permitted `.env` write is copying the blank `.env.example` when `.env` is absent. Never inspect, edit, display, or source `.env`, dump environment variables, or ask for a connection string in chat. The reviewed CLI loads its own settings.

## Workflow

1. **Check the runtime.** Work from the repository root. Use `.venv/bin/python` if present and runnable; verify its version meets `pyproject.toml`. Otherwise check `python3 --version`, then create `.venv` using the command in [README.md](../../../README.md). Reuse a working environment; preserve a broken `.venv` and report the blocker rather than deleting or overwriting it. If Python is absent, too old, or lacks `venv`, give the platform-appropriate local prerequisite and wait for the user before retrying. Never install system packages automatically.
2. **Install the package.** Run the editable-install command from [README.md](../../../README.md) with `.venv/bin/python`. For a connection-only request on an already installed runtime, skip installation unless a dependency check fails. Verify with `.venv/bin/python -m pip check` and `.venv/bin/python -m growthos --help`. If downloads are blocked, request the host's network/command approval; if unavailable or denied, stop with the blocker. Keep standard package verification and TLS intact.
3. **Prepare local configuration.** If the user still needs to configure the connection, run this no-clobber command from the repository root (creates a private `.env` only when absent; preserves existing files and symlinks without reading them):

   ```bash
   if [ ! -e .env ] && [ ! -L .env ]; then
     (umask 077; cp -n .env.example .env)
   fi
   ```

   Then give direct instructions in chat (not just a README link):
   > Open the project directory (`gwos-analyst`) in your local editor, locate the `.env` file (enable hidden files if needed), and add your provided read-only database URL after `GROWTHOS_READER_DSN=`. Save the file and tell me when you're ready; don't share the URL in chat.

   Wait for user confirmation before checking the connection. Skip this handoff if the user already configured the connection.
4. **Check the connection.** Run `.venv/bin/python -m growthos status` through the host approval mechanism required by `AGENTS.md`. Use only its sanitized result. On failure, follow the recovery table below. When the user must act, give one clear next action, wait for confirmation, and retry only the affected check.
5. **Close with evidence.** Confirm setup is ready only when `pip check`, `growthos --help`, and `growthos status` all succeed. State which stored months are available and surface any returned warnings; an empty month list means connected without reporting data. Connection success does not certify source freshness or completeness. If blocked, distinguish an installed runtime from an unverified connection. Stop here without generating an unsolicited report or saving setup state.

## Connection recovery

| CLI error | Next action |
| --- | --- |
| `CONFIGURATION` | Follow step 3 to create a missing `.env` and give local editing instructions. If already configured, ask the user to verify `GROWTHOS_READER_DSN` privately. |
| `DEPENDENCY` | Re-run `.venv/bin/python -m pip install -e .`, then repeat `pip check`, `--help`, and `status`. |
| `TLS` | Ask the user to use the administrator-provided hostname-verified URL (`sslmode=verify-full`); keep TLS verification enabled. |
| `READ_ONLY_ROLE` | Ask the user to obtain the dedicated read-only reporting connection from their administrator. |
| `DATABASE_UNAVAILABLE` | Ask the user to check network access and confirm the connection with their administrator privately. This error alone does not identify the root cause. |
| `STORE_INTEGRITY`, `SCHEMA`, `PROVENANCE`, or other errors | Report the sanitized code and message and escalate to the database administrator; leave schema, permissions, and reporting data unchanged. |
