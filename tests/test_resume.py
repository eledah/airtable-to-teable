"""Offline tests for downloader resume/skip + attachment filename helpers."""

import csv
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from airtable_teable import download as dl  # noqa: E402
from airtable_teable.attachments import parse_filenames  # noqa: E402
from airtable_teable.utils import make_stored_name, sanitize  # noqa: E402

CALLS = {"schema": 0, "records": 0}


def _fake_schema(token, base_id):
    CALLS["schema"] += 1
    return [
        {"id": "tblT1", "name": "Alpha", "fields": [{"name": "Name", "type": "text"}]},
        {"id": "tblT2", "name": "Beta", "fields": [{"name": "Name", "type": "text"}]},
    ]


def _fake_pages(token, base_id, table_id):
    CALLS["records"] += 1
    for i in range(3):
        yield {"id": f"rec{i}", "fields": {"Name": f"row{i}"}}


dl.airtable.get_schema = _fake_schema
dl.airtable.page_records = _fake_pages


def _write_csv(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "Name"])
        for r in rows:
            w.writerow([r, f"row{r}"])


def _run_case(base_dir):
    global CALLS
    CALLS = {"schema": 0, "records": 0}
    from airtable_teable.utils import load_saved_counts
    saved = load_saved_counts(os.path.join(base_dir, "manifest.json"))
    return dl.dump_base("pat_fake", {"id": "appX", "name": "BaseName"},
                        base_dir, False, saved, False)


def test_cold_run_pulls_everything():
    with tempfile.TemporaryDirectory() as d:
        info = _run_case(d)
        assert CALLS["records"] == 2
        assert [t["skipped"] for t in info["tables"]] == [False, False]


def test_complete_run_skips_both_tables():
    with tempfile.TemporaryDirectory() as d:
        _write_csv(os.path.join(d, "BaseName__Alpha.csv"), [0, 1, 2])
        _write_csv(os.path.join(d, "BaseName__Beta.csv"), [0, 1, 2])
        manifest = [{"base": "BaseName", "id": "appX", "tables": [
            {"table": "Alpha", "table_id": "tblT1", "records": 3},
            {"table": "Beta", "table_id": "tblT2", "records": 3}]}]
        with open(os.path.join(d, "manifest.json"), "w") as f:
            json.dump(manifest, f)
        info = _run_case(d)
        assert [t["skipped"] for t in info["tables"]] == [True, True]
        assert CALLS["records"] == 0


def test_partial_run_skips_only_matching_table():
    with tempfile.TemporaryDirectory() as d:
        _write_csv(os.path.join(d, "BaseName__Alpha.csv"), [0, 1, 2])
        _write_csv(os.path.join(d, "BaseName__Beta.csv"), [0])  # truncated
        manifest = [{"base": "BaseName", "id": "appX", "tables": [
            {"table": "Alpha", "table_id": "tblT1", "records": 3},
            {"table": "Beta", "table_id": "tblT2", "records": 3}]}]
        with open(os.path.join(d, "manifest.json"), "w") as f:
            json.dump(manifest, f)
        info = _run_case(d)
        assert [t["skipped"] for t in info["tables"]] == [True, False]
        assert CALLS["records"] == 1


def test_attachment_helpers():
    assert sanitize('a/b:c') == "a_b_c"
    name = make_stored_name("recABC", "Poster", "photo.jpg")
    assert name.startswith("recABC__Poster__") and name.endswith(".jpg")
    assert len(name.encode("utf-8")) <= 255
    assert parse_filenames(json.dumps([name])) == [name]
    assert parse_filenames('["recABC"]') == []
    assert parse_filenames("hello") == []
