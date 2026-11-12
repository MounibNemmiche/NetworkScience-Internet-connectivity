"""Cumulative provider removals with fixed-baseline connectivity denominators."""

from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass, replace
from numbers import Integral
from types import MappingProxyType
from typing import Callable, Iterable

import networkx as nx
import numpy as np
from scipy.sparse.csgraph import connected_components

from .graph_loader import COUNTRY_CODES, GraphBaseline


METRICS = ("focal_external_fraction", "focal_external_pair_fraction", "largest_component_fraction")


class RobustnessError(ValueError):
    """An invalid removal experiment or inconsistent connectivity result."""


@dataclass(frozen=True)
class Connectivity:
    remaining_nodes: int
    remaining_external_asns: int
    component_count: int
    focal_connected_count: int
    reachable_pair_count: int
    largest_component_nodes: int
    focal_external_fraction: float
    focal_external_pair_fraction: float
    largest_component_fraction: float

    def to_dict(self) -> dict:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


@dataclass(frozen=True)
class RobustnessResult:
    runs: tuple[dict, ...]
    removal_order: tuple[dict, ...]
    summary: tuple[dict, ...]
    settings: dict
    trial_runtimes: tuple[dict, ...]
    runtime_seconds: float


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RobustnessError(message)


class RobustnessEngine:
    """Snapshot G in CSR form, masking deleted nodes before every component pass.

    This recomputes connected components for every evaluated graph. It uses no
    dense matrix or all-pairs paths and never mutates the baseline graph.
    """

    def __init__(self, graph: nx.Graph, focal_asns: Iterable[int], external_asns: Iterable[int]) -> None:
        _require(isinstance(graph, nx.Graph) and not graph.is_directed() and not graph.is_multigraph(),
                 "The robustness graph must be simple and undirected")
        _require(nx.number_of_selfloops(graph) == 0 and graph.number_of_nodes() > 0,
                 "The baseline graph must be nonempty and have no self-loops")
        self.nodes = tuple(sorted(graph.nodes()))
        self.index = {asn: position for position, asn in enumerate(self.nodes)}
        self.focal = frozenset(focal_asns)
        self.external = frozenset(external_asns)
        _require(bool(self.focal) and bool(self.external), "Focal and external sets must be nonempty")
        _require(not self.focal & self.external, "Focal and external sets must be disjoint")
        _require(self.focal <= self.index.keys() and self.external <= self.index.keys(),
                 "Focal/external ASNs must belong to the baseline graph")
        self.adjacency = nx.to_scipy_sparse_array(graph, nodelist=self.nodes, weight=None, format="csr")
        self._focal_mask = np.array([asn in self.focal for asn in self.nodes], dtype=bool)
        self._external_mask = np.array([asn in self.external for asn in self.nodes], dtype=bool)

    def evaluate(self, removed_asns: Iterable[int] = ()) -> Connectivity:
        try:
            removed = frozenset(removed_asns)
        except TypeError as exc:
            raise RobustnessError("Removed ASNs must be an iterable of hashable node IDs") from exc
        _require(removed <= self.index.keys(), "Removal contains ASNs outside the baseline graph")
        alive = np.ones(len(self.nodes), dtype=bool)
        for asn in removed:
            alive[self.index[asn]] = False
        count = int(alive.sum())
        if count == 0:
            return Connectivity(0, 0, 0, 0, 0, 0, 0.0, 0.0, 0.0)
        remaining = self.adjacency[alive][:, alive]
        component_count, labels = connected_components(remaining, directed=False, return_labels=True)
        sizes = np.bincount(labels, minlength=component_count)
        focal_counts = np.bincount(labels[self._focal_mask[alive]], minlength=component_count)
        external_counts = np.bincount(labels[self._external_mask[alive]], minlength=component_count)
        connected_focal = int(focal_counts[external_counts > 0].sum())
        pairs = sum(int(focal) * int(external) for focal, external in zip(focal_counts, external_counts))
        largest = int(sizes.max())
        return Connectivity(
            count, int(external_counts.sum()), int(component_count), connected_focal, pairs, largest,
            connected_focal / len(self.focal), pairs / (len(self.focal) * len(self.external)),
            largest / len(self.nodes),
        )


def targeted_provider_order(baseline: GraphBaseline) -> tuple[int, ...]:
    providers = baseline.candidate_providers
    _require(len(providers) >= 2, "At least two candidate providers are required")
    _require(providers <= baseline.external_asns, "Candidates must belong to baseline E")
    for asn in providers:
        _require(bool(baseline.provider_customers.get(asn))
                 and baseline.provider_customers[asn] <= baseline.focal_asns,
                 f"Provider AS{asn} must serve distinct baseline focal customers")
    return tuple(sorted(providers, key=lambda asn: (-len(baseline.provider_customers[asn]),
                                                   -baseline.baseline_degrees[asn], asn)))


def summarize_runs(rows: Iterable[dict]) -> tuple[dict, ...]:
    rows = tuple(rows)
    summary = []
    for k in sorted({row["k"] for row in rows}):
        selected = [row for row in rows if row["k"] == k]
        targeted = [row for row in selected if row["strategy"] == "targeted"]
        random_rows = [row for row in selected if row["strategy"] == "random"]
        _require(len(targeted) == 1 and bool(random_rows), "Each k needs one targeted and at least one random run")
        for metric in METRICS:
            q10, median, q90 = np.quantile([row[metric] for row in random_rows], [0.1, 0.5, 0.9], method="linear")
            summary.append({"k": k, "metric": metric, "targeted": targeted[0][metric],
                            "random_median": float(median), "random_q10": float(q10),
                            "random_q90": float(q90), "random_trials": len(random_rows)})
    return tuple(summary)


def run_robustness(baseline: GraphBaseline, *, max_removed_providers: int = 20,
                   random_seeds: Iterable[int] = tuple(range(1001, 1031)),
                   progress: Callable[[str], None] | None = None) -> RobustnessResult:
    started = time.perf_counter()
    _require(isinstance(max_removed_providers, Integral) and not isinstance(max_removed_providers, bool)
             and max_removed_providers >= 0, "max_removed_providers must be a nonnegative integer")
    try:
        seeds = tuple(random_seeds)
    except TypeError as exc:
        raise RobustnessError("Random seeds must be a nonempty integer sequence") from exc
    _require(bool(seeds) and all(isinstance(seed, Integral) and not isinstance(seed, bool) for seed in seeds),
             "Random seeds must be a nonempty integer sequence")
    seeds = tuple(map(int, seeds))
    _require(len(seeds) == len(set(seeds)), "Random seeds must be unique")
    targeted = targeted_provider_order(baseline)
    maximum = min(int(max_removed_providers), len(targeted))
    engine = RobustnessEngine(baseline.graph, baseline.focal_asns, baseline.external_asns)
    baseline_metrics = engine.evaluate()
    _require(all(getattr(baseline_metrics, metric) == 1.0 for metric in METRICS),
             "The baseline graph must be connected with nonempty F and E")
    emit = progress if progress is not None else lambda message: None
    experiments = [("targeted", 0, None, targeted)]
    for trial, seed in enumerate(seeds, start=1):
        order = sorted(baseline.candidate_providers)
        random.Random(seed).shuffle(order)
        experiments.append(("random", trial, seed, tuple(order)))
    runs, orders, runtimes = [], [], []
    for strategy, trial, seed, order in experiments:
        trial_started = time.perf_counter()
        _require(frozenset(order) == baseline.candidate_providers and len(order) == len(targeted),
                 "Every removal order must be a permutation of the same candidate pool")
        orders.extend({"strategy": strategy, "trial": trial, "seed": seed, "position": position, "asn": asn}
                      for position, asn in enumerate(order, start=1))
        previous = baseline_metrics
        for k in range(maximum + 1):
            result = baseline_metrics if k == 0 else engine.evaluate(order[:k])
            _require(result.remaining_nodes == len(baseline.graph) - k, "Node deletion count is inconsistent")
            for metric in METRICS:
                value = getattr(result, metric)
                _require(math.isfinite(value) and 0 <= value <= 1, f"Invalid connectivity metric {metric}")
                _require(value <= getattr(previous, metric) + 1e-12, f"Cumulative removals increased {metric}")
            runs.append({"strategy": strategy, "trial": trial, "seed": seed, "k": k,
                         "last_removed_asn": None if k == 0 else order[k - 1], **result.to_dict()})
            previous = result
        elapsed = time.perf_counter() - trial_started
        runtimes.append({"strategy": strategy, "trial": trial, "seed": seed, "seconds": elapsed})
        if strategy == "targeted" or trial == 1 or trial % 5 == 0 or trial == len(seeds):
            label = "random pilot" if trial == 1 else strategy
            emit(f"{label}: trial {trial}, k=0..{maximum}, {elapsed:.3f}s")
    return RobustnessResult(tuple(runs), tuple(orders), summarize_runs(runs), {
        "requested_max_removed_providers": int(max_removed_providers), "max_removed_providers": maximum,
        "candidate_count": len(targeted), "random_trials": len(seeds), "random_seeds": list(seeds),
        "targeted_ordering": "focal_customer_count_desc, degree_desc, asn_asc; fixed at baseline",
        "random_ordering": "random.Random(seed).shuffle on sorted baseline P; one permutation per trial",
        "denominators": {"focal": len(baseline.focal_asns), "external": len(baseline.external_asns),
                         "graph_nodes": len(baseline.graph)},
        "component_engine": "scipy.sparse.csgraph.connected_components after each cumulative deletion",
        "quantile_method": "linear", "random_band": "10th-90th percentile simulation spread; not a confidence interval",
    }, tuple(runtimes), time.perf_counter() - started)


def assigned_country_sensitivity(baseline: GraphBaseline) -> GraphBaseline:
    """Derive a separate scenario; never rewrite the supplied package or its report."""
    external = frozenset(asn for asn in baseline.external_asns if baseline.nodes[asn].country in COUNTRY_CODES)
    providers = frozenset(baseline.candidate_providers & external)
    customers = MappingProxyType({asn: baseline.provider_customers[asn] for asn in sorted(providers)})
    order = tuple(sorted(providers, key=lambda asn: (-len(customers[asn]), -baseline.baseline_degrees[asn], asn)))
    return replace(baseline, external_asns=external, candidate_providers=providers,
                   provider_customers=customers, targeted_order=order)
