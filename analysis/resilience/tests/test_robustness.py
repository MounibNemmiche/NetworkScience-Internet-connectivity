"""Independent component counting and reproducible cumulative failure scenarios."""

from types import SimpleNamespace

import networkx as nx
import numpy as np
import pytest

from analysis.resilience.graph_loader import load_graph
from analysis.resilience.robustness import (
    METRICS, RobustnessEngine, RobustnessError, assigned_country_sensitivity,
    run_robustness, summarize_runs, targeted_provider_order,
)
from analysis.resilience.tests.test_graph_loader import _package


def _bridge():
    graph = nx.Graph([(1, 10), (2, 10), (10, 20), (2, 20)])
    return SimpleNamespace(graph=nx.freeze(graph), focal_asns=frozenset({1, 2}),
                           external_asns=frozenset({10, 20}), candidate_providers=frozenset({10, 20}),
                           provider_customers={10: frozenset({1, 2}), 20: frozenset({2})},
                           baseline_degrees=dict(graph.degree()))


def _oracle(graph, focal, external, removed):
    remaining = graph.copy()
    remaining.remove_nodes_from(removed)
    groups = list(nx.connected_components(remaining))
    focal_connected = sum(len(group & focal) for group in groups if group & external)
    pairs = sum(len(group & focal) * len(group & external) for group in groups)
    largest = max(map(len, groups), default=0)
    return focal_connected / len(focal), pairs / (len(focal) * len(external)), largest / len(graph)


def test_bridge_deletion_and_fixed_external_denominator():
    baseline = _bridge()
    engine = RobustnessEngine(baseline.graph, baseline.focal_asns, baseline.external_asns)
    assert tuple(getattr(engine.evaluate(), key) for key in METRICS) == (1, 1, 1)
    one = engine.evaluate({10})
    assert tuple(getattr(one, key) for key in METRICS) == (0.5, 0.25, 0.5)
    assert one.reachable_pair_count == 1
    assert one.remaining_external_asns == 1
    assert one.component_count == 2
    two = engine.evaluate({10, 20})
    assert tuple(getattr(two, key) for key in METRICS) == (0, 0, 0.25)
    assert two.remaining_nodes == 2
    assert baseline.graph.number_of_edges() == 4


@pytest.mark.parametrize("removed", [set(), {10}, {20}, {10, 20}, {1}, {1, 2, 10, 20}])
def test_sparse_components_match_independent_networkx(removed):
    baseline = _bridge()
    result = RobustnessEngine(baseline.graph, baseline.focal_asns, baseline.external_asns).evaluate(removed)
    assert tuple(getattr(result, key) for key in METRICS) == _oracle(
        baseline.graph, baseline.focal_asns, baseline.external_asns, removed)


def test_unknown_country_nodes_remain_transit_nodes():
    graph = nx.Graph([(1, 40), (40, 10)])
    engine = RobustnessEngine(graph, {1}, {10})
    assert engine.evaluate().focal_external_fraction == 1
    assert engine.evaluate({40}).focal_external_fraction == 0


def test_no_surviving_nodes_has_zero_metrics():
    result = RobustnessEngine(nx.path_graph([1, 10]), {1}, {10}).evaluate({1, 10})
    assert all(getattr(result, key) == 0 for key in METRICS)
    assert result.component_count == result.remaining_nodes == 0


def test_targeted_order_is_fixed_customer_then_degree_then_asn(tmp_path):
    baseline = load_graph(_package(tmp_path))
    assert targeted_provider_order(baseline) == (10, 20, 30)
    result = run_robustness(baseline, max_removed_providers=20, random_seeds=[11, 22])
    assert result.settings["max_removed_providers"] == 3
    assert len(result.runs) == 12
    assert len(result.removal_order) == 9
    assert [row["asn"] for row in result.removal_order if row["strategy"] == "targeted"] == [10, 20, 30]


def test_orders_and_scores_reproduce_and_reuse_prefixes(tmp_path):
    baseline = load_graph(_package(tmp_path))
    before = tuple(baseline.graph.edges())
    first = run_robustness(baseline, max_removed_providers=2, random_seeds=[1001, 1002, 1003])
    second = run_robustness(baseline, max_removed_providers=2, random_seeds=[1001, 1002, 1003])
    assert first.removal_order == second.removal_order
    assert first.runs == second.runs
    assert first.summary == second.summary
    for trial in range(4):
        order = [row["asn"] for row in first.removal_order if row["trial"] == trial]
        assert set(order) == baseline.candidate_providers
        rows = [row for row in first.runs if row["trial"] == trial]
        assert [row["k"] for row in rows] == [0, 1, 2]
        for row in rows:
            expected = _oracle(baseline.graph, baseline.focal_asns, baseline.external_asns, order[:row["k"]])
            assert tuple(row[key] for key in METRICS) == expected
    assert tuple(baseline.graph.edges()) == before


def test_linear_quantiles_recomputed_from_trials(tmp_path):
    result = run_robustness(load_graph(_package(tmp_path)), max_removed_providers=2,
                            random_seeds=[11, 22, 33, 44])
    assert len(result.summary) == 9
    for row in result.summary:
        values = [run[row["metric"]] for run in result.runs if run["k"] == row["k"] and run["strategy"] == "random"]
        expected = np.quantile(values, [0.1, 0.5, 0.9], method="linear")
        assert (row["random_q10"], row["random_median"], row["random_q90"]) == tuple(expected)


def test_sensitivity_excludes_eu_without_changing_input(tmp_path):
    baseline = load_graph(_package(tmp_path))
    other = assigned_country_sensitivity(baseline)
    assert other.graph is baseline.graph
    assert other.external_asns == {10, 20}
    assert other.candidate_providers == {10, 20}
    assert baseline.external_asns == {10, 20, 30}
    assert baseline.nodes[30].country == other.nodes[30].country == "EU"
    assert other.focal_asns == baseline.focal_asns
    assert other.report is baseline.report  # no claim that the saved package changed


@pytest.mark.parametrize("maximum,seeds", [(-1, [1]), (1.2, [1]), (True, [1]), (2, []),
                                          (2, [1, 1]), (2, [True]), (2, [1.2]), (2, None)])
def test_invalid_settings_fail(maximum, seeds):
    with pytest.raises(RobustnessError):
        run_robustness(_bridge(), max_removed_providers=maximum, random_seeds=seeds)


@pytest.mark.parametrize("focal,external", [(set(), {10}), ({1}, set()), ({1}, {1}), ({99}, {10})])
def test_invalid_reference_sets_fail(focal, external):
    with pytest.raises(RobustnessError):
        RobustnessEngine(nx.path_graph([1, 10]), focal, external)


def test_nonconnected_baseline_rejected_by_experiment():
    baseline = _bridge()
    baseline.graph = nx.Graph([(1, 10), (2, 20)])
    with pytest.raises(RobustnessError, match="connected"):
        run_robustness(baseline, random_seeds=[1])


def test_invalid_removed_asn_rejected():
    with pytest.raises(RobustnessError, match="outside"):
        RobustnessEngine(nx.path_graph([1, 10]), {1}, {10}).evaluate({99})


def test_summary_rejects_missing_random_baseline():
    with pytest.raises(RobustnessError):
        summarize_runs([{"k": 0, "strategy": "targeted", **{key: 1 for key in METRICS}}])


def test_zero_removal_budget_preserves_all_baselines():
    result = run_robustness(_bridge(), max_removed_providers=0, random_seeds=[1, 2])
    assert len(result.runs) == 3
    assert all(row[key] == 1 for row in result.runs for key in METRICS)
