from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

import pytest

from dashboard import aggregate, render_html, scan
from dashboard.store import Store

FIXTURES = Path(__file__).parent / "fixtures" / "transcripts"
NOW = dt.datetime(2026, 7, 30, 12, 0, 0)

# Derived by hand in the plan's Task 7 table, corrected by Amendment A's
# Change 9 for the fourth (subagent) fixture worth $0.015. If this changes,
# either the fixtures changed or the pricing model regressed.
EXPECTED_TOTAL = 0.2585   # was 0.2435 before Amendment A


def test_full_pipeline_produces_the_expected_total(tmp_path):
    with Store(tmp_path / "history.db") as db:
        db.ingest(scan.scan(FIXTURES))
        data = aggregate.build(db.records(), db.titles(), now=NOW)
    assert data.all_time.cost == pytest.approx(EXPECTED_TOTAL, abs=1e-9)


def test_full_pipeline_is_idempotent_over_repeated_runs(tmp_path):
    db_path = tmp_path / "history.db"
    totals = []
    for _ in range(3):
        with Store(db_path) as db:
            db.ingest(scan.scan(FIXTURES))
            totals.append(aggregate.build(db.records(), db.titles(), now=NOW).all_time.cost)
    assert totals[0] == totals[1] == totals[2] == pytest.approx(EXPECTED_TOTAL, abs=1e-9)


def test_pipeline_counts_five_messages_and_two_projects(tmp_path):
    with Store(tmp_path / "history.db") as db:
        db.ingest(scan.scan(FIXTURES))
        data = aggregate.build(db.records(), db.titles(), now=NOW)
    assert data.all_time.messages == 5
    assert {bar.label for bar in data.by_project} == {"alpha", "beta"}


def test_pipeline_splits_main_from_subagent_cost(tmp_path):
    with Store(tmp_path / "history.db") as db:
        db.ingest(scan.scan(FIXTURES))
        data = aggregate.build(db.records(), db.titles(), now=NOW)
    assert data.subagent_cost == pytest.approx(0.015, abs=1e-9)
    assert data.main_cost == pytest.approx(0.2435, abs=1e-9)


def test_pipeline_reports_the_unpriced_model(tmp_path):
    with Store(tmp_path / "history.db") as db:
        db.ingest(scan.scan(FIXTURES))
        data = aggregate.build(db.records(), db.titles(), now=NOW)
    assert data.unpriced_models == ["claude-future-9"]


def test_pipeline_output_renders(tmp_path):
    with Store(tmp_path / "history.db") as db:
        db.ingest(scan.scan(FIXTURES))
        page = render_html.render(aggregate.build(db.records(), db.titles(), now=NOW))
    assert page.startswith("<!DOCTYPE html>")
    assert "<script" not in page.lower()


@pytest.mark.skipif(
    not os.environ.get("DASHBOARD_LIVE_SMOKE"),
    reason="set DASHBOARD_LIVE_SMOKE=1 to run against the real ~/.claude tree",
)
def test_live_smoke_is_positive_and_idempotent(tmp_path):
    """Opt-in only. Asserts shape, never a total - the live tree changes with
    every Claude Code message, so a pinned figure would fail within minutes."""
    db_path = tmp_path / "live.db"
    projects = scan.default_projects_dir()
    with Store(db_path) as db:
        db.ingest(scan.scan(projects))
        first = aggregate.build(db.records(), db.titles(), now=dt.datetime.now()).all_time.cost
    with Store(db_path) as db:
        inserted_second_time = db.ingest(scan.scan(projects, skip=db.file_stats()))
        second = aggregate.build(db.records(), db.titles(), now=dt.datetime.now()).all_time.cost
    assert first > 0
    assert inserted_second_time == 0
    assert second == pytest.approx(first)


# --- multi-source pipeline ----------------------------------------------

from dashboard import ingest  # noqa: E402

COWORK_FIXTURES = Path(__file__).parent / "fixtures" / "cowork"
COWORK_TOTAL = 0.21          # derived in tests/test_cowork.py
COMBINED_TOTAL = EXPECTED_TOTAL + COWORK_TOTAL


def _combined(tmp_path):
    with Store(tmp_path / "history.db") as db:
        db.ingest(ingest.scan_sources(ingest.default_sources(FIXTURES, COWORK_FIXTURES)))
        return aggregate.build(db.records(), db.titles(), now=NOW)


def test_combined_pipeline_totals_both_surfaces(tmp_path):
    assert _combined(tmp_path).all_time.cost == pytest.approx(COMBINED_TOTAL, abs=1e-9)


def test_combined_pipeline_is_idempotent(tmp_path):
    """Re-scanning must not double-count across surfaces either — the audit
    mirrors inside Cowork sessions make this the interesting case."""
    db_path = tmp_path / "history.db"
    totals = []
    for _ in range(3):
        with Store(db_path) as db:
            db.ingest(ingest.scan_sources(ingest.default_sources(FIXTURES, COWORK_FIXTURES)))
            totals.append(aggregate.build(db.records(), db.titles(), now=NOW).all_time.cost)
    assert totals[0] == totals[1] == totals[2] == pytest.approx(COMBINED_TOTAL, abs=1e-9)


def test_source_shares_sum_to_the_whole(tmp_path):
    data = _combined(tmp_path)
    assert sum(bar.cost for bar in data.by_source) == pytest.approx(COMBINED_TOTAL, abs=1e-9)


def test_adding_cowork_does_not_change_the_claude_code_figure(tmp_path):
    """The new surface must be purely additive: existing Claude Code spend
    reads exactly as it did before multi-source ingest."""
    data = _combined(tmp_path)
    code = next(bar for bar in data.by_source if bar.label == "Claude Code")
    assert code.cost == pytest.approx(EXPECTED_TOTAL, abs=1e-9)


def test_combined_pipeline_renders(tmp_path):
    out = render_html.render(_combined(tmp_path))
    assert out.startswith("<!DOCTYPE html>")
    assert "Desktop (Cowork)" in out
