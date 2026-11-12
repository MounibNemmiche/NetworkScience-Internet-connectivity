"""Check Louvain orchestration and independent community-table accounting."""

import math
from types import MappingProxyType, SimpleNamespace

import networkx as nx
import pytest

from analysis.resilience.community_analysis import (
    CommunityAnalysisError,
    canonical_partition,
    partition_agreement,
    run_louvain,
)
from analysis.resilience.graph_loader import NodeMetadata


@pytest.fixture
def baseline():
    graph = nx.Graph()
    graph.add_nodes_from([1, 2, 3, 4, 10, 11, 12, 13])
    for group in ([1, 2, 3, 4], [10, 11, 12, 13]):
        graph.add_edges_from((a, b) for i, a in enumerate(group) for b in group[i + 1:])
    graph.add_edge(4, 10)
    focal = frozenset({1, 2, 11})
    external = frozenset({3, 4, 10, 12})
    countries = {1: "IT", 2: "IT", 3: "US", 4: "GB", 10: "NA", 11: "IT", 12: "GB", 13: ""}
    nodes = {asn: NodeMetadata(asn, f"AS{asn}", f"org-{asn}", f"Organization {asn}",
                               countries[asn], asn == 13) for asn in graph}
    return SimpleNamespace(
        graph=nx.freeze(graph), nodes=MappingProxyType(nodes),
        analysis_asns=frozenset(graph), focal_asns=focal, external_asns=external,
        candidate_providers=frozenset({3, 10}),
        provider_customers=MappingProxyType({3: frozenset({1, 2}), 10: frozenset({11})}),
        baseline_degrees=MappingProxyType(dict(graph.degree())),
        report={"baseline_fingerprint": "tiny-test-baseline", "warnings": []},
    )


def test_canonical_partition_uses_size_then_smallest_asn():
    graph = nx.path_graph(range(1, 7))
    assert canonical_partition(graph, [{1}, {2, 3}, {4, 5, 6}]) == (
        frozenset({4, 5, 6}), frozenset({2, 3}), frozenset({1}))
    graph = nx.path_graph(range(1, 5))
    assert canonical_partition(graph, [{3, 4}, {1, 2}]) == (
        frozenset({1, 2}), frozenset({3, 4}))


@pytest.mark.parametrize("groups", [
    [], [{1, 2, 3, 4}, set()], [{1, 2}, {2, 3, 4}], [{1, 2}, {3}],
    [{1, 2}, {3, 4, 99}], [[1, 1, 2], [3, 4]],
])
def test_canonical_partition_rejects_invalid_cover(groups):
    with pytest.raises(CommunityAnalysisError):
        canonical_partition(nx.path_graph(range(1, 5)), groups)


def test_louvain_repeatability_complete_coverage_and_library_modularity(baseline):
    before_edges = set(baseline.graph.edges())
    first = run_louvain(baseline, seeds=(11, 22, 33))
    second = run_louvain(baseline, seeds=(33, 11, 22))
    by_seed = {run.seed: run for run in second.runs}
    assert {run.seed for run in first.runs} == {11, 22, 33}
    for run in first.runs:
        assert run.communities == by_seed[run.seed].communities
        assert run.communities == (frozenset({1, 2, 3, 4}), frozenset({10, 11, 12, 13}))
        assert set().union(*run.communities) == set(baseline.graph)
        assert sum(len(group) for group in run.communities) == len(baseline.graph)
        expected = nx.community.modularity(baseline.graph, run.communities, weight=None, resolution=1)
        assert run.scores.modularity == pytest.approx(expected, abs=1e-12)
        assert run.networkx_modularity == pytest.approx(expected, abs=1e-12)
        assert run.scores.modularity == pytest.approx(11 / 26)
        assert run.louvain_seconds >= 0
        assert run.scoring_seconds >= 0
    assert first.selected.seed == second.selected.seed == 11
    assert first.runs[0].scores.walk is first.runs[1].scores.walk
    assert set(baseline.graph.edges()) == before_edges
    assert nx.is_frozen(baseline.graph)


def test_louvain_arguments_and_seed_tie_break_are_explicit(baseline, monkeypatch):
    calls = []

    def fixed_partition(graph, **kwargs):
        calls.append(kwargs)
        assert graph is baseline.graph
        return [{10, 11, 12, 13}, {1, 2, 3, 4}]

    monkeypatch.setattr(nx.community, "louvain_communities", fixed_partition)
    analysis = run_louvain(baseline, seeds=(33, 22, 11), threshold=1e-8)
    assert analysis.selected.seed == 11
    assert {call["seed"] for call in calls} == {11, 22, 33}
    assert all(call["weight"] is None and call["resolution"] == 1.0 for call in calls)
    assert all(call["threshold"] == 1e-8 and call["max_level"] is None for call in calls)
    assert all(call["backend"] == "networkx" for call in calls)


def test_selection_uses_modularity_before_seed(baseline, monkeypatch):
    good = [{1, 2, 3, 4}, {10, 11, 12, 13}]

    def partitions(graph, **kwargs):
        return {11: [set(graph)], 22: good, 33: [{node} for node in graph]}[kwargs["seed"]]

    monkeypatch.setattr(nx.community, "louvain_communities", partitions)
    result = run_louvain(baseline)
    assert result.selected.seed == 22
    assert result.selected.scores.modularity == max(run.scores.modularity for run in result.runs)


def test_invalid_algorithm_output_is_rejected(baseline, monkeypatch):
    monkeypatch.setattr(nx.community, "louvain_communities", lambda graph, **kwargs: [{1, 2}])
    with pytest.raises(ValueError):
        run_louvain(baseline, seeds=(11,))


def test_summary_and_edge_accounting_reconcile_independently(baseline):
    result = run_louvain(baseline, seeds=(11,))
    rows = result.summary_rows
    assert sum(row["node_count"] for row in rows) == len(baseline.graph)
    assert sum(row["focal_asn_count"] for row in rows) == len(baseline.focal_asns)
    assert sum(row["external_asn_count"] for row in rows) == len(baseline.external_asns)
    assert sum(row["unknown_country_asn_count"] for row in rows) == 1
    assert sum(row["candidate_provider_count"] for row in rows) == len(baseline.candidate_providers)
    assert sum(row["node_fraction"] for row in rows) == pytest.approx(1)
    assert sum(row["focal_fraction_of_all"] for row in rows) == pytest.approx(1)
    assert sum(row["candidate_fraction_of_all"] for row in rows) == pytest.approx(1)
    assert sum(row["degree_volume"] for row in rows) == 2 * baseline.graph.number_of_edges()

    edge_rows = result.edge_rows
    pairs = [(row["source_community"], row["target_community"]) for row in edge_rows]
    assert len(pairs) == len(set(pairs))
    assert all(a <= b for a, b in pairs)
    assert sum(row["edge_count"] for row in edge_rows) == baseline.graph.number_of_edges()
    assignment = {node: i for i, group in enumerate(result.selected.communities) for node in group}
    observed_edges = {(row["source_community"], row["target_community"]): row["edge_count"] for row in edge_rows}
    reference = {}
    for a, b in baseline.graph.edges():
        pair = tuple(sorted((assignment[a], assignment[b])))
        reference[pair] = reference.get(pair, 0) + 1
    assert observed_edges == reference

    for row in rows:
        i = row["community_id"]
        group = result.selected.communities[i]
        assert row["seed"] == result.selected.seed
        assert row["node_count"] == len(group)
        assert row["focal_asn_count"] == len(group & baseline.focal_asns)
        assert row["candidate_provider_count"] == len(group & baseline.candidate_providers)
        assert row["focal_fraction_within_community"] == pytest.approx(len(group & baseline.focal_asns) / len(group))
        internal = sum(a in group and b in group for a, b in baseline.graph.edges())
        boundary = sum((a in group) != (b in group) for a, b in baseline.graph.edges())
        assert row["internal_edges"] == internal
        assert row["boundary_edges"] == boundary
        assert row["degree_volume"] == 2 * internal + boundary


def test_provider_membership_and_seed_sensitivity(baseline):
    result = run_louvain(baseline)
    rows = result.provider_rows
    assert {row["asn"] for row in rows} == baseline.candidate_providers
    assert len(rows) == len(baseline.candidate_providers)
    for row in rows:
        assert row["asn"] in result.selected.communities[row["community_id"]]
        assert row["focal_customer_count"] == len(baseline.provider_customers[row["asn"]])
    pairs = {frozenset((row["seed_a"], row["seed_b"])) for row in result.sensitivity_rows}
    assert pairs == {frozenset({11, 22}), frozenset({11, 33}), frozenset({22, 33})}
    for row in result.sensitivity_rows:
        assert row["adjusted_rand_index"] == pytest.approx(1)
        assert row["variation_information_bits"] == pytest.approx(0, abs=1e-12)


@pytest.mark.parametrize("seeds", [(), (11, 11), (True,), (1.5,), ("11",), None])
def test_invalid_seeds_are_rejected(baseline, seeds):
    with pytest.raises(CommunityAnalysisError):
        run_louvain(baseline, seeds=seeds)


@pytest.mark.parametrize("resolution", [0, 0.5, 2, math.nan, True, "1.0"])
def test_noncourse_resolution_is_rejected(baseline, resolution):
    with pytest.raises(CommunityAnalysisError):
        run_louvain(baseline, resolution=resolution)


@pytest.mark.parametrize("threshold", [0, -1, math.inf, math.nan, True, "small"])
def test_invalid_threshold_is_rejected(baseline, threshold):
    with pytest.raises(CommunityAnalysisError):
        run_louvain(baseline, threshold=threshold)


def test_declared_damping_is_forwarded_to_the_shared_walk(baseline):
    result = run_louvain(baseline, seeds=(11, 22), damping=0.4)
    assert all(run.scores.walk.damping == 0.4 for run in result.runs)


@pytest.mark.parametrize("left,right,ari,vi", [
    ([{1, 2}, {3, 4}], [{3, 4}, {1, 2}], 1, 0),
    ([{1, 2}, {3, 4}], [{1, 3}, {2, 4}], -0.5, 2),
    ([{1, 2, 3, 4}], [{1, 2}, {3, 4}], 0, 1),
    ([{1}, {2}, {3}, {4}], [{1}, {2}, {3}, {4}], 1, 0),
    ([{1}], [{1}], 1, 0),
])
def test_partition_agreement_matches_scalar_anchors(left, right, ari, vi):
    result = partition_agreement(left, right)
    reverse = partition_agreement(right, left)
    assert result["adjusted_rand_index"] == pytest.approx(ari, abs=1e-12)
    assert result["variation_information_bits"] == pytest.approx(vi, abs=1e-12)
    assert reverse == pytest.approx(result, abs=1e-12)


@pytest.mark.parametrize("left,right", [
    ([], []), ([{1, 2}], [{1, 3}]), ([{1, 2}, {2, 3}], [{1, 2, 3}]),
    ([{1, 2}], [{1}, set(), {2}]),
])
def test_partition_agreement_rejects_invalid_universes(left, right):
    with pytest.raises(CommunityAnalysisError):
        partition_agreement(left, right)
