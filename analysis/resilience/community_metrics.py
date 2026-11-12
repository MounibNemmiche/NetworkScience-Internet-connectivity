"""Score supplied partitions using the four Part 2 course definitions.

The graph is simple, undirected and unweighted. No partition optimization runs.
InfoMap uses the two-level map equation with recorded uniform teleportation
(Part2_community.pdf, PDF p66). Sparse edge sums replace dense transition matrices.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from numbers import Integral, Real
from types import MappingProxyType
from typing import Hashable, Iterable, Mapping

import networkx as nx
import numpy as np
from scipy import sparse


class CommunityScoreError(ValueError):
    """Invalid graph, invalid partition, or unconverged random walk."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise CommunityScoreError(message)


def _xlogx(values: np.ndarray) -> float:
    positive = values[values > 0]
    return float(np.sum(positive * np.log2(positive)))


@dataclass(frozen=True)
class WalkResult:
    probabilities: Mapping[Hashable, float]
    damping: float
    iterations: int
    l1_residual: float
    residual_tolerance: float
    l1_error_bound: float
    max_iter: int

    def to_dict(self) -> dict:
        """Return compact diagnostics, excluding the full node-probability mapping."""
        return {
            "damping": self.damping,
            "teleportation": "uniform",
            "teleportation_recorded": True,
            "dangling_redistribution": "uniform",
            "iterations": self.iterations,
            "l1_residual": self.l1_residual,
            "residual_tolerance": self.residual_tolerance,
            "l1_error_bound": self.l1_error_bound,
            "max_iter": self.max_iter,
            "probability_sum": math.fsum(self.probabilities.values()),
        }


@dataclass(frozen=True)
class CommunityScores:
    node_count: int
    edge_count: int
    community_count: int
    modularity: float
    normalized_cut: float | None
    course_nmi: float | None
    infomap_bits: float
    mutual_information_bits: float
    community_entropy_bits: float
    community_node_counts: tuple[int, ...]
    community_degree_volumes: tuple[int, ...]
    community_visit_probabilities: tuple[float, ...]
    community_exit_rates: tuple[float, ...]
    undefined_reasons: Mapping[str, str]
    walk: WalkResult

    def to_dict(self) -> dict:
        return {
            "node_count": self.node_count,
            "edge_count": self.edge_count,
            "community_count": self.community_count,
            "modularity": self.modularity,
            "normalized_cut": self.normalized_cut,
            "course_nmi": self.course_nmi,
            "infomap_bits": self.infomap_bits,
            "mutual_information_bits": self.mutual_information_bits,
            "community_entropy_bits": self.community_entropy_bits,
            "community_node_counts": list(self.community_node_counts),
            "community_degree_volumes": list(self.community_degree_volumes),
            "community_visit_probabilities": list(self.community_visit_probabilities),
            "community_exit_rates": list(self.community_exit_rates),
            "undefined_reasons": dict(self.undefined_reasons),
            "conventions": {
                "weighted": False,
                "modularity_resolution": 1.0,
                "normalized_cut_aggregation": "sum",
                "nmi_definition": "edge-end community mutual information / community entropy",
                "infomap_definition": "two-level map equation of this supplied partition",
                "log_base": 2,
                "infomap_unit": "bits per random-walk step",
            },
            "walk": self.walk.to_dict(),
        }


class CommunityScorer:
    """Prepare a graph and accurate random walk once, then score many partitions.

    The adjacency is captured at construction. Later graph mutations do not alter
    this scorer. Input edge attributes, including ``weight``, are ignored.
    The graph must have at least one edge; isolates are supported with explicitly
    declared uniform dangling redistribution. Ncut is undefined for zero-volume
    communities. A zero community entropy makes course NMI undefined.
    """

    def __init__(self, graph: nx.Graph, *, damping: float = 0.85,
                 residual_tolerance: float = 1e-12, max_iter: int = 10000) -> None:
        _require(isinstance(graph, nx.Graph), "Expected a NetworkX graph")
        _require(not graph.is_directed(), "Community scores require an undirected graph")
        _require(not graph.is_multigraph(), "Community scores require a simple graph, not a multigraph")
        _require(nx.number_of_selfloops(graph) == 0, "Self-loops are not supported")
        _require(graph.number_of_nodes() > 0, "Community scores require a nonempty graph")
        _require(graph.number_of_edges() > 0, "Community scores require at least one edge (not an edgeless graph)")
        _require(isinstance(damping, Real) and not isinstance(damping, (bool, np.bool_))
                 and math.isfinite(damping) and 0 <= damping < 1,
                 "Damping must be finite and satisfy 0 <= damping < 1")
        _require(isinstance(residual_tolerance, Real)
                 and not isinstance(residual_tolerance, (bool, np.bool_))
                 and math.isfinite(residual_tolerance) and residual_tolerance > 0,
                 "Residual tolerance must be positive and finite")
        _require(isinstance(max_iter, Integral) and not isinstance(max_iter, (bool, np.bool_))
                 and max_iter > 0, "max_iter must be a positive integer")
        self.node_order = tuple(graph.nodes())
        self._index = {node: i for i, node in enumerate(self.node_order)}
        self.node_count = len(self.node_order)
        self.edge_count = graph.number_of_edges()
        self._adjacency = nx.to_scipy_sparse_array(
            graph, nodelist=self.node_order, weight=None, dtype=np.float64, format="csr")
        self._degree = np.asarray(self._adjacency.sum(axis=1)).ravel()
        self._source = np.repeat(np.arange(self.node_count, dtype=np.intp), np.diff(self._adjacency.indptr))
        self._target = self._adjacency.indices
        self._dangling = self._degree == 0
        self._damping = float(damping)
        self._visits, iterations, residual = self._stationary_walk(float(residual_tolerance), int(max_iter))
        self._visits.flags.writeable = False
        self._p_over_degree = np.divide(self._visits, self._degree,
                                        out=np.zeros(self.node_count), where=~self._dangling)
        self._node_entropy = -_xlogx(self._visits)
        self.walk = WalkResult(
            probabilities=MappingProxyType(dict(zip(self.node_order, self._visits.tolist()))),
            damping=self._damping, iterations=iterations, l1_residual=residual,
            residual_tolerance=float(residual_tolerance),
            l1_error_bound=residual / (1 - self._damping), max_iter=int(max_iter),
        )

    def _walk_step(self, probabilities: np.ndarray) -> np.ndarray:
        relative = np.divide(probabilities, self._degree, out=np.zeros(self.node_count),
                             where=~self._dangling)
        uniform_mass = 1 - self._damping + self._damping * probabilities[self._dangling].sum()
        return self._damping * (self._adjacency @ relative) + uniform_mass / self.node_count

    def _stationary_walk(self, tolerance: float, max_iter: int) -> tuple[np.ndarray, int, float]:
        probabilities = np.full(self.node_count, 1 / self.node_count)
        residual = math.inf
        for iteration in range(1, max_iter + 1):
            updated = self._walk_step(probabilities)
            updated /= updated.sum()
            delta = float(np.abs(updated - probabilities).sum())
            probabilities = updated
            if delta <= tolerance:
                # Certify the returned vector with an actual additional update.
                # The tolerance is total L1, never multiplied by the node count.
                residual = float(np.abs(self._walk_step(probabilities) - probabilities).sum())
                if residual <= tolerance:
                    return probabilities, iteration, residual
        residual = float(np.abs(self._walk_step(probabilities) - probabilities).sum())
        if residual <= tolerance:
            return probabilities, max_iter, residual
        raise CommunityScoreError(
            f"PageRank failed to converge after {max_iter} iterations: total L1 residual "
            f"{residual:.6g} exceeds tolerance {tolerance:.6g}")

    def _partition_labels(self, communities: Iterable[Iterable[Hashable]]) -> tuple[np.ndarray, int]:
        _require(not isinstance(communities, (Mapping, str, bytes)),
                 "Partition must contain node groups, not a mapping or string")
        try:
            groups = iter(communities)
        except TypeError as exc:
            raise CommunityScoreError("Partition must be an iterable of node groups") from exc
        labels = np.full(self.node_count, -1, dtype=np.intp)
        count = 0
        for count, group in enumerate(groups, start=1):
            _require(not isinstance(group, (str, bytes)), "Each community must be a node group, not a string")
            size = 0
            try:
                for node in group:
                    _require(node in self._index, f"Partition contains an unknown node: {node!r}")
                    index = self._index[node]
                    _require(labels[index] == -1, f"Partition repeats or overlaps node {node!r}")
                    labels[index] = count - 1
                    size += 1
            except TypeError as exc:
                raise CommunityScoreError("Each community must be an iterable of hashable nodes") from exc
            _require(size > 0, "Partition contains an empty community")
        _require(count > 0, "Partition must contain at least one community")
        _require(bool(np.all(labels >= 0)), "Partition is missing graph nodes")
        return labels, count

    def score(self, communities: Iterable[Iterable[Hashable]]) -> CommunityScores:
        labels, count = self._partition_labels(communities)
        sizes = np.bincount(labels, minlength=count)
        volumes = np.bincount(labels, weights=self._degree, minlength=count)
        total_volume = 2 * self.edge_count
        marginal = volumes / total_volume
        # This matrix has community shape but only observed nonzero edge entries.
        # Even for singleton partitions, storage remains O(n + m), not O(n^2).
        edge_counts = sparse.coo_array(
            (self._adjacency.data, (labels[self._source], labels[self._target])),
            shape=(count, count)).tocsr()
        internal_counts = edge_counts.diagonal()
        modularity = float(np.sum(internal_counts / total_volume - marginal**2))
        reasons = {}
        if np.any(volumes == 0):
            normalized_cut = None
            reasons["normalized_cut"] = "At least one community has zero degree volume."
        else:
            normalized_cut = float(np.sum((volumes - internal_counts) / volumes))
        joint = edge_counts.tocoo()
        joint_values = joint.data / total_volume
        mutual_information = float(np.sum(joint_values * (
            np.log2(joint_values) - np.log2(marginal[joint.row]) - np.log2(marginal[joint.col]))))
        # Remove only negligible negative roundoff from a mathematically nonnegative MI.
        if -1e-12 < mutual_information < 0:
            mutual_information = 0.0
        community_entropy = -_xlogx(marginal)
        if community_entropy == 0:
            nmi = None
            reasons["course_nmi"] = "Community entropy is zero."
        else:
            nmi = mutual_information / community_entropy

        module_visits = np.bincount(labels, weights=self._visits, minlength=count)
        crossing = labels[self._source] != labels[self._target]
        outside_degree = np.bincount(self._source[crossing], minlength=self.node_count)
        link_exits = self._damping * np.bincount(
            labels, weights=self._p_over_degree * outside_degree, minlength=count)
        dangling_visits = np.bincount(
            labels, weights=self._visits * self._dangling, minlength=count)
        recorded_uniform_exits = ((1 - self._damping) * module_visits
                                  + self._damping * dangling_visits) * (1 - sizes / self.node_count)
        exit_rates = link_exits + recorded_uniform_exits
        total_exit = float(exit_rates.sum())
        # Expanded two-level map equation. Zero exit rates contribute x log x = 0.
        infomap = (_xlogx(np.array([total_exit])) - 2 * _xlogx(exit_rates)
                   + _xlogx(module_visits + exit_rates) + self._node_entropy)
        return CommunityScores(
            node_count=self.node_count, edge_count=self.edge_count, community_count=count,
            modularity=modularity, normalized_cut=normalized_cut, course_nmi=nmi,
            infomap_bits=float(infomap), mutual_information_bits=mutual_information,
            community_entropy_bits=community_entropy,
            community_node_counts=tuple(int(value) for value in sizes),
            community_degree_volumes=tuple(int(value) for value in volumes),
            community_visit_probabilities=tuple(float(value) for value in module_visits),
            community_exit_rates=tuple(float(value) for value in exit_rates),
            undefined_reasons=MappingProxyType(reasons), walk=self.walk,
        )


def score_partition(graph: nx.Graph, communities: Iterable[Iterable[Hashable]],
                    **settings) -> CommunityScores:
    """Convenience for one partition; reuse CommunityScorer for several partitions."""
    return CommunityScorer(graph, **settings).score(communities)
