"""Every contract link between two services, in both directions, one page at a time.

The system graph folds a pair's links into one edge per ``(source, target,
kind)`` and keeps at most :data:`.system_graph.MAX_EDGE_REFS` contract ids on
it. This is the read behind such an edge: the links themselves, each end
resolved to its service with the resolver the graph builder used, so a row
here lands on exactly the edge the map drew.

Pure over the stored artifacts: the ``nodes`` of ``system_graph.json`` and the
``contracts``, ``contract_links`` and ``service_links`` rows of
``contracts.json``. Any server holding those payloads answers the same way.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from repowise.core.workspace.reads import _consumer_id, _link_haystack, _matches_terms, link_row
from repowise.core.workspace.system_graph import (
    boundaries_from_nodes,
    edge_kind_for_contract_type,
    resolve_service_id,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

__all__ = ["A_TO_B", "B_TO_A", "links_between"]

#: The row's ``direction``: ``a`` calls / depends on ``b``, or the reverse.
A_TO_B = "a_to_b"
B_TO_A = "b_to_a"


def links_between(
    nodes: Sequence[dict],
    contracts: Iterable[dict],
    contract_links: Iterable[dict],
    service_links: Iterable[dict] = (),
    *,
    a: str,
    b: str,
    kind: str | None = None,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any] | None:
    """The links between service nodes *a* and *b*, both directions, paged.

    Each row is a contract-link row (:func:`.reads.link_row`) plus its edge
    ``kind``, its ``direction`` (:data:`A_TO_B` when *a* is the consumer) and
    the ``provider_origin`` / ``consumer_origin`` of the contracts at its ends.
    Service links (a call whose host names a service but no route, a gateway
    route) are included, as the graph counts them.

    ``q`` keeps rows where every whitespace-separated term matches, ``kind``
    keeps one edge kind. ``total`` counts the filtered rows the page is cut
    from; ``totals`` counts per kind and direction under ``q`` but not
    ``kind``, so a kind filter keeps every kind's count visible.

    None when *a* or *b* is not a node of the graph.
    """
    node_ids = {n["id"]: n for n in nodes}
    if a not in node_ids or b not in node_ids:
        return None
    boundaries = boundaries_from_nodes(nodes)
    repos = {node_ids[a]["repo"], node_ids[b]["repo"]}
    terms = (q or "").lower().split()

    matched: list[tuple[str, str, dict]] = []
    for lk in [*contract_links, *service_links]:
        if lk.get("consumer_repo") not in repos or lk.get("provider_repo") not in repos:
            continue
        # The builder's own reading of a link: a service link has no provider
        # file, so it lands on the provider's repo-root node.
        source = resolve_service_id(lk["consumer_repo"], lk.get("consumer_file"), boundaries)
        target = resolve_service_id(
            lk["provider_repo"], lk.get("provider_file") or None, boundaries
        )
        if (source, target) == (a, b) and a != b:
            direction = A_TO_B
        elif (source, target) == (b, a) and a != b:
            direction = B_TO_A
        else:
            continue
        if terms and not _matches_terms(_link_haystack(lk), terms):
            continue
        matched.append((direction, edge_kind_for_contract_type(lk.get("contract_type", "")), lk))

    totals: dict[tuple[str, str], int] = {}
    for direction, link_kind, _ in matched:
        totals[(link_kind, direction)] = totals.get((link_kind, direction), 0) + 1

    rows = [m for m in matched if kind is None or m[1] == kind]
    rows.sort(
        key=lambda m: (
            m[0],
            m[1],
            m[2].get("contract_id") or "",
            m[2].get("consumer_file") or "",
            m[2].get("provider_file") or "",
        )
    )
    origins = _origins(contracts, repos)
    page = [
        _row(lk, link_kind, direction, origins)
        for direction, link_kind, lk in rows[offset : offset + limit]
    ]
    return {
        "a": a,
        "b": b,
        "links": page,
        "total": len(rows),
        "totals": [{"kind": k, "direction": d, "count": n} for (k, d), n in sorted(totals.items())],
    }


def _origins(contracts: Iterable[dict], repos: set[str]) -> dict[tuple, str | None]:
    """``(role, repo, file, contract_id) -> origin`` for the pair's repos; first row wins."""
    # One pass over the stored contracts per call, filtered to two repos. Fine at
    # census scale; a caller serving many pairs would index once and pass it in.
    out: dict[tuple, str | None] = {}
    for c in contracts:
        if c.get("repo") in repos:
            key = (c.get("role"), c.get("repo"), c.get("file_path"), c.get("contract_id"))
            out.setdefault(key, c.get("origin"))
    return out


def _row(lk: dict, kind: str, direction: str, origins: dict[tuple, str | None]) -> dict[str, Any]:
    return {
        **link_row(lk),
        "kind": kind,
        "direction": direction,
        "provider_origin": origins.get(
            ("provider", lk.get("provider_repo"), lk.get("provider_file"), lk.get("contract_id"))
        ),
        "consumer_origin": origins.get(
            ("consumer", lk.get("consumer_repo"), lk.get("consumer_file"), _consumer_id(lk))
        ),
    }
