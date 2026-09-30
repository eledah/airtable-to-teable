"""Stage 3 — attach real files to already-imported Teable tables.

Replaces each placeholder text column (JSON array of ``rec__field__file``
names) with a real ``attachment`` field and streams every referenced file via
``uploadAttachment``. Idempotent: reuses existing attachment fields and skips
files already present by name. ``--prune`` deletes each source file right
after its upload verifies (bounded disk buffer) and removes the base folder.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import sys
import time

from . import config, teable
from .utils import sanitize

STORED_NAME = re.compile(r"^rec\w+__.*__.+")


def parse_filenames(v) -> list:
    """Stored filenames from a CSV cell, or ``[]`` if not attachment-like."""
    v = str(v).strip()
    if not (v.startswith("[") and v.endswith("]")):
        return []
    try:
        arr = json.loads(v)
    except Exception:
        return []
    if not (isinstance(arr, list) and arr and
            all(isinstance(x, str) and STORED_NAME.match(x) for x in arr)):
        return []
    return arr


def find_base_folder(out: str, base_name: str):
    """Backup folder for a base (exact, case-insensitive, then via manifest)."""
    for exact in (True, False):
        for d in os.listdir(out):
            if not os.path.isdir(os.path.join(out, d)):
                continue
            hit = (d == base_name) if exact else (d.lower() == base_name.lower())
            if hit:
                return os.path.join(out, d)
    try:
        with open(os.path.join(out, "manifest.json"), encoding="utf-8") as f:
            for e in json.load(f):
                bdir = os.path.basename(e.get("dir") or "")
                if (e.get("base") or "") == base_name and bdir:
                    return os.path.join(out, bdir)
    except (OSError, ValueError):
        pass
    return None


def _table_name_map(out: str, folder: str) -> dict:
    mapping = {}
    try:
        with open(os.path.join(out, "manifest.json"), encoding="utf-8") as f:
            for e in json.load(f):
                if os.path.basename(e.get("dir") or "") == folder:
                    for t in e.get("tables", []):
                        mapping[sanitize(t.get("table", ""))] = t.get("table", "")
                    break
    except (OSError, ValueError):
        pass
    return mapping


def _resolve_teable_base(teable_url: str, token: str, base_name: str):
    bases = teable.list_bases(teable_url, token)
    tb = next((x for x in bases if x.get("name") == base_name), None)
    if not tb:
        tb = next((x for x in bases
                   if (x.get("name") or "").strip() == base_name.strip()), None)
    return tb


def process_base(teable_url: str, token: str, out: str, base_name: str, prune: bool) -> None:
    bdir = find_base_folder(out, base_name)
    if not bdir:
        print(f"!! no folder for base '{base_name}' in {out}", flush=True)
        return
    tb = _resolve_teable_base(teable_url, token, base_name)
    if not tb:
        print(f"!! no Teable base named '{base_name}'", flush=True)
        return
    base_id = tb["id"]
    tables = teable.list_tables(teable_url, token, base_id)
    tid_by_name = {t["name"]: t["id"] for t in tables}
    table_name_map = _table_name_map(out, os.path.basename(bdir))

    csvs = [f for f in sorted(os.listdir(bdir)) if f.endswith(".csv")]
    total_uploaded, total_cols = 0, 0
    for csvname in csvs:
        stem = os.path.splitext(csvname)[0]
        if "__" in stem:
            key = stem.split("__", 1)[1]
            tname = table_name_map.get(key, key)
        else:
            tname = (next(iter(table_name_map.values()))
                     if len(table_name_map) == 1 else os.path.basename(bdir))
        tid = tid_by_name.get(tname)
        if not tid:
            print(f"  !! table '{tname}' not found in Teable", flush=True)
            continue
        with open(os.path.join(bdir, csvname), encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        att_cols = []
        if rows:
            for col in rows[0].keys():
                if col == "id":
                    continue
                if any(parse_filenames(r.get(col)) for r in rows):
                    att_cols.append(col)
        if not att_cols:
            continue
        teable_recs = teable.list_all_record_dicts(teable_url, token, tid)
        teable_by_aid = {rec.get("fields", {}).get("id"): rec["id"]
                         for rec in teable_recs if rec.get("fields", {}).get("id")}
        fields = teable.list_fields(teable_url, token, tid)
        att_dir = os.path.join(bdir, os.path.basename(bdir) + "_attachments", tname)

        for col in att_cols:
            old = next((f for f in fields if f["name"] == col), None)
            existing_att = next((f for f in fields
                                 if f["name"] == col and f["type"] == "attachment"), None)
            if existing_att:
                new_id = existing_att["id"]
            else:
                if old:
                    teable.delete_field(teable_url, token, tid, old["id"])
                new_field = teable.create_field(
                    teable_url, token, tid, {"type": "attachment", "name": col})
                new_id = new_field["id"]
                fields.append({"name": col, "id": new_id})
            total_cols += 1
            have: dict = {}
            for rec in teable.list_all_record_dicts(teable_url, token, tid):
                aid = rec.get("fields", {}).get("id")
                if aid and rec["id"] in teable_by_aid.values():
                    att = rec["fields"].get(col)
                    if isinstance(att, list):
                        have[rec["id"]] = {a.get("name") for a in att if isinstance(a, dict)}
            n_uploaded, n_skipped = 0, 0
            for r in rows:
                fnames = parse_filenames(r.get(col))
                if not fnames:
                    continue
                rid = teable_by_aid.get(r.get("id"))
                if not rid:
                    continue
                cur = have.setdefault(rid, set())
                for fn in fnames:
                    if fn in cur:
                        n_skipped += 1
                        continue
                    fp = os.path.join(att_dir, fn)
                    if not os.path.exists(fp):
                        print(f"    !! missing file {fn}", flush=True)
                        continue
                    try:
                        teable.upload_attachment(teable_url, token, tid, rid, new_id, fp)
                        n_uploaded += 1
                        cur.add(fn)
                        if prune:
                            try:
                                os.remove(fp)
                            except OSError as e:
                                print(f"    !! could not remove {fn}: {e}", flush=True)
                    except Exception as e:
                        print(f"    !! upload failed {fn}: {e}", flush=True)
                    time.sleep(config.TEABLE_SLEEP)
            total_uploaded += n_uploaded
            print(f"  [att] {tname}.{col}: uploaded {n_uploaded}, skipped {n_skipped}"
                  f" (already present) -> {new_id}", flush=True)

    print(f"[base] {base_name}: {total_cols} attachment columns, {total_uploaded} files uploaded",
          flush=True)
    if prune:
        shutil.rmtree(bdir)
        print(f"       pruned source folder {bdir}", flush=True)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Add attachments to imported Teable bases")
    ap.add_argument("--token", default="", help="Teable PAT (default: $TEABLE_TOKEN)")
    ap.add_argument("--token-file", default="")
    ap.add_argument("--teable-url", default="", help="Teable base URL (default: $TEABLE_API_URL)")
    ap.add_argument("--out", required=True, help="backup root")
    ap.add_argument("--bases", default="", help="comma-separated base names")
    ap.add_argument("--all", action="store_true", help="process every base folder")
    ap.add_argument("--prune", action="store_true",
                    help="delete source folder after successful upload")
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    teable_url = config.resolve_teable_url(args.teable_url)
    token = config.resolve_teable_token(args.token, args.token_file)
    if not token:
        print("error: no Teable token (pass --token or set $TEABLE_TOKEN)")
        return 2
    if args.all:
        bases = sorted(d for d in os.listdir(args.out)
                       if os.path.isdir(os.path.join(args.out, d)))
    else:
        bases = [x.strip() for x in args.bases.split(",") if x.strip()]
    if not bases:
        print("error: specify --bases or --all")
        return 2
    failed = []
    for b in bases:
        try:
            process_base(teable_url, token, args.out, b, args.prune)
        except Exception as e:
            print(f"!! FAILED base {b}: {e}", flush=True)
            failed.append(b)
    if failed:
        print(f"FAILED ({len(failed)}): {', '.join(failed)}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
