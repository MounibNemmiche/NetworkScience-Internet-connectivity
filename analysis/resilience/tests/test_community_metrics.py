"""Independent dense/scalar checks of the course community-score definitions.

The dense oracle is intentionally restricted to tiny fixtures. Production code
must stay sparse; the oracle solves stationarity with linear algebra rather than
sharing the production PageRank iteration or entropy expansion.
"""

import json
import math

import networkx as nx
import numpy as np
import pytest

from analysis.resilience.community_metrics import (
    CommunityScoreError,
    CommunityScorer,
    score_partition,
)


def _entropy(probabilities):
    return -math.fsum(float(p) * math.log2(float(p)) for p in probabilities if p > 0)


def _dense_reference(graph, communities, damping=0.85):
    """Direct probabilities and codebooks; no production metric helper is used."""
    nodes = tuple(graph)
    n = len(nodes)
    index = {node: position for position, node in enumerate(nodes)}
    groups = [[index[node] for node in group] for group in communities]
    adjacency = np.zeros((n, n), dtype=float)
    for a, b in graph.edges:
        adjacency[index[a], index[b]] = 1
        adjacency[index[b], index[a]] = 1
    degree = adjacency.sum(axis=1)
    transition = np.empty((n, n), dtype=float)
    for source in range(n):
        link_distribution = adjacency[source] / degree[source] if degree[source] else np.full(n, 1 / n)
        transition[source] = damping * link_distribution + (1 - damping) / n

    # Solve the stationary equations plus normalization, independent of iteration.
    equations = transition.T - np.eye(n)
    equations[-1] = 1
    rhs = np.zeros(n)
    rhs[-1] = 1
    visits = np.linalg.solve(equations, rhs)

    joint = np.empty((len(groups), len(groups)), dtype=float)
    for i, left in enumerate(groups):
        for j, right in enumerate(groups):
            joint[i, j] = adjacency[np.ix_(left, right)].sum() / adjacency.sum()
    marginal = joint.sum(axis=1)
    modularity = math.fsum(joint[i, i] - marginal[i] ** 2 for i in range(len(groups)))
    ncut = (math.fsum((marginal[i] - joint[i, i]) / marginal[i] for i in range(len(groups)))
            if np.all(marginal > 0) else None)
    entropy = _entropy(marginal)
    mutual_information = math.fsum(
        joint[i, j] * math.log2(joint[i, j] / (marginal[i] * marginal[j]))
        for i in range(len(groups)) for j in range(len(groups)) if joint[i, j] > 0
    )
    nmi = mutual_information / entropy if entropy > 0 else None

    all_indices = set(range(n))
    exits = []
    module_visits = []
    for group in groups:
        outside = sorted(all_indices - set(group))
        exits.append(math.fsum(visits[u] * transition[u, v] for u in group for v in outside))
        module_visits.append(float(visits[group].sum()))
    total_exit = math.fsum(exits)
    description_length = total_exit * _entropy([q / total_exit for q in exits]) if total_exit else 0.0
    for group, exit_rate, visit_mass in zip(groups, exits, module_visits):
        usage = exit_rate + visit_mass
        codebook = [exit_rate / usage] + [float(visits[u]) / usage for u in group]
        description_length += usage * _entropy(codebook)
    return {
        "modularity": modularity, "normalized_cut": ncut, "course_nmi": nmi,
        "infomap_bits": description_length, "exits": exits, "module_visits": module_visits,
        "probabilities": dict(zip(nodes, visits)), "transition": transition,
        "mutual_information_bits": mutual_information, "community_entropy_bits": entropy,
    }


def _assert_matches_dense(graph, communities, damping=0.85):
    scorer = CommunityScorer(graph, damping=damping)
    scores = scorer.score(communities)
    reference = _dense_reference(graph, communities, damping)
    for name in ("modularity", "normalized_cut", "course_nmi", "infomap_bits",
                 "mutual_information_bits", "community_entropy_bits"):
        expected = reference[name]
        actual = getattr(scores, name)
        if expected is None:
            assert actual is None
        else:
            assert actual == pytest.approx(expected, abs=2e-10)
    assert scores.community_exit_rates == pytest.approx(reference["exits"], abs=2e-11)
    assert scores.community_visit_probabilities == pytest.approx(reference["module_visits"], abs=2e-11)
    for node, expected in reference["probabilities"].items():
        assert scorer.walk.probabilities[node] == pytest.approx(expected, abs=2e-11)
    assert scores.modularity == pytest.approx(nx.community.modularity(graph, communities, weight=None), abs=1e-12)
    return scorer, scores, reference


def test_path4_hand_calculation_distinguishes_pagerank_from_degree_stationarity():
    graph = nx.path_graph(4)
    scorer, scores, _ = _assert_matches_dense(graph, [{0, 1}, {2, 3}])
    assert scores.modularity == pytest.approx(1 / 6)
    assert scores.normalized_cut == pytest.approx(2 / 3)
    assert scores.course_nmi == pytest.approx(0.08170416594551039)
    assert scores.infomap_bits == pytest.approx(2.402029215960382)
    assert scorer.walk.probabilities[0] == pytest.approx(10 / 57)
    assert scorer.walk.probabilities[1] == pytest.approx(37 / 114)
    assert scores.community_exit_rates == pytest.approx([10 / 57, 10 / 57])


def test_cycle4_independence_and_disassortative_dependence_are_not_partition_agreement():
    graph = nx.cycle_graph(4)
    _, adjacent, _ = _assert_matches_dense(graph, [{0, 1}, {2, 3}])
    _, alternating, _ = _assert_matches_dense(graph, [{0, 2}, {1, 3}])
    assert adjacent.modularity == pytest.approx(0)
    assert adjacent.normalized_cut == pytest.approx(1)
    assert adjacent.course_nmi == pytest.approx(0, abs=1e-14)
    assert adjacent.infomap_bits == pytest.approx(2.877443751081734)
    assert alternating.modularity == pytest.approx(-0.5)
    assert alternating.normalized_cut == pytest.approx(2)
    assert alternating.course_nmi == pytest.approx(1)
    assert alternating.infomap_bits == pytest.approx(3.8478916327435444)


def test_recorded_teleportation_has_exit_flow_between_disconnected_components():
    graph = nx.Graph([(0, 1), (2, 3)])
    _, scores, _ = _assert_matches_dense(graph, [{0, 1}, {2, 3}])
    assert scores.community_exit_rates == pytest.approx([0.0375, 0.0375])
    assert scores.infomap_bits == pytest.approx(1.467434328863306)
    assert scores.infomap_bits != pytest.approx(1.0)  # unrecorded teleport result


def test_one_community_has_undefined_nmi_and_single_node_codebook():
    graph = nx.path_graph(4)
    scorer, scores, _ = _assert_matches_dense(graph, [set(graph)])
    assert scores.modularity == pytest.approx(0)
    assert scores.normalized_cut == pytest.approx(0)
    assert scores.course_nmi is None
    assert scores.undefined_reasons
    assert scores.infomap_bits == pytest.approx(_entropy(scorer.walk.probabilities.values()))
    assert scores.community_exit_rates == pytest.approx([0])


@pytest.mark.parametrize("graph,communities", [
    (nx.complete_graph(4), [{0}, {1}, {2}, {3}]),
    (nx.star_graph(4), [{0, 1}, {2, 3, 4}]),
    (nx.barbell_graph(3, 1), [{0, 1, 2}, {3}, {4, 5, 6}]),
    (nx.path_graph(6), [{0, 2, 4}, {1, 3, 5}]),
    (nx.Graph([(0, 1), (1, 2), (2, 0), (3, 4)]), [{0, 3}, {1, 2, 4}]),
])
@pytest.mark.parametrize("damping", [0.0, 0.4, 0.85, 0.95])
def test_small_graph_scores_match_dense_linear_solve_and_codebooks(graph, communities, damping):
    _assert_matches_dense(graph, communities, damping)


def test_isolated_node_uniform_dangling_extension_and_zero_volume_ncut():
    graph = nx.Graph([(0, 1)])
    graph.add_node(2)
    _, scores, _ = _assert_matches_dense(graph, [{0, 1}, {2}])
    assert scores.normalized_cut is None
    assert scores.course_nmi is None  # all observed edge ends belong to one group
    assert "normalized_cut" in scores.undefined_reasons
    assert "course_nmi" in scores.undefined_reasons
    assert math.isfinite(scores.infomap_bits)


def test_stationarity_certificate_uses_total_l1_residual():
    graph = nx.path_graph(9)
    scorer, _, reference = _assert_matches_dense(graph, [{0, 1, 2}, {3, 4, 5}, {6, 7, 8}])
    vector = np.array([scorer.walk.probabilities[node] for node in graph])
    actual_residual = float(np.abs(reference["transition"].T @ vector - vector).sum())
    assert actual_residual <= scorer.walk.residual_tolerance + 1e-15
    assert scorer.walk.l1_residual == pytest.approx(actual_residual, abs=2e-15)
    assert scorer.walk.l1_error_bound == pytest.approx(scorer.walk.l1_residual / (1 - scorer.walk.damping))
    true_vector = np.array([reference["probabilities"][node] for node in graph])
    assert np.abs(vector - true_vector).sum() <= scorer.walk.l1_error_bound + 1e-14
    assert math.fsum(scorer.walk.probabilities.values()) == pytest.approx(1, abs=1e-14)
    assert scorer.walk.iterations > 1


def test_nonconvergence_is_a_failure_not_a_normalized_approximation():
    with pytest.raises(CommunityScoreError):
        CommunityScorer(nx.path_graph(4), max_iter=1, residual_tolerance=1e-14)


def test_edge_weights_are_explicitly_ignored():
    graph = nx.path_graph(4)
    weighted = graph.copy()
    for position, (a, b) in enumerate(weighted.edges):
        weighted[a][b]["weight"] = 10 ** position
    original = score_partition(graph, [{0, 1}, {2, 3}])
    actual = score_partition(weighted, [{0, 1}, {2, 3}])
    for name in ("modularity", "normalized_cut", "course_nmi", "infomap_bits"):
        assert getattr(actual, name) == pytest.approx(getattr(original, name))


def test_relabeling_insertion_and_community_order_do_not_change_scores():
    graph = nx.path_graph(4)
    original = score_partition(graph, [{0, 1}, {2, 3}])
    names = {0: ("node", 0), 1: "middle", 2: 2.5, 3: ("node", 3)}
    relabeled = nx.Graph()
    relabeled.add_nodes_from(names[node] for node in reversed(tuple(graph)))
    relabeled.add_edges_from((names[b], names[a]) for a, b in reversed(tuple(graph.edges)))
    result = score_partition(relabeled, [{names[2], names[3]}, {names[0], names[1]}])
    for name in ("modularity", "normalized_cut", "course_nmi", "infomap_bits"):
        assert getattr(result, name) == pytest.approx(getattr(original, name))


def test_scorer_reuses_walk_and_retains_graph_snapshot():
    graph = nx.path_graph(4)
    scorer = CommunityScorer(graph)
    walk = scorer.walk
    initial = scorer.score([{0, 1}, {2, 3}])
    graph.add_edge(0, 3)
    later = scorer.score([{0, 1}, {2, 3}])
    assert scorer.walk is walk
    assert later.infomap_bits == pytest.approx(initial.infomap_bits)
    assert later.modularity == pytest.approx(initial.modularity)
    assert later.edge_count == 3


def test_result_fields_and_serialization_are_inspectable():
    scorer = CommunityScorer(nx.path_graph(4))
    scores = scorer.score([{0, 1}, {2, 3}])
    assert scores.node_count == 4
    assert scores.edge_count == 3
    assert scores.community_count == 2
    assert isinstance(scores.to_dict(), dict)
    assert isinstance(scorer.walk.to_dict(), dict)
    json.dumps(scores.to_dict(), allow_nan=False)
    json.dumps(scorer.walk.to_dict(), allow_nan=False)
    with pytest.raises(TypeError):
        scorer.walk.probabilities[0] = 0.5


@pytest.mark.parametrize("communities", [
    [], [{0, 1}, {2}], [{0, 1}, {1, 2, 3}], [{0, 1}, {2, 3, 99}],
    [{0, 1}, {2, 3}, set()], [[0, 0, 1], [2, 3]],
])
def test_invalid_partitions_are_rejected(communities):
    scorer = CommunityScorer(nx.path_graph(4))
    with pytest.raises(CommunityScoreError):
        scorer.score(communities)


@pytest.mark.parametrize("graph", [
    nx.Graph(), nx.empty_graph(3), nx.DiGraph([(0, 1)]),
    nx.MultiGraph([(0, 1), (0, 1)]), nx.Graph([(0, 0), (0, 1)]),
])
def test_unsupported_graphs_are_rejected(graph):
    with pytest.raises(CommunityScoreError):
        CommunityScorer(graph)


@pytest.mark.parametrize("kwargs", [
    {"damping": -0.1}, {"damping": 1.0}, {"damping": float("nan")},
    {"damping": float("inf")}, {"residual_tolerance": 0},
    {"residual_tolerance": -1}, {"residual_tolerance": float("nan")},
    {"residual_tolerance": float("inf")}, {"max_iter": 0}, {"max_iter": -1},
    {"max_iter": 1.5}, {"max_iter": True},
])
def test_invalid_numeric_settings_are_rejected(kwargs):
    with pytest.raises(CommunityScoreError):
        CommunityScorer(nx.path_graph(4), **kwargs)
