"""Single independent entry point for Student B's complete numerical analysis."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from .graph_loader import ValidationError, load_graph
from . import run_communities, run_robustness
from .validate_graph import write_reports as write_graph_reports


def _hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _verified_manifest(path, output, baseline, *, check_source=True):
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("status") != "complete":
        raise ValueError(f"{path.name}: incomplete or invalid run manifest")
    if not isinstance(data.get("output_hashes"), dict) or not data["output_hashes"]:
        raise ValueError(f"{path.name}: missing output hashes")
    if not isinstance(data.get("source_hashes"), dict) or not data["source_hashes"]:
        raise ValueError(f"{path.name}: missing source hashes")
    required = ({"community_assignments.csv", "community_quality.csv", "community_summary.csv",
                 "community_edges.csv", "community_provider_membership.csv", "seed_sensitivity.csv",
                 "community_findings.md"} if path.name == "community_run_manifest.json" else
                {"robustness_runs.csv", "removal_order.csv", "robustness_summary.csv", "robustness_findings.md"})
    if not required <= data["output_hashes"].keys():
        raise ValueError(f"{path.name}: missing required output entries")
    if data.get("baseline_fingerprint") != baseline.report["baseline_fingerprint"]:
        raise ValueError(f"{path.name}: input baseline differs; rerun this analysis")
    if data.get("config_sha256") != baseline.report["config_sha256"]:
        raise ValueError(f"{path.name}: configuration differs; rerun this analysis")
    for name, wanted in data.get("output_hashes", {}).items():
        file = (output / name).resolve()
        if not file.is_relative_to(output) or not file.is_file() or _hash(file) != wanted:
            raise ValueError(f"{path.name}: missing or changed output {name}")
    if check_source:
        for name, wanted in data.get("source_hashes", {}).items():
            if Path(name).name != name:
                raise ValueError(f"{path.name}: invalid source filename")
            if _hash(Path(__file__).parent / name) != wanted:
                raise ValueError(f"{path.name}: source {name} changed; rerun this analysis")
    return data


def write_combined_record(output, baseline, communities, robustness):
    quality = next(run["scores"] for run in communities["runs"] if run["seed"] == communities["selected_seed"])
    report = [
        "# Student B: communities and robustness", "",
        "The complete numerical workflow has been run and validated on the recorded v1 scenario. "
        "The country-label sensitivity is kept separate; shared sign-off is not inferred.", "",
        f"The baseline contains {len(baseline.graph):,} ASes, {baseline.graph.number_of_edges():,} "
        f"undirected edges, {len(baseline.focal_asns):,} Italian ASes and "
        f"{len(baseline.candidate_providers)} candidate external providers.", "",
        f"Louvain seed {communities['selected_seed']} was selected by highest modularity: "
        f"{quality['community_count']} communities, Q={quality['modularity']:.6f}. "
        "Every partition was evaluated with the four course measures.", "",
        f"The failure analysis contains {robustness['primary']['run_rows']} primary metric rows: "
        f"one targeted sequence and {robustness['primary']['settings']['random_trials']} random sequences, "
        f"each including baseline and {robustness['primary']['settings']['max_removed_providers']} "
        "cumulative removal steps. The actual budget and all seeds are recorded in the manifest.", "",
        "## Read the results", "",
        "- [Community findings and figures](community_findings.md).",
        "- [Provider-removal findings and sensitivity](robustness_findings.md).",
        "- [Graph validation](graph_validation.md), where available.",
        "- [Metric verification](metric_checks/verification.md), where available.", "",
        "## Reproduce", "",
        "From the repository root, after installing `analysis/resilience/requirements.txt`:", "",
        "```powershell", "python -m analysis.resilience.run_resilience --hash-policy newline-compatible", "```", "",
        "To retain existing community outputs, use `--reuse-communities`; their baseline, "
        "configuration, source and output hashes must match. This option never bypasses "
        "validation of changed or incomplete community results.", "",
        "`input_contract.json` records the exact input bytes, LF-normalized hashes, baseline "
        "sets and provider order. Newline compatibility accepts only demonstrated LF/CRLF "
        "equivalence. Neither runner modifies the shared package or Student A's code/results.", "",
        "## Interpretation and limits", "",
        "The main run preserves the supplied classification. The alternate run excludes "
        "non-assigned country labels from E/P and keeps those nodes as transit nodes. "
        "Both support inspection before the students agree on a shared convention.", "",
        "The provider-removal order is derived from direct Italian-customer counts, baseline "
        "degree and ASN. It is independent of saved importance rankings.", "",
        "Results describe inferred topological connectivity, not traffic, bandwidth or people "
        "losing Internet access. One snapshot and one focal country are analysed.", "",
    ]
    with (output / "findings.md").open("w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(report))
    contract = {
        "status": "recorded technical baseline; shared human sign-off remains open",
        "baseline_fingerprint": baseline.report["baseline_fingerprint"],
        "snapshot": baseline.report["snapshot"], "country_policy": baseline.report["country_policy"],
        "processed_files": baseline.report["processed_hashes"],
        "counts": baseline.report["counts"], "focal_asns": sorted(baseline.focal_asns),
        "external_set_sha256": hashlib.sha256("\n".join(map(str, sorted(baseline.external_asns))).encode()).hexdigest(),
        "candidate_provider_asns": sorted(baseline.candidate_providers),
        "targeted_order": list(baseline.targeted_order),
        "sensitivity": robustness["sensitivity"],
    }
    with (output / "input_contract.json").open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(contract, handle, indent=2, sort_keys=True)
        handle.write("\n")
    outputs = {**communities["output_hashes"], **robustness["output_hashes"]}
    for name in ("community_run_manifest.json", "robustness_run_manifest.json", "findings.md", "input_contract.json"):
        outputs[name] = _hash(output / name)
    for name in ("graph_validation.json", "graph_validation.md", "candidate_providers.csv"):
        if (output / name).is_file():
            outputs[name] = _hash(output / name)
    manifest = {
        "status": "complete_for_recorded_v1_scenario", "requires_shared_signoff": True,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "baseline_fingerprint": baseline.report["baseline_fingerprint"],
        "source_commit": baseline.report["source_commit"], "config_sha256": baseline.report["config_sha256"],
        "input_counts": baseline.report["counts"], "input_warnings": baseline.report["warnings"],
        "selected_louvain_seed": communities["selected_seed"], "community_assignment_rows": communities["assignment_rows"],
        "primary_robustness_rows": robustness["primary"]["run_rows"],
        "sensitivity_robustness_rows": None if robustness["sensitivity"] is None else robustness["sensitivity"]["run_rows"],
        "source_hashes": {"run_resilience.py": _hash(Path(__file__))},
        "subrun_manifests": {name: _hash(output / name) for name in
                             ("community_run_manifest.json", "robustness_run_manifest.json")},
        "output_hashes": outputs,
    }
    with (output / "run_manifest.json").open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(manifest, handle, indent=2, sort_keys=True)
        handle.write("\n")


def main(argv=None):
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=root / "config/project.json")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--hash-policy", choices=("exact", "newline-compatible"), default="exact")
    parser.add_argument("--reuse-communities", action="store_true")
    parser.add_argument("--reuse-robustness", action="store_true")
    args = parser.parse_args(argv)
    try:
        config_path = args.config.resolve()
        baseline = load_graph(config_path, hash_policy=args.hash_policy)
        config = json.loads(config_path.read_text(encoding="utf-8"))
        output = (config_path.parent.parent / (args.output_dir or config["paths"]["results_resilience"])).resolve()
        protected = [Path(baseline.report["package_directory"])]
        if config["paths"].get("raw_data"):
            protected.append((config_path.parent.parent / config["paths"]["raw_data"]).resolve())
        if any(output == path or output.is_relative_to(path) for path in protected):
            raise ValueError("Student B results cannot be written into shared inputs")
        forwarded = ["--config", str(config_path), "--hash-policy", args.hash_policy, "--output-dir", str(output)]
        if args.reuse_communities:
            communities = _verified_manifest(output / "community_run_manifest.json", output, baseline)
            print("Reusing verified community outputs.", flush=True)
        else:
            if run_communities.main(forwarded) != 0:
                return 1
            communities = _verified_manifest(output / "community_run_manifest.json", output, baseline)
        if not args.reuse_robustness:
            if run_robustness.main(forwarded) != 0:
                return 1
        robustness = _verified_manifest(output / "robustness_run_manifest.json", output, baseline)
        if args.reuse_robustness:
            print("Reusing verified robustness outputs.", flush=True)
        write_graph_reports(baseline, output, project_root=config_path.parent.parent)
        write_combined_record(output, baseline, communities, robustness)
    except (ValidationError, OSError, ValueError, TypeError) as exc:
        print(f"Student B workflow failed: {exc}", file=sys.stderr)
        return 1
    print(f"Student B numerical workflow complete: {output / 'findings.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
