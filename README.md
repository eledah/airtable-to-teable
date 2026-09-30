# Airtable → Teable migration pipeline

Production-tested, stdlib-only pipeline that moves a **full Airtable estate**
(data + relations + tags + files) into **self-hosted Teable Community** —
resumably, incrementally, and verifiably.

Battle-tested on ~62 bases / ~490k records / ~2.4M link cells / ~117k
attachments with **0 link mismatches** on the 22k-cell benchmark base.

```
Airtable API ──▶ download ──▶ backup/ (CSV + files + manifest.json)
                                    ──▶ import ──▶ Teable (tables, records, links, selects)
                                                ──▶ attach ──▶ Teable attachment fields
                                                          ──▶ prune source (optional)
migrate orchestrates import → attach → prune for already-downloaded bases.
sync streams download → import → attach → prune ONE base at a time (tight disks).
export (optional) snapshots any Teable base back to SQLite.
```

No dependencies. Python ≥ 3.9. Works against any Teable Community instance.

## Give this to your AI agent

Fastest path: paste the block below to your AI coding agent. It assumes nothing
is set up yet — the agent clones this repo, reconnoitres your machine itself
(Docker, Teable, nginx/TLS, tokens, disk), and only asks about what code cannot
determine.
(Full version: [`docs/AGENT_PROMPT.md`](docs/AGENT_PROMPT.md).)

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

## Quickstart

```bash
git clone https://github.com/eledah/airtable-to-teable.git
cd airtable-to-teable
cp .env.example .env   # then fill in tokens + TEABLE_API_URL
# or: export AIRTABLE_TOKEN=pat... TEABLE_TOKEN=... TEABLE_API_URL=https://teable.example

# 1. Backup Airtable -> ./backup (Ctrl-C safe, resume with the same command)
PYTHONPATH=src python3 -m airtable_teable download --out ./backup
# or via installed entry point: airtable-teable download --out ./backup

# 2. Peek at what would be imported (no API calls)
PYTHONPATH=src python3 -m airtable_teable import --out ./backup --dry-run

# 3. Import one base (data + links + selects)
PYTHONPATH=src python3 -m airtable_teable import --out ./backup --bases "My Base"

# 4. Upload its files as real attachment fields (idempotent, safe to re-run)
PYTHONPATH=src python3 -m airtable_teable attach --out ./backup --bases "My Base"
# add --prune to delete the source folder after verified upload

# 5. Or run everything unattended, smallest-first, resumable:
PYTHONPATH=src python3 -m airtable_teable migrate --out ./backup

# 5b. Tight disk? Stream one base at a time (never hold the full estate):
#     download → import → attach → prune per base, resumable via sync_state.json
PYTHONPATH=src python3 -m airtable_teable sync --out ./backup
PYTHONPATH=src python3 -m airtable_teable sync --out ./backup --bases "My Base,appXXX" --dry-run

# 6. (Optional) snapshot a Teable base back to SQLite
PYTHONPATH=src python3 -m airtable_teable export <teableBaseId> ./out.sqlite
```

Install once with `pip install -e .` and drop the `PYTHONPATH=src` prefix
(the `airtable-teable` command is registered as an entry point).

## Stages

| Stage | Command | Input → Output | Idempotent? |
|---|---|---|---|
| 1. download | `airtable-teable download --out ./backup` | Airtable API → `backup/<Base>/…csv`, `<Base>_attachments/…`, `manifest.json` | Yes (per-table skip by row count, per-file skip by size; `--force` to redo) |
| 2. import | `airtable-teable import --out ./backup [--bases A,B] [--no-links]` | CSVs → Teable base (primary-name field, real links + multi-selects) | No — creates a new base each run; delete partials before retry |
| 3. attach | `airtable-teable attach --out ./backup --bases A [--prune]` / `--all` | Files → Teable `attachment` fields (replaces placeholder text) | Yes (reuses fields, skips uploaded names) |
| 4. migrate | `airtable-teable migrate --out ./backup` | Orchestrates 2→3→prune for every pending base, smallest-first | Yes (state file; only marks done after source pruned) |
| 4b. sync | `airtable-teable sync --out ./backup [--bases Name,appID]` | Streams download→import→attach→prune ONE base at a time (peak disk ≈ one base) | Yes (`sync_state.json`; per-table/per-file resume; partial Teable base cleaned for retry) |
| 5. export | `airtable-teable export <baseId> out.sqlite` | Teable → SQLite (text only) | N/A |

## Configuration

| Var / flag | Meaning | Default |
|---|---|---|
| `AIRTABLE_TOKEN` / `--token` (download) | Airtable PAT | — (required) |
| `TEABLE_TOKEN` / `--token` (import/attach/…) | Teable PAT | `–token-file` / `./.teable_token` fallback |
| `TEABLE_API_URL` / `--teable-url` | Teable base URL | `http://localhost:3000` |
| `BACKUP_DIR` / `--out` | Backup root | `./backup` |
| `--space` | Teable spaceId | first space |
| `--batch` | Records per bulk call | 100 |
| `AIRTABLE_RATE_SLEEP`, `TEABLE_SLEEP` | Pacing (s) | 0.3 / 0.15 |
| `MIGRATE_MIN_FREE_KB` | Orchestrator stop guard | 400 000 (≈400 MB) |

Flags beat env vars; env beats built-ins. Never commit `.env` or
`.teable_token` (both git-ignored).

## How relations survive

- **Links:** a column becomes a real Teable `link` (many-many, `isOneWay`) iff
  every non-empty cell is an array of bare `rec…` ids resolving to exactly one
  table in the same base. Lookup/rollup (`X (from Y)`), cross-base and mixed
  columns stay text. Target ids are deduped per cell.
- **Tags:** genuine string arrays become `multipleSelect` (≤100 choices,
  non-numeric, no ids/filenames/lookups). `null` elements dropped, `[]` kept.
- **Names:** the first column after `id` becomes the Teable primary, so link
  cells show human names instead of `rec…` ids.
- **Files:** `rec__field__file` text cells are replaced by real `attachment`
  fields; each file streams in ≤1 MiB chunks and is deleted right after upload
  with `--prune`.

Full rationale: [`docs/MIGRATION_NOTES.md`](docs/MIGRATION_NOTES.md).

## Backup layout

```
backup/
  manifest.json                 # real base/table names + per-table counts
  <Base>/
    <Base>.csv                  # single-table base
    <Base>__<Table>.csv         # multi-table base (UTF-8 BOM, `id` first col)
    <Base>_attachments/<Table>/recXXXX__Field__file.ext
```

## Verification

```bash
# offline unit tests (no network)
python3 -m pytest tests/ -q
# or stdlib-only:
python3 -m unittest discover -s tests

# plan without touching either API
PYTHONPATH=src python3 -m airtable_teable import --out ./backup --dry-run
PYTHONPATH=src python3 -m airtable_teable migrate --out ./backup --dry-run

# after import: compare Teable record counts to manifest.json;
# after attach: every attachment field's file count matches the source folder
# (the attach step prints uploaded/skipped per column).
```

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `_csv.Error: field larger than limit` | Upgrade this repo — `field_size_limit(max)` is set in every stage; don't reintroduce a plain `csv` reader. |
| Link values bled across fields | Link fields must be `isOneWay:true` (`teable.create_field_link` does this). |
| `Cannot set duplicate recordId in the same cell` | Dedupe target ids per cell (already done in `import_data`). |
| 429 storms on download | Keep `AIRTABLE_RATE_SLEEP=0.3` + backoff; don't parallelise per-table pulls. |
| Bulk PATCH 408 on huge link tables | Re-run that base (delete the partial Teable base first) or lower `--batch`. |
| 3 empty rows per new table | `clear_seed_records` handles it; if you disabled it, re-enable. |
| `v2` endpoints 404 | Use `/api/…` paths (this client does); community builds lack `v2`. |
| Disk fills mid-migration | Run `migrate` (smallest-first + guards + `--prune` streaming) instead of manual loops; grow the volume for the largest bases. |

## Project layout

```
src/airtable_teable/  download.py import_data.py attachments.py migrate.py sync.py
                      export_sqlite.py airtable.py teable.py config.py utils.py
                      cli.py (__main__.py)
tests/                test_classify.py test_resume.py test_utils.py test_sync.py
docs/                 MIGRATION_NOTES.md
.env.example  pyproject.toml  requirements.txt  LICENSE
```

## License

MIT — see [LICENSE](LICENSE).
