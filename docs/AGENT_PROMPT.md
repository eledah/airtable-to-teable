# Agent prompt — paste this to your AI agent

Copy the whole codeblock below into your AI coding agent (with this repo
cloned). Same prompt is embedded in `README.md` ("Give this to your AI
agent").

```
You are helping me migrate Airtable → Teable using the airtable-to-teable repo
(already cloned — read README.md and docs/MIGRATION_NOTES.md first; code is in
src/airtable_teable/, run stages with PYTHONPATH=src python3 -m airtable_teable
<stage> --help; stdlib only, no installs needed).

Work WITH me — do not run anything with side effects until I confirm each step.
Follow these steps:

1. Inspect: confirm the repo layout, whether `.env` / `.teable_token` exist,
   and run `PYTHONPATH=src python3 -m pytest tests/ -q` to prove the toolchain works.
2. Interview me — ask ALL of these before proposing a plan:
   a. Scope: whole Airtable estate or specific bases? (If subset, get exact base
      names or IDs for `--bases`.)
   b. Teable: what is the self-hosted URL (`$TEABLE_API_URL`) and target space
      (a spaceId, or just use the first space)? Can you reach it?
   c. Credentials: do I have an Airtable PAT (`$AIRTABLE_TOKEN`) and a Teable PAT
      (`$TEABLE_TOKEN`)? Tell me where to create each if not.
   d. Disk: how much free space is on this box vs. the rough backup size?
      Recommend `sync` (tight disk — one base at a time) or `download` + `migrate`.
   e. Attachments: import files as real Teable attachment fields? Prune each source
      folder after verified upload (default) or keep it (`--keep`)?
   f. Links/tags: default (real links + multi-selects) or data-only (`--no-links`)?
      Do I want the optional SQLite `export` afterwards?
3. Plan: show me the exact `.env` contents (with `...` placeholders everywhere I must
   paste a secret), then the exact command sequence, always starting with `--dry-run`.
4. Execute one stage at a time, only after I approve; verify after each stage
   (record counts vs. `manifest.json`, attachment uploaded/skipped counts, free disk).
5. Report at the end: what migrated, what failed or was deferred (and why), free disk,
   and the exact resume command.

Rules: never print or echo tokens; never commit `.env` or `.teable_token`; never
delete a Teable base without asking me first; never hand-edit state files
(`sync_state.json` / `migration_state.json` are the resume mechanism — to redo a base,
delete its Teable base and drop its name from the state file, then re-run).
```
