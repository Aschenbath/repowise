"""Service groups: fold a large estate's services into a few named systems.

A map of 300 services is a wall of cards. Grouping gives it a first level to
read: each service joins at most one group, found by three sources in priority
order, each claiming only what the earlier ones left:

1. **tag**: the repo declares it in ``.repowise-workspace.yaml``. A
   ``group:<name>`` tag wins; otherwise the repo's first tag.
2. **name**: a shared leading name of repo aliases (``ordering.api``,
   ``ordering.worker`` -> ``ordering``), kept only when it spans 2+ repos and
   under half the estate's repos. A prefix that covers half the estate is an
   organisation prefix, not a system, so the family is split on its next name
   token instead (``acme-billing-*`` inside ``acme-*``), up to
   :data:`MAX_NAME_DEPTH` tokens.
3. **linked**: community detection over runtime and data edges among the
   services still ungrouped, labelled by the member most services call.

A group is kept only with 2+ members; a lone service falls through to the next
source and may end ungrouped. Groups are emitted only when they reduce
something: more than :data:`MIN_SERVICES_TO_GROUP` services and at least two
groups. Below that every service is drawn on its own and no node carries a
group.

Pure and deterministic: inputs are walked in sorted order and the community
detection is seeded, so the same graph always yields the same groups. It reads
only node and edge attributes, so a caller holding a stored ``system_graph.json``
can run it through :meth:`SystemGraph.from_dict`.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from repowise.core.workspace.system_graph import SystemEdge, SystemNode

_log = logging.getLogger("repowise.workspace.grouping")

#: Groups appear only above this many services; a smaller map reads fine flat.
MIN_SERVICES_TO_GROUP = 40

#: ...and only when at least this many groups (each 2+ members) form.
MIN_GROUPS = 2

#: A name family must stay under this share of the estate's repos.
MAX_NAME_FAMILY_SHARE = 0.5

#: Deepest alias prefix a name family may use (``a``, ``a-b``, ``a-b-c``).
MAX_NAME_DEPTH = 3

#: Edge kinds the linkage source clusters on: what services call or share at
#: run time. Package edges fan into library hubs and would pull every service
#: toward the shared libraries; co-change is correlation, not a dependency.
LINKAGE_KINDS: frozenset[str] = frozenset({"http", "grpc", "socket", "event", "db"})

#: Linkage is skipped (those services stay ungrouped) past this many linked
#: service pairs. Louvain is near-linear in edges; at this size it runs well
#: under a second. Ceiling: an estate past it needs a declared ``group:`` tag
#: or a faster partitioner.
MAX_LINKAGE_PAIRS = 50_000

#: Fixed seed for the community detection, the same one file communities use.
LINKAGE_SEED = 42

GROUP_SOURCES: tuple[str, ...] = ("tag", "name", "linked")

_GROUP_TAG_PREFIX = "group:"
_TOKEN = re.compile(r"[A-Za-z0-9]+")


@dataclass(frozen=True)
class ServiceGroup:
    """One group of services: what the map draws as a single card."""

    id: str  # "<source>:<key>", stable across rebuilds of the same estate
    label: str
    source: str  # one of GROUP_SOURCES
    member_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "source": self.source,
            "member_count": self.member_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ServiceGroup:
        return cls(
            id=data["id"],
            label=data.get("label", data["id"]),
            source=data.get("source", ""),
            member_count=data.get("member_count", 0),
        )


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------


def _declared_name(tags: Iterable[str]) -> str | None:
    """The repo's declared group: a ``group:<name>`` tag, else its first tag."""
    tags = [t.strip() for t in tags if t and t.strip()]
    for tag in tags:
        if tag.startswith(_GROUP_TAG_PREFIX) and tag[len(_GROUP_TAG_PREFIX) :].strip():
            return tag[len(_GROUP_TAG_PREFIX) :].strip()
    return tags[0] if tags else None


def _by_tag(
    nodes: list[SystemNode], repo_tags: Mapping[str, Iterable[str]]
) -> dict[str, list[SystemNode]]:
    groups: dict[str, list[SystemNode]] = {}
    for node in nodes:
        name = _declared_name(repo_tags.get(node.repo, ()))
        if name is not None:
            groups.setdefault(name, []).append(node)
    return groups


def _alias_prefix(alias: str, depth: int) -> tuple[str, str] | None:
    """``(key, label)`` for the first *depth* name tokens of *alias*.

    None when the alias has fewer tokens. An alias of exactly *depth* tokens
    joins the family its whole name starts (``ordering`` with ``ordering.api``).
    """
    tokens = list(_TOKEN.finditer(alias))
    if len(tokens) < depth:
        return None
    key = ".".join(t.group().lower() for t in tokens[:depth])
    return key, alias[tokens[0].start() : tokens[depth - 1].end()]


def _by_name(nodes: list[SystemNode], estate_repos: int) -> dict[str, tuple[str, list[SystemNode]]]:
    """Name families: ``{key: (label, members)}``, each 2+ repos and under half the estate."""
    max_repos = estate_repos * MAX_NAME_FAMILY_SHARE
    found: dict[str, tuple[str, list[SystemNode]]] = {}
    pending: list[list[SystemNode]] = [nodes]
    for depth in range(1, MAX_NAME_DEPTH + 1):
        too_wide: list[list[SystemNode]] = []
        for pool in pending:
            families: dict[str, tuple[str, list[SystemNode]]] = {}
            for node in pool:
                prefix = _alias_prefix(node.repo, depth)
                if prefix is not None:
                    key, label = prefix
                    families.setdefault(key, (label, []))[1].append(node)
            for key, (label, members) in families.items():
                repos = len({m.repo for m in members})
                if repos < 2:
                    continue
                if repos < max_repos:
                    found[key] = (label, members)
                else:
                    too_wide.append(members)
        pending = too_wide
    return found


def _by_linkage(
    nodes: list[SystemNode], edges: list[SystemEdge]
) -> list[list[SystemNode]]:
    """Seeded Louvain over runtime and data edges among *nodes*.

    Louvain because it needs no target group count, is near-linear, ships with
    networkx (a core dependency, unlike Leiden's graspologic, which would make
    the result depend on what is installed), and is deterministic under a fixed
    seed and a fixed insertion order.
    """
    by_id = {n.id: n for n in nodes}
    pair_weight: dict[tuple[str, str], int] = {}
    for edge in edges:
        if edge.kind not in LINKAGE_KINDS or edge.source == edge.target:
            continue
        if edge.source not in by_id or edge.target not in by_id:
            continue
        pair = (min(edge.source, edge.target), max(edge.source, edge.target))
        pair_weight[pair] = pair_weight.get(pair, 0) + max(edge.weight, 1)
    if not pair_weight:
        return []
    if len(pair_weight) > MAX_LINKAGE_PAIRS:
        _log.info(
            "Skipping linkage grouping: %d linked service pairs exceeds the %d cap",
            len(pair_weight),
            MAX_LINKAGE_PAIRS,
        )
        return []

    import networkx as nx  # deferred: only estates past the size gate pay the import

    graph = nx.Graph()
    graph.add_nodes_from(sorted({n for pair in pair_weight for n in pair}))
    graph.add_weighted_edges_from((a, b, w) for (a, b), w in sorted(pair_weight.items()))
    communities = nx.community.louvain_communities(graph, weight="weight", seed=LINKAGE_SEED)
    return [[by_id[nid] for nid in sorted(c)] for c in communities if len(c) >= 2]


def _fan_in_anchor(members: list[SystemNode], edges: list[SystemEdge]) -> SystemNode:
    """The member the most distinct services call (ties: name, then id)."""
    ids = {m.id for m in members}
    callers: dict[str, set[str]] = {nid: set() for nid in ids}
    for edge in edges:
        if edge.kind in LINKAGE_KINDS and edge.target in ids and edge.source != edge.target:
            callers[edge.target].add(edge.source)
    return min(members, key=lambda m: (-len(callers[m.id]), m.name, m.id))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def group_services(
    nodes: list[SystemNode],
    edges: list[SystemEdge],
    repo_tags: Mapping[str, Iterable[str]] | None = None,
) -> tuple[dict[str, str], list[ServiceGroup]]:
    """Assign services to groups: ``({node_id: group_id}, groups)``.

    Both are empty when the estate is at or under
    :data:`MIN_SERVICES_TO_GROUP` services or fewer than :data:`MIN_GROUPS`
    groups form. *repo_tags* maps a repo alias to its declared tags.
    """
    if len(nodes) <= MIN_SERVICES_TO_GROUP:
        return {}, []

    remaining = sorted(nodes, key=lambda n: n.id)
    estate_repos = len({n.repo for n in remaining})
    claimed: list[tuple[ServiceGroup, list[SystemNode]]] = []

    def claim(group_id: str, label: str, source: str, members: list[SystemNode]) -> None:
        claimed.append((ServiceGroup(group_id, label, source, len(members)), members))

    for name, members in sorted(_by_tag(remaining, repo_tags or {}).items()):
        if len(members) >= 2:
            claim(f"tag:{name}", name, "tag", members)
    taken = {m.id for _, members in claimed for m in members}
    remaining = [n for n in remaining if n.id not in taken]

    for key, (label, members) in sorted(_by_name(remaining, estate_repos).items()):
        claim(f"name:{key}", label, "name", members)
    taken = {m.id for _, members in claimed for m in members}
    remaining = [n for n in remaining if n.id not in taken]

    for members in _by_linkage(remaining, edges):
        anchor = _fan_in_anchor(members, edges)
        claim(f"linked:{anchor.id}", anchor.name, "linked", members)

    if len(claimed) < MIN_GROUPS:
        return {}, []
    assignment = {m.id: group.id for group, members in claimed for m in members}
    groups = sorted((g for g, _ in claimed), key=lambda g: (-g.member_count, g.id))
    return assignment, groups
