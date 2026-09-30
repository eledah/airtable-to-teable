"""Optional Stage 5 — export a Teable base to SQLite (TEXT only, no binaries).

Conventions: links -> joined display titles, attachments -> joined filenames,
multi-selects -> joined choices, everything else -> raw text. One SQLite table
per Teable table plus a ``__meta`` table.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from . import config, teable


def quote_ident(s: str) -> str:
    return '"' + s.replace('"', '""') + '"'


def ser_value(v, ftype: str) -> str:
    if v is None:
        return ""
    if isinstance(v, list):
        if not v:
            return ""
        if ftype in ("link", "attachment"):
            parts = []
            for item in v:
                if isinstance(item, dict):
                    parts.append(item.get("title") or item.get("name")
                                 or item.get("id") or "")
                else:
                    parts.append(str(item))
            return "; ".join(p for p in parts if p)
        return "; ".join(str(x) for x in v if x is not None)
    if isinstance(v, dict):
        return json.dumps(v, ensure_ascii=False)
    return str(v)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Export a Teable base to SQLite (text only)")
    ap.add_argument("base_id", help="Teable base id (e.g. teab...)")
    ap.add_argument("out_sqlite", help="output .sqlite path")
    ap.add_argument("--token", default="")
    ap.add_argument("--token-file", default="")
    ap.add_argument("--teable-url", default="")
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    teable_url = config.resolve_teable_url(args.teable_url)
    token = config.resolve_teable_token(args.token, args.token_file)
    if not token:
        print("error: no Teable token (pass --token or set $TEABLE_TOKEN)")
        return 2
    if os.path.exists(args.out_sqlite):
        os.remove(args.out_sqlite)

    tables = teable.list_tables(teable_url, token, args.base_id)
    print(f"Base has {len(tables)} tables")

    conn = sqlite3.connect(args.out_sqlite)
    cur = conn.cursor()

    base_name = args.base_id
    try:
        meta = teable.api("GET", teable_url, f"/api/base/{args.base_id}", token)
        base_name = meta.get("name", args.base_id) if isinstance(meta, dict) else args.base_id
    except Exception:
        pass

    cur.execute("CREATE TABLE __meta (key TEXT PRIMARY KEY, value TEXT)")
    cur.execute("INSERT INTO __meta VALUES ('base_id', ?), ('base_name', ?), ('exported_at', ?)",
                (args.base_id, base_name, time.strftime("%Y-%m-%d %H:%M:%S")))

    summary = []
    for t in sorted(tables, key=lambda x: x.get("order", 0)):
        tname, tid = t["name"], t["id"]
        fields = teable.list_fields(teable_url, token, tid)
        seen: set = set()
        cols = [("__teable_id", "singleLineText")]
        for f in fields:
            fname = f["name"]
            key = fname
            n = 1
            while key.lower() in seen:
                n += 1
                key = f"{fname}__{n}"
            seen.add(key.lower())
            cols.append((key, f.get("type", "")))

        col_defs = ",\n  ".join(f"{quote_ident(c)} TEXT" for c, _ in cols)
        cur.execute(f"CREATE TABLE {quote_ident(tname)} (\n  {col_defs}\n)")
        print(f"  [table] {tname}: {len(cols)} columns")

        ftypes = dict(cols)
        n = 0
        skip = 0
        while True:
            d = teable.api("GET", teable_url, f"/api/table/{tid}/record", token,
                           params={"take": 1000, "skip": skip, "fieldKeyType": "name"})
            recs = d.get("records", [])
            for r in recs:
                fvals = r.get("fields", {})
                row = [r.get("id", "") if c == "__teable_id"
                       else ser_value(fvals.get(c), ftypes[c]) for c, _ in cols]
                cur.execute(f"INSERT INTO {quote_ident(tname)} VALUES "
                            f"({','.join('?' for _ in row)})", row)
                n += 1
            if len(recs) < 1000:
                break
            skip += 1000
            time.sleep(config.TEABLE_SLEEP)
        conn.commit()
        summary.append((tname, n, len(cols)))
        print(f"       -> {n} records")

    conn.close()
    print("\n=== SUMMARY ===")
    for tname, n, c in summary:
        print(f"  {tname}: {n} records, {c} columns")
    print(f"\nExported to {args.out_sqlite}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
