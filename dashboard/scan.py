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

from .dates import instant, local_day
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
            usage = message.get("usage")
            if not isinstance(usage, dict) or not usage:
                continue
            message_id = message.get("id")
            if not message_id or message_id in seen_ids:
                continue
            seen_ids.add(message_id)

            write_5m, write_1h = _cache_writes(usage)
            ts = entry.get("timestamp") or ""
            records.append(
                UsageRecord(
                    message_id=message_id,
                    ts=ts,
                    day=local_day(ts),
                    model=message.get("model") or "(unknown)",
                    project=resolve_project(entry.get("cwd")),
                    skill=str(entry.get("attributionSkill") or "(none)"),
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
                )
            )
            last_emitted = len(records) - 1
            pending_work = 0.0
            pending_wait = 0.0
        # Tool runs after the last message that carried usage have nowhere
        # to land, because only usage-bearing messages become records. Give
        # them to the last record this file produced rather than lose them:
        # 4.8 hours were stranded this way across 441 real transcripts.
        if (pending_work or pending_wait) and last_emitted is not None:
            tail = records[last_emitted]
            records[last_emitted] = tail._replace(
                work_seconds=tail.work_seconds + pending_work,
                wait_seconds=tail.wait_seconds + pending_wait,
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
