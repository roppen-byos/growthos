---
name: growthos-setup
description: Guide GrowthOS analyst installation, local reporting-connection setup, and recovery from environment or connection failures. Use for requests such as "set me up", "install GrowthOS", or "setup failed".
---

# GrowthOS Setup

## Boundaries

Follow repository-root [AGENTS.md](../../../AGENTS.md) for credentials and command
approval. Work entirely in this analyst folder. Run installation and checks;
ask the user only for local actions you cannot safely perform.

Credentials stay local: the only permitted `.env` write is copying the blank
`.env.example` when `.env` is absent. Never inspect, edit,
display or source `.env`, dump environment variables, or ask for a connection
string in chat. The reviewed CLI may load its own settings.

## Workflow

1. **Check the runtime.** Work from the repository root. Use `.venv/bin/python`
   if present and runnable; check its version against `pyproject.toml`. Otherwise
   check `python3 --version`, then create `.venv` using the README's setup command.
   Reuse a working environment; preserve a broken `.venv` and report
   the blocker rather than deleting or overwriting it. If Python is absent,
   too old, or lacks `venv`, give the platform-appropriate local prerequisite and
   wait for the user before retrying. Do not install system packages automatically.
2. **Install the package.** Run the README's editable-install command with the
   project interpreter. For a connection-only request on an already installed
   runtime, skip installation unless a dependency check fails. Verify with
   `.venv/bin/python -m pip check` and `.venv/bin/python -m growthos --help`.
   If downloads are blocked, request the host's network/command approval; if
   unavailable or denied, stop with the blocker. Use normal package verification
   rather than disabling TLS or changing package indexes to bypass an error.
3. **Prepare local configuration.** If the user needs to configure the connection,
   run this command from the repository root. It creates a private `.env`
   only when absent; preserve existing files and symlinks without reading them.

   ```bash
   if [ ! -e .env ] && [ ! -L .env ]; then
     (umask 077; cp -n .env.example .env)
   fi
   ```

   Then give direct instructions in chat, not just a README link:
   "Open the project directory (`gwos-analyst`) in your local editor, locate the
   `.env` file (enable hidden files if needed), and add your provided read-only
   database URL after `GROWTHOS_READER_DSN=`. Save the file and tell me when you're ready; don't share
   the URL in chat."
   Wait for confirmation before checking the connection. Skip this handoff if
   the user already configured the connection.
4. **Check the connection.** Run `.venv/bin/python -m growthos status` through
   the host approval mechanism required by AGENTS.md. Use only its sanitized
   result. On failure, follow the recovery table. When the user must act,
   explain one next action, wait for confirmation, then retry the affected check.
   Do not repeat a failing command without a relevant change.
5. **Close with evidence.** Say setup is ready only when package checks and
   `status` succeed. State whether stored months are available and report returned
   warnings; an empty catalogue means connected without reporting data. Connection
   success does not certify data freshness or completeness. If blocked, distinguish
   an installed runtime from an unverified connection. Stop here: no automatic
   report, question bank, or saved setup state.

## Connection recovery

| CLI error | Next action |
| --- | --- |
| `CONFIGURATION` | Follow step 3 to prepare a missing `.env` and give direct local editing instructions. If already configured, ask the user to correct it privately. |
| `DEPENDENCY` | Re-run the editable installation with the same project interpreter, then repeat package and connection checks. |
| `TLS` | Ask the user to use the provided hostname-verified connection (`sslmode=verify-full`); preserve TLS verification. |
| `READ_ONLY_ROLE` | Ask the user to obtain the dedicated read-only reporting connection from their administrator. |
| `DATABASE_UNAVAILABLE` | Ask the user to check network access and have their administrator confirm the connection privately. This error alone does not identify the cause. |
| Other errors | Report the sanitized code/message and the remaining blocker; leave schema, permissions and reporting data unchanged. |
