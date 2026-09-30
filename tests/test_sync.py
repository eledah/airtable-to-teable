"""Offline tests for the streaming sync planner (no network)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from airtable_teable.sync import folder_for_base, matches_base  # noqa: E402


def test_matches_base_by_id_or_name():
    base = {"id": "appX", "name": "Demo"}
    assert matches_base(base, set())
    assert matches_base(base, {"appX"})
    assert matches_base(base, {"Demo"})
    assert not matches_base(base, {"Other"})
    assert matches_base(base, {"Other", "Demo"})


def test_folder_for_base_flat_and_collision():
    out = "/tmp/out"
    assert folder_for_base(out, {"id": "appX", "name": "Demo"}, set()) == "/tmp/out/Demo"
    # Sanitized collision gets a stable id suffix, never reuses a taken name.
    first = folder_for_base(out, {"id": "app111", "name": "A/B"}, {"A_B"})
    second = folder_for_base(out, {"id": "app222", "name": "A/B"}, {"A_B", first.split("/")[-1]})
    assert first != second
    assert first.startswith("/tmp/out/A_B")
