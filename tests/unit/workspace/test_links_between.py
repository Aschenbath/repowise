"""Links between two services, read from the stored artifacts.

``eshop.json`` is cut from a real index of the eShop sample: every node of its
system graph, the contract links touching webapp or ordering.api, the
contracts at their ends and the service links.
"""

from __future__ import annotations

import json
from pathlib import Path

from repowise.core.workspace.contracts import Contract, ContractLink
from repowise.core.workspace.cross_repo import CrossRepoOverlay
from repowise.core.workspace.extractors.service_boundary import ServiceBoundary
from repowise.core.workspace.links_between import A_TO_B, B_TO_A, links_between
from repowise.core.workspace.system_graph import build_system_graph

FIXTURE = Path(__file__).parents[2] / "fixtures" / "workspace_links" / "eshop.json"


def _eshop(**kwargs):
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return links_between(
        data["nodes"],
        data["contracts"],
        data["contract_links"],
        data["service_links"],
        **kwargs,
    )


def test_webapp_and_ordering_api_share_eight_links_all_one_way():
    result = _eshop(a="webapp", b="ordering.api")
    assert result["total"] == 8
    assert result["totals"] == [
        {"kind": "event", "direction": A_TO_B, "count": 6},
        {"kind": "http", "direction": A_TO_B, "count": 2},
    ]
    assert {lk["direction"] for lk in result["links"]} == {A_TO_B}
    http = [lk for lk in result["links"] if lk["kind"] == "http"]
    assert [lk["contract_id"] for lk in http] == [
        "http::GET::/api/orders",
        "http::POST::/api/orders",
    ]
    assert {lk["strategy"] for lk in http} == {"service_mapped"}


def test_swapping_the_pair_flips_the_direction_only():
    result = _eshop(a="ordering.api", b="webapp")
    assert result["total"] == 8
    assert {lk["direction"] for lk in result["links"]} == {B_TO_A}


def test_rows_carry_both_ends_origin():
    row = _eshop(a="webapp", b="ordering.api", kind="event")["links"][0]
    assert row["provider_origin"] == "dataflow"
    assert row["consumer_origin"] == "client_call"


def test_kind_filters_rows_but_keeps_every_kind_counted():
    result = _eshop(a="webapp", b="ordering.api", kind="http")
    assert result["total"] == 2
    assert {lk["kind"] for lk in result["links"]} == {"http"}
    assert sum(t["count"] for t in result["totals"]) == 8


def test_q_narrows_rows_and_totals():
    result = _eshop(a="webapp", b="ordering.api", q="orderstatuschangedtopaid")
    assert result["total"] == 1
    assert result["totals"] == [{"kind": "event", "direction": A_TO_B, "count": 1}]


def test_pages_are_cut_from_the_ordered_rows():
    whole = _eshop(a="webapp", b="ordering.api")["links"]
    page = _eshop(a="webapp", b="ordering.api", limit=3, offset=6)
    assert page["total"] == 8
    assert page["links"] == whole[6:9]


def test_a_gateway_route_counts_as_the_graph_counts_it():
    result = _eshop(a="webapp", b="catalog.api")
    assert sorted(lk["strategy"] for lk in result["links"]) == ["gateway_route", "service_mapped"]


def test_unknown_service_is_none():
    assert _eshop(a="webapp", b="nope") is None


def _contract(repo, file_path, cid, role):
    return Contract(
        repo=repo,
        contract_id=cid,
        contract_type="http",
        role=role,
        file_path=file_path,
        symbol_name=cid,
        confidence=0.85,
    )


def _link(cid, consumer_file, provider_file):
    return ContractLink(
        contract_id=cid,
        contract_type="http",
        match_type="exact",
        confidence=0.8,
        provider_repo="shop",
        provider_file=provider_file,
        provider_symbol=cid,
        provider_service=None,
        consumer_repo="shop",
        consumer_file=consumer_file,
        consumer_symbol=cid,
        consumer_service=None,
    )


def test_two_services_in_one_repo_resolve_like_the_graph():
    boundaries = {
        "shop": [
            ServiceBoundary(service_path="services/orders", service_name="orders"),
            ServiceBoundary(service_path="services/orders/worker", service_name="worker"),
            ServiceBoundary(service_path="services/cart", service_name="cart"),
        ]
    }
    links = [
        _link("http::GET::/orders", "services/cart/api.py", "services/orders/routes.py"),
        _link("http::POST::/orders", "services/cart/api.py", "services/orders/routes.py"),
        _link("http::GET::/carts", "services/orders/client.py", "services/cart/routes.py"),
        # The deeper boundary is its own service, not orders.
        _link("http::GET::/jobs", "services/cart/api.py", "services/orders/worker/jobs.py"),
        # Same service on both ends: never an edge, never a row.
        _link("http::GET::/self", "services/cart/a.py", "services/cart/b.py"),
    ]
    contracts = [_contract("shop", "services/orders/routes.py", "http::GET::/orders", "provider")]
    graph = build_system_graph(contracts, links, CrossRepoOverlay(), boundaries).to_dict()
    cart, orders = "shop::services/cart", "shop::services/orders"

    result = links_between(
        graph["nodes"],
        [c.to_dict() for c in contracts],
        [lk.to_dict() for lk in links],
        a=cart,
        b=orders,
    )

    assert result["totals"] == [
        {"kind": "http", "direction": A_TO_B, "count": 2},
        {"kind": "http", "direction": B_TO_A, "count": 1},
    ]
    weights = {(e["source"], e["target"]): e["weight"] for e in graph["edges"]}
    assert weights[(cart, orders)] == 2
    assert weights[(orders, cart)] == 1
    assert result["links"][0]["provider_origin"] is None  # stored before origins
    assert (
        links_between(graph["nodes"], [], [lk.to_dict() for lk in links], a=cart, b=cart)["total"]
        == 0
    )
