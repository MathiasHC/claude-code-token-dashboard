"""Claude Desktop's local agent mode ("Cowork").

Cowork runs Claude Code inside Desktop, redirecting HOME to a per-session
directory. Each session therefore keeps a private, complete copy of the
ordinary transcript tree:

    <root>/<install>/<org>/local_<id>/.claude/projects/<encoded-cwd>/<session>.jsonl

The JSONL is byte-for-byte the same shape Claude Code writes, so scan.scan
parses it unchanged. Only two things differ, and this module supplies both:

1. Every session's cwd is its own `outputs` directory, so the default
   basename rule would file all of them under the single project "outputs".
   Each session has a sidecar `local_<id>.json` carrying the folder the user
   actually pointed the session at, plus a human title; we prefer those.

2. Sessions also write an `audit.jsonl` mirroring the transcript's assistant
   messages. It carries the same `message.id` values, so scan's existing
   dedup drops it — verified against live data, where the audit files add
   exactly zero records and zero cost. Nothing here needs to exclude them.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from pathlib import Path

SESSION_DIR_RE = re.compile(r"/(local_[0-9a-f-]+)(?:/|$)")
FALLBACK_LABEL = "(cowork)"

#: Targets the transcript trees directly instead of walking each session's
#: whole private .claude directory. Still recursive below `projects/`, so
#: nested subagent and workflow transcripts are picked up as before.
TRANSCRIPT_PATTERN = "*/*/local_*/.claude/projects/**/*.jsonl"


def default_cowork_dir() -> Path:
    override = os.environ.get("CLAUDE_COWORK_DIR")
    if override:
        return Path(override)
    return (
        Path.home()
        / "Library"
        / "Application Support"
        / "Claude"
        / "local-agent-mode-sessions"
    )


def _label_for(meta: dict) -> str:
    """Prefer the folder the session was pointed at, then its title."""
    folders = meta.get("userSelectedFolders")
    if isinstance(folders, list):
        for folder in folders:
            if isinstance(folder, str) and folder.strip():
                name = os.path.basename(folder.rstrip("/"))
                if name:
                    return name
    title = meta.get("title")
    if isinstance(title, str) and title.strip():
        return " ".join(title.split())
    return FALLBACK_LABEL


def session_labels(root: Path) -> dict[str, str]:
    """Map each `local_<id>` session directory to a project label.

    A malformed or unreadable sidecar is skipped rather than fatal: one bad
    session must not cost us the whole surface's usage.
    """
    labels: dict[str, str] = {}
    try:
        candidates = sorted(Path(root).glob("*/*/local_*.json"))
    except OSError:
        return labels
    for path in candidates:
        try:
            meta = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, ValueError):
            continue
        if not isinstance(meta, dict):
            continue
        session_dir = meta.get("sessionId") or path.stem
        if isinstance(session_dir, str) and session_dir:
            labels[session_dir] = _label_for(meta)
    return labels


def lazy_project_resolver(load: Callable[[], dict[str, str]]):
    """Build a cwd -> project-label function, loading labels only on demand.

    A warm refresh usually reads no Cowork transcript at all, and reading
    every sidecar JSON to answer zero questions cost more per request than
    every other part of the scan combined. Deferring the load until the
    first record needs a label makes that cost nothing on the passes that
    resolve nothing, and unchanged on the passes that do.
    """
    labels: dict[str, str] = {}
    loaded = False

    def resolve(cwd: str | None) -> str:
        nonlocal loaded
        if not cwd:
            return FALLBACK_LABEL
        matched = SESSION_DIR_RE.search(cwd)
        if not matched:
            return FALLBACK_LABEL
        if not loaded:
            labels.update(load())
            loaded = True
        return labels.get(matched.group(1), FALLBACK_LABEL)

    return resolve
