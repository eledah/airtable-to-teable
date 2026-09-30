"""Filesystem + CSV helpers shared by every pipeline stage (stdlib only)."""

from __future__ import annotations

import csv
import json
import os
import re
import sys

# Large long-text cells exceed CPython's 128 KB csv default. Must be raised in
# every module that reads or writes the backup CSVs.
csv.field_size_limit(min(2**31 - 1, sys.maxsize))

_SANITIZE_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def sanitize(name: object) -> str:
    """Filesystem-safe folder/file fragment (mirrors Airtable downloader)."""
    s = _SANITIZE_RE.sub("_", str(name)).strip()
    return s or "unnamed"


def clean_name(name: object) -> str:
    """Human display name derived from a sanitized file stem."""
    name = re.sub(r"_+", "_", str(name).strip())
    name = re.sub(r"\s+", " ", name).strip(" _")
    return name or "table"


def truncate_utf8(s: str, max_bytes: int) -> str:
    """Truncate to max_bytes of UTF-8 without splitting a code point."""
    if max_bytes <= 0:
        return ""
    if len(s.encode("utf-8")) <= max_bytes:
        return s
    b = s.encode("utf-8")[:max_bytes]
    while b and (b[-1] & 0xC0) == 0x80:
        b = b[:-1]
    return b.decode("utf-8", "ignore")


def make_stored_name(rec_id: str, field: str, fname: str) -> str:
    """``rec__field__file.ext`` name that fits ext4's 255-byte NAME_MAX.

    Keeps the ``rec__field__`` prefix (used later to recognise attachment
    cells) and the extension, truncating the middle original-filename part.
    """
    fname = sanitize(fname)
    stem, ext = os.path.splitext(fname)
    prefix = f"{rec_id}__{sanitize(field)}__"
    ext = truncate_utf8(ext, 11)
    room = 255 - len(prefix.encode("utf-8")) - len(ext.encode("utf-8"))
    if room < 1:
        ext, room = "", 255 - len(prefix.encode("utf-8"))
    return prefix + truncate_utf8(stem, room) + ext


def count_csv_rows(path: str) -> int:
    """Data rows (excl. header), correctly handling embedded newlines."""
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.reader(f)
            next(reader, None)
            return sum(1 for _ in reader)
    except (OSError, UnicodeDecodeError):
        return 0


def read_csv_rows(path: str):
    """Return ``(columns, rows)``; empty-string normalised, BOM-tolerant."""
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        cols = reader.fieldnames or []
        rows = [{k: (v if v is not None else "") for k, v in r.items()} for r in reader]
    return cols, rows


def find_csvs(base_dir: str) -> list[str]:
    return [
        os.path.join(base_dir, f)
        for f in sorted(os.listdir(base_dir))
        if f.endswith(".csv") and os.path.isfile(os.path.join(base_dir, f))
    ]


def load_manifest(manifest_path: str) -> dict:
    """``{folder_basename: {"name": real_base, "tables": {sanitized: real}}}``."""
    result: dict = {}
    try:
        with open(manifest_path, encoding="utf-8") as f:
            for entry in json.load(f):
                bdir = os.path.basename(entry.get("dir") or "")
                tables = {
                    sanitize(t.get("table", "")): t.get("table")
                    for t in entry.get("tables", [])
                }
                result[bdir] = {"name": entry.get("base", ""), "tables": tables}
    except (OSError, ValueError, TypeError):
        pass
    return result


def load_saved_counts(manifest_path: str) -> dict:
    """``{table_id: record_count}`` from a previous downloader manifest."""
    counts: dict = {}
    try:
        with open(manifest_path, encoding="utf-8") as f:
            for entry in json.load(f):
                for t in entry.get("tables", []):
                    if t.get("table_id"):
                        counts[t["table_id"]] = t["records"]
    except (OSError, ValueError, TypeError):
        pass
    return counts


def write_manifest_atomic(manifest_path: str, manifest: list) -> None:
    """Atomic manifest write (temp file + rename) so interrupts can't corrupt it."""
    tmp = manifest_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    os.replace(tmp, manifest_path)


def base_and_table_names(folder: str, filename: str, manifest: dict) -> tuple[str, str]:
    """Real (unsanitized) base/table names for a CSV file."""
    info = manifest.get(folder, {})
    bname = info.get("name") or clean_name(folder)
    tables = info.get("tables", {})
    stem = os.path.splitext(os.path.basename(filename))[0]
    if "__" in stem:
        key = stem.split("__", 1)[1]
        tname = tables.get(key) or clean_name(key)
    else:
        tname = next(iter(tables.values())) if len(tables) == 1 else clean_name(folder)
    return bname, tname
