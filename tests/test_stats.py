"""The second wave of breakdowns, and the trivia line.

Most of these guard against a figure being *plausible but wrong*: a cache
miss charged at the full write rate rather than the gap, an MCP column
counting the 82% of messages that call no MCP tool, a weekend share taken
from UTC rather than the local day.
"""

from __future__ import annotations

import datetime as dt
import json

import pytest

from dashboard import aggregate, pricing, render_html, scan, store
from dashboard.models import UsageRecord

NOW = dt.datetime(2026, 8, 11, 18, 0, 0)  # a Tuesday


def rec(message_id: str, day: str = "2026-08-11", **over) -> UsageRecord:
    base = dict(
        message_id=message_id, ts=f"{day}T10:00:00.000Z", day=day,
        model="claude-opus-4-8", project="p", skill="(none)", session_id="s",
        input_tokens=0, output_tokens=1_000_000, cache_read_tokens=0,
        cache_write_5m=0, cache_write_1h=0, speed="standard",
    )
    base.update(over)
    return UsageRecord(**base)


def view(records, **kw):
    return aggregate.build(records, {}, now=NOW, **kw).scoped


# --- cache misses -------------------------------------------------------

def test_a_miss_is_charged_at_the_gap_not_the_whole_write():
    """The tokens had to be paid for either way. What the miss cost is the
    difference between the write rate and the read rate it should have been,
    so charging the full 1.25x would overstate the waste by ~9%."""
    v = view([rec("m1", cache_missed_tokens=1_000_000, cache_miss_reason="tools_changed")])
    premium = pricing.CACHE_WRITE_5M_MULTIPLIER - pricing.CACHE_READ_MULTIPLIER
    assert v.cache_miss_cost == pytest.approx(5.0 * premium)
    assert v.cache_miss_cost < 5.0 * pricing.CACHE_WRITE_5M_MULTIPLIER


def test_the_dominant_miss_reason_is_named_in_words():
    records = [
        rec("m1", cache_missed_tokens=9_000_000, cache_miss_reason="system_changed"),
        rec("m2", cache_missed_tokens=1_000_000, cache_miss_reason="tools_changed"),
    ]
    assert view(records).cache_miss_reason == "the system prompt changed"


def test_an_unknown_miss_reason_is_shown_verbatim():
    v = view([rec("m1", cache_missed_tokens=1_000, cache_miss_reason="gremlins")])
    assert v.cache_miss_reason == "gremlins"


def test_an_unpriced_model_contributes_no_miss_cost():
    v = view([rec("m1", model="claude-future-9", cache_missed_tokens=5_000_000)])
    assert v.cache_missed_tokens == 5_000_000
    assert v.cache_miss_cost == 0.0


def test_the_page_reports_the_waste_beside_what_caching_saved():
    records = [rec("m1", cache_read_tokens=1_000_000,
                   cache_missed_tokens=2_000_000, cache_miss_reason="system_changed")]
    out = render_html.render(aggregate.build(records, {}, now=NOW))
    assert "caching saved" in out
    assert "cache misses cost" in out
    assert "mostly the system prompt changed" in out


def test_no_miss_line_when_the_cache_held():
    out = render_html.render(aggregate.build([rec("m1")], {}, now=NOW))
    assert "cache misses cost" not in out


# --- the new breakdowns -------------------------------------------------

def test_effort_branch_and_mcp_each_get_a_panel():
    records = [rec("m1", effort="xhigh", branch="main", mcp_server="browser")]
    out = render_html.render(aggregate.build(records, {}, now=NOW))
    for heading in ("BY EFFORT &middot;", "BY BRANCH &middot;", "BY MCP SERVER &middot;"):
        assert heading in out, heading


def test_messages_calling_no_mcp_tool_are_not_a_bucket():
    """82% of messages carry no MCP server. An empty-string row would be the
    biggest bar in the panel and would mean nothing."""
    records = [rec("m1", mcp_server=""), rec("m2", mcp_server="browser")]
    labels = [b.label for b in view(records).by_mcp]
    assert labels == ["browser"]


def test_mcp_shares_are_of_total_spend_not_of_mcp_spend():
    """A server's share answers "how much of everything went through this",
    which is the question worth asking. Renormalising within MCP would make
    one lightly-used server look like the whole story."""
    records = [rec("m1", mcp_server=""), rec("m2", mcp_server="browser")]
    assert view(records).by_mcp[0].share == pytest.approx(0.5)


def test_branches_are_capped_like_the_other_breakdowns():
    records = [rec(f"m{i}", branch=f"b{i}") for i in range(aggregate.TOP_N + 4)]
    assert len(view(records).by_branch) == aggregate.TOP_N


def test_effort_is_not_capped_because_there_are_only_a_few():
    records = [rec(f"m{i}", effort=e) for i, e in
               enumerate(("xhigh", "high", "max", "(none)"))]
    assert len(view(records).by_effort) == 4


# --- trivia -------------------------------------------------------------

def test_tools_per_reply_counts_stop_reasons():
    records = [rec(f"m{i}", stop_reason="tool_use") for i in range(9)]
    records.append(rec("m9", stop_reason="end_turn"))
    v = view(records)
    assert v.tools_per_reply == pytest.approx(9.0)


def test_tools_per_reply_is_zero_rather_than_dividing_by_zero():
    assert view([rec("m1", stop_reason="tool_use")]).tools_per_reply == 0.0


def test_the_weekend_share_uses_the_local_day():
    """2026-08-15 is a Saturday. Taking the weekday from a UTC timestamp
    instead would misfile the evening either side of midnight."""
    records = [rec("m1", day="2026-08-15"), rec("m2", day="2026-08-11")]
    assert view(records, range_key="all").weekend_share == pytest.approx(0.5)


def test_the_busiest_hour_is_the_modal_hour():
    records = [rec("m1", hour=9), rec("m2", hour=14), rec("m3", hour=14)]
    assert view(records).busiest_hour == 14


def test_records_with_no_hour_do_not_vote():
    assert view([rec("m1", hour=-1)]).busiest_hour == -1


def test_the_priciest_message_is_the_maximum_not_the_total():
    records = [rec("m1", output_tokens=1_000_000), rec("m2", output_tokens=4_000_000)]
    assert view(records).priciest_message == pytest.approx(100.0)


def test_denials_and_injections_sum_within_the_range():
    records = [rec("m1", denials=2, injections=5),
               rec("m2", day="2026-01-02", denials=9, injections=9)]
    v = view(records, range_key="today")
    assert (v.denials, v.injections) == (2, 5)


def test_the_trivia_line_omits_what_it_has_no_figure_for():
    out = render_html.render(aggregate.build([rec("m1", hour=11)], {}, now=NOW))
    assert "busiest at 11:00" in out
    assert "tool calls refused" not in out
    assert "context injections" not in out


def test_the_trivia_line_is_absent_entirely_when_there_is_nothing_to_say():
    out = render_html.render(aggregate.build([], {}, now=NOW))
    assert "tool calls per reply" not in out
    assert "busiest at" not in out


# --- scan and store -----------------------------------------------------

def at(seconds: float) -> str:
    base = dt.datetime(2026, 8, 11, 10, 0, 0, tzinfo=dt.timezone.utc)
    return (base + dt.timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def write(tmp_path, *entries):
    path = tmp_path / "proj" / "s.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")
    return tmp_path


def assistant(offset, mid, **extra):
    entry = {
        "type": "assistant", "timestamp": at(offset), "sessionId": "s1",
        "cwd": "/tmp/p", "isSidechain": False,
        "message": {"id": mid, "model": "claude-opus-4-8", "stop_reason": "tool_use",
                    "usage": {"input_tokens": 1, "output_tokens": 1}},
    }
    entry.update(extra)
    return entry


def test_scan_reads_effort_branch_and_mcp(tmp_path):
    got = scan.scan(write(tmp_path, assistant(
        0, "m1", effort="xhigh", gitBranch="main", attributionMcpServer="browser",
    ))).records[0]
    assert (got.effort, got.branch, got.mcp_server) == ("xhigh", "main", "browser")


def test_scan_reads_the_cache_miss_out_of_diagnostics(tmp_path):
    entry = assistant(0, "m1")
    entry["message"]["diagnostics"] = {
        "cache_miss_reason": {"type": "tools_changed", "cache_missed_input_tokens": 327_106}
    }
    got = scan.scan(write(tmp_path, entry)).records[0]
    assert got.cache_missed_tokens == 327_106
    assert got.cache_miss_reason == "tools_changed"


def test_a_message_with_no_diagnostics_reports_no_miss(tmp_path):
    got = scan.scan(write(tmp_path, assistant(0, "m1"))).records[0]
    assert (got.cache_missed_tokens, got.cache_miss_reason) == (0, "")


def test_denials_and_injections_attach_to_the_following_message(tmp_path):
    denied = {"type": "user", "timestamp": at(5), "sessionId": "s1",
              "toolDenialKind": "user-rejected",
              "message": {"content": [{"type": "tool_result"}]}}
    injected = {"type": "attachment", "timestamp": at(6), "sessionId": "s1",
                "attachment": {"type": "task_reminder"}}
    got = scan.scan(write(tmp_path, assistant(0, "m1"), denied, injected,
                          assistant(10, "m2"))).records
    assert [r.denials for r in got] == [0, 1]
    assert [r.injections for r in got] == [0, 1]


def test_the_new_columns_survive_a_store_round_trip(tmp_path):
    entry = assistant(0, "m1", effort="max", gitBranch="dev", attributionMcpServer="db")
    entry["message"]["diagnostics"] = {
        "cache_miss_reason": {"type": "system_changed", "cache_missed_input_tokens": 42}
    }
    result = scan.scan(write(tmp_path, entry))
    with store.Store(tmp_path / "s.db") as db:
        db.ingest(result)
        row = db.records()[0]
    assert (row.effort, row.branch, row.mcp_server) == ("max", "dev", "db")
    assert (row.cache_missed_tokens, row.cache_miss_reason) == (42, "system_changed")
    assert row.stop_reason == "tool_use"


def test_a_database_from_before_these_columns_still_opens(tmp_path):
    """Old rows read back as "never recorded" rather than as a measurement."""
    import sqlite3

    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE usage (message_id TEXT PRIMARY KEY, ts TEXT NOT NULL,"
        " day TEXT NOT NULL, model TEXT NOT NULL, project TEXT, skill TEXT,"
        " session_id TEXT, input_tokens INTEGER DEFAULT 0, output_tokens INTEGER"
        " DEFAULT 0, cache_read_tokens INTEGER DEFAULT 0, cache_write_5m INTEGER"
        " DEFAULT 0, cache_write_1h INTEGER DEFAULT 0, speed TEXT,"
        " is_subagent INTEGER DEFAULT 0)"
    )
    con.execute("INSERT INTO usage (message_id, ts, day, model)"
                " VALUES ('old','t','2026-08-01','claude-opus-4-8')")
    con.commit()
    con.close()

    with store.Store(path) as db:
        row = db.records()[0]
    assert row.effort == "(none)"
    assert row.branch == "(none)"
    assert row.mcp_server == ""
    assert row.cache_missed_tokens == 0
    assert row.hour == -1
