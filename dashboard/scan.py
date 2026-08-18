"""Reads Claude Code transcripts. The only module that knows the JSONL shape."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple

from .dates import instant, local_day, local_hour
from .models import UsageRecord

WORKTREE_RE = re.compile(r"^(?P<parent>.*?)/\.claude/worktrees/")
COMMAND_NAME_RE = re.compile(r"<command-name>([^<]+)</command-name>")

DEFAULT_PATTERN = "**/*.jsonl"

#: Bytes of a transcript hashed to decide whether it is the same file we read
#: last time. Transcripts are append-only, so an unchanged prefix means the
#: bytes before `offset` cannot have moved and it is safe to resume from it.
HEAD_BYTES = 4096

#: Longest gap between two records still counted as the machine working.
#:
#: A transcript records one timestamp per message and no durations, so the
#: only measurable unit of machine time is the gap between consecutive
#: records — model generation plus whatever tool ran in between.
#:
#: Gaps above this are dropped rather than clamped. Clamping was tried and
#: it *invents* time: on a real 71,000-record history, 158 gaps longer than
#: half an hour summed to 124 days of session-left-open idle, and clamping
#: them to the cap would have added ~79 hours of work that never happened.
#: Dropping them costs the tail of genuinely long tool runs, which is the
#: cheaper error. 98.6% of real gaps are under a minute; the measured total
#: moves from 74h to 169h as this constant goes from 60s to 1800s, so it is
#: the single assumption the whole figure rests on.
MAX_WORK_GAP_SECONDS = 300.0

#: Longest gap ending at a human turn still counted as the person being
#: there. Longer than MAX_WORK_GAP_SECONDS on purpose: reading a long answer
#: and composing a reply legitimately takes minutes, where a model turn that
#: takes five is already an outlier. Above this nobody is at the keyboard,
#: and the seconds belong to neither side of the clock.
MAX_WAIT_GAP_SECONDS = 900.0

#: Assistant messages never carry the permission mode. It arrives as its own
#: record — `{"type": "permission-mode", "permissionMode": ...}` — and on
#: some user turns, with no timestamp on either. So it is tracked as state in
#: file order and stamped onto the messages that follow.
#:
#: Measured on 663 real transcripts: where a session records a mode at all it
#: does so before its first assistant message (median and max both zero), so
#: nothing inside a covered session is misattributed. What is not covered is
#: whole sessions that never record one — 37% of messages — and those stay
#: UNKNOWN_MODE rather than being assumed into the majority.
UNKNOWN_MODE = "(not recorded)"

#: How many assistant messages a `Skill` tool call stays "pending" while we
#: wait for the run it started. Measured on real transcripts: 44 of 45 calls
#: are followed by their attributed run within three messages. Without a
#: bound, an unconsumed call matched a run 56 messages later and credited
#: the wrong trigger.
SKILL_TRIGGER_WINDOW = 5

#: Who started a skill run.
ORIGIN_MODEL = "model"
ORIGIN_USER = "you"
ORIGIN_SUBAGENT = "subagent"
ORIGIN_UNKNOWN = "(not recorded)"


class FileState(NamedTuple):
    """What we knew about a transcript after the last pass.

    offset is a byte position at a line boundary — never mid-line, so a
    transcript caught mid-append resumes cleanly rather than losing the
    record that was being written.
    """

    size: int
    mtime: float
    offset: int = 0
    head: str = ""


@dataclass(frozen=True)
class ScanResult:
    records: list[UsageRecord] = field(default_factory=list)
    titles: dict[str, str] = field(default_factory=dict)
    file_stats: dict[str, FileState] = field(default_factory=dict)
    malformed_lines: int = 0
    files_read: int = 0


def default_projects_dir() -> Path:
    override = os.environ.get("CLAUDE_PROJECTS_DIR")
    if override:
        return Path(override)
    return Path.home() / ".claude" / "projects"


def project_from_cwd(cwd: str | None) -> str:
    if not cwd:
        return "(unknown)"
    matched = WORKTREE_RE.match(cwd)
    base = matched.group("parent") if matched else cwd
    return os.path.basename(base.rstrip("/")) or "(root)"


def session_title(raw: str) -> str:
    command = COMMAND_NAME_RE.search(raw)
    if command:
        return command.group(1).strip()
    return " ".join(raw.split())


# `scan.local_day` is re-exported from .dates — see the import above. It is
# how the rest of the package has always named this, but the parsing itself
# belongs with the other timestamp handling rather than with the JSONL reader.


def _is_human_turn(entry: dict, kind: str | None) -> bool:
    """Whether this record is the person typing, rather than the machine.

    Tool results come back as `user` records too — they are the agent
    feeding itself — so the type alone does not separate them. A human turn
    carries plain text; a tool result carries tool_result blocks. Meta
    records are injected by the harness and are not somebody typing either.
    """
    if kind != "user" or entry.get("isMeta"):
        return False
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, str):
        return True
    if isinstance(content, list):
        return not any(
            isinstance(block, dict) and block.get("type") == "tool_result"
            for block in content
        )
    return False


def _plain_text(message: dict | None) -> str:
    """The human-typed text of a message, however it is shaped."""
    content = (message or {}).get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return ""


def _skill_origin(
    skill: str, sidechain: bool, tool_skill: str | None, command: str | None
) -> str:
    """Who started this skill run.

    Three traces, and on real transcripts they partition cleanly: a `Skill`
    tool call in the assistant's own output, a slash command the person
    typed, or neither — which happens only inside subagent transcripts,
    where the skill is inherited from whoever spawned the agent and the
    trigger lives in the parent file.
    """
    short = skill.split(":")[-1]
    if tool_skill and short in tool_skill:
        return ORIGIN_MODEL
    if command and short in command:
        return ORIGIN_USER
    if sidechain:
        return ORIGIN_SUBAGENT
    return ORIGIN_UNKNOWN


def _cache_miss(message: dict) -> tuple[int, str]:
    """Prefix tokens the cache failed to hold, and why.

    Lives in message.diagnostics.cache_miss_reason. A miss means the prefix
    is re-processed and billed at cache-*write* rates rather than the 0.1x
    read rate, so the gap between the two is money spent on nothing.
    """
    diagnostics = message.get("diagnostics")
    if not isinstance(diagnostics, dict):
        return 0, ""
    reason = diagnostics.get("cache_miss_reason")
    if not isinstance(reason, dict):
        return 0, ""
    return int(reason.get("cache_missed_input_tokens") or 0), str(reason.get("type") or "")


def _cache_writes(usage: dict) -> tuple[int, int]:
    nested = usage.get("cache_creation")
    if isinstance(nested, dict) and (
        nested.get("ephemeral_5m_input_tokens") or nested.get("ephemeral_1h_input_tokens")
    ):
        return (
            int(nested.get("ephemeral_5m_input_tokens") or 0),
            int(nested.get("ephemeral_1h_input_tokens") or 0),
        )
    return int(usage.get("cache_creation_input_tokens") or 0), 0


def _digest(handle, length: int) -> str:
    """Hash the first `length` bytes. Empty for a zero length."""
    if length <= 0:
        return ""
    handle.seek(0)
    return hashlib.sha256(handle.read(length)).hexdigest()


def _resume_offset(handle, previous: FileState | None, size: int) -> int:
    """Byte offset to resume this transcript from, or 0 to re-read it whole.

    Resuming is only safe when this is demonstrably the same file, extended.
    Each way that can fail falls back to a full re-read rather than risk
    silently skipping records:

    - no previous state, or none carrying an offset (rows written before
      offsets were recorded have offset 0 and an empty head)
    - the file is now shorter than the offset, so it was truncated
    - the bytes we already consumed have changed, so it was rewritten in
      place rather than appended to

    The digest deliberately spans min(HEAD_BYTES, offset) rather than a
    fixed HEAD_BYTES: for a transcript still smaller than HEAD_BYTES the
    fixed prefix *is* the whole file, so every append would change it and
    no read would ever resume.
    """
    if previous is None or previous.offset <= 0 or not previous.head:
        return 0
    if size < previous.offset:
        return 0
    if _digest(handle, min(HEAD_BYTES, previous.offset)) != previous.head:
        return 0
    return previous.offset


def scan(
    projects_dir: Path,
    skip: dict[str, FileState] | None = None,
    *,
    source: str = "code",
    project_resolver: Callable[[str | None], str] | None = None,
    pattern: str = DEFAULT_PATTERN,
) -> ScanResult:
    """Walk every transcript under projects_dir.

    skip maps a path to the FileState left by the last pass. A file whose
    size and mtime are unchanged is not reopened at all. A file that has
    only grown is resumed from its recorded offset rather than re-parsed
    from the top — the active session's transcript is rewritten-by-append
    on every message, and re-reading it whole was the single largest cost
    in a warm refresh.

    source stamps every record with the Claude surface it came from.
    project_resolver overrides how a record's cwd becomes a project label:
    Cowork sessions all run in a per-session `outputs` directory, so the
    default basename rule would file every one of them under "outputs".
    pattern narrows the walk; Cowork's per-session `.claude` trees mean the
    default recursive glob visits hundreds of directories per session to
    find a handful of transcripts.
    """
    skip = skip or {}
    resolve_project = project_resolver or project_from_cwd
    records: list[UsageRecord] = []
    titles: dict[str, str] = {}
    stats: dict[str, FileState] = {}
    seen_ids: set[str] = set()
    malformed = 0
    files_read = 0

    # Recursive: subagent transcripts live at <project>/<session>/subagents/,
    # and workflow agents deeper still under subagents/workflows/wf_<id>/.
    for path in sorted(Path(projects_dir).glob(pattern)):
        key = str(path)
        try:
            info = path.stat()
        except OSError:
            continue
        previous = skip.get(key)
        if previous is not None and (previous.size, previous.mtime) == (
            info.st_size,
            info.st_mtime,
        ):
            stats[key] = previous
            continue
        files_read += 1

        try:
            handle = path.open("rb")
        except OSError:
            # Deliberately no stats[key] here: recording a file as ingested
            # before it was successfully read would mark it done forever.
            continue
        with handle:
            start = _resume_offset(handle, previous, info.st_size)
            handle.seek(start)
            chunk = handle.read()

            # Only whole lines: a transcript caught mid-append ends in a
            # partial line, and consuming it would both miss that record
            # and leave the offset mid-line for the next pass.
            end = chunk.rfind(b"\n") + 1
            head = _digest(handle, min(HEAD_BYTES, start + end))
        # Per file: transcripts are one conversation each, so machine time
        # never spans two of them. Resuming mid-file starts with no previous
        # moment, which drops one gap and is not worth carrying state for.
        previous_moment = None
        pending_work = 0.0
        pending_wait = 0.0
        last_emitted = None
        mode = UNKNOWN_MODE
        pending_denials = 0
        pending_injections = 0
        pending_tool_skill = None
        pending_tool_age = 0
        pending_command = None
        previous_skill = None
        run_origin = ""
        run_id = ""
        run_start = 0
        prompt_id = ""
        for raw in chunk[:end].split(b"\n"):
            if not raw.strip():
                continue
            try:
                entry = json.loads(raw.decode("utf-8", "replace"))
            except ValueError:
                malformed += 1
                continue
            if not isinstance(entry, dict):
                malformed += 1
                continue

            session_id = entry.get("sessionId") or entry.get("session_id") or ""
            kind = entry.get("type")

            # Only user records carry promptId — 19_682 of them against 0
            # assistant records on a real history — so the id has to be
            # held from the turn that opened the prompt until the messages
            # that answer it. Tool results repeat their own prompt's id,
            # which is what makes a whole turn group rather than just its
            # first reply.
            declared_prompt = entry.get("promptId")
            if declared_prompt:
                prompt_id = str(declared_prompt)

            declared = entry.get("permissionMode")
            if declared:
                mode = str(declared)
            if entry.get("toolDenialKind"):
                pending_denials += 1
            if kind == "user":
                for name in COMMAND_NAME_RE.findall(_plain_text(entry.get("message"))):
                    pending_command = name.strip().lstrip("/")
            if kind == "attachment":
                # Not files somebody pasted: 33% are task reminders, 16%
                # tool deltas, 14% skill listings. They are context the
                # harness pushes in, and the tool/instruction ones are what
                # invalidate the prompt cache.
                pending_injections += 1

            # Machine time, accumulated across every record type. A gap that
            # ends at a human turn is the human thinking and is discarded;
            # everything else is the model generating or a tool running.
            # Tool results are not emitted as records, so their gaps pile up
            # in `pending` and land on the assistant message that follows.
            moment = instant(entry.get("timestamp") or "")
            if moment is not None:
                if previous_moment is not None:
                    gap = (moment - previous_moment).total_seconds()
                    # A gap ending at a human turn is the human thinking,
                    # so it is not added — but anything already pending is
                    # machine work that genuinely happened and must survive
                    # to be attributed to the next message. Zeroing it here
                    # silently dropped 10.9 hours on a real history.
                    if _is_human_turn(entry, kind):
                        if 0 < gap <= MAX_WAIT_GAP_SECONDS:
                            pending_wait += gap
                    elif 0 < gap <= MAX_WORK_GAP_SECONDS:
                        pending_work += gap
                previous_moment = moment

            if kind == "user" and not entry.get("isMeta") and session_id not in titles:
                # Only when reading from the top: resuming mid-file, the
                # first user message we see is not the session's first, and
                # emitting it would overwrite a correct stored title.
                if start == 0:
                    content = (entry.get("message") or {}).get("content")
                    if isinstance(content, str) and content.strip():
                        titles[session_id] = session_title(content)
                continue

            if kind != "assistant":
                continue
            message = entry.get("message") or {}

            # Before the dedupe below: one assistant message is written to
            # the transcript more than once, and the tool_use block can
            # arrive in a later copy than the attribution did. Dropping the
            # duplicate discards the Skill call with it, which credited 42
            # of 45 model-invoked runs to nobody.
            for block in message.get("content") or []:
                if (
                    isinstance(block, dict)
                    and block.get("type") == "tool_use"
                    and block.get("name") == "Skill"
                ):
                    pending_tool_skill = str((block.get("input") or {}).get("skill") or "")
                    pending_tool_age = 0
                    # The call may land after its own run has already begun,
                    # for the same reason. Upgrade the run rather than lose it.
                    if (
                        run_id
                        and run_origin == ORIGIN_UNKNOWN
                        and previous_skill
                        and previous_skill.split(":")[-1] in pending_tool_skill
                    ):
                        run_origin = ORIGIN_MODEL
                        for index in range(run_start, len(records)):
                            if records[index].skill_run == run_id:
                                records[index] = records[index]._replace(
                                    skill_origin=ORIGIN_MODEL
                                )
                        pending_tool_skill = None

            usage = message.get("usage")
            if not isinstance(usage, dict) or not usage:
                continue
            message_id = message.get("id")
            if not message_id or message_id in seen_ids:
                continue
            seen_ids.add(message_id)

            skill = str(entry.get("attributionSkill") or "(none)")
            if skill != previous_skill:
                run_origin, run_id = "", ""
                if skill != "(none)":
                    run_id = str(message_id)
                    run_start = len(records)
                    run_origin = _skill_origin(
                        skill,
                        bool(entry.get("isSidechain")),
                        pending_tool_skill if pending_tool_age <= SKILL_TRIGGER_WINDOW else None,
                        pending_command,
                    )
                    pending_tool_skill = pending_command = None
                previous_skill = skill
            if pending_tool_skill is not None:
                pending_tool_age += 1

            write_5m, write_1h = _cache_writes(usage)
            ts = entry.get("timestamp") or ""
            missed, miss_reason = _cache_miss(message)
            records.append(
                UsageRecord(
                    message_id=message_id,
                    ts=ts,
                    day=local_day(ts),
                    model=message.get("model") or "(unknown)",
                    project=resolve_project(entry.get("cwd")),
                    skill=skill,
                    session_id=session_id,
                    input_tokens=int(usage.get("input_tokens") or 0),
                    output_tokens=int(usage.get("output_tokens") or 0),
                    cache_read_tokens=int(usage.get("cache_read_input_tokens") or 0),
                    cache_write_5m=write_5m,
                    cache_write_1h=write_1h,
                    speed=usage.get("speed"),
                    is_subagent=bool(entry.get("isSidechain")),
                    source=source,
                    work_seconds=pending_work,
                    wait_seconds=pending_wait,
                    mode=mode,
                    effort=str(entry.get("effort") or "(none)"),
                    branch=str(entry.get("gitBranch") or "(none)"),
                    mcp_server=str(entry.get("attributionMcpServer") or ""),
                    cache_missed_tokens=missed,
                    cache_miss_reason=miss_reason,
                    stop_reason=str(message.get("stop_reason") or ""),
                    hour=local_hour(ts),
                    denials=pending_denials,
                    injections=pending_injections,
                    skill_origin=run_origin,
                    skill_run=run_id,
                    prompt_run=prompt_id,
                )
            )
            last_emitted = len(records) - 1
            pending_work = 0.0
            pending_wait = 0.0
            pending_denials = 0
            pending_injections = 0
        # Tool runs after the last message that carried usage have nowhere
        # to land, because only usage-bearing messages become records. Give
        # them to the last record this file produced rather than lose them:
        # 4.8 hours were stranded this way across 441 real transcripts.
        leftover = pending_work or pending_wait or pending_denials or pending_injections
        if leftover and last_emitted is not None:
            tail = records[last_emitted]
            records[last_emitted] = tail._replace(
                work_seconds=tail.work_seconds + pending_work,
                wait_seconds=tail.wait_seconds + pending_wait,
                denials=tail.denials + pending_denials,
                injections=tail.injections + pending_injections,
            )

        stats[key] = FileState(
            size=info.st_size, mtime=info.st_mtime, offset=start + end, head=head
        )

    return ScanResult(
        records=records,
        titles=titles,
        file_stats=stats,
        malformed_lines=malformed,
        files_read=files_read,
    )
