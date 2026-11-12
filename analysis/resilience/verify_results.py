"""Audit saved outputs with independent component samples and table reconciliation."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import re
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import networkx as nx
import numpy as np

from .graph_loader import load_graph
from .robustness import METRICS, assigned_country_sensitivity


def _csv(path):
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _check(condition, message):
    if not condition:
        raise ValueError(message)


def _robustness(folder, baseline, settings):
    raw, order_rows, summary = (_csv(folder / name) for name in
                                ("robustness_runs.csv", "removal_order.csv", "robustness_summary.csv"))
    orders = defaultdict(list)
    for row in order_rows:
        orders[(row["strategy"], int(row["trial"]), row["seed"])].append((int(row["position"]), int(row["asn"])))
    maximum, trials = settings["max_removed_providers"], settings["random_trials"]
    _check(len(raw) == (trials + 1) * (maximum + 1), "Wrong number of robustness rows")
    _check(len(orders) == trials + 1, "Wrong number of removal sequences")
    sequences = {}
    for key, entries in orders.items():
        entries.sort()
        sequence = [asn for _, asn in entries]
        _check([position for position, _ in entries] == list(range(1, len(sequence) + 1)), "Removal positions repeat or skip")
        _check(len(sequence) == len(baseline.candidate_providers) and set(sequence) == baseline.candidate_providers,
               "Removal pool differs from P")
        expected = sorted(baseline.candidate_providers)
        if key[0] == "random":
            random.Random(int(key[2])).shuffle(expected)
        else:
            expected.sort(key=lambda asn: (-len(baseline.provider_customers[asn]), -baseline.baseline_degrees[asn], asn))
        _check(sequence == expected, "Saved order does not match the declared rule/seed")
        sequences[key] = sequence
    by_trial = defaultdict(list)
    for row in raw:
        by_trial[(row["strategy"], int(row["trial"]), row["seed"])].append(row)
    _check(by_trial.keys() == orders.keys(), "Run and order identifiers differ")
    for key, rows in by_trial.items():
        rows.sort(key=lambda row: int(row["k"]))
        _check([int(row["k"]) for row in rows] == list(range(maximum + 1)), "Removal counts missing or duplicated")
        for row in rows:
            k = int(row["k"])
            _check(int(row["remaining_nodes"]) == len(baseline.graph) - k, "Wrong remaining node count")
            _check(int(row["remaining_external_asns"]) == len(baseline.external_asns) - k, "Wrong remaining E count")
            expected = (int(row["focal_connected_count"]) / len(baseline.focal_asns),
                        int(row["reachable_pair_count"]) / (len(baseline.focal_asns) * len(baseline.external_asns)),
                        int(row["largest_component_nodes"]) / len(baseline.graph))
            _check(all(float(row[metric]) == value for metric, value in zip(METRICS, expected)), "Fixed denominator mismatch")
        for metric in METRICS:
            values = [float(row[metric]) for row in rows]
            _check(values[0] == 1 and all(0 <= value <= 1 for value in values), "Invalid baseline/bounds")
            _check(all(a >= b for a, b in zip(values, values[1:])), "Metric increased after removal")
    for row in summary:
        selected = [run for run in raw if int(run["k"]) == int(row["k"])]
        values = [float(run[row["metric"]]) for run in selected if run["strategy"] == "random"]
        expected = np.percentile(values, [10, 50, 90], method="linear")
        _check(all(math.isclose(float(row[key]), value, abs_tol=1e-14) for key, value in
                   zip(("random_q10", "random_median", "random_q90"), expected)), "Percentile binding mismatch")
        targeted = next(run for run in selected if run["strategy"] == "targeted")
        _check(float(row["targeted"]) == float(targeted[row["metric"]]), "Targeted summary differs from raw output")
    # Independent NetworkX component oracle on selected nonbaseline snapshots.
    sampled = []
    for key in sorted(sequences):
        if key[0] == "targeted":
            counts = sorted({min(1, maximum), min(10, maximum), maximum})
        elif key[1] == 1:
            counts = sorted({min(5, maximum), maximum})
        elif key[1] == trials:
            counts = [maximum]
        else:
            continue
        for k in counts:
            removed = frozenset(sequences[key][:k])
            view = nx.subgraph_view(baseline.graph, filter_node=lambda asn: asn not in removed)
            components = list(nx.connected_components(view))
            connected = sum(len(group & baseline.focal_asns) for group in components if group & baseline.external_asns)
            pairs = sum(len(group & baseline.focal_asns) * len(group & baseline.external_asns) for group in components)
            largest = max(map(len, components), default=0)
            row = next(row for row in by_trial[key] if int(row["k"]) == k)
            _check((connected, pairs, largest) == (int(row["focal_connected_count"]), int(row["reachable_pair_count"]),
                                                  int(row["largest_component_nodes"])), "Independent component sample failed")
            sampled.append({"strategy": key[0], "trial": key[1], "k": k})
    return {"metric_rows": len(raw), "order_rows": len(order_rows), "summary_rows": len(summary),
            "independent_component_samples": sampled}


def main(argv=None):
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=root / "config/project.json")
    args = parser.parse_args(argv)
    baseline = load_graph(args.config, hash_policy="newline-compatible")
    config = json.loads(args.config.read_text(encoding="utf-8"))
    out = (args.config.resolve().parent.parent / config["paths"]["results_resilience"]).resolve()
    combined = json.loads((out / "run_manifest.json").read_text(encoding="utf-8"))
    for name, wanted in combined["output_hashes"].items():
        file = (out / name).resolve()
        _check(file.is_relative_to(out) and _hash(file) == wanted, f"Combined output hash mismatch: {name}")
    for manifest_name in ("community_run_manifest.json", "robustness_run_manifest.json", "run_manifest.json"):
        manifest = json.loads((out / manifest_name).read_text(encoding="utf-8"))
        for name, wanted in manifest["source_hashes"].items():
            _check(Path(name).name == name and _hash(Path(__file__).parent / name) == wanted, f"Source changed: {name}")
    assignments = defaultdict(dict)
    for row in _csv(out / "community_assignments.csv"):
        seed, asn, community = int(row["seed"]), int(row["asn"]), int(row["community_id"])
        _check(asn not in assignments[seed], "Repeated community assignment")
        assignments[seed][asn] = community
    quality = _csv(out / "community_quality.csv")
    for row in quality:
        labels = assignments[int(row["seed"])]
        _check(labels.keys() == baseline.analysis_asns, "Partition coverage mismatch")
        groups = defaultdict(set)
        for asn, label in labels.items():
            groups[label].add(asn)
        _check(list(sorted(groups)) == list(range(len(groups))), "Noncanonical community IDs")
        ordered = sorted(groups.values(), key=lambda group: (-len(group), min(group)))
        _check(all(groups[index] == group for index, group in enumerate(ordered)), "Community ID order mismatch")
        q = nx.community.modularity(baseline.graph, list(groups.values()), weight=None, resolution=1)
        _check(math.isclose(q, float(row["modularity"]), abs_tol=1e-12), "Independent modularity mismatch")
    selected = max(quality, key=lambda row: (float(row["modularity"]), -int(row["seed"])))
    _check(int(selected["seed"]) == combined["selected_louvain_seed"], "Incorrect selected seed")
    summary = _csv(out / "community_summary.csv")
    _check(sum(int(row["node_count"]) for row in summary) == len(baseline.graph), "Community node totals")
    _check(sum(int(row["focal_asn_count"]) for row in summary) == len(baseline.focal_asns), "Community F totals")
    _check(sum(int(row["candidate_provider_count"]) for row in summary) == len(baseline.candidate_providers), "Community P totals")
    labels = assignments[int(selected["seed"])]
    quotient = Counter(tuple(sorted((labels[a], labels[b]))) for a, b in baseline.graph.edges())
    exported = {(int(row["source_community"]), int(row["target_community"])): int(row["edge_count"])
                for row in _csv(out / "community_edges.csv")}
    _check(dict(quotient) == exported, "Quotient edges differ from direct reconstruction")
    robust_manifest = json.loads((out / "robustness_run_manifest.json").read_text(encoding="utf-8"))
    primary = _robustness(out, baseline, robust_manifest["primary"]["settings"])
    alternate = _robustness(out / "sensitivity/eu_excluded", assigned_country_sensitivity(baseline),
                            robust_manifest["sensitivity"]["settings"]) if robust_manifest["sensitivity"] else None
    junit = ET.parse(out / "tests.junit.xml")
    suites = list(junit.getroot().iter("testsuite"))
    test_count = sum(int(suite.attrib.get("tests", 0)) for suite in suites)
    failures = sum(int(suite.attrib.get("failures", 0)) + int(suite.attrib.get("errors", 0)) for suite in suites)
    _check(test_count > 0 and failures == 0, "Saved unit-test run contains failures")
    report = {"status": "passed", "created_at_utc": datetime.now(timezone.utc).isoformat(),
              "community_assignment_rows": sum(map(len, assignments.values())),
              "community_seeds": sorted(assignments), "selected_seed": int(selected["seed"]),
              "primary": primary, "sensitivity": alternate, "tests_passed": test_count,
              "combined_manifest_sha256": _hash(out / "run_manifest.json"),
              "verifier_sha256": _hash(Path(__file__)),
              "scope": "All exported hashes, orders, denominators, monotonicity and quantile bindings; "
                       "NetworkX modularity and quotient reconstruction; listed independent component samples."}
    with (out / "final_validation.json").open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(report, handle, indent=2, sort_keys=True)
        handle.write("\n")
    lines = ["# Final numerical checks", "", f"Status: passed. Saved unit tests: {test_count} passed, no failures.", "",
             f"Validated {report['community_assignment_rows']:,} community assignments and selected seed {report['selected_seed']}.", "",
             f"Primary scenario: {primary['metric_rows']} metric rows and {primary['order_rows']} full-order rows. "
             f"Independent NetworkX checks covered {len(primary['independent_component_samples'])} listed component snapshots.", "",
             "All saved scenario orders, fixed denominators, bounds, monotonicity and percentile bindings were checked. "
             "The JSON record distinguishes these full-table checks from sampled independent component recalculation.", "",
             "The country-label sensitivity was checked separately. Source/output hashes matched the recorded manifests. "
             "Fresh-environment byte-level reproduction is also recorded in reproduction_check.json.", ""]
    with (out / "final_validation.md").open("w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines))
    print(f"Final validation passed: {test_count} tests; {primary['metric_rows']} primary rows; "
          f"{len(primary['independent_component_samples'])} independent component snapshots per scenario.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
