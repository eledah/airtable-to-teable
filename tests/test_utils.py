"""Offline tests for filename utils + Teable link-value dedupe rule."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from airtable_teable.utils import (  # noqa: E402
    base_and_table_names,
    clean_name,
    sanitize,
    truncate_utf8,
)


def test_sanitize_and_clean():
    assert sanitize("a/b\\c:d") == "a_b_c_d"
    assert sanitize("") == "unnamed"
    assert clean_name("a__b  c ") == "a_b c"


def test_truncate_utf8_keeps_chars_intact():
    s = "سلام دنیا"
    out = truncate_utf8(s, 7)
    assert len(out.encode("utf-8")) <= 7
    out.encode("utf-8").decode("utf-8")  # must not raise


def test_manifest_name_resolution():
    manifest = {"plain": {"name": "Real Base", "tables": {"T": "Real Table"}}}
    b, t = base_and_table_names("plain", "plain__T.csv", manifest)
    assert (b, t) == ("Real Base", "Real Table")
    b2, t2 = base_and_table_names("plain", "plain.csv", {"plain": {"name": "R", "tables": {"T": "T"}}})
    assert b2 == "R"


def test_link_dedupe_rule():
    ids = ["a", "b", "a", "c", "b"]
    assert list(dict.fromkeys(ids)) == ["a", "b", "c"]
