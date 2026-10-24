"""The estate report document for a workspace, built once for every surface.

``repowise report estate`` and ``GET /api/workspace/estate`` both call
:func:`build_estate_document`, so the CLI and the web page read one document.
It opens each member's index the way the estate sections do
(:meth:`WorkspaceReportContext.each_index`), runs any repo-scope section, then
the workspace-scope sections once over the stored workspace artifacts.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from repowise.core.report.build import assemble, available_sections, collect_repo, collect_workspace
from repowise.core.report.model import ReportContext, Section, WorkspaceReportContext


async def _member(
    alias: str, root: Path, sections: Sequence[tuple[str, Section]]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """``(repos entry, repo-scope sections)`` for one member; unindexed when its
    index is absent, unreadable or holds no repository row."""
    from repowise.core.workspace.registry import open_repo_db, repo_db_path
    from repowise.core.workspace.repo_index import checkout_repository

    unindexed = ({"alias": alias, "name": root.name, "indexed": False}, [])
    if not repo_db_path(root).is_file():
        return unindexed
    try:
        engine, sessions = await open_repo_db(root)
    except Exception:  # one unreadable index must not lose the rest
        return unindexed
    try:
        async with sessions() as session:
            repo = await checkout_repository(session, root)
            if repo is None:
                return unindexed
            ctx = ReportContext(session, repo.id, root, alias=alias, head_commit=repo.head_commit)
            return await collect_repo(ctx, list(sections))
    except Exception:
        return unindexed
    finally:
        await engine.dispose()


async def build_estate_document(
    ws_root: Path, sections: Sequence[tuple[str, Section]] | None = None
) -> dict[str, Any]:
    """The ``estate`` report over the workspace at *ws_root*; *sections* defaults
    to every registered estate section."""
    picked = list(sections) if sections is not None else available_sections("estate")
    ctx = WorkspaceReportContext(ws_root)
    parts = [
        await _member(entry.alias, (ws_root / entry.path).resolve(), picked)
        for entry in ctx.config().repos
    ]
    ctx.repos = [entry for entry, _ in parts]
    workspace = await collect_workspace(ctx, picked)
    document = assemble({"kind": "workspace", "name": ws_root.name}, parts, workspace=workspace)
    document["kind"] = "estate"
    return document


__all__ = ["build_estate_document"]
