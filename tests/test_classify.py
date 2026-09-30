"""Offline unit tests for link / multi-select classification (no network)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from airtable_teable.import_data import (  # noqa: E402
    classify_link_columns,
    classify_multiselect_columns,
    parse_str_array,
)


def test_parse_str_array_drops_nulls_and_keeps_empty():
    assert parse_str_array('["a", null]') == ["a"]
    assert parse_str_array("[]") == []
    assert parse_str_array("[null]") == []
    assert parse_str_array("plain") is None
    assert parse_str_array('["recABC1234567890"]') == ["recABC1234567890"]


def test_link_column_clean_single_target():
    rows = [
        {"id": "recAAAAAAAAAAAAAA", "tags": '["recBBBBBBBBBBBBB"]'},
        {"id": "recBBBBBBBBBBBBB", "tags": ""},
    ]
    rec_tables = {"recBBBBBBBBBBBBB": {"t2.csv"}}
    assert classify_link_columns(rows, rec_tables) == {"tags": "t2.csv"}


def test_link_column_excludes_lookup_and_mixed_and_unresolved():
    rows = [
        {"id": "x", "L (from T)": '["recBBBBBBBBBBBBB"]'},  # lookup -> text
        {"id": "x", "mixed": '["recBBBBBBBBBBBBB"]'},
        {"id": "y", "mixed": "hello"},
        {"id": "x", "ghost": '["recZZZZZZZZZZZZZ"]'},  # unresolved -> text
    ]
    rec_tables = {"recBBBBBBBBBBBBB": {"t2.csv"}}
    out = classify_link_columns(rows, rec_tables)
    assert "L (from T)" not in out
    assert "mixed" not in out
    assert "ghost" not in out


def test_link_column_excludes_multi_target():
    rows = [{"id": "x", "rel": '["recBBBBBBBBBBBBB", "recCCCCCCCCCCCCC"]'}]
    rec_tables = {"recBBBBBBBBBBBBB": {"a.csv"}, "recCCCCCCCCCCCCC": {"b.csv"}}
    assert classify_link_columns(rows, rec_tables) == {}


def test_multiselect_real_tags_vs_rollups():
    rows = [
        {"id": "a", "genre": '["doc", "bio"]', "year": '["1401", "1402"]'},
        {"id": "b", "genre": "[]", "year": '["1403"]'},
    ]
    out = classify_multiselect_columns(rows)
    assert set(out) == {"genre"}
    assert sorted(out["genre"]) == ["bio", "doc"]


def test_multiselect_excludes_links_attachments_lookups():
    rows = [{"id": "a",
             "link": '["recBBBBBBBBBBBBB"]',
             "file": '["recAAA__F__x.jpg"]',
             "X (from Y)": '["a", "b"]'}]
    assert classify_multiselect_columns(rows) == {}
