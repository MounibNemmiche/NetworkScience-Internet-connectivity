"""Export the validated baseline and saved seed-22 partition for Gephi Desktop."""
from __future__ import annotations

import csv
import hashlib
import json
import math
from pathlib import Path
import random

import networkx as nx

from .graph_loader import load_graph


def main():
    root = Path(__file__).resolve().parents[2]
    output = root / "results/resilience/gephi"
    output.mkdir(parents=True, exist_ok=True)
    baseline = load_graph(root / "config/project.json", hash_policy="newline-compatible")
    graph = baseline.graph.copy()
    assignments = root / "results/resilience/community_assignments.csv"
    with assignments.open(newline="", encoding="utf-8") as handle:
        partition = {int(r["asn"]): int(r["community_id"]) for r in csv.DictReader(handle)
                     if int(r["seed"]) == 22}
    if set(partition) != set(graph):
        raise ValueError("Saved partition does not cover the validated baseline")
    removed = set(baseline.targeted_order[:20])
    remaining = graph.subgraph(set(graph) - removed)
    externally_reachable = set()
    for component in nx.connected_components(remaining):
        if component & baseline.external_asns:
            externally_reachable.update(component & baseline.focal_asns)
    disconnected = baseline.focal_asns - externally_reachable
    generator = random.Random(22)
    order = {asn: i for i, asn in enumerate(baseline.targeted_order, 1)}
    largest = {c for c, _ in sorted(__import__('collections').Counter(partition.values()).items(),
                                  key=lambda pair: (-pair[1], pair[0]))[:8]}
    counts = {}
    for asn in graph:
        metadata = baseline.nodes[asn]
        focal = asn in baseline.focal_asns
        provider = asn in baseline.candidate_providers
        role = "Italian AS" if focal else "Candidate provider" if provider else "Other AS"
        radius = 1500 * math.sqrt(generator.random())
        angle = 2 * math.pi * generator.random()
        graph.nodes[asn].update(
            label=f"AS{asn}", country=metadata.country, organization=metadata.org_name,
            community_id=partition[asn], community_key=str(partition[asn]),
            community_display=f"C{partition[asn]}" if partition[asn] in largest else "Other communities",
            baseline_degree=baseline.baseline_degrees[asn],
            log_degree=math.log1p(baseline.baseline_degrees[asn]), role=role,
            focal_or_provider="yes" if focal or provider else "no",
            candidate_provider="yes" if provider else "no",
            survives_targeted_20="no" if asn in removed else "yes",
            failure_status=("Lost external reachability" if asn in disconnected else
                            "Italian AS: externally reachable" if focal else
                            "Removed provider" if asn in removed else "Surviving provider" if provider else "Other AS"),
            targeted_position=order.get(asn, 0),
            viz={"position": {"x": radius * math.cos(angle), "y": radius * math.sin(angle), "z": 0.0},
                 "size": 1.0 + math.log1p(baseline.baseline_degrees[asn])},
        )
    path = output / "internet_seed22.gexf"
    nx.write_gexf(graph, path, encoding="utf-8", prettyprint=False)
    subsets = {
        "full": set(graph),
        "italian_and_providers": set(baseline.focal_asns | baseline.candidate_providers),
        "community_1": {a for a in graph if partition[a] == 1},
        "community_21": {a for a in graph if partition[a] == 21},
        "providers": set(baseline.candidate_providers),
        "italian_and_providers_after_20": set(baseline.focal_asns | baseline.candidate_providers) - removed,
    }
    for name, nodes in subsets.items():
        counts[name] = {"nodes": len(nodes), "edges": graph.subgraph(nodes).number_of_edges()}
    manifest = {
        "graph": "simple undirected unweighted AS topology; snapshot 20261001",
        "community_seed": 22, "community_resolution": 1.0, "communities": len(set(partition.values())),
        "baseline_fingerprint": baseline.report.get("baseline_fingerprint"),
        "community_assignments_sha256": hashlib.sha256(assignments.read_bytes()).hexdigest(),
        "gexf_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "views": counts, "removed_asns": baseline.targeted_order[:20],
        "focal_losing_external_reachability": sorted(disconnected),
        "reachability_computed_on": "full graph after the 20 saved targeted provider removals",
        "initial_coordinates": "Random seed 22 disk; replaced by Gephi layouts for screenshots",
        "visualization_only": "Community display groups the eight largest communities by raw ID; others share grey. All 98 raw IDs remain in community_id.",
    }
    (output / "input_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"file": str(path), "views": counts, "lost_external": len(disconnected)}, indent=2))


if __name__ == "__main__":
    main()
