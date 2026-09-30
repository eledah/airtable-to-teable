# Agent prompt — paste this to your AI agent

Copy the whole codeblock below into your AI coding agent. It assumes NOTHING is
set up yet: the agent clones the repo, reconnoitres your machine itself
(Docker, Teable, nginx/TLS, tokens, disk — all via commands), and only asks
about what code cannot determine. Same prompt is embedded in `README.md`
("Give this to your AI agent").

```
You are helping me migrate Airtable → Teable. Nothing is set up yet — start by
cloning the pipeline repo, then RECON my machine yourself before asking me
anything. Only ask what code cannot determine. Do not run anything with side
effects (writes, deploys, DNS/nginx changes, imports) until I confirm each step.

0. Clone and verify:
   git clone https://github.com/eledah/airtable-to-teable.git && cd airtable-to-teable
   Read README.md, docs/MIGRATION_NOTES.md and docs/SETUP_PROCESS.md (proven recipe:
   Teable on loopback first, domain + TLS last).
   Run stages with PYTHONPATH=src python3 -m airtable_teable <stage> --help
   (stdlib only, no installs). Prove the toolchain with
   PYTHONPATH=src python3 -m pytest tests/ -q.

1. Recon — run ALL of this yourself and report findings in a compact table:
   - Docker: `docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'`
     and `docker compose ls`. Is a teable/teable-db/teable-cache stack already up?
     Which compose dir (e.g. /opt/teable-standalone)? Healthy?
   - Teable API: `curl -s -o /dev/null -w '%{http_code}' http://localhost:3000/` and the
     same for $TEABLE_API_URL if set. If a Teable PAT exists (env/.teable_token),
     validate it READ-ONLY via GET /api/space — never print the token.
   - Web: `ss -ltnp | grep -E ':(80|443)'`, server_name/cert lines from `nginx -T`,
     `certbot certificates`. Any existing domain + TLS?
   - Airtable: if $AIRTABLE_TOKEN exists, validate READ-ONLY via GET /v0/meta/bases
     and LIST the base names/ids for me — never print the token.
   - Disk: `df -h /` free space. Recommend `sync` (tight disk, one base at a time)
     vs `download` + `migrate` YOURSELF from the numbers — don't ask me.
   - Leftovers: `.env` / `.teable_token` / `backup/` / `sync_state.json` present?

2. Confirm — ask ONLY what recon couldn't settle, each with your detected default
   I can just approve:
   a. Teable missing/unhealthy → "Deploy the loopback Compose stack from
      docs/SETUP_PROCESS.md? [yes]" / "Teable answers at <url> — use it? [yes]".
   b. Domain → only if needed: publish on a public domain? Which — detected <d>
      from nginx/certs, or a new one? (Requires DNS A/AAAA to this box + ports
      80/443. Order: loopback → migrate → publish.)
   c. Scope → show the detected base list: "all N bases, or pick which?"
   d. Credentials → only the missing/invalid ones; tell me where to create each.
   e. I will apply these unless you object: real attachment fields + prune after
      verified upload; real links + multi-selects; no SQLite export.

3. Plan: exact `.env` contents (with `...` everywhere I must paste a secret), exact
   command sequence starting with `--dry-run`, plus ordered infra steps if
   deploying/publishing (loopback → healthy → migrate → nginx + DNS + TLS → publish).
4. Execute one approved step at a time; verify after each (counts vs manifest.json,
   attachment uploaded/skipped counts, curl health checks, free disk).
5. Final report: migrated / failed-deferred + why, free disk, exact resume command,
   public URL if published.

Rules: never print or echo tokens; never commit `.env` / `.teable_token`; never
delete a Teable base and never touch DNS/nginx without asking me first; never
hand-edit state files (`sync_state.json` / `migration_state.json` are the resume
mechanism — to redo a base, delete its Teable base and drop its name from the
state file, then re-run).
```