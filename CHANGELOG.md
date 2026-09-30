# Changelog

All notable changes to this project are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [1.1.0] - 2026-09-30

### Added
- `sync` stage: streaming `download → import → attach → prune`, one base at a
  time, for estates that don't fit on disk alongside the Teable volume.
  Resumable via `sync_state.json` (per-table/per-file resume, idempotent
  attach, partial Teable bases cleaned for retry). `--bases` accepts names or
  IDs; `--keep` / `--no-links` / `--no-attachments` / `--force` / `--dry-run`.
- `tests/test_sync.py` (offline planner tests).
- `sync_state.json` / `sync_run.log` git-ignored.

## [1.0.0] - 2026-09-30

### Added
- Initial public release, distilled from a production migration (~62 bases /
  ~490k records / ~2.4M link cells / ~117k attachments).
- Stages: `download` (Airtable → CSV + files + manifest, resumable),
  `import` (CSVs → Teable with real `isOneWay` links + multi-selects),
  `attach` (files → real attachment fields, idempotent, `--prune`),
  `migrate` (orchestrates import → attach → prune, smallest-first, disk guards),
  `export` (Teable → SQLite, text only).
- Unified CLI (`airtable-teable <stage>`, `python -m airtable_teable`), env+flag
  config (`$AIRTABLE_TOKEN`, `$TEABLE_TOKEN`, `$TEABLE_API_URL`), offline test
  suite, `docs/MIGRATION_NOTES.md` (9 Teable quirks + classification rules).
