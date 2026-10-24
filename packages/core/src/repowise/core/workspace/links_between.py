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
    from collections.abc import Iterable, Iterator, Sequence

__all__ = ["A_TO_B", "B_TO_A", "links_between"]

#: The row's ``direction``: ``a`` calls / depends on ``b``, or the reverse.
A_TO_B = "a_to_b"
B_TO_A = "b_to_a"


def links_between(
    nodes: Sequence[dict],
    store: dict,
    *,
    a: str,
    b: str,
    kind: str | None = None,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any] | None:
    """The links between service nodes *a* and *b*, both directions, paged.

    *nodes* are ``system_graph.json``'s nodes; *store* is the parsed
    ``contracts.json`` payload (``contracts``, ``contract_links`` and, when
    present, ``service_links``).

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
    by_id = {n["id"]: n for n in nodes}
    if a not in by_id or b not in by_id:
        return None
    repos = {by_id[a]["repo"], by_id[b]["repo"]}
    terms = (q or "").lower().split()
    matched = [
        m
        for m in _pair_links(nodes, store, a, b, repos)
        if not terms or _matches_terms(_link_haystack(m[2]), terms)
    ]

    totals: dict[tuple[str, str], int] = {}
    for direction, link_kind, _ in matched:
        totals[(link_kind, direction)] = totals.get((link_kind, direction), 0) + 1

    rows = sorted((m for m in matched if kind is None or m[1] == kind), key=_row_order)
    origins = _origins(store.get("contracts", []), repos)
    return {
        "a": a,
        "b": b,
        "links": [_row(lk, k, d, origins) for d, k, lk in rows[offset : offset + limit]],
        "total": len(rows),
        "totals": [{"kind": k, "direction": d, "count": n} for (k, d), n in sorted(totals.items())],
    }


def _pair_links(
    nodes: Sequence[dict], store: dict, a: str, b: str, repos: set[str]
) -> Iterator[tuple[str, str, dict]]:
    """``(direction, edge kind, link)`` for every stored link from one of *a*, *b* to the other."""
    if a == b:
        return  # a service's calls into itself are never an edge
    boundaries = boundaries_from_nodes(nodes)
    direction_of = {(a, b): A_TO_B, (b, a): B_TO_A}
    for lk in [*store.get("contract_links", []), *store.get("service_links", [])]:
        if lk.get("consumer_repo") not in repos or lk.get("provider_repo") not in repos:
            continue
        # The builder's own reading of a link: a service link has no provider
        # file, so it lands on the provider's repo-root node.
        source = resolve_service_id(lk["consumer_repo"], lk.get("consumer_file"), boundaries)
        target = resolve_service_id(
            lk["provider_repo"], lk.get("provider_file") or None, boundaries
        )
        direction = direction_of.get((source, target))
        if direction is not None:
            yield direction, edge_kind_for_contract_type(lk.get("contract_type", "")), lk


def _row_order(m: tuple[str, str, dict]) -> tuple[str, ...]:
    direction, kind, lk = m
    return (
        direction,
        kind,
        lk.get("contract_id") or "",
        lk.get("consumer_file") or "",
        lk.get("provider_file") or "",
    )


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
