"""Retain hand-checkable score calculations and an optional full-graph check."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import platform
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import networkx as nx
import numpy as np
import scipy

if __package__:
    from .community_metrics import CommunityScoreError, CommunityScorer
    from .graph_loader import ValidationError, load_graph
else:
    from community_metrics import CommunityScoreError, CommunityScorer
    from graph_loader import ValidationError, load_graph


METRICS = ("modularity", "normalized_cut", "course_nmi", "infomap_bits")


def _entropy(*probabilities: float) -> float:
    return -math.fsum(p * math.log2(p) for p in probabilities if p > 0)


def _assert_close(observed: float | None, expected: float | None, name: str) -> None:
    if expected is None:
        if observed is not None:
            raise CommunityScoreError(f"{name}: expected an undefined value, got {observed}")
    elif observed is None or not math.isclose(observed, expected, rel_tol=1e-10, abs_tol=1e-10):
        raise CommunityScoreError(f"{name}: expected {expected}, got {observed}")


def _tiny_cases() -> list[dict]:
    # Scalar calculations intentionally do not use the scorer's sparse helpers.
    path = nx.path_graph(4)
    cycle = nx.cycle_graph(4)
    disconnected = nx.Graph([(0, 1), (2, 3)])
    isolate = nx.Graph([(0, 1)])
    isolate.add_node(2)
    specifications = [
        ("path4_paired", path, [{0, 1}, {2, 3}],
         (1 / 6, 2 / 3, (2 / 3) * math.log2(4 / 3) + (1 / 3) * math.log2(2 / 3),
          20 / 57 + (77 / 57) * _entropy(20 / 77, 20 / 77, 37 / 77)),
         "r=(10/57,37/114,37/114,10/57); q_i=10/57; "
         "L=20/57+(77/57)H(20/77,20/77,37/77)"),
        ("cycle4_adjacent", cycle, [{0, 1}, {2, 3}],
         (0.0, 1.0, 0.0, 0.5 + 1.5 * math.log2(3)),
         "r_u=1/4; q_i=1/4; L=1/2+(3/2)log2(3)"),
        ("cycle4_alternating", cycle, [{0, 2}, {1, 3}],
         (-0.5, 2.0, 1.0, 37 / 40 + (77 / 40) * _entropy(37 / 77, 20 / 77, 20 / 77)),
         "r_u=1/4; q_i=37/80; L=37/40+(77/40)H(37/77,20/77,20/77)"),
        ("cycle4_one_community", cycle, [{0, 1, 2, 3}], (0.0, 0.0, None, 2.0),
         "H(community)=0, so NMI undefined; q=0; L=H(r)=2"),
        ("cycle4_singletons", cycle, [{0}, {1}, {2}, {3}],
         (-0.25, 4.0, 0.5, (77 / 80) * 2 + (157 / 80) * _entropy(77 / 157, 80 / 157)),
         "r_u=1/4; q_i=77/320; L=(77/80)*2+(157/80)H(77/157,80/157)"),
        ("disconnected_edges", disconnected, [{0, 1}, {2, 3}],
         (0.5, 0.0, 1.0, 0.075 + 1.075 * _entropy(3 / 43, 20 / 43, 20 / 43)),
         "No crossing edges; recorded teleport gives q_i=3/80; "
         "L=3/40+(43/40)H(3/43,20/43,20/43)"),
        ("edge_and_isolate", isolate, [{0, 1}, {2}],
         (0.0, None, None,
          4 / 43 + (42 / 43) * _entropy(1 / 21, 10 / 21, 10 / 21)
          + (5 / 43) * _entropy(2 / 5, 3 / 5)),
         "Uniform dangling redistribution: r=(20/43,20/43,3/43); q_i=2/43; "
         "zero edge-volume community makes Ncut undefined"),
    ]
    results = []
    for name, graph, partition, expected_values, manual in specifications:
        scorer = CommunityScorer(graph)
        scores = scorer.score(partition)
        expected = dict(zip(METRICS, expected_values))
        for metric, value in expected.items():
            _assert_close(getattr(scores, metric), value, f"{name}/{metric}")
        library_modularity = nx.community.modularity(graph, partition, weight=None, resolution=1)
        _assert_close(scores.modularity, library_modularity, f"{name}/NetworkX modularity")
        results.append({
            "case": name, "status": "passed", "manual_calculation": manual,
            "edges": [list(edge) for edge in graph.edges()],
            "nodes": list(graph.nodes()), "partition": [sorted(group) for group in partition],
            "expected": expected, "observed": scores.to_dict(),
            "networkx_modularity": library_modularity,
            "stationary_probabilities": dict(scorer.walk.probabilities),
        })
    return results


def _real_graph_check(config_path: Path, hash_policy: str) -> dict:
    print("Loading the shared package for the full-graph fixed-partition check...", flush=True)
    baseline = load_graph(config_path, hash_policy=hash_policy)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    settings = config.get("pagerank", {})
    if not isinstance(settings, dict) or settings.get("teleportation") != "uniform" or settings.get("weighted") is not False:
        raise CommunityScoreError("The shared PageRank settings must declare uniform teleportation and weighted=false")
    damping = settings.get("damping")
    scorer = CommunityScorer(baseline.graph, damping=damping)
    # A fixed, deliberately trivial partition checks all-node coverage and the
    # map-equation entropy identity. It is not a discovered community result.
    scores = scorer.score([baseline.analysis_asns])
    node_entropy = _entropy(*scorer.walk.probabilities.values())
    _assert_close(scores.modularity, 0.0, "real graph/one-community modularity")
    _assert_close(scores.normalized_cut, 0.0, "real graph/one-community Ncut")
    _assert_close(scores.course_nmi, None, "real graph/one-community NMI")
    _assert_close(scores.infomap_bits, node_entropy, "real graph/one-community entropy identity")
    return {
        "status": "passed", "partition": "all nodes in one fixed community; no Louvain run",
        "source_commit": baseline.report["source_commit"],
        "baseline_fingerprint": baseline.report["baseline_fingerprint"],
        "config_sha256": baseline.report["config_sha256"],
        "manifest_sha256": baseline.report["manifest_sha256"],
        "input_warnings": baseline.report["warnings"],
        "independent_node_entropy_bits": node_entropy,
        "observed": scores.to_dict(),
    }


def _write_results(report: dict, output_directory: Path) -> None:
    output_directory.mkdir(parents=True, exist_ok=True)
    with (output_directory / "verification.json").open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(report, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")
    with (output_directory / "checked_scores.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["case", "metric", "expected", "observed", "absolute_error"],
                                lineterminator="\n")
        writer.writeheader()
        for case in report["tiny_cases"]:
            for metric in METRICS:
                expected, observed = case["expected"][metric], case["observed"][metric]
                writer.writerow({"case": case["case"], "metric": metric,
                                 "expected": "undefined" if expected is None else expected,
                                 "observed": "undefined" if observed is None else observed,
                                 "absolute_error": "" if expected is None else abs(observed - expected)})
    lines = [
        "# Community-score verification", "",
        f"All {len(report['tiny_cases'])} retained small-graph examples passed their scalar checks "
        "and the independent NetworkX modularity comparison.", "",
        "Conventions: unweighted graph; modularity resolution 1; summed Ncut; edge-end "
        "community NMI; two-level map equation of the supplied partition; damping 0.85; "
        "uniform, recorded teleportation; base-2 logarithms. Isolates use uniform dangling redistribution.", "",
        "| Case | Modularity | Ncut | Course NMI | InfoMap, bits/step |",
        "|---|---:|---:|---:|---:|",
    ]
    for case in report["tiny_cases"]:
        values = ["undefined" if case["observed"][metric] is None else f"{case['observed'][metric]:.12g}"
                  for metric in METRICS]
        lines.append(f"| {case['case']} | {' | '.join(values)} |")
    lines.extend(["", "## Retained manual calculations", "", "Here H denotes base-2 Shannon entropy.", ""])
    lines.extend(f"- **{case['case']}**: `{case['manual_calculation']}`." for case in report["tiny_cases"])
    lines.extend([
        "", "The alternating cycle partition has NMI 1 and negative modularity. Course NMI "
        "captures dependence, including bipartite structure; it is not an assortativity score. "
        "Disconnected components still have nonzero InfoMap exit rates because teleportation is recorded.", "",
        "The pytest suite supplies a separate dense-matrix oracle: a stationary linear solve, "
        "direct transition exit probabilities and direct codebook entropies. This command retains "
        "the scalar examples; it does not invoke pytest.", "",
    ])
    real = report["real_graph_check"]
    if real is not None:
        observed = real["observed"]
        walk = observed["walk"]
        lines.extend([
            "## Full-graph check", "",
            f"Passed on {observed['node_count']:,} nodes and {observed['edge_count']:,} edges using "
            "one fixed community containing every node. This verifies execution at the project "
            "graph size; it is not a community-discovery result.", "",
            f"Modularity and Ncut are zero; NMI is undefined because community entropy is zero. "
            f"InfoMap is {observed['infomap_bits']:.12g} bits/step, equal to the independently "
            "summed node-visit entropy.", "",
            f"PageRank: {walk['iterations']} iterations; total L1 residual "
            f"{walk['l1_residual']:.6g}; tolerance {walk['residual_tolerance']:.6g}; "
            f"L1 error bound {walk['l1_error_bound']:.6g}.", "",
            "The stationary vector was computed locally from G. No Student A importance result "
            "was read. The input country-label and checksum-portability warnings remain in "
            "the JSON record and do not change this topology-only check.", "",
        ])
    lines.extend([
        "## Sources and reproduction", "",
        "Definitions: `Part2_community.pdf`, PDF pp. 17, 48, 66 and 72-73. The lecture's "
        "InfoMap exit expression counts teleportation. Its NMI formula gives zero under "
        "independence; the contradictory explanatory sentence is not used.", "",
        "Run from the repository root:", "", "```powershell",
        "python -m analysis.resilience.verify_community_metrics --check-real-graph", "```", "",
        "The JSON record contains expected and observed values, walk diagnostics, source hashes "
        "and environment versions. `checked_scores.csv` contains the small-graph comparisons.", "",
    ])
    with (output_directory / "verification.md").open("w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines))


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=root / "config" / "project.json")
    parser.add_argument("--check-real-graph", action="store_true")
    parser.add_argument("--hash-policy", choices=("exact", "newline-compatible"), default="exact")
    parser.add_argument("--output-dir", type=Path, default=root / "results" / "resilience" / "metric_checks")
    args = parser.parse_args(argv)
    started = time.perf_counter()
    try:
        config_path = args.config.resolve()
        project_root = config_path.parent.parent
        output_directory = args.output_dir.resolve()
        if config_path.is_file():
            config = json.loads(config_path.read_text(encoding="utf-8"))
            paths = config.get("paths", {}) if isinstance(config, dict) else {}
            if not isinstance(paths, dict):
                raise CommunityScoreError("Configuration paths must be an object")
            for key in ("processed_data", "raw_data"):
                if paths.get(key):
                    if not isinstance(paths[key], str):
                        raise CommunityScoreError(f"Configuration paths.{key} must be a string")
                    protected = (project_root / paths[key]).resolve()
                    if output_directory == protected or protected in output_directory.parents:
                        raise CommunityScoreError("Verification reports must not be written inside shared inputs")
        cases = _tiny_cases()
        real_check = _real_graph_check(config_path, args.hash_policy) if args.check_real_graph else None
        source_hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                         for path in (Path(__file__), Path(__file__).with_name("community_metrics.py"))}
        report = {
            "status": "passed", "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "tiny_cases": cases, "real_graph_check": real_check,
            "absolute_and_relative_tolerance": 1e-10,
            "source_hashes": source_hashes,
            "environment": {"python": platform.python_version(), "networkx": nx.__version__,
                            "numpy": np.__version__, "scipy": scipy.__version__},
            "runtime_seconds": round(time.perf_counter() - started, 3),
        }
        _write_results(report, output_directory)
    except (CommunityScoreError, ValidationError, OSError, ValueError) as exc:
        print(f"Community metric verification failed: {exc}", file=sys.stderr)
        return 1
    print(f"Verified {len(cases)} small-graph examples and all four course scores.")
    if real_check is not None:
        walk = real_check["observed"]["walk"]
        print(f"Full-graph check passed; PageRank total L1 residual {walk['l1_residual']:.6g}.")
    print(f"Verification records saved in {output_directory}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
