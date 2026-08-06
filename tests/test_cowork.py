"""Claude Desktop (Cowork) discovery, labelling and multi-source ingest."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from dashboard import aggregate, cowork, ingest, pricing, scan
from dashboard.store import Store

COWORK_FIXTURES = Path(__file__).parent / "fixtures" / "cowork"
CODE_FIXTURES = Path(__file__).parent / "fixtures" / "transcripts"
NOW = dt.datetime(2026, 7, 30, 12, 0, 0)

# Hand-derived from the fixture bytes against the rate table:
#   msg_cw1  opus-5   in 1_000 @$5/MTok            = 0.005
#                     out 2_000 @$25/MTok          = 0.05
#                     cache read 100_000 @0.1x     = 0.05
#                     cache write 1h 10_000 @2.0x  = 0.10   -> 0.205
#   msg_cw2  haiku-4-5 out 1_000 @$5/MTok                   -> 0.005
COWORK_TOTAL = 0.21


def _cost(records) -> float:
    return sum(pricing.cost(r).total for r in records)


# --- labelling ----------------------------------------------------------

def test_session_label_prefers_the_selected_folder_over_the_title():
    """Cowork sessions all run in a directory called `outputs`, so the folder
    the user actually pointed at is the only useful project label."""
    labels = cowork.session_labels(COWORK_FIXTURES)
    assert labels["local_aaa"] == "gamma"


def test_session_label_falls_back_to_the_title_when_no_folder_was_selected(tmp_path):
    org = tmp_path / "inst" / "org"
    org.mkdir(parents=True)
    (org / "local_ccc.json").write_text(
        json.dumps({"sessionId": "local_ccc", "title": "Refactor   the  parser"}),
        encoding="utf-8",
    )
    assert cowork.session_labels(tmp_path)["local_ccc"] == "Refactor the parser"


def test_unreadable_sidecar_does_not_lose_the_other_sessions(tmp_path):
    """One corrupt session must not cost us the whole surface's usage."""
    org = tmp_path / "inst" / "org"
    org.mkdir(parents=True)
    (org / "local_bad.json").write_text("{not json", encoding="utf-8")
    (org / "local_ok.json").write_text(
        json.dumps({"sessionId": "local_ok", "title": "fine"}), encoding="utf-8"
    )
    labels = cowork.session_labels(tmp_path)
    assert "local_bad" not in labels
    assert labels["local_ok"] == "fine"


def test_resolver_maps_a_session_cwd_to_its_label():
    resolve = cowork.project_resolver(cowork.session_labels(COWORK_FIXTURES))
    assert resolve("/x/local-agent-mode-sessions/i/o/local_aaa/outputs") == "gamma"


@pytest.mark.parametrize("cwd", [None, "", "/somewhere/else/outputs"])
def test_resolver_falls_back_when_the_session_is_unknown(cwd):
    resolve = cowork.project_resolver({})
    assert resolve(cwd) == cowork.FALLBACK_LABEL


def test_session_without_a_sidecar_still_reports_its_usage():
    """local_bbb has a transcript but no sidecar JSON. Its cost must appear
    under the fallback label rather than vanish."""
    result = ingest.scan_sources(ingest.default_sources(CODE_FIXTURES, COWORK_FIXTURES))
    unlabelled = [r for r in result.records if r.project == cowork.FALLBACK_LABEL]
    assert _cost(unlabelled) == pytest.approx(0.005, abs=1e-9)


def test_default_cowork_dir_honours_the_environment_override(monkeypatch):
    monkeypatch.setenv("CLAUDE_COWORK_DIR", "/tmp/elsewhere")
    assert cowork.default_cowork_dir() == Path("/tmp/elsewhere")


# --- source stamping ----------------------------------------------------

def test_scan_stamps_the_source_on_every_record():
    assert {r.source for r in scan.scan(CODE_FIXTURES).records} == {"code"}


def test_scan_defaults_to_code_so_existing_callers_are_unaffected():
    assert scan.scan(CODE_FIXTURES, source="code").records[0].source == "code"


def test_scan_accepts_a_project_resolver_override():
    result = scan.scan(COWORK_FIXTURES, project_resolver=lambda cwd: "FIXED")
    assert {r.project for r in result.records} == {"FIXED"}


# --- multi-source ingest ------------------------------------------------

def test_ingest_labels_each_surface_and_keeps_both():
    result = ingest.scan_sources(ingest.default_sources(CODE_FIXTURES, COWORK_FIXTURES))
    by_source = {}
    for record in result.records:
        by_source.setdefault(record.source, []).append(record)
    assert set(by_source) == {"code", "cowork"}
    assert _cost(by_source["cowork"]) == pytest.approx(COWORK_TOTAL, abs=1e-9)


def test_audit_mirror_does_not_double_count():
    """Each Cowork session also writes audit.jsonl, repeating the transcript's
    assistant messages with the same message.id. Checked against live data:
    every audit id already appeared in the transcripts, so the mirror
    contributes nothing. If dedup ever regressed, this surface would
    silently inflate."""
    audit = COWORK_FIXTURES / "inst" / "org" / "local_aaa" / "audit.jsonl"
    assert audit.exists(), "fixture must contain the audit mirror to be meaningful"
    assert "msg_cw1" in audit.read_text(encoding="utf-8")

    result = ingest.scan_sources(ingest.default_sources(CODE_FIXTURES, COWORK_FIXTURES))
    ids = [r.message_id for r in result.records]
    assert ids.count("msg_cw1") == 1
    cowork_records = [r for r in result.records if r.source == "cowork"]
    assert _cost(cowork_records) == pytest.approx(COWORK_TOTAL, abs=1e-9)


def test_a_missing_cowork_directory_is_not_an_error(tmp_path):
    """Cowork does not exist on a machine without Claude Desktop."""
    result = ingest.scan_sources(
        ingest.default_sources(CODE_FIXTURES, tmp_path / "nope")
    )
    assert result.records
    assert {r.source for r in result.records} == {"code"}


def test_sources_sharing_a_message_id_are_counted_once(tmp_path):
    """The two live roots share no ids, but a surface that ever mirrored
    another's transcripts must not be counted twice."""
    mirror = tmp_path / "mirror" / "proj"
    mirror.mkdir(parents=True)
    original = next((CODE_FIXTURES / "-Users-demo-beta").glob("*.jsonl"))
    (mirror / "copy.jsonl").write_text(original.read_text(encoding="utf-8"), encoding="utf-8")

    both = ingest.scan_sources(
        [
            ingest.Source(name="code", root=CODE_FIXTURES),
            ingest.Source(name="cowork", root=tmp_path / "mirror"),
        ]
    )
    only_code = ingest.scan_sources([ingest.Source(name="code", root=CODE_FIXTURES)])
    assert _cost(both.records) == pytest.approx(_cost(only_code.records), abs=1e-9)


# --- through the store and aggregate ------------------------------------

def test_source_survives_a_store_round_trip(tmp_path):
    with Store(tmp_path / "history.db") as db:
        db.ingest(ingest.scan_sources(ingest.default_sources(CODE_FIXTURES, COWORK_FIXTURES)))
        sources = {r.source for r in db.records()}
    assert sources == {"code", "cowork"}


def test_dashboard_splits_cost_by_source(tmp_path):
    with Store(tmp_path / "history.db") as db:
        db.ingest(ingest.scan_sources(ingest.default_sources(CODE_FIXTURES, COWORK_FIXTURES)))
        data = aggregate.build(db.records(), db.titles(), now=NOW)
    amounts = {bar.label: bar.cost for bar in data.by_source}
    assert amounts["Desktop (Cowork)"] == pytest.approx(COWORK_TOTAL, abs=1e-9)
    assert amounts["Claude Code"] == pytest.approx(0.2585, abs=1e-9)
    assert sum(bar.share for bar in data.by_source) == pytest.approx(1.0, abs=1e-9)


def test_cowork_project_label_reaches_the_project_breakdown(tmp_path):
    with Store(tmp_path / "history.db") as db:
        db.ingest(ingest.scan_sources(ingest.default_sources(CODE_FIXTURES, COWORK_FIXTURES)))
        data = aggregate.build(db.records(), db.titles(), now=NOW)
    assert "gamma" in {bar.label for bar in data.by_project}
    assert "outputs" not in {bar.label for bar in data.by_project}


def test_an_unknown_source_is_shown_rather_than_dropped():
    """A future surface must appear on the page before SOURCE_LABELS knows it."""
    record = scan.scan(CODE_FIXTURES).records[0]
    future = record._replace(source="chrome")
    data = aggregate.build([future], {}, now=NOW)
    assert [bar.label for bar in data.by_source] == ["chrome"]
