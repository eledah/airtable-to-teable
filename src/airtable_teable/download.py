"""Stage 1 — Airtable -> local backup (CSVs + attachment files).

Layout::

    <out>/
      manifest.json
      <Base>/                       (or <Workspace>/<Base>/ with workspace perms)
        <Base>.csv                  (single-table base)
        <Base>__<Table>.csv         (multi-table base)
        <Base>_attachments/<Table>/
          recXXXX__Field__file.ext

Resume: tables whose CSV exists AND whose row count matches the saved manifest
are skipped; attachments matched by size are skipped. ``--force`` re-pulls.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import time

from . import airtable, config
from .utils import (
    count_csv_rows,
    load_saved_counts,
    make_stored_name,
    sanitize,
    write_manifest_atomic,
)


def cell_value(value):
    """Encode a field value for CSV (scalars readable, structures -> JSON)."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return json.dumps(value, ensure_ascii=False)


def download_attachment(token: str, att: dict, att_dir: str, rec_id: str, field: str):
    """Save one attachment. Returns the stored filename (for the CSV cell)."""
    url = att.get("url", "")
    if not url:
        return None
    url += ("&" if "?" in url else "?") + "download=1"
    fname = att.get("filename", "file")
    stored = make_stored_name(rec_id, field, fname)
    dest = os.path.join(att_dir, stored)
    if os.path.exists(dest) and os.path.getsize(dest) == att.get("size", -1):
        return stored  # resume
    time.sleep(config.AIRTABLE_RATE_SLEEP)
    try:
        data = airtable.http_get(url, token, binary=True)
    except Exception as e:
        print(f"    !! attachment FAILED {stored}: {e}", flush=True)
        return None
    with open(dest, "wb") as f:
        f.write(data)
    return stored


def dump_table(token, base_id, table, base_dir, base_name,
               want_attachments, multi_table, saved_counts, force):
    tname, tid = table["name"], table["id"]
    att_fields = {f["name"] for f in table.get("fields", []) if f["type"] == "multipleAttachments"}
    field_order = [f["name"] for f in table.get("fields", [])]

    safe_b = sanitize(base_name)
    safe_t = sanitize(tname)
    csv_path = (os.path.join(base_dir, f"{safe_b}__{safe_t}.csv") if multi_table
                else os.path.join(base_dir, f"{safe_b}.csv"))

    att_dir = os.path.join(base_dir, f"{safe_b}_attachments", safe_t) if want_attachments else None
    if want_attachments:
        os.makedirs(att_dir, exist_ok=True)

    if not force:
        saved = saved_counts.get(tid)
        existing = count_csv_rows(csv_path)
        if saved is not None and existing == saved and existing > 0:
            print(f"  [table] {tname}: SKIP (already complete, {existing} rows)", flush=True)
            return {"table": tname, "table_id": tid, "records": existing, "skipped": True}

    rows, all_fields, count = [], set(field_order), 0
    for rec in airtable.page_records(token, base_id, tid):
        fields = rec.get("fields", {})
        all_fields.update(fields.keys())
        row = {"id": rec["id"]}
        for fname, fval in fields.items():
            if want_attachments and fname in att_fields and isinstance(fval, list):
                names = []
                for att in fval:
                    if isinstance(att, dict) and att.get("url"):
                        nm = download_attachment(token, att, att_dir, rec["id"], fname)
                        if nm:
                            names.append(nm)
                row[fname] = json.dumps(names, ensure_ascii=False) if names else ""
            else:
                row[fname] = cell_value(fval)
        rows.append(row)
        count += 1

    extra = sorted(all_fields - set(field_order) - {"id"})
    columns = ["id"] + field_order + extra
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"  [table] {tname}: {count} rows -> {os.path.basename(csv_path)}", flush=True)
    return {"table": tname, "table_id": tid, "records": count, "skipped": False}


def dump_base(token, base, base_dir, want_attachments, saved_counts, force):
    bid, bname = base["id"], base["name"]
    os.makedirs(base_dir, exist_ok=True)
    print(f"[base] {bname} ({bid})", flush=True)
    try:
        tables = airtable.get_schema(token, bid)
    except Exception as e:
        print(f"  !! FAILED to fetch schema for {bname}: {e}", flush=True)
        return {"base": bname, "id": bid, "tables": [], "records": 0, "error": str(e)}
    multi_table = len(tables) > 1
    tables_out, total = [], 0
    for t in tables:
        try:
            info = dump_table(token, bid, t, base_dir, bname, want_attachments,
                              multi_table, saved_counts, force)
        except Exception as e:
            print(f"  !! FAILED table {t.get('name', '?')}: {e}", flush=True)
            info = {"table": t.get("name", "?"), "table_id": t.get("id"),
                    "records": 0, "skipped": False, "error": str(e)}
        tables_out.append(info)
        total += info["records"]
    return {"base": bname, "id": bid, "tables": tables_out, "records": total}


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Download Airtable data + attachments")
    ap.add_argument("--token", default="", help="Airtable PAT (default: $AIRTABLE_TOKEN)")
    ap.add_argument("--out", required=True, help="root output folder")
    ap.add_argument("--bases", default="", help="comma-separated base IDs to include (default: all)")
    ap.add_argument("--skip-bases", default="", help="comma-separated base IDs to skip")
    ap.add_argument("--no-attachments", action="store_true", help="skip downloading files")
    ap.add_argument("--force", action="store_true", help="ignore resume/skip, re-pull everything")
    ap.add_argument("--workspaces-map", default="", help="JSON file mapping base_id -> workspace name")
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    token = config.resolve_airtable_token(args.token)
    if not token:
        print("error: no Airtable token (pass --token or set $AIRTABLE_TOKEN)")
        return 2
    os.makedirs(args.out, exist_ok=True)
    manifest_path = os.path.join(args.out, "manifest.json")
    saved_counts = {} if args.force else load_saved_counts(manifest_path)
    if saved_counts:
        print(f"Resume: {len(saved_counts)} tables marked complete (skippable).")

    bases = airtable.list_bases(token)
    print(f"Found {len(bases)} bases.")

    include = {x.strip() for x in args.bases.split(",") if x.strip()}
    if include:
        bases = [b for b in bases if b["id"] in include]
        print(f"Filtered to {len(bases)} bases.")

    skip = {x.strip() for x in args.skip_bases.split(",") if x.strip()}
    if skip:
        before = len(bases)
        bases = [b for b in bases if b["id"] not in skip]
        print(f"Skipping {before - len(bases)} bases (--skip-bases).")

    ws_map: dict = {}
    if args.workspaces_map and os.path.exists(args.workspaces_map):
        with open(args.workspaces_map, encoding="utf-8") as f:
            ws_map = json.load(f)
        print("Using workspaces-map override.")
    else:
        ws_map = airtable.list_workspaces(token)
        if ws_map:
            print(f"Grouped by {len(set(ws_map.values()))} workspaces (API).")
        else:
            print("Workspace grouping unavailable (token lacks permission) -> one folder per base.")

    manifest = []
    for b in bases:
        ws = ws_map.get(b["id"])
        bdir = (os.path.join(args.out, sanitize(ws), sanitize(b["name"])) if ws
                else os.path.join(args.out, sanitize(b["name"])))
        try:
            info = dump_base(token, b, bdir, not args.no_attachments, saved_counts, args.force)
        except Exception as e:
            print(f"  !! FAILED base {b['name']}: {e}", flush=True)
            info = {"base": b["name"], "id": b["id"], "tables": [], "records": 0, "error": str(e)}
        info["workspace"] = ws or None
        info["dir"] = bdir
        manifest.append(info)
        write_manifest_atomic(manifest_path, manifest)

    failed = [m for m in manifest if m.get("error")]
    print(f"\nDone. Manifest: {manifest_path}")
    print(f"Total bases: {len(manifest)}  Total records: {sum(m['records'] for m in manifest)}")
    if failed:
        print(f"FAILED ({len(failed)}): " + ", ".join(f"{m['base']} ({m['id']})" for m in failed))
        print("These will be retried on the next run; everything else is marked complete.")
        return 1
    return 0
