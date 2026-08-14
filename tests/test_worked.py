"""Machine time: how long the model and its tools were actually working.

Unlike the footprint, this is measured rather than modelled — but only if
the attribution is right, and getting it wrong is silent. Two leaks were
found on real data after the first implementation looked fine: discarding
pending work at a human turn (10.9 hours) and stranding tool runs that
followed the last usage-bearing message in a transcript (4.8 hours). Both
have tests here.
"""

from __future__ import annotations

import datetime as dt
import json

import pytest

from dashboard import aggregate, render_html, scan, store
from dashboard.models import UsageRecord

NOW = dt.datetime(2026, 8, 11, 18, 0, 0)
BASE = dt.datetime(2026, 8, 11, 10, 0, 0, tzinfo=dt.timezone.utc)


def at(seconds: float) -> str:
    return (BASE + dt.timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def assistant(offset: float, message_id: str, subagent: bool = False) -> dict:
    return {
        "type": "assistant",
        "timestamp": at(offset),
        "sessionId": "s1",
        "isSidechain": subagent,
        "cwd": "/tmp/proj",
        "message": {
            "id": message_id,
            "model": "claude-opus-4-8",
            "usage": {"input_tokens": 1, "output_tokens": 1},
        },
    }


def tool_result(offset: float) -> dict:
    return {
        "type": "user",
        "timestamp": at(offset),
        "sessionId": "s1",
        "message": {"content": [{"type": "tool_result", "tool_use_id": "t1"}]},
    }


def human(offset: float, text: str = "do the thing") -> dict:
    return {
        "type": "user",
        "timestamp": at(offset),
        "sessionId": "s1",
        "message": {"content": text},
    }


def transcript(tmp_path, *entries):
    path = tmp_path / "proj" / "session.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    # Trailing newline matters: scan deliberately consumes only whole
    # lines, so a fixture without one loses its last record — which is the
    # same guard that stops a transcript caught mid-append being misread.
    path.write_text(
        "".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8"
    )
    return tmp_path


def worked(tmp_path, *entries) -> float:
    result = scan.scan(transcript(tmp_path, *entries))
    return sum(r.work_seconds for r in result.records)


# --- what counts as the machine working ---------------------------------

def test_the_gap_before_an_assistant_message_is_machine_time(tmp_path):
    """The model was generating for those seconds."""
    assert worked(tmp_path, human(0), assistant(30, "m1")) == pytest.approx(30)


def test_a_tool_run_counts_even_though_it_emits_no_record(tmp_path):
    """assistant -> tool_result -> assistant. Only the assistant messages
    become records, so the tool's 20 seconds have to be carried forward and
    attributed to the message that follows it."""
    total = worked(tmp_path, human(0), assistant(10, "m1"), tool_result(30), assistant(35, "m2"))
    assert total == pytest.approx(35)


def test_waiting_for_a_human_is_not_machine_time(tmp_path):
    """The 600-second think before the human replies belongs to the human."""
    total = worked(tmp_path, human(0), assistant(10, "m1"), human(610), assistant(620, "m2"))
    assert total == pytest.approx(20), "10s generating twice, nothing for the wait"


def test_pending_work_survives_a_human_turn(tmp_path):
    """The leak that cost 10.9 hours on real data. A tool finishes, the human
    interjects before the agent speaks again — the tool still ran, and its
    time must reach the next record rather than being zeroed."""
    total = worked(
        tmp_path,
        human(0), assistant(10, "m1"), tool_result(40), human(300), assistant(310, "m2"),
    )
    assert total == pytest.approx(50), "10s + 30s tool; the 260s wait excluded"


def test_work_after_the_last_priced_message_is_not_stranded(tmp_path):
    """The second leak, worth 4.8 hours across 441 transcripts: a tool that
    runs after the final usage-bearing message has no later record to land
    on, so it is added to the last one instead of vanishing."""
    total = worked(tmp_path, human(0), assistant(10, "m1"), tool_result(45))
    assert total == pytest.approx(45)


# --- the idle threshold -------------------------------------------------

def test_a_gap_over_the_threshold_is_dropped_not_clamped(tmp_path):
    """Clamping invents time. On real data 158 gaps longer than half an hour
    summed to 124 days of session-left-open idle, and clamping them to the
    cap would have added ~79 hours of work that never happened."""
    long_gap = scan.MAX_WORK_GAP_SECONDS + 60
    total = worked(tmp_path, human(0), assistant(10, "m1"), assistant(10 + long_gap, "m2"))
    assert total == pytest.approx(10), "the long gap contributes nothing at all"
    assert total < scan.MAX_WORK_GAP_SECONDS, "clamping would have added the cap"


def test_a_gap_exactly_at_the_threshold_still_counts(tmp_path):
    total = worked(tmp_path, human(0), assistant(scan.MAX_WORK_GAP_SECONDS, "m1"))
    assert total == pytest.approx(scan.MAX_WORK_GAP_SECONDS)


# --- through the store and the aggregator -------------------------------

def test_work_seconds_survives_a_store_round_trip(tmp_path):
    result = scan.scan(transcript(tmp_path, human(0), assistant(30, "m1"), tool_result(50)))
    with store.Store(tmp_path / "h.db") as db:
        db.ingest(result)
        rows = db.records()
    assert sum(r.work_seconds for r in rows) == pytest.approx(50)


def test_a_database_written_before_the_column_existed_still_opens(tmp_path):
    """The migration path. Old rows read back as zero, which understates
    history rather than inventing it — those gaps were never recorded."""
    path = tmp_path / "old.db"
    import sqlite3

    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE usage (message_id TEXT PRIMARY KEY, ts TEXT NOT NULL,"
        " day TEXT NOT NULL, model TEXT NOT NULL, project TEXT, skill TEXT,"
        " session_id TEXT, input_tokens INTEGER DEFAULT 0, output_tokens INTEGER"
        " DEFAULT 0, cache_read_tokens INTEGER DEFAULT 0, cache_write_5m INTEGER"
        " DEFAULT 0, cache_write_1h INTEGER DEFAULT 0, speed TEXT,"
        " is_subagent INTEGER DEFAULT 0)"
    )
    con.execute(
        "INSERT INTO usage (message_id, ts, day, model) VALUES ('old','t','2026-08-01','m')"
    )
    con.commit()
    con.close()

    with store.Store(path) as db:
        rows = db.records()
    assert len(rows) == 1
    assert rows[0].work_seconds == 0.0


def rec(message_id: str, seconds: float, day: str = "2026-08-11", **over) -> UsageRecord:
    base = dict(
        message_id=message_id, ts=f"{day}T10:00:00.000Z", day=day,
        model="claude-opus-4-8", project="p", skill="(none)", session_id="s",
        input_tokens=0, output_tokens=1_000, cache_read_tokens=0,
        cache_write_5m=0, cache_write_1h=0, speed="standard", work_seconds=seconds,
    )
    base.update(over)
    return UsageRecord(**base)


def test_the_aggregator_sums_machine_time_within_the_range():
    records = [rec("m1", 60), rec("m2", 90, day="2026-01-02")]
    today = aggregate.build(records, {}, now=NOW, range_key="today")
    everything = aggregate.build(records, {}, now=NOW, range_key="all")
    assert today.scoped.worked_seconds == pytest.approx(60)
    assert everything.scoped.worked_seconds == pytest.approx(150)


def test_subagent_machine_time_is_tracked_separately():
    records = [rec("m1", 100), rec("m2", 300, is_subagent=True)]
    view = aggregate.build(records, {}, now=NOW).scoped
    assert view.worked_seconds == pytest.approx(400)
    assert view.subagent_worked_seconds == pytest.approx(300)
    assert view.subagent_worked_share == pytest.approx(0.75)


# --- the panel ----------------------------------------------------------

@pytest.mark.parametrize(
    "seconds,expected",
    [(0, "0s"), (45, "45s"), (90, "1m 30s"), (3600, "1h"), (5400, "1h 30m"),
     (86400, "1d"), (398_400, "4d 14h")],
)
def test_durations_are_shown_in_at_most_two_units(seconds, expected):
    assert render_html._duration(seconds) == expected


def test_the_panel_says_worked_and_never_saved():
    """The whole point of the distinction. Time saved needs a counterfactual
    the transcripts cannot supply; time worked is arithmetic on timestamps."""
    out = render_html.render(aggregate.build([rec("m1", 7200)], {}, now=NOW))
    assert "AI WORKED" in out
    assert "of machine time" in out
    assert "not a measure of time saved" in out
    assert "saved" not in out.replace("not a measure of time saved", "")


def test_the_panel_admits_that_parallel_agents_are_summed():
    records = [rec("m1", 3600), rec("m2", 3600, is_subagent=True)]
    out = render_html.render(aggregate.build(records, {}, now=NOW))
    assert "running in parallel" in out
    assert "subagents" in out


def test_the_panel_states_how_it_was_measured():
    out = render_html.render(aggregate.build([rec("m1", 7200)], {}, now=NOW))
    assert "measured from transcript timestamps" in out
    assert f"gaps over {int(scan.MAX_WORK_GAP_SECONDS / 60)} min counted as idle" in out


def test_the_panel_sits_above_the_footprint_cards():
    out = render_html.render(aggregate.build([rec("m1", 7200)], {}, now=NOW))
    assert out.index('class="workrow"') < out.index('class="fpstrip"')


def test_no_panel_without_measured_time():
    """A fresh database has no work_seconds at all. '0s of machine time'
    would read as a measurement of nothing rather than an absence."""
    out = render_html.render(aggregate.build([rec("m1", 0)], {}, now=NOW))
    assert "AI WORKED" not in out
