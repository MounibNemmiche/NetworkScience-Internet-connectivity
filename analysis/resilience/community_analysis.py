"""Seeded Louvain analysis on the validated Student B baseline."""

from __future__ import annotations

import math
import time
from collections import Counter
from dataclasses import dataclass
from itertools import combinations
from numbers import Integral, Real
from typing import Callable, Iterable

import networkx as nx

from .community_metrics import CommunityScoreError, CommunityScorer, CommunityScores
from .graph_loader import GraphBaseline


class CommunityAnalysisError(ValueError):
    """Invalid Louvain settings, partition or derived output."""


@dataclass(frozen=True)
class PartitionRun:
    seed: int
    communities: tuple[frozenset[int], ...]
    scores: CommunityScores
    louvain_seconds: float
    scoring_seconds: float
    networkx_modularity: float


@dataclass(frozen=True)
class CommunityAnalysis:
    runs: tuple[PartitionRun, ...]
    selected: PartitionRun
    summary_rows: list[dict]
    edge_rows: list[dict]
    provider_rows: list[dict]
    sensitivity_rows: list[dict]
    scorer_seconds: float
    resolution: float
    threshold: float


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise CommunityAnalysisError(message)


def _partition_mapping(communities: Iterable[Iterable[int]]) -> tuple[dict[int, int], list[frozenset[int]]]:
    _require(not isinstance(communities, (str, bytes, dict)), "Partition must contain nonempty node groups")
    membership = {}
    groups = []
    try:
        for community_id, group in enumerate(communities):
            _require(not isinstance(group, (str, bytes)), "Each community must be a node group")
            nodes = []
            for node in group:
                _require(node not in membership, f"Partition overlaps or repeats node {node!r}")
                membership[node] = community_id
                nodes.append(node)
            _require(bool(nodes), "Partition contains an empty community")
            groups.append(frozenset(nodes))
    except TypeError as exc:
        raise CommunityAnalysisError("Partition must contain iterable groups of hashable nodes") from exc
    _require(bool(groups), "Partition must contain at least one community")
    return membership, groups


def canonical_partition(graph: nx.Graph, communities: Iterable[Iterable[int]]) -> tuple[frozenset[int], ...]:
    """Validate coverage, then label by descending size and smallest-ASN tie-break."""
    membership, groups = _partition_mapping(communities)
    _require(set(membership) == set(graph), "Partition must cover every graph node exactly once")
    try:
        return tuple(sorted(groups, key=lambda group: (-len(group), min(group))))
    except TypeError as exc:
        raise CommunityAnalysisError("Canonical community labels require mutually comparable node IDs") from exc


def partition_agreement(partition_a: Iterable[Iterable[int]], partition_b: Iterable[Iterable[int]]) -> dict:
    """Label-invariant ARI and variation of information; neither is course NMI."""
    labels_a, groups_a = _partition_mapping(partition_a)
    labels_b, groups_b = _partition_mapping(partition_b)
    _require(labels_a.keys() == labels_b.keys(), "Partitions must contain the same node set")
    n = len(labels_a)
    contingency = Counter((labels_a[node], labels_b[node]) for node in labels_a)
    sizes_a, sizes_b = [len(group) for group in groups_a], [len(group) for group in groups_b]
    choose2 = lambda size: size * (size - 1) // 2
    pairs = choose2(n)
    cells = sum(choose2(value) for value in contingency.values())
    pairs_a, pairs_b = sum(map(choose2, sizes_a)), sum(map(choose2, sizes_b))
    denominator = (pairs_a + pairs_b) * pairs - 2 * pairs_a * pairs_b
    ari = 1.0 if denominator == 0 else 2 * (cells * pairs - pairs_a * pairs_b) / denominator
    entropy_a = -math.fsum((size / n) * math.log2(size / n) for size in sizes_a)
    entropy_b = -math.fsum((size / n) * math.log2(size / n) for size in sizes_b)
    information = math.fsum((value / n) * math.log2(value * n / (sizes_a[a] * sizes_b[b]))
                            for (a, b), value in contingency.items())
    vi = max(0.0, entropy_a + entropy_b - 2 * information)
    return {"adjusted_rand_index": float(ari), "variation_information_bits": float(vi)}


def _summaries(baseline: GraphBaseline, selected: PartitionRun) -> tuple[list[dict], list[dict], list[dict]]:
    membership = {asn: index for index, group in enumerate(selected.communities) for asn in group}
    pairs: Counter[tuple[int, int]] = Counter()
    for a, b in baseline.graph.edges():
        source, target = membership[a], membership[b]
        pairs[(min(source, target), max(source, target))] += 1
    internal = Counter()
    boundary = Counter()
    for (a, b), count in pairs.items():
        if a == b:
            internal[a] += count
        else:
            boundary[a] += count
            boundary[b] += count
    edge_rows = [{"seed": selected.seed, "source_community": a, "target_community": b, "edge_count": count}
                 for (a, b), count in sorted(pairs.items())]
    summary_rows = []
    for community_id, group in enumerate(selected.communities):
        focal_count = len(group & baseline.focal_asns)
        external_count = len(group & baseline.external_asns)
        provider_count = len(group & baseline.candidate_providers)
        countries = Counter(baseline.nodes[asn].country or "unknown" for asn in group)
        summary_rows.append({
            "seed": selected.seed, "community_id": community_id,
            "node_count": len(group), "node_fraction": len(group) / len(baseline.graph),
            "focal_asn_count": focal_count,
            "focal_fraction_of_all": focal_count / len(baseline.focal_asns),
            "focal_fraction_within_community": focal_count / len(group),
            "external_asn_count": external_count,
            "unknown_country_asn_count": len(group) - focal_count - external_count,
            "candidate_provider_count": provider_count,
            "candidate_fraction_of_all": provider_count / len(baseline.candidate_providers),
            "internal_edges": internal[community_id], "boundary_edges": boundary[community_id],
            "degree_volume": sum(baseline.baseline_degrees[asn] for asn in group),
            "top_countries": ";".join(f"{country}:{count}" for country, count in
                                      sorted(countries.items(), key=lambda item: (-item[1], item[0]))[:3]),
        })
    provider_rows = []
    order = getattr(baseline, "targeted_order", tuple(sorted(baseline.candidate_providers)))
    for position, asn in enumerate(order, start=1):
        node = baseline.nodes[asn]
        provider_rows.append({
            "seed": selected.seed, "asn": asn, "org_name": node.org_name, "country": node.country,
            "community_id": membership[asn], "focal_customer_count": len(baseline.provider_customers[asn]),
            "baseline_degree": baseline.baseline_degrees[asn], "targeted_position": position,
        })
    _require(sum(row["node_count"] for row in summary_rows) == len(baseline.graph), "Community node totals failed")
    _require(sum(row["focal_asn_count"] for row in summary_rows) == len(baseline.focal_asns), "Focal totals failed")
    _require(sum(row["candidate_provider_count"] for row in summary_rows) == len(baseline.candidate_providers),
             "Provider totals failed")
    _require(sum(row["edge_count"] for row in edge_rows) == baseline.graph.number_of_edges(), "Quotient edge totals failed")
    _require(all(row["degree_volume"] == 2 * row["internal_edges"] + row["boundary_edges"]
                 for row in summary_rows), "Community degree-volume identity failed")
    return summary_rows, edge_rows, provider_rows


def run_louvain(baseline: GraphBaseline, *, seeds: Iterable[int] = (11, 22, 33),
                resolution: float = 1.0, threshold: float = 1e-7, damping: float = 0.85,
                progress: Callable[[str], None] | None = None) -> CommunityAnalysis:
    """Use the first configured run as a pilot and reuse it in the final comparison."""
    try:
        configured_seeds = tuple(seeds)
    except TypeError as exc:
        raise CommunityAnalysisError("Seeds must be a nonempty sequence of unique integers") from exc
    _require(bool(configured_seeds) and all(isinstance(seed, Integral) and not isinstance(seed, bool)
                                           for seed in configured_seeds), "Seeds must be nonempty and integer-valued")
    normalized_seeds = tuple(int(seed) for seed in configured_seeds)
    _require(len(set(normalized_seeds)) == len(normalized_seeds), "Louvain seeds must be unique")
    _require(isinstance(resolution, Real) and not isinstance(resolution, bool)
             and math.isfinite(resolution) and resolution == 1,
             "Course scoring requires Louvain resolution exactly 1")
    _require(isinstance(threshold, Real) and not isinstance(threshold, bool)
             and math.isfinite(threshold) and threshold > 0, "Louvain threshold must be positive and finite")
    emit = progress if progress is not None else lambda message: None
    emit("Preparing sparse score calculations and the stationary walk...")
    started = time.perf_counter()
    try:
        scorer = CommunityScorer(baseline.graph, damping=damping)
    except CommunityScoreError as exc:
        raise CommunityAnalysisError(str(exc)) from exc
    scorer_seconds = time.perf_counter() - started
    runs = []
    for index, seed in enumerate(normalized_seeds):
        label = "Pilot" if index == 0 else "Louvain"
        emit(f"{label}: starting seed {seed} on the full graph...")
        started = time.perf_counter()
        partition = nx.community.louvain_communities(
            baseline.graph, weight=None, resolution=1.0, threshold=float(threshold),
            max_level=None, seed=seed, backend="networkx")
        louvain_seconds = time.perf_counter() - started
        communities = canonical_partition(baseline.graph, partition)
        started = time.perf_counter()
        scores = scorer.score(communities)
        library_modularity = float(nx.community.modularity(baseline.graph, communities, weight=None, resolution=1))
        _require(math.isclose(scores.modularity, library_modularity, rel_tol=1e-10, abs_tol=1e-10),
                 f"Seed {seed}: modularity disagrees with the independent NetworkX calculation")
        scoring_seconds = time.perf_counter() - started
        runs.append(PartitionRun(seed, communities, scores, louvain_seconds, scoring_seconds, library_modularity))
        emit(f"{label}: seed {seed} -> {len(communities)} communities, Q={scores.modularity:.8f}; "
             f"Louvain {louvain_seconds:.2f}s, scoring {scoring_seconds:.2f}s.")
    selected = max(runs, key=lambda run: (run.scores.modularity, -run.seed))
    summary_rows, edge_rows, provider_rows = _summaries(baseline, selected)
    sensitivity = [{"seed_a": a.seed, "seed_b": b.seed, **partition_agreement(a.communities, b.communities)}
                   for a, b in combinations(sorted(runs, key=lambda run: run.seed), 2)]
    emit(f"Selected seed {selected.seed}: highest modularity, with smaller seed breaking an exact tie.")
    return CommunityAnalysis(tuple(runs), selected, summary_rows, edge_rows, provider_rows, sensitivity,
                             scorer_seconds, float(resolution), float(threshold))
