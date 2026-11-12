"""Run the full provider-removal experiment and country-label sensitivity."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import platform
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import matplotlib
import networkx as nx
import numpy as np
import scipy

from .graph_loader import ValidationError, load_graph
from .robustness import METRICS, RobustnessError, assigned_country_sensitivity, run_robustness
from .robustness_plots import LABELS, render_robustness_figures


def _hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _csv(path, rows):
    rows = tuple(rows)
    if not rows:
        raise RobustnessError(f"Cannot export an empty table: {path}")
    with Path(path).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=tuple(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _findings(baseline, result, sensitivity, sensitivity_baseline):
    maximum = result.settings["max_removed_providers"]
    final = [row for row in result.summary if row["k"] == maximum]
    lines = [
        "# Provider removals and topological connectivity", "",
        f"The primary experiment uses the supplied v1 baseline: {len(baseline.graph):,} nodes, "
        f"{len(baseline.focal_asns):,} Italian ASes, {len(baseline.external_asns):,} external ASes "
        f"and {len(baseline.candidate_providers)} candidate providers. It compares one targeted "
        f"sequence with {result.settings['random_trials']} uniform random permutations, "
        f"for k=0..{maximum} cumulative provider removals.", "",
        f"At k={maximum}:", "",
        "| Connectivity measure | Targeted retained | Random median | Random 10th-90th percentiles |",
        "|---|---:|---:|---:|",
    ]
    for row in final:
        lines.append(f"| {LABELS[row['metric']]} | {row['targeted']:.4%} | {row['random_median']:.4%} | "
                     f"{row['random_q10']:.4%}-{row['random_q90']:.4%} |")
    lines.extend(["", "## Interpretation", ""])
    for row in final:
        difference = 100 * (row["random_median"] - row["targeted"])
        relation = "below" if difference > 0 else "above" if difference < 0 else "equal to"
        lines.append(f"- **{LABELS[row['metric']]}**: the targeted retained fraction is "
                     f"{abs(difference):.4f} percentage points {relation} the random median at k={maximum}.")
    lines.extend([
        "", "These comparisons describe the configured endpoint, not a guarantee that the "
        "targeted curve is worse at every k or in every random trial. The provider order was "
        "fixed from distinct Italian-customer counts, baseline degree and ASN; it did not use PageRank.", "",
        "The pair measure falls both when external endpoints are deleted and when components "
        "separate. Even if G remained connected after k deletions from E, its maximum pair "
        "fraction would be `(|E|-k)/|E|`, not 1. Provider-customer coverage is therefore not "
        "itself a prediction of the observed connectivity loss.", "",
        "## Curves", "",
        "![Connectivity loss overview](figures/robustness_loss_overview.png)", "",
        "The overview shows loss in percentage points with a separate vertical scale for each "
        "metric. Every individual figure below also shows the complete fraction scale from 0 to 100%. "
        "Random bands are the 10th-90th percentiles of simulation outcomes, not confidence intervals "
        "for real Internet outages.", "",
    ])
    for metric in METRICS:
        lines.extend([f"![{LABELS[metric]}](figures/robustness_{metric}.png)", ""])
    lines.extend(["## Country-label sensitivity", ""])
    if sensitivity is not None:
        common_maximum = min(maximum, sensitivity.settings["max_removed_providers"])
        compared_primary = [row for row in result.summary if row["k"] == common_maximum]
        lines.extend([
            "The main run preserves the supplied country assignments. A separate scenario excludes "
            "non-assigned country labels from E and P while retaining those nodes as transit nodes. "
            "The shared CSV package is never rewritten. This is a sensitivity analysis, not a claim "
            "that both students have agreed on a new package convention.", "",
            f"The alternate scenario uses E={len(sensitivity_baseline.external_asns):,} and "
            f"P={len(sensitivity_baseline.candidate_providers)}; G and F are unchanged. "
            "The removed candidate in this package is AS1273, labelled EU. Each scenario uses "
            "its own frozen baseline denominators and candidate pool, so the comparison includes "
            "both classification and removal-order effects.", "",
            f"Comparison at the common removal count k={common_maximum}:", "",
            "| Measure | v1 targeted | Alternate targeted | v1 random median | Alternate random median |",
            "|---|---:|---:|---:|---:|",
        ])
        other = {row["metric"]: row for row in sensitivity.summary if row["k"] == common_maximum}
        for row in compared_primary:
            alternative = other[row["metric"]]
            lines.append(f"| {LABELS[row['metric']]} | {row['targeted']:.4%} | {alternative['targeted']:.4%} | "
                         f"{row['random_median']:.4%} | {alternative['random_median']:.4%} |")
        lines.extend(["", "Complete alternate orders, runs and percentile summaries are saved in "
                      "`sensitivity/eu_excluded/`. The main conclusions should be checked against "
                      "both scenarios when choosing the shared classification convention.", ""])
    else:
        lines.extend(["No alternate run was needed because the supplied external set had no non-assigned labels.", ""])
    lines.extend([
        "## Methods and reproducibility", "",
        "- One uniformly shuffled, sorted candidate pool per fixed seed; prefix deletions accumulate across k.",
        "- Every trial starts from the same graph. ASNs are removed individually, including incident edges.",
        "- Connected components are recomputed with SciPy sparse graph routines after every deletion.",
        "- For each remaining component C, count focal nodes f_C and external nodes e_C. "
        "The three measures are `sum(f_C where e_C>0)/|F|`, `sum(f_C*e_C)/(|F|*|E|)` "
        "and `largest_component_size/|G|`, using fixed baseline denominators.",
        "- Baseline values equal 1, cumulative metrics never increase, and order/score checks "
        "run before results are exported. All random trials and the targeted sequence use the "
        "same k values and candidate pool within each scenario.",
        "- The first random trial is the runtime pilot and is retained in the full experiment. "
        "Random seeds and per-trial runtime are recorded in `robustness_run_manifest.json`.", "",
        "## Limits and remaining coordination", "",
        "The data are inferred and incomplete. Registration country does not locate equipment. "
        "Edges carry no traffic or bandwidth; undirected paths may violate routing policy. "
        "These ASN-removal scenarios do not estimate users disconnected, traffic loss or an "
        "actual outage. Results cover one country and snapshot, and organizations with multiple "
        "ASNs are not removed as one entity.", "",
        "The main run's country convention and byte/LF input hashes are recorded explicitly. "
        "Shared classification sign-off, Student A's PageRank convergence correction and current "
        "course submission/disclosure instructions remain collaboration items; none is silently "
        "treated as completed by this experiment.", "",
        "Reproduce from the repository root:", "", "```powershell",
        "python -m analysis.resilience.run_robustness --hash-policy newline-compatible", "```", "",
    ])
    return "\n".join(lines)


def main(argv=None):
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=root / "config/project.json")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--hash-policy", choices=("exact", "newline-compatible"), default="exact")
    args = parser.parse_args(argv)
    started = time.perf_counter()
    try:
        config_path = args.config.resolve()
        baseline = load_graph(config_path, hash_policy=args.hash_policy)
        config = json.loads(config_path.read_text(encoding="utf-8"))
        settings = config.get("robustness")
        if not isinstance(settings, dict):
            raise RobustnessError("Configuration must declare robustness settings")
        seeds = settings.get("random_seeds")
        if not isinstance(seeds, list) or type(settings.get("random_trials")) is not int or settings["random_trials"] != len(seeds):
            raise RobustnessError("random_trials must equal the number of configured random seeds")
        if settings.get("targeted_ordering") != "focal_customer_count_desc, degree_desc, asn_asc":
            raise RobustnessError("The configured targeted ordering differs from the implemented fixed-baseline rule")
        output = (config_path.parent.parent / (args.output_dir or config["paths"]["results_resilience"])).resolve()
        protected = [Path(baseline.report["package_directory"])]
        if config["paths"].get("raw_data"):
            protected.append((config_path.parent.parent / config["paths"]["raw_data"]).resolve())
        if any(output == path or output.is_relative_to(path) for path in protected):
            raise RobustnessError("Results cannot be written into shared inputs")
        emit = lambda message: print(message, flush=True)
        print("Primary experiment: preserve supplied v1 sets.", flush=True)
        result = run_robustness(baseline, max_removed_providers=settings.get("max_removed_providers"),
                                random_seeds=seeds, progress=emit)
        alternative_baseline = assigned_country_sensitivity(baseline)
        alternative = None
        if alternative_baseline.external_asns != baseline.external_asns:
            print("Sensitivity: exclude non-assigned country labels from E/P; keep G/F unchanged.", flush=True)
            alternative = run_robustness(alternative_baseline,
                                         max_removed_providers=settings.get("max_removed_providers"),
                                         random_seeds=seeds, progress=emit)
        output.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".robustness-staging-", dir=output) as temporary:
            stage = Path(temporary).resolve()
            if not stage.is_relative_to(output) or stage == output:
                raise RobustnessError("Temporary output escaped the result directory")
            for folder, run in [(stage, result)] + ([(stage / "sensitivity/eu_excluded", alternative)] if alternative else []):
                folder.mkdir(parents=True, exist_ok=True)
                _csv(folder / "robustness_runs.csv", run.runs)
                _csv(folder / "removal_order.csv", run.removal_order)
                _csv(folder / "robustness_summary.csv", run.summary)
            render_robustness_figures(result.summary, output_directory=stage / "figures",
                                      focal_count=len(baseline.focal_asns), external_count=len(baseline.external_asns),
                                      graph_count=len(baseline.graph), provider_count=len(baseline.candidate_providers),
                                      random_trials=result.settings["random_trials"])
            with (stage / "robustness_findings.md").open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(_findings(baseline, result, alternative, alternative_baseline))
            package = Path(baseline.report["package_directory"])
            if _hash(config_path) != baseline.report["config_sha256"] or _hash(package / "manifest.json") != baseline.report["manifest_sha256"]:
                raise RobustnessError("Shared configuration or manifest changed during the run")
            for name, info in baseline.report["processed_hashes"].items():
                if _hash(package / name) != info["actual_sha256"]:
                    raise RobustnessError(f"Input {name} changed during the run")
            source = Path(__file__).parent
            manifest = {
                "status": "complete", "scope": "provider removals; main v1 scenario plus declared country sensitivity",
                "created_at_utc": datetime.now(timezone.utc).isoformat(), "source_commit": baseline.report["source_commit"],
                "baseline_fingerprint": baseline.report["baseline_fingerprint"],
                "input_counts": baseline.report["counts"], "input_warnings": baseline.report["warnings"],
                "config_sha256": baseline.report["config_sha256"], "manifest_sha256": baseline.report["manifest_sha256"],
                "processed_input_hashes": baseline.report["processed_hashes"],
                "primary": {"country_policy": baseline.report["country_policy"], "settings": result.settings,
                            "run_rows": len(result.runs), "order_rows": len(result.removal_order),
                            "trial_runtimes": result.trial_runtimes, "runtime_seconds": result.runtime_seconds},
                "sensitivity": None if alternative is None else {
                    "policy": "exclude non-assigned country labels from E/P; preserve all transit nodes",
                    "settings": alternative.settings, "run_rows": len(alternative.runs),
                    "order_rows": len(alternative.removal_order), "trial_runtimes": alternative.trial_runtimes,
                    "runtime_seconds": alternative.runtime_seconds,
                    "excluded_external_asns": sorted(baseline.external_asns - alternative_baseline.external_asns),
                    "excluded_candidate_asns": sorted(baseline.candidate_providers - alternative_baseline.candidate_providers)},
                "source_hashes": {name: _hash(source / name) for name in
                                  ("graph_loader.py", "robustness.py", "robustness_plots.py", "run_robustness.py")},
                "environment": {"python": platform.python_version(), "networkx": nx.__version__,
                                "numpy": np.__version__, "scipy": scipy.__version__, "matplotlib": matplotlib.__version__},
                "runtime_seconds": time.perf_counter() - started,
                "output_hashes": {path.relative_to(stage).as_posix(): _hash(path)
                                  for path in sorted(stage.rglob("*")) if path.is_file()},
            }
            with (stage / "robustness_run_manifest.json").open("w", encoding="utf-8", newline="\n") as handle:
                json.dump(manifest, handle, indent=2, sort_keys=True, allow_nan=False)
                handle.write("\n")
            for source_path in sorted((path for path in stage.rglob("*") if path.is_file()),
                                      key=lambda path: (path.name == "robustness_run_manifest.json", path.as_posix())):
                destination = (output / source_path.relative_to(stage)).resolve()
                if not destination.is_relative_to(output):
                    raise RobustnessError("Output path escaped result directory")
                destination.parent.mkdir(parents=True, exist_ok=True)
                os.replace(source_path, destination)
    except (ValidationError, RobustnessError, OSError, ValueError, TypeError) as exc:
        print(f"Robustness experiment failed: {exc}", file=sys.stderr)
        return 1
    print(f"Robustness complete: {len(result.runs)} primary rows. Findings: {output / 'robustness_findings.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
