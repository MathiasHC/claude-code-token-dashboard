"""Composes the scan across every Claude surface that keeps local transcripts.

Today that is Claude Code and Claude Desktop's Cowork mode. Both write the
same JSONL, so this module only decides *which roots* to walk and *what to
call* what it finds — the parsing all still happens in scan.py.

Surfaces without local token data (Desktop chat, Claude in Chrome, claude.ai)
are deliberately absent: they bill server-side and persist no usage on disk,
so there is nothing here to read.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import cowork, scan
from .scan import ScanResult


@dataclass(frozen=True)
class Source:
    """One transcript root and the label its records carry."""

    name: str
    root: Path
    #: Built per scan, not once, so sessions created since the last refresh
    #: still resolve to a project label instead of the fallback.
    resolver_factory: Callable[[Path], Callable[[str | None], str]] | None = None
    pattern: str = scan.DEFAULT_PATTERN


def default_sources(
    projects_dir: Path | None = None,
    cowork_dir: Path | None = None,
) -> list[Source]:
    return [
        Source(
            name="code",
            root=projects_dir if projects_dir is not None else scan.default_projects_dir(),
        ),
        Source(
            name="cowork",
            root=cowork_dir if cowork_dir is not None else cowork.default_cowork_dir(),
            resolver_factory=lambda root: cowork.lazy_project_resolver(
                lambda: cowork.session_labels(root)
            ),
            # Each Cowork session carries a whole private .claude tree
            # (backups, plugins, tasks, sessions...), so a bare **/*.jsonl
            # walks hundreds of directories per session to reach a handful of
            # transcripts — measured at roughly 8x the targeted glob. The only
            # files it stops visiting are the audit.jsonl mirrors, which
            # contribute nothing.
            pattern=cowork.TRANSCRIPT_PATTERN,
        ),
    ]


def scan_sources(
    sources: list[Source],
    skip: dict[str, tuple[int, float]] | None = None,
) -> ScanResult:
    """Scan every source and merge the results into one.

    Records are deduplicated by message_id across sources as well as within
    them. On live data the two roots share no message IDs at all, but a
    surface that ever mirrored another's transcripts must not double-count,
    and the cost of the guard is one set lookup per record.
    """
    skip = skip or {}
    records = []
    titles: dict[str, str] = {}
    file_stats: dict[str, tuple[int, float]] = {}
    seen: set[str] = set()
    malformed = 0
    files_read = 0

    for source in sources:
        root = Path(source.root)
        if not root.is_dir():
            # A surface that is not installed is simply absent, not an error:
            # Cowork does not exist on a machine without Claude Desktop.
            continue
        resolver = source.resolver_factory(root) if source.resolver_factory else None
        result = scan.scan(
            root,
            skip,
            source=source.name,
            project_resolver=resolver,
            pattern=source.pattern,
        )
        for record in result.records:
            if record.message_id in seen:
                continue
            seen.add(record.message_id)
            records.append(record)
        for session_id, title in result.titles.items():
            titles.setdefault(session_id, title)
        file_stats.update(result.file_stats)
        malformed += result.malformed_lines
        files_read += result.files_read

    return ScanResult(
        records=records,
        titles=titles,
        file_stats=file_stats,
        malformed_lines=malformed,
        files_read=files_read,
    )
