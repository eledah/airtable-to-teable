# Migration notes — Teable quirks & decisions (battle-tested)

Distilled from a full production migration (~62 bases, ~490k records, ~2.4M
link cells, ~117k attachments) into self-hosted Teable Community. Read this
before changing classification or API code — every rule below cost a debugging
session.

## Verified API recipes (Community edition)

Base URL is configurable (`$TEABLE_API_URL`, `--teable-url`). Auth:
`Authorization: Bearer <PAT>`. Use the docs-style `/api/...` paths — the
`/api/v2/...` variants mostly 404 on community builds.

| Op | Recipe |
|---|---|
| List spaces | `GET /api/space` → `[{id,name}]`; auto-pick first |
| Create base | `POST /api/base` `{"spaceId","name"}` → `{id}` |
| List bases | `GET /api/base/access/all` |
| Create table | `POST /api/base/{baseId}/table/` `{"name","fields":[...]}`; first field is the primary |
| List records | `GET /api/table/{id}/record?take=1000&skip=N` (take ≤ 1000) |
| Bulk create | `POST /api/table/{id}/record` `{"fieldKeyType":"name","typecast":true,"records":[...]}` (batch ~100) |
| Bulk update | `PATCH /api/table/{id}/record` `{"records":[{"id","fields":{...}}]}` |
| Bulk delete | `DELETE /api/table/{id}/record?recordIds=<id>&recordIds=<id>` |
| Link field | `POST /api/table/{id}/field` `{"type":"link","name", "options":{"relationship":"manyMany","isOneWay":true,"foreignTableId"}}`; cell = `[{"id": teableId}]` |
| Multi-select | `{"type":"multipleSelect","name", "options":{"choices":[{"name"},...]}}`; cell = `[choice, ...]` |
| Attachment | create `{"type":"attachment","name"}` then `POST /api/table/{t}/record/{r}/{f}/uploadAttachment` multipart `file=@path` (HTTP 201) |

## Nine bugs/quirks and their fixes

1. **Seeded placeholder records.** New tables ship with ~3 empty rows. Always
   list + DELETE them before inserting real data.
2. **Symmetric-link reconciliation corrupts multi-link data.** With two tables
   sharing several many-many links, populating both directions lets Teable's
   reverse sync bleed values across fields. Fix: `isOneWay:true` on every link
   field; each side is set independently from source. (Verified: 22,119 cells,
   0 mismatches.)
3. **CSV field-size limit.** Raise `csv.field_size_limit(sys.maxsize)` in every
   reader/writer or large long-text cells abort with `_csv.Error`.
4. **Attachment download rate limiting.** Pace downloads (`RATE_SLEEP`) and use
   exponential backoff with jitter + `Retry-After` (16 attempts, 600 s cap) or
   429 storms kill runs.
5. **Multi-select arrays contain `null`/`[]`.** Drop nulls, accept `[]`, or whole
   columns silently fall back to text.
6. **Lookup/rollup columns (`X (from Y)`) are read-only reverse sides.** Exclude
   them from BOTH link and select classification or you get bogus link fields
   and duplicate-target errors.
7. **Teable rejects duplicate ids in one link cell.** Lookup arrays can repeat
   an id — dedupe (`dict.fromkeys`) before PATCH or bulk updates fail.
8. **Attachment loader reads the same CSVs** — it needs the same
   `field_size_limit` fix.
9. **Bulk link PATCH can 408** on the heaviest tables. Re-run the base (import
   is per-base; delete the partial Teable base first) or shrink the batch.

## Classification rules

- `id` column: kept as `singleLineText` named `id` (traceability).
- Primary: first column after `id` → Teable primary, so links show names.
- **Link:** name lacks `(from`; every non-empty cell is a JSON array of bare
  `rec…` ids (`^rec[A-Za-z0-9]{12,20}$`) ALL resolving to exactly one table in
  the base. Ids deduped before write.
- **Multi-select:** every non-empty cell parses to plain strings (not rec-ids,
  not `__` filenames); name lacks `(from`; ≤100 distinct choices; not mostly
  numeric (excludes year/date rollups).
- Everything else → `singleLineText`.

## Pipeline order (per base)

1. Parse CSVs → `rec_id → {tables}` map. 2. Classify columns. 3. Create base.
4. Create tables (name-primary + id + text + selects), clear seeds, bulk-create
   records (`airtableId → teableId`). 5. Create link fields (all targets exist
   now), bulk-PATCH link values.

## Import decisions (defaults)

- End goal: attachments live **inside Teable** as real attachment fields.
- Incremental method: import → attach → prune source, smallest-first (disk
  guard: stop below 400 MB free; defer a base whose CSV data won't fit + margin).
- Only unambiguous intra-base links become real links (~98%); ambiguous
  (lookup/cross-base/mixed) stay text.
- Importer is NOT idempotent (delete a partial base before retry);
  attachment loader IS idempotent (reuses fields, skips uploaded names).

## Disk math

Uploading relocates bytes (source file deleted as the volume grows), so the
net cost of a base is its CSV data + DB bloat — not its attachment size.
`--prune` deletes each file right after its upload verifies, keeping the
transfer buffer bounded; uploads stream in ≤1 MiB chunks (bounded RAM).

Two orchestrators cover both disk situations: `migrate` runs import → attach
→ prune over an already-downloaded `backup/` (smallest-first, disk guards);
`sync` streams download → import → attach → prune one base at a time, so peak
disk stays ~one base instead of the whole estate. Both resume from their state
file and only mark a base done after its source folder is gone.
