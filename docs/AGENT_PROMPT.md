# Agent prompt — paste this to your AI agent

Copy the whole codeblock below into your AI coding agent. It assumes NOTHING is
set up yet: the agent clones the repo, checks whether Teable is even installed,
asks about a public domain, then interviews you and runs the migration with you.
Same prompt is embedded in `README.md` ("Give this to your AI agent").

```
You are helping me migrate Airtable → Teable. Nothing is set up yet — start by
cloning the pipeline repo, then work WITH me. Do not run anything with side
effects until I confirm each step.

0. Clone and verify:
   git clone https://github.com/eledah/airtable-to-teable.git && cd airtable-to-teable
   Read README.md, docs/MIGRATION_NOTES.md and docs/SETUP_PROCESS.md (the proven
   recipe: Teable on loopback first, domain + TLS last).
   Run stages with PYTHONPATH=src python3 -m airtable_teable <stage> --help
   (stdlib only, no installs). Prove the toolchain with
   PYTHONPATH=src python3 -m pytest tests/ -q.

1. Inspect my machine and report exactly what you find: is Docker available? Is a
   Teable server already running? (Check `docker ps` and
   `curl -s -o /dev/null -w '%{http_code}' $TEABLE_API_URL`, default
   http://localhost:3000.) Is nginx / any TLS cert already in place?

2. Interview me — ask ALL of these before proposing a plan:
   a. Teable server: already running at some URL, or do I need you to deploy it?
      (If deploy: Docker Compose community standalone bound to 127.0.0.1:3000 only,
      per docs/SETUP_PROCESS.md — public exposure comes later, never bind Teable
      directly to the internet.)
   b. Domain: should Teable end up on a public domain (nginx reverse proxy +
      Let's Encrypt TLS, per docs/SETUP_PROCESS.md)? If so: which domain, is its
      DNS under your control (A/AAAA pointing at this box?), are ports 80/443 open?
      Note the order: get Teable healthy on loopback and migrate first, publish last.
   c. Scope: whole Airtable estate or specific bases? (If subset: exact base names
      or IDs for `--bases`.)
   d. Credentials: Airtable PAT ($AIRTABLE_TOKEN), Teable PAT ($TEABLE_TOKEN)?
      Tell me where to create each if missing.
   e. Disk: free space vs. rough backup size? Recommend `sync` (tight disk — one
      base at a time) or `download` + `migrate`.
   f. Attachments: real attachment fields? Prune each source folder after verified
      upload (default) or keep it (`--keep`)?
   g. Links/tags: default (real links + multi-selects) or data-only (`--no-links`)?
      Do I want the optional SQLite `export` afterwards?
3. Plan: the exact `.env` contents (with `...` everywhere I must paste a secret),
   the exact command sequence starting with `--dry-run`, plus — if a server/domain
   deploy is wanted — the ordered infra steps (Teable on loopback → verify healthy
   → migrate → nginx + DNS + TLS → publish).
4. Execute one approved step at a time; verify after each (record counts vs.
   `manifest.json`, attachment uploaded/skipped counts, curl health checks, free disk).
5. Final report: what migrated, what failed or was deferred (and why), free disk,
   the exact resume command, and the public URL if published.

Rules: never print or echo tokens; never commit `.env` / `.teable_token`; never
delete a Teable base and never touch DNS/nginx without asking me first; never
hand-edit state files (`sync_state.json` / `migration_state.json` are the resume
mechanism — to redo a base, delete its Teable base and drop its name from the
state file, then re-run).
```
