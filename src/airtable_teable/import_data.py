"""Stage 2 — CSV backup -> Teable (data + real links + multi-selects).

Order per base (links need all tables + records first):
  1. create base, 2. create tables (text/selects only), 3. create records,
  4. add link fields (``isOneWay`` manyMany), 5. bulk-set link values.

See docs/MIGRATION_NOTES.md for the Teable quirks this handles.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

from . import config, teable
from .utils import base_and_table_names, find_csvs, load_manifest, read_csv_rows

BARE_REC = re.compile(r"^rec[A-Za-z0-9]{12,20}$")


def classify_link_columns(rows: list[dict], rec_tables: dict) -> dict:
    """``{column: target_csv_fname}`` for clean intra-base record links.

    A column qualifies when every non-empty cell is an array of bare ``rec…``
    ids and ALL ids resolve to exactly one other table in the base.
    Lookup/rollup columns (``X (from Y)``), attachment arrays, cross-base and
    mixed columns stay text.
    """
    if not rows:
        return {}
    link_cols = {}
    for col in rows[0].keys():
        if col == "id":
            continue
        if "(from" in col.lower():
            continue
        all_arrays, targets, unresolved, has_value = True, set(), False, False
        for r in rows:
            v = r.get(col)
            if not v:
                continue
            v = str(v).strip()
            if not v:
                continue
            has_value = True
            if not (v.startswith('["') and "rec" in v):
                all_arrays = False
                break
            try:
                arr = json.loads(v)
            except Exception:
                all_arrays = False
                break
            if not (isinstance(arr, list) and arr and
                    all(isinstance(x, str) and BARE_REC.match(x) for x in arr)):
                all_arrays = False
                break
            for x in arr:
                t = rec_tables.get(x)
                if t is None:
                    unresolved = True
                else:
                    targets.update(t)
        if has_value and all_arrays and not unresolved and len(targets) == 1:
            link_cols[col] = next(iter(targets))
    return link_cols


def parse_str_array(v) -> list | None:
    """Parse a CSV cell into ``list[str]`` or ``None`` (not a string array).

    Drops Airtable's ``null`` elements; ``[]`` is a valid empty value.
    """
    v = str(v).strip()
    if not (v.startswith("[") and v.endswith("]")):
        return None
    try:
        arr = json.loads(v)
    except Exception:
        return None
    if isinstance(arr, list) and all(x is None or isinstance(x, str) for x in arr):
        return [x for x in arr if x is not None]
    return None


def classify_multiselect_columns(rows: list[dict]) -> dict:
    """``{column: [choices]}`` for genuine tag-like multi-select columns.

    Excludes lookup/rollup (``X (from Y)``), link id arrays, attachment
    filename arrays, >100 choices (Airtable's cap) and predominantly-numeric
    columns (year/date rollups).
    """
    if not rows:
        return {}
    sel = {}
    for col in rows[0].keys():
        if col == "id":
            continue
        if "(from" in col.lower():
            continue
        all_arrays, has_value, choices = True, False, set()
        for r in rows:
            v = r.get(col)
            if not v:
                continue
            arr = parse_str_array(v)
            if arr is None:
                all_arrays = False
                break
            has_value = True
            for x in arr:
                if BARE_REC.match(x) or "__" in x:
                    all_arrays = False
                    break
                choices.add(x)
            if not all_arrays:
                break
        if not (has_value and all_arrays and choices and len(choices) <= 100):
            continue
        numeric = sum(1 for c in choices if isinstance(c, str) and c.strip().lstrip("+-").isdigit())
        if numeric > 0.5 * len(choices):
            continue
        sel[col] = sorted(choices)
    return sel


def import_base(teable_url, token, space_id, folder, bdir, manifest, batch, do_links):
    csvs = find_csvs(bdir)
    tables = []
    for c in csvs:
        bname, tname = base_and_table_names(folder, os.path.basename(c), manifest)
        cols, rows = read_csv_rows(c)
        aids = [r.get("id", "") for r in rows]
        tables.append({"fname": os.path.basename(c), "tname": tname,
                       "cols": cols, "rows": rows, "aids": aids})

    rec_tables: dict = {}
    for t in tables:
        for aid in t["aids"]:
            if aid:
                rec_tables.setdefault(aid, set()).add(t["fname"])

    total_links = 0
    for t in tables:
        t["link_cols"] = classify_link_columns(t["rows"], rec_tables)
        t["sel_cols"] = classify_multiselect_columns(t["rows"])
        t["name_col"] = next((c for c in t["cols"] if c != "id"), None)
        t["text_cols"] = [c for c in t["cols"]
                          if c != "id" and c != t["name_col"]
                          and c not in t["link_cols"] and c not in t["sel_cols"]]
        total_links += len(t["link_cols"])

    bname = manifest.get(folder, {}).get("name") or folder
    base = teable.create_base(teable_url, token, space_id, bname)
    base_id = base["id"]
    print(f"[base] {bname} -> {base_id} ({len(tables)} tables, {total_links} links)", flush=True)

    for t in tables:  # Phase A: tables + records
        field_specs = []
        if t["name_col"]:
            field_specs.append({"type": "singleLineText", "name": t["name_col"]})
        field_specs.append({"type": "singleLineText", "name": "id"})
        field_specs += [{"type": "singleLineText", "name": c} for c in t["text_cols"]]
        field_specs += [{"type": "multipleSelect", "name": c,
                         "options": {"choices": [{"name": x} for x in t["sel_cols"][c]]}}
                        for c in t["sel_cols"]]
        table = teable.create_table(teable_url, token, base_id, t["tname"], field_specs)
        t["tid"] = table["id"]
        teable.clear_seed_records(teable_url, token, t["tid"])
        records = []
        for r in t["rows"]:
            f = {}
            if t["name_col"]:
                f[t["name_col"]] = r.get(t["name_col"], "") or ""
            f["id"] = r.get("id", "") or ""
            for c in t["text_cols"]:
                f[c] = r.get(c, "") or ""
            for c in t["sel_cols"]:
                arr = parse_str_array(r.get(c, ""))
                f[c] = arr if arr else []
            records.append({"fields": f})
        ids = teable.create_records(teable_url, token, t["tid"], records, batch)
        t["id_map"] = dict(zip(t["aids"], ids))
        print(f"  [table] {t['tname']}: {len(ids)} records -> {t['tid']}"
              + (f" ({len(t['link_cols'])} link fields pending)" if t["link_cols"] else ""),
              flush=True)

    if not do_links:
        return

    tid_by_fname = {t["fname"]: t for t in tables}
    global_idmap: dict = {}
    for t in tables:
        global_idmap.update(t["id_map"])

    for t in tables:  # Phase B: link fields + values
        if not t["link_cols"]:
            continue
        for col, target_fname in t["link_cols"].items():
            target = tid_by_fname.get(target_fname)
            if target is None:
                print(f"    !! skip link {t['tname']}.{col}: target table missing", flush=True)
                continue
            teable.create_field_link(teable_url, token, t["tid"], col, target["tid"])
            print(f"    [link] {t['tname']}.{col} -> {target['tname']}", flush=True)
        updates = []
        for idx, r in enumerate(t["rows"]):
            teable_rid = t["id_map"].get(t["aids"][idx])
            if not teable_rid:
                continue
            fields = {}
            for col in t["link_cols"]:
                v = r.get(col)
                if not v:
                    continue
                try:
                    arr = json.loads(str(v).strip())
                except Exception:
                    continue
                if not (isinstance(arr, list) and arr):
                    continue
                target_ids = [global_idmap[x] for x in arr if global_idmap.get(x)]
                target_ids = list(dict.fromkeys(target_ids))  # Teable rejects dupes
                if target_ids:
                    fields[col] = [{"id": i} for i in target_ids]
            if fields:
                updates.append((teable_rid, fields))
        if updates:
            n = teable.update_records(teable_url, token, t["tid"], updates, batch)
            print(f"    [link values] {t['tname']}: updated {n} records", flush=True)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Import Airtable CSVs into Teable (data + links)")
    ap.add_argument("--token", default="", help="Teable PAT (default: $TEABLE_TOKEN / .teable_token)")
    ap.add_argument("--token-file", default="", help="path to file holding the Teable PAT")
    ap.add_argument("--teable-url", default="", help="Teable base URL (default: $TEABLE_API_URL)")
    ap.add_argument("--out", required=True, help="root of the downloader output")
    ap.add_argument("--space", default="", help="spaceId (default: first space)")
    ap.add_argument("--bases", default="", help="comma-separated base folder names (default: all)")
    ap.add_argument("--batch", type=int, default=config.TEABLE_BATCH)
    ap.add_argument("--no-links", action="store_true", help="import data only, skip links")
    ap.add_argument("--dry-run", action="store_true", help="print plan without calling the API")
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    teable_url = config.resolve_teable_url(args.teable_url)
    token = config.resolve_teable_token(args.token, args.token_file)
    if not token and not args.dry_run:
        print("error: no Teable token (pass --token or set $TEABLE_TOKEN)")
        return 2
    if not os.path.isdir(args.out):
        print(f"error: {args.out} is not a directory")
        return 2

    manifest = load_manifest(os.path.join(args.out, "manifest.json"))
    if manifest:
        print(f"Using manifest.json for base/table names ({len(manifest)} bases).")
    else:
        print("No manifest.json found — using cleaned filenames for names.")

    space_id = args.space
    if not args.dry_run and not space_id:
        spaces = teable.list_spaces(teable_url, token)
        if not spaces:
            print("error: no spaces accessible with this token")
            return 1
        space_id = spaces[0]["id"]
        print(f"Using space: {spaces[0]['name']} ({space_id}) [Teable: {teable_url}]")

    base_dirs = sorted(d for d in os.listdir(args.out)
                       if os.path.isdir(os.path.join(args.out, d)))
    if args.bases:
        want = {x.strip() for x in args.bases.split(",") if x.strip()}
        base_dirs = [d for d in base_dirs if d in want]
    base_dirs = [d for d in base_dirs if find_csvs(os.path.join(args.out, d))]

    print(f"Plan: {len(base_dirs)} bases to import into space {space_id}"
          + ("" if args.no_links else " (with links)"))

    created, failed = 0, []
    for b in base_dirs:
        bdir = os.path.join(args.out, b)
        if args.dry_run:
            n = sum(len(read_csv_rows(c)[1]) for c in find_csvs(bdir))
            print(f"  [dry] base '{b}': {len(find_csvs(bdir))} table(s), {n} records")
            continue
        try:
            import_base(teable_url, token, space_id, b, bdir, manifest,
                        args.batch, not args.no_links)
            created += 1
        except Exception as e:
            print(f"  !! FAILED base {b}: {e}", flush=True)
            failed.append(b)

    print(f"\nDone. Imported {created} bases.")
    if args.dry_run:
        print("(dry run — nothing was created)")
    if failed:
        print(f"FAILED ({len(failed)}): {', '.join(failed)}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
