"""Run and export Student B's baseline Louvain community analysis."""

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

from .community_analysis import CommunityAnalysis, CommunityAnalysisError, run_louvain
from .community_metrics import CommunityScoreError
from .community_plots import render_community_figures
from .graph_loader import GraphBaseline, ValidationError, load_graph


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_csv(path: Path, rows: list[dict], fields: tuple[str, ...] | None = None) -> None:
    columns = fields if fields is not None else tuple(rows[0])
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _quality_rows(analysis: CommunityAnalysis) -> list[dict]:
    rows = []
    for run in sorted(analysis.runs, key=lambda item: item.seed):
        scores = run.scores
        rows.append({
            "seed": run.seed, "community_count": scores.community_count,
            "modularity": scores.modularity, "normalized_cut": scores.normalized_cut,
            "course_nmi": scores.course_nmi, "infomap_bits": scores.infomap_bits,
            "selected": run.seed == analysis.selected.seed,
            "networkx_modularity": run.networkx_modularity,
            "louvain_seconds": run.louvain_seconds, "scoring_seconds": run.scoring_seconds,
            "resolution": analysis.resolution, "threshold": analysis.threshold,
            "weighted": False, "damping": scores.walk.damping,
            "teleportation": "uniform", "teleportation_recorded": True, "log_base": 2,
            "pagerank_l1_residual": scores.walk.l1_residual,
            "pagerank_residual_tolerance": scores.walk.residual_tolerance,
        })
    return rows


def _findings(baseline: GraphBaseline, analysis: CommunityAnalysis) -> str:
    selected = analysis.selected
    rows = analysis.summary_rows
    n, m = len(baseline.graph), baseline.graph.number_of_edges()
    focal_total, provider_total = len(baseline.focal_asns), len(baseline.candidate_providers)
    by_focal = sorted(rows, key=lambda row: (-row["focal_asn_count"], row["community_id"]))
    by_providers = sorted(rows, key=lambda row: (-row["candidate_provider_count"], row["community_id"]))
    largest, focal_leader, provider_leader = rows[0], by_focal[0], by_providers[0]
    qualities = [run.scores.modularity for run in analysis.runs]
    shortest_map = min(analysis.runs, key=lambda run: (run.scores.infomap_bits, run.seed))
    lines = [
        "# Communities in the observed AS graph", "",
        f"Louvain found {len(rows)} communities in the selected partition of the "
        f"{n:,}-node, {m:,}-edge baseline graph. Seed {selected.seed} had the highest "
        f"modularity ({selected.scores.modularity:.6f}) among the configured runs.", "",
        "The communities describe this inferred, undirected topology. They do not establish "
        "geographic, ownership or routing-policy boundaries.", "",
        "## Seed comparison", "",
        "| Seed | Communities | Modularity | Ncut, sum | Course NMI | InfoMap, bits/step | Selected |",
        "|---|---:|---:|---:|---:|---:|:---:|",
    ]
    for run in sorted(analysis.runs, key=lambda item: item.seed):
        scores = run.scores
        cut = "undefined" if scores.normalized_cut is None else f"{scores.normalized_cut:.6f}"
        nmi = "undefined" if scores.course_nmi is None else f"{scores.course_nmi:.6f}"
        lines.append(f"| {run.seed} | {scores.community_count} | {scores.modularity:.6f} | {cut} | "
                     f"{nmi} | {scores.infomap_bits:.6f} | {'yes' if run is selected else ''} |")
    lines.extend([
        "", f"The modularity range was {min(qualities):.6f}-{max(qualities):.6f} "
        f"(spread {max(qualities) - min(qualities):.6f}). The selected run maximizes modularity; "
        "the other three course measures evaluate the same partitions. An exact modularity "
        "tie is broken by the smaller seed.", "",
    ])
    if shortest_map.seed != selected.seed:
        lines.extend([
            f"Seed {shortest_map.seed} has the shortest map-equation length "
            f"({shortest_map.scores.infomap_bits:.6f} bits/step), whereas the selected seed "
            f"{selected.seed} has {selected.scores.infomap_bits:.6f} bits/step. "
            "The measures therefore do not all prefer the same partition.", "",
        ])
    if analysis.sensitivity_rows:
        lines.extend([
            "Pairwise label agreement is reported separately from the course edge-end NMI. "
            "ARI equals 1 and variation of information equals 0 for identical partitions.", "",
            "| Seeds | Adjusted Rand index | Variation of information, bits |",
            "|---|---:|---:|",
        ])
        for row in analysis.sensitivity_rows:
            lines.append(f"| {row['seed_a']} / {row['seed_b']} | {row['adjusted_rand_index']:.6f} | "
                         f"{row['variation_information_bits']:.6f} |")
        lines.append("")
    lines.extend([
        "These runs assess the seed sensitivity of one algorithm on one snapshot. They are "
        "not a comparison of different algorithms or a statistical confidence interval.", "",
        "## Selected partition", "",
        f"Community {largest['community_id']} is largest: {largest['node_count']:,} nodes "
        f"({largest['node_fraction']:.1%} of G). The five largest communities together contain "
        f"{sum(row['node_count'] for row in rows[:5]) / n:.1%} of all nodes. Community IDs "
        "are assigned by descending size, then smallest ASN; they are labels, not identities "
        "shared across seeds.", "",
        f"The {focal_total:,} Italian focal ASes appear in "
        f"{sum(row['focal_asn_count'] > 0 for row in rows)} communities. Community "
        f"{focal_leader['community_id']} contains the most: {focal_leader['focal_asn_count']:,} "
        f"({focal_leader['focal_fraction_of_all']:.1%} of F). Italian ASes account for "
        f"{focal_leader['focal_fraction_within_community']:.1%} of that community's nodes.", "",
        f"The {provider_total:,} candidate external providers appear in "
        f"{sum(row['candidate_provider_count'] > 0 for row in rows)} communities. Community "
        f"{provider_leader['community_id']} contains the most candidates: "
        f"{provider_leader['candidate_provider_count']} ({provider_leader['candidate_fraction_of_all']:.1%} of P). "
        "Provider membership here is derived from direct provider-to-Italian-customer records, "
        "independently of Student A's PageRank ranking.", "",
        "Leading communities by Italian-AS count:", "",
        "| Community | All nodes | Italian ASes | Share of F | Candidate providers |",
        "|---|---:|---:|---:|---:|",
    ])
    for row in by_focal[:min(8, len(rows))]:
        lines.append(f"| {row['community_id']} | {row['node_count']:,} | {row['focal_asn_count']:,} | "
                     f"{row['focal_fraction_of_all']:.1%} | {row['candidate_provider_count']} |")
    if len(by_focal) > 1 and by_focal[1]["focal_asn_count"] > 0:
        runner_up = by_focal[1]
        lines.extend([
            "", f"Community {runner_up['community_id']} has the second-largest Italian-AS "
            f"count: {runner_up['focal_asn_count']:,} of its {runner_up['node_count']:,} nodes "
            f"are registered in Italy ({runner_up['focal_fraction_within_community']:.1%}). "
            "This distinguishes the number of Italian ASes from their share within a community.",
        ])
    lines.extend([
        "", "The complete table, including smaller communities and recorded country labels, "
        "is in `community_summary.csv`. `community_provider_membership.csv` identifies each "
        "candidate ASN and its selected community. Registration labels do not locate equipment.", "",
        "## Figures", "",
        "![Community sizes](figures/community_sizes.png)", "",
        "Community sizes use the selected partition. Any Other category is the sum of omitted "
        "communities, as labelled in the figure; the full table retains every community.", "",
        "![Community composition](figures/community_composition.png)", "",
        "Population shares use separate baseline denominators for all nodes, Italian ASes "
        "and candidate providers. The view's selection and any Other aggregation are labelled; "
        "they do not change the analysis graph or score calculations.", "",
        "![Intercommunity connectivity](figures/community_connectivity.png)", "",
        "The heatmap displays aggregated intercommunity edge counts for the labelled "
        "largest-community subset, with its displayed transformation noted on the color bar. "
        "The diagonal is masked because it would represent internal edges. The CSV "
        "`community_edges.csv` includes internal counts and every community pair with an edge. "
        "Its unordered rows count each original edge once; symmetric heatmap cells repeat "
        "the same pair for readability.", "",
        "## Method and checks", "",
        f"Input snapshot: `{baseline.report['snapshot']}`. Package policy: "
        f"`{baseline.report['country_policy']}`. Baseline fingerprint: "
        f"`{baseline.report['baseline_fingerprint']}`.", "",
        f"Louvain: unweighted G, resolution {analysis.resolution:g}, threshold {analysis.threshold:g}, "
        "no maximum level, explicit NetworkX backend. The first configured seed served as "
        "the pilot and is reused in this comparison. Nodes and edges come from the loader's "
        "stable ASN ordering.", "",
        "All four scores use the full G and each complete partition. Modularity was checked "
        "independently with NetworkX. Ncut is a sum. Course NMI is mutual information of "
        "edge-end community labels divided by their entropy; it can capture disassortative "
        "dependence too. InfoMap scores the supplied Louvain partition using recorded uniform "
        f"teleportation, damping {selected.scores.walk.damping:g} and base-2 logarithms. "
        "No Infomap partition optimization ran.", "",
        f"The locally computed stationary walk converged in {selected.scores.walk.iterations} "
        f"iterations, with total L1 residual {selected.scores.walk.l1_residual:.6g} against "
        f"tolerance {selected.scores.walk.residual_tolerance:.6g}. The prepared walk was reused "
        "across all partitions. No importance-result files were read.", "",
        "Every seed assigns every G node exactly once. Summary totals reconcile to G/F/E/P, "
        "and each community satisfies `degree_volume = 2*internal_edges + boundary_edges`. "
        "Quotient counts sum to the original edge count. Source/input/output hashes, library "
        "versions, seed settings, scores and runtimes are in `community_run_manifest.json`.", "",
        "## Limitations and outstanding decisions", "",
    ])
    for warning in baseline.report["warnings"]:
        lines.append(f"- **{warning['code']}**: {warning['message']}")
        if "asns_by_label" in warning:
            labels = ", ".join(f"{len(asns)} {label}-labelled ASes" for label, asns in
                               sorted(warning["asns_by_label"].items()))
            providers = ", ".join(f"AS{asn}" for asn in warning["affected_candidate_providers"]) or "none"
            lines.append(f"- Supplied country assignments were preserved, including {labels}. "
                         f"Affected candidate providers: {providers}. A revised convention may change "
                         "composition/provider counts while leaving topology-only partitions and "
                         "scores unchanged if G stays the same.")
    lines.extend([
        "- CAIDA relationships are inferred and incomplete. Registration country is not "
        "equipment location, and the graph contains no traffic or bandwidth information.",
        "- An undirected connection does not establish a policy-valid Internet route. "
        "This community analysis does not measure outage losses or perform provider removals.",
        "- Three seeds characterize only the configured runs. Louvain is a heuristic, "
        "and similar modularity values do not imply identical node assignments.", "",
        "Course definitions: `Part2_community.pdf`, PDF pp. 17, 48, 66, 72-73 and 133. "
        "See `analysis/resilience/COMMUNITY_METRICS.md` for the implemented conventions and "
        "retained hand calculations.", "",
        "Reproduce from the repository root:", "", "```powershell",
        "python -m analysis.resilience.run_communities", "```", "",
    ])
    return "\n".join(lines)


def _export(baseline: GraphBaseline, analysis: CommunityAnalysis, stage: Path,
            config_path: Path, total_started: float) -> None:
    with (stage / "community_assignments.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(("seed", "asn", "community_id"))
        for run in sorted(analysis.runs, key=lambda item: item.seed):
            labels = {asn: index for index, group in enumerate(run.communities) for asn in group}
            writer.writerows((run.seed, asn, labels[asn]) for asn in sorted(labels))
    _write_csv(stage / "community_quality.csv", _quality_rows(analysis))
    _write_csv(stage / "community_summary.csv", analysis.summary_rows)
    _write_csv(stage / "community_edges.csv", analysis.edge_rows)
    _write_csv(stage / "community_provider_membership.csv", analysis.provider_rows)
    _write_csv(stage / "seed_sensitivity.csv", analysis.sensitivity_rows,
               ("seed_a", "seed_b", "adjusted_rand_index", "variation_information_bits"))
    render_community_figures(analysis.summary_rows, analysis.edge_rows,
                             selected_seed=analysis.selected.seed, output_directory=stage / "figures")
    with (stage / "community_findings.md").open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(_findings(baseline, analysis))
    # Refuse to publish a run whose local inputs changed while it was computing.
    if _hash(config_path) != baseline.report["config_sha256"]:
        raise CommunityAnalysisError("The shared configuration changed during the run")
    directory = Path(baseline.report["package_directory"])
    if _hash(directory / "manifest.json") != baseline.report["manifest_sha256"]:
        raise CommunityAnalysisError("The package manifest changed during the run")
    for name, record in baseline.report["processed_hashes"].items():
        if _hash(directory / name) != record["actual_sha256"]:
            raise CommunityAnalysisError(f"Input {name} changed during the run")
    code_directory = Path(__file__).parent
    sources = ("graph_loader.py", "community_metrics.py", "community_analysis.py",
               "community_plots.py", "run_communities.py")
    manifest = {
        "status": "complete", "scope": "baseline communities only; no provider removals",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "snapshot": baseline.report["snapshot"], "source_commit": baseline.report["source_commit"],
        "implementation_state": "Local implementation identified by source hashes; this command creates no commit.",
        "baseline_fingerprint": baseline.report["baseline_fingerprint"],
        "input_counts": baseline.report["counts"], "input_warnings": baseline.report["warnings"],
        "country_policy": baseline.report["country_policy"],
        "hash_policy": baseline.report["hash_policy"], "processed_input_hashes": baseline.report["processed_hashes"],
        "config_sha256": baseline.report["config_sha256"], "manifest_sha256": baseline.report["manifest_sha256"],
        "source_hashes": {name: _hash(code_directory / name) for name in sources},
        "settings": {"algorithm": "Louvain", "backend": "networkx", "weighted": False,
                     "resolution": analysis.resolution, "threshold": analysis.threshold, "max_level": None,
                     "seeds": [run.seed for run in analysis.runs],
                     "community_id_order": "descending node count, then smallest ASN; zero-based",
                     "selection": "highest modularity; exact tie -> smaller seed",
                     "walk": analysis.selected.scores.walk.to_dict()},
        "selected_seed": analysis.selected.seed,
        "assignment_rows": sum(run.scores.node_count for run in analysis.runs),
        "runs": [{"seed": run.seed, "scores": run.scores.to_dict(),
                  "networkx_modularity": run.networkx_modularity,
                  "louvain_seconds": run.louvain_seconds, "scoring_seconds": run.scoring_seconds}
                 for run in analysis.runs],
        "seed_sensitivity": analysis.sensitivity_rows,
        "scorer_preparation_seconds": analysis.scorer_seconds,
        "runtime_seconds": time.perf_counter() - total_started,
        "environment": {"python": platform.python_version(), "networkx": nx.__version__,
                        "numpy": np.__version__, "scipy": scipy.__version__, "matplotlib": matplotlib.__version__},
        "output_hashes": {path.relative_to(stage).as_posix(): _hash(path)
                          for path in sorted(stage.rglob("*")) if path.is_file()},
    }
    with (stage / "community_run_manifest.json").open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(manifest, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=root / "config" / "project.json")
    parser.add_argument("--hash-policy", choices=("exact", "newline-compatible"), default="exact")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args(argv)
    started = time.perf_counter()
    try:
        config_path = args.config.resolve()
        baseline = load_graph(config_path, hash_policy=args.hash_policy)
        config = json.loads(config_path.read_text(encoding="utf-8"))
        settings, pagerank = config.get("louvain"), config.get("pagerank")
        if not isinstance(settings, dict) or not isinstance(pagerank, dict):
            raise CommunityAnalysisError("Configuration must declare Louvain and PageRank settings")
        if pagerank.get("weighted") is not False or pagerank.get("teleportation") != "uniform":
            raise CommunityAnalysisError("Community scoring requires unweighted G and uniform teleportation")
        output_setting = args.output_dir or config["paths"].get("results_resilience", "results/resilience")
        if not isinstance(output_setting, (str, Path)) or not str(output_setting):
            raise CommunityAnalysisError("Output directory must be a nonempty path")
        project_root = config_path.parent.parent
        output_directory = (project_root / output_setting).resolve()
        protected = [Path(baseline.report["package_directory"])]
        if config["paths"].get("raw_data"):
            protected.append((project_root / config["paths"]["raw_data"]).resolve())
        if any(output_directory == path or path in output_directory.parents for path in protected):
            raise CommunityAnalysisError("Community results must not be written inside shared inputs")
        analysis = run_louvain(
            baseline, seeds=settings.get("seeds"), resolution=settings.get("resolution"),
            threshold=settings.get("threshold", 1e-7), damping=pagerank.get("damping"),
            progress=lambda message: print(message, flush=True))
        output_directory.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".community-staging-", dir=output_directory) as temporary:
            stage = Path(temporary).resolve()
            if stage == output_directory or not stage.is_relative_to(output_directory):
                raise CommunityAnalysisError("Temporary output path is outside the result directory")
            _export(baseline, analysis, stage, config_path, started)
            paths = sorted((path for path in stage.rglob("*") if path.is_file()),
                           key=lambda path: (path.name == "community_run_manifest.json", path.as_posix()))
            # Individual files are replaced only after every computation/export
            # succeeds. The manifest is replaced last to identify the new run.
            for source in paths:
                destination = (output_directory / source.relative_to(stage)).resolve()
                if not destination.is_relative_to(output_directory):
                    raise CommunityAnalysisError("Output path escaped the result directory")
                destination.parent.mkdir(parents=True, exist_ok=True)
                os.replace(source, destination)
    except (ValidationError, CommunityAnalysisError, CommunityScoreError, OSError, ValueError) as exc:
        print(f"Community analysis failed: {exc}", file=sys.stderr)
        return 1
    print(f"Community analysis complete. Findings: {output_directory / 'community_findings.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
