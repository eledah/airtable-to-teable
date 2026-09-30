"""Stage 6 — streaming sync: download -> import -> attach -> prune, one base at a time.

This is the disk-frugal loop for estates that don't fit locally alongside the
Teable volume: each base is pulled from Airtable, imported into Teable, given
real attachment fields, and its source folder deleted before the next base
starts. Peak disk stays ~one base, not the whole estate.

Resumable: ``--state`` records a base only after its source folder is gone
(or after verified attach with ``--keep``). Interrupted runs resume safely —
completed tables/files are skipped by row-count/size, the attachment loader is
idempotent, and the importer is skipped when the Teable base already exists.

Safety: the importer is NOT idempotent (it creates a new base per run). If an
import fails midway, sync best-effort deletes the partial Teable base so the
next run retries cleanly; nothing is marked done until the folder is pruned.
To redo a finished base, delete its Teable base manually and drop its name
from the state file.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys

from . import airtable, attachments, config, download, import_data, teable
from .migrate import Logger, data_kb, free_disk_kb, load_state, save_state
from .utils import load_manifest, load_saved_counts, sanitize, write_manifest_atomic


def matches_base(base: dict, want: set) -> bool:
    """Match a ``--bases`` entry by Airtable id OR by exact base name."""
    if not want:
        return True
    return base.get("id") in want or base.get("name") in want


def folder_for_base(out: str, base: dict, taken: set) -> str:
    """Flat ``<out>/<sanitized name>/`` dir; suffix on sanitized collision."""
    candidate = sanitize(base.get("name") or base.get("id"))
    if candidate not in taken:
        return os.path.join(out, candidate)
    suffix = (base.get("id") or "")[-4:] or "x"
    candidate = f"{candidate}_{suffix}"
    n = 1
    while candidate in taken:
        n += 1
        candidate = f"{candidate}_{n}"
    return os.path.join(out, candidate)


def upsert_manifest_entry(manifest_path: str, info: dict) -> None:
    """Replace (by base id) or append one base entry, atomically."""
    manifest: list = []
    try:
        with open(manifest_path, encoding="utf-8") as f:
            loaded = json.load(f)
            if isinstance(loaded, list):
                manifest = loaded
    except (OSError, ValueError):
        manifest = []
    manifest = [m for m in manifest if m.get("id") != info.get("id")]
    manifest.append(info)
    write_manifest_atomic(manifest_path, manifest)


def teable_base_by_name(teable_url: str, token: str, name: str):
    for b in teable.list_bases(teable_url, token):
        if b.get("name") == name or (b.get("name") or "").strip() == name.strip():
            return b
    return None


def delete_partial_base(teable_url: str, token: str, name: str, log: Logger) -> None:
    try:
        tb = teable_base_by_name(teable_url, token, name)
        if tb:
            teable.delete_base(teable_url, token, tb["id"])
            log.log(f"   cleaned partial Teable base '{name}' ({tb['id']}) for retry.")
    except Exception as e:
        log.log(f"   !! could not clean partial base '{name}': {e}")


def sync_one_base(air_token: str, teable_url: str, teable_token: str, space_id: str,
                  out: str, base: dict, bdir: str, args, log: Logger) -> bool:
    """Download -> import -> attach -> prune for a single base. Returns done."""
    bname = base["name"]
    manifest_path = os.path.join(out, "manifest.json")

    # 1) Download (resumable unless --force).
    saved_counts = {} if args.force else load_saved_counts(manifest_path)
    try:
        info = download.dump_base(air_token, base, bdir,
                                  not args.no_attachments, saved_counts, args.force)
    except Exception as e:
        log.log(f"   !! download failed '{bname}': {e}")
        return False
    if info.get("error"):
        log.log(f"   !! download incomplete '{bname}': {info['error']}")
        return False
    info["workspace"] = None
    info["dir"] = bdir
    upsert_manifest_entry(manifest_path, info)

    # Per-base guard: the CSV data + DB bloat must fit with a safety margin.
    # (Attachments relocate bytes, so only data counts — same rule as migrate.)
    if data_kb(bdir) > free_disk_kb() - config.SAFETY_KB:
        log.log(f"   !! data won't fit (free {free_disk_kb() // 1024}MB) — "
                f"leaving '{bname}' downloaded for later; stopping.")
        return False

    # 2) Import (skip when the Teable base already exists — importer isn't idempotent).
    if teable_base_by_name(teable_url, teable_token, bname):
        log.log(f"   [import] '{bname}' already in Teable — skipping data import.")
    else:
        try:
            manifest = load_manifest(manifest_path)
            import_data.import_base(teable_url, teable_token, space_id,
                                    os.path.basename(bdir), bdir, manifest,
                                    args.batch, not args.no_links)
        except Exception as e:
            log.log(f"   !! import failed '{bname}': {e}")
            delete_partial_base(teable_url, teable_token, bname, log)
            return False
        if not teable_base_by_name(teable_url, teable_token, bname):
            log.log(f"   !! import did not create base '{bname}'; will retry.")
            return False

    # 3) Attachments (+ prune).
    if args.no_attachments:
        if not args.keep:
            shutil.rmtree(bdir, ignore_errors=True)
        return True if args.keep or not os.path.isdir(bdir) else False
    try:
        attachments.process_base(teable_url, teable_token, out, bname, prune=not args.keep)
    except Exception as e:
        log.log(f"   !! attachments failed '{bname}': {e}")
        return False
    if args.keep:
        return True
    return not os.path.isdir(bdir)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Stream Airtable -> Teable one base at a time (download+import+attach+prune)")
    ap.add_argument("--token", default="", help="Airtable PAT (default: $AIRTABLE_TOKEN)")
    ap.add_argument("--teable-token", default="", help="Teable PAT (default: $TEABLE_TOKEN)")
    ap.add_argument("--token-file", default="", help="file holding the Teable PAT")
    ap.add_argument("--teable-url", default="", help="Teable base URL (default: $TEABLE_API_URL)")
    ap.add_argument("--out", default="", help="backup root (default: $BACKUP_DIR or ./backup)")
    ap.add_argument("--space", default="", help="Teable spaceId (default: first space)")
    ap.add_argument("--bases", default="",
                    help="comma-separated Airtable base names or IDs (default: all)")
    ap.add_argument("--batch", type=int, default=config.TEABLE_BATCH)
    ap.add_argument("--no-links", action="store_true", help="import data only, skip links")
    ap.add_argument("--no-attachments", action="store_true", help="skip file download/upload")
    ap.add_argument("--keep", action="store_true", help="keep source folders (debug; no prune)")
    ap.add_argument("--force", action="store_true", help="force re-download (ignore resume)")
    ap.add_argument("--state", default="", help="state file (default: ./sync_state.json)")
    ap.add_argument("--log", default="", help="run log (default: ./sync_run.log)")
    ap.add_argument("--min-free-kb", type=int, default=config.MIN_FREE_KB)
    ap.add_argument("--dry-run", action="store_true", help="list bases, do nothing")
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    out = args.out or os.environ.get("BACKUP_DIR", os.path.join(os.getcwd(), "backup"))
    state_path = args.state or os.path.join(os.getcwd(), "sync_state.json")
    log = Logger(args.log or os.path.join(os.getcwd(), "sync_run.log"))

    air_token = config.resolve_airtable_token(args.token)
    if not air_token and not args.dry_run:
        print("error: no Airtable token (pass --token or set $AIRTABLE_TOKEN)")
        return 2
    teable_url = config.resolve_teable_url(args.teable_url)
    teable_token = config.resolve_teable_token(args.teable_token, args.token_file)
    if not teable_token and not args.dry_run:
        print("error: no Teable token (pass --teable-token or set $TEABLE_TOKEN)")
        return 2
    os.makedirs(out, exist_ok=True)

    if args.dry_run:
        bases = airtable.list_bases(air_token) if air_token else []
        want = {x.strip() for x in args.bases.split(",") if x.strip()}
        bases = [b for b in bases if matches_base(b, want)]
        done = load_state(state_path)
        for b in bases:
            mark = "done" if b["name"] in done else "pending"
            log.log(f"  [dry] {b['name']} ({b['id']}) — {mark}")
        log.log(f"Dry run: {len(bases)} bases, {len(done)} done. Nothing downloaded.")
        return 0

    done = load_state(state_path)
    log.log(f"Sync start. Done: {len(done)} (Teable: {teable_url}, out: {out})")

    try:
        bases = airtable.list_bases(air_token)
    except Exception as e:
        log.log(f"!! could not list Airtable bases: {e}")
        return 1
    want = {x.strip() for x in args.bases.split(",") if x.strip()}
    bases = [b for b in bases if matches_base(b, want)]
    log.log(f"Found {len(bases)} bases to consider.")

    try:
        spaces = teable.list_spaces(teable_url, teable_token)
        space_id = args.space or (spaces[0]["id"] if spaces else "")
        if not space_id:
            log.log("error: no spaces accessible with this token")
            return 1
        log.log(f"Using space: {(spaces[0]['name'] if spaces and not args.space else space_id)}")
    except Exception as e:
        log.log(f"error: could not resolve Teable space: {e}")
        return 1

    taken = {d for d in os.listdir(out) if os.path.isdir(os.path.join(out, d))}
    processed_ok = 0
    for i, base in enumerate(bases, 1):
        bname = base["name"]
        if bname in done:
            continue
        log.log(f"\n===== [{i}/{len(bases)}] {bname} ({base['id']}) =====")
        if free_disk_kb() < args.min_free_kb:
            log.log(f"!! FREE DISK {free_disk_kb() // 1024}MB < guard "
                    f"{args.min_free_kb // 1024}MB — stopping.")
            break
        # Reuse an existing (resumed) folder if the manifest already knows it,
        # else allocate a fresh flat dir.
        bdir = None
        try:
            with open(os.path.join(out, "manifest.json"), encoding="utf-8") as f:
                for e in json.load(f):
                    if e.get("id") == base["id"] and e.get("dir"):
                        cand = e["dir"] if os.path.isabs(e["dir"]) \
                            else os.path.join(out, os.path.basename(e["dir"]))
                        if os.path.isdir(cand):
                            bdir = cand
                            break
        except (OSError, ValueError):
            pass
        if bdir is None:
            bdir = folder_for_base(out, base, taken)
            taken.add(os.path.basename(bdir))
        ok = sync_one_base(air_token, teable_url, teable_token, space_id,
                           out, base, bdir, args, log)
        if ok and (args.keep or not os.path.isdir(bdir)):
            done.add(bname)
            save_state(state_path, done)
            processed_ok += 1
            log.log(f"✓ {bname} synced & {'kept' if args.keep else 'pruned'}.")
        else:
            log.log(f"? {bname} incomplete — will resume next run.")

    log.log(f"\nSync pass complete. Synced this run: {processed_ok}. "
            f"Total done: {len(done)}. Free disk: {free_disk_kb() // 1024} MB.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
