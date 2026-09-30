# Process write-up — Airtable → Teable on this VPS, and putting it behind `presenter.ir`

A single narrative of what was built, in what order, what broke, and what state
things are in now. Written from the live system (2026-09-30), cross-checked
against `/root/airtable/PROGRESS.md` and the teable stack itself.

---

## 1. What the goal actually was

Two goals, in this order:

1. **Run Teable on this VPS** as a self-hosted replacement for Airtable, and
   migrate a full Airtable estate into it.
2. **Expose it on a real domain** (`presenter.ir`) with working DNS and TLS.

The domain work was sequenced deliberately: Teable was bound to `127.0.0.1`
first and only published once it was healthy and populated, so the migration
ran entirely over loopback and never had to survive a half-configured public
endpoint.

The migration target was **~62 Airtable bases, ~490k records, ~2.4M link cells,
~117k attachment files**. Final state: **57 bases migrated, all attachments
uploaded, all source pruned.**

---

## 2. Phase 1 — Teable standalone

Deployed as a three-service Docker Compose stack in `/opt/teable-standalone/`:

| Service | Image | Notes |
|---|---|---|
| `teable` | `ghcr.io/teableio/teable-community:latest` | `127.0.0.1:3000` only — never bound publicly |
| `teable-db` | `postgres:15.4` | volume `teable-db` |
| `teable-cache` | `redis:7.2.4` | volume `teable-cache`, AOF on, password required |

Key decisions:

- **Community edition**, not a licensed build. This matters throughout: the
  `/api/v2/...` endpoints mostly 404, and there are no AI-gated features.
- **`ports: '127.0.0.1:3000:3000'`** rather than `3000:3000`. Port publishing is
  done by nginx; Teable itself has no reason to be reachable from outside.
- Compose healthchecks gate startup (`depends_on: condition: service_healthy`),
  so `docker compose up` doesn't race Postgres/Redis readiness.
- Secrets live in `/opt/teable-standalone/.env` (chmod 600), plus a Teable
  personal access token at `/root/airtable/.teable_token` (chmod 600) used by
  all migration tooling.

---

## 3. Phase 2 — DNS with PowerDNS

PowerDNS Authoritative 5.0.2 runs **on this box**, serving the domain for
itself — this VPS is both the authoritative nameserver and the origin.

Configuration (`/etc/powerdns/`):

- `pdns.conf` — `local-address=185.19.33.45 2001:1af8:4020:a010::1700`
  (IPv4 + IPv6), `default-soa-content` pointing at `ns1.presenter.ir`.
- `pdns.d/backend-sqlite.conf` — `launch=gsqlite3` against
  `/var/lib/powerdns/pdns.sqlite3`, DNSSEC off, `synchronous=0`.
- `pdns.d/bind.conf` — additionally loads the bind backend with an empty
  autoprimary config.

**Don't be misled by the logs.** The journal line
`[bindbackend] Parsing 0 domain(s)` is *not* a problem — that's the bind
backend correctly reporting an empty autoprimary zone list. The real zone
lives in the gsqlite3 backend. (Note the gsqlite3 schema uses a `domains`
table, not `zones` — querying for `zones` looks like an empty database when it
isn't.)

Actual zone contents in `pdns.sqlite3`:

| Name | Type | Value |
|---|---|---|
| `presenter.ir` | SOA | `ns1.presenter.ir hostmaster.presenter.ir …` |
| `presenter.ir` | NS | `ns1`, `ns2` |
| `presenter.ir` | A / AAAA | `185.19.33.45` / `2001:1af8:4020:a010::1700` |
| `www.presenter.ir` | A / AAAA | same |
| `ns1`, `ns2.presenter.ir` | A / AAAA | same |
| `chat.presenter.ir` | A | `185.19.33.45` |
| `ravi.presenter.ir` | A | `185.19.33.45` |

Because ns1/ns2 are the host itself, the glue records are self-referential and
resolution works with no third-party dependency.

> **Risk:** the authoritative data is a single un-backed-up SQLite file owned
> by `pdns:pdns`. If it is lost, the domain goes dark silently. A periodic
> `sqlite3 .backup` off-box is the cheap fix.

---

## 4. Phase 3 — nginx and TLS

Three vhosts, all terminating TLS and proxying to loopback:

| vhost | upstream |
|---|---|
| `presenter.ir` / `www` | `127.0.0.1:3000` (Teable) |
| `chat.presenter.ir` | separate app |
| `ravi.presenter.ir` | separate app |

Common shape, worth copying for future vhosts:

- Port 80 serves **only** `/.well-known/acme-challenge/` from
  `/var/www/certbot`, then 301s everything else to HTTPS. This keeps renewals
  working without ever serving content over plaintext.
- Port 443 sets `http2 on` and proxies with `Upgrade`/`Connection: upgrade`
  headers plus `proxy_read_timeout 3600s` — required for websockets and long
  uploads.
- `client_max_body_size 200m` on the Teable vhost, because attachment uploads
  are large.
- Real IP / `X-Forwarded-*` headers forwarded so Teable logs and link
  generation see the public scheme.

Certificates are Let's Encrypt via webroot (`/etc/letsencrypt/live/`), with a
renewal timer that has been running clean — most recently
`2026-09-30 03:43: "No renewals were attempted"`, all three certs valid into
November 2026.

---

## 5. Phase 4 — The migration itself

Three original scripts, then an orchestrator:

| Script | Role |
|---|---|
| `airtable_download.py` | Airtable → CSV + attachments, resumable |
| `import_to_teable.py` | CSV → Teable (data, links, selects) |
| `add_attachments.py` | real attachment fields + file uploads, idempotent |
| `run_remaining.py` | orchestrator: import → attach → prune, smallest-first |

**Per-base pipeline order matters** and is not arbitrary:

1. Parse all CSVs in the base folder → build `rec_id → {tables}` map.
2. Classify every column (link / multi-select / plain text); pick the primary.
3. `POST /api/base` → baseId.
4. Per table: create table (name-primary + `id` + text + selects), **clear the
   seeded placeholder rows**, bulk-create records capturing
   `airtableId → teableId`.
5. **Only then** create link fields and bulk-`PATCH` link values.

Links must come last because a link field needs its target table *and* its
target records to already exist.

**Disk strategy.** Teable stores attachments on the same disk, so uploading
relocates bytes rather than freeing them. The working method was therefore
per-base: import → attach → prune the source folder, smallest base first, so
peak disk stayed ~one base. `run_remaining.py` added a free-space guard (soft
stop before filling) and a deferral guard (skip a base too large for current
free space).

### The bugs that cost the most time

These nine are worth preserving verbatim — each one killed a run:

1. **New tables ship with ~3 empty seeded rows.** Must be listed and deleted
   before inserting real data, or every table gets junk records.
2. **Symmetric link fields corrupt multi-link data.** With two tables sharing
   several many-many links and both directions populated, Teable's reverse
   field sync bleeds values across fields. Fix: `isOneWay: true` on every link
   field, each side set independently. Verified on 22,119 cells, 0 mismatches.
3. **Python's default 128 KB CSV field limit** aborts on long-text cells
   (`_csv.Error`). Needs `csv.field_size_limit(sys.maxsize)` in *every*
   reader/writer — including the attachment loader, which reads the same CSVs.
4. **Unpaced attachment downloads caused 429 storms** that killed runs. Needs
   a per-file sleep plus exponential backoff with jitter (16 attempts,
   honours `Retry-After`, 600 s cap).
5. **Airtable multi-select arrays contain `null` and `[]`.** Must drop nulls
   and accept empty arrays, or the entire column silently degrades to text.
6. **Lookup/rollup columns (`X (from Y)`) are read-only reverse sides.** Exclude
   from *both* link and select classification or you get bogus link fields and
   duplicate-target errors.
7. **Teable rejects duplicate record ids in one link cell.** Lookup arrays can
   repeat an id; dedupe with `dict.fromkeys` before `PATCH`.
8. **Bulk link `PATCH` can 408** on the heaviest tables. Recover by deleting
   the partial base and re-running that base.
9. **Importer is not idempotent** — it creates a new base per run. Delete the
   old one first. (`add_attachments.py` *is* idempotent.)

### Storage incidents

- **Disk hit 100% mid-migration.** The user grew the VPS to 58 GB / 3.8 GB RAM,
  after which the same orchestrator finished the remaining ~10 large bases
  unattended. A corrupted Redis AOF from the full-disk event was fixed by
  clearing the (non-durable) cache volume.
- **Disk hit 100% again post-migration** — cause was a nightly cron
  (`/usr/local/bin/teable-backup.sh`) tarring the entire 12 GB `teable-data`
  volume with 14-day retention, i.e. ~168 GB of cumulative snapshots. Backups
  were **not wanted**: cron entry, script, and 13 GB of `backups/` were
  deleted. **Teable currently has no backups at all.** If a safety net is ever
  wanted, back up `pg_dump` (~660 MB) to off-box storage — not the 24 GB
  attachment volume.

---

## 6. Phase 5 — Consolidation into `airtable-to-teable`

The working scripts were rewritten as a clean installable package at
`/root/airtable-to-teable/`:

```
src/airtable_teable/  download.py import_data.py attachments.py migrate.py sync.py
                      export_sqlite.py airtable.py teable.py config.py utils.py cli.py
tests/                test_classify.py test_resume.py test_utils.py test_sync.py
docs/                 MIGRATION_NOTES.md
```

`sync.py` is the new stage-6 disk-frugal loop: download → import → attach →
prune, one base at a time, resumable via a state file, with the safety
property that a partially-imported Teable base is best-effort deleted so the
next run retries cleanly.

This work is committed and released as **v1.1.0** on
`github.com/eledah/airtable-to-teable` (pipeline + `sync` + agent prompt +
`CHANGELOG.md`). Tests pass (16 passed).

---

## 7. Verified current state

| Check | Result |
|---|---|
| `https://presenter.ir/` | HTTP 307 → Teable auth redirect ✅ |
| `http://127.0.0.1:3000/` | HTTP 307 ✅ |
| `presenter.ir` A / NS | `185.19.33.45`, `ns1/ns2.presenter.ir` ✅ |
| `pdns`, `nginx` | active/running ✅ |
| `teable`, `teable-db`, `teable-cache` | Up, healthy ✅ |
| Migration | 57/57 bases done; `backup/` pruned to 128 KB ✅ |
| Tests | 16 passed ✅ |

---

## 8. Open items

1. **Disk at 89%** (51 G used / 58 G, 6.6 G free). The 13 GB freed by deleting
   the backup cron has largely been re-consumed. Watch before re-running any
   import.
2. **No Teable backups.** Deliberate, but currently zero recovery path.
3. **PowerDNS zone data is a single un-backed-up SQLite file.** One disk event
   and the domain disappears.
4. ~~Uncommitted `airtable-to-teable` work~~ — done (committed, released v1.1.0).
5. The `haas-*` containers (5 Hermes agent containers) all report **unhealthy**
   — unrelated to this project, but noise in `docker ps`.

---

## 9. If you had to do it again

- Stand up the app on loopback **first**, publish it last.
- Assume the disk is the binding constraint, not CPU or RAM — Teable puts
  attachments on the same filesystem you imported them from.
- Trust but verify every Teable API assumption; community edition differs from
  the docs, and its reverse-field link sync is actively harmful on multi-link
  tables.
- Make each stage independently resumable and idempotent from the start. The
  single biggest time saver in this migration was the state file plus per-base
  retry, not any individual script.
- Never enable "backup the whole data volume" cron on a single-disk host.
