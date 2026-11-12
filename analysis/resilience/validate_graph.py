"""Validate Student B's inputs and save a report; no analysis or removals run."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

if __package__:
    from .graph_loader import GraphBaseline, ValidationError, load_graph
else:
    from graph_loader import GraphBaseline, ValidationError, load_graph


def write_reports(baseline: GraphBaseline, output_directory: Path, *, project_root: Path | None = None) -> None:
    output_directory.mkdir(parents=True, exist_ok=True)
    # Keep the absolute path in memory for input/output safety checks, while
    # publishing only a portable path in the saved report.
    report = dict(baseline.report)
    package = Path(report["package_directory"]).resolve()
    report_root = (project_root or Path(__file__).resolve().parents[2]).resolve()
    if package.is_relative_to(report_root):
        report["package_directory"] = package.relative_to(report_root).as_posix()
        report["package_directory_scope"] = "repository-relative"
    else:
        report["package_directory"] = package.name
        report["package_directory_scope"] = "directory name only; outside the project root"
    with (output_directory / "graph_validation.json").open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(report, handle, indent=2, ensure_ascii=False, sort_keys=True)
        handle.write("\n")
    lines = [
        "# Shared graph loading and validation", "",
        f"Status: **{report['status']}**. The supplied baseline passed structural checks.", "",
        "The graph is available for community development. Final robustness runs still require "
        "agreement on shared input conventions; this loader does not certify that agreement.", "",
        f"Snapshot: `{report['snapshot']}`. Focal country: `{report['focal_country']}`.",
        f"Source commit: `{report['source_commit'] or 'unavailable'}`.",
        f"Baseline fingerprint: `{report['baseline_fingerprint']}`.", "",
        "| Quantity | Count |", "|---|---:|",
    ]
    lines.extend(f"| {key} | {value:,} |" for key, value in report["counts"].items())
    lines.extend(["", "## Items to resolve", ""])
    if report["warnings"]:
        for warning in report["warnings"]:
            lines.append(f"- **{warning['code']}**: {warning['message']}")
            if "asns_by_label" in warning:
                for label, asns in warning["asns_by_label"].items():
                    lines.append(f"  - `{label}`: {len(asns)} nodes; ASNs {', '.join(map(str, asns))}.")
                affected = warning["affected_candidate_providers"]
                lines.append(f"  - Affected candidate providers: {', '.join(map(str, affected)) or 'none'}.")
    else:
        lines.append("No package-quality warnings were found. Confirm shared input conventions before final runs.")
    lines.extend(["", "## Checks and reproducibility", ""])
    lines.extend(f"- {check}." for check in report["checks"])
    lines.extend([
        "", f"{report['scope']}", "",
        "Country values are loaded as text: Namibia's `NA` is preserved. Unknown-country nodes "
        "remain in the topology. Supplied focal/external assignments are preserved, including "
        "any flagged regional labels.", "",
        "The JSON report records byte and LF-normalized hashes, the hash policy, configuration, "
        "manifest and loader hashes, runtime and environment. The fingerprint identifies the "
        "six input CSVs after LF normalization, snapshot and classification policy; it does not "
        "claim that two differently ordered CSVs have the same fingerprint.", "",
        f"Environment: Python {report['environment']['python']}, "
        f"NetworkX {report['environment']['networkx']}. Runtime: {report['runtime_seconds']} seconds.", "",
        "`candidate_providers.csv` records the derived candidate pool and baseline ordering. "
        "It is an input summary, not a removal experiment.", "",
    ])
    with (output_directory / "graph_validation.md").open("w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines))
    with (output_directory / "candidate_providers.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["targeted_position", "asn", "org_name", "country",
                                "focal_customer_count", "focal_customer_fraction", "baseline_degree"],
                                lineterminator="\n")
        writer.writeheader()
        for position, asn in enumerate(baseline.targeted_order, start=1):
            node = baseline.nodes[asn]
            count = len(baseline.provider_customers[asn])
            writer.writerow({"targeted_position": position, "asn": asn, "org_name": node.org_name,
                             "country": node.country, "focal_customer_count": count,
                             "focal_customer_fraction": count / len(baseline.focal_asns),
                             "baseline_degree": baseline.baseline_degrees[asn]})


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=root / "config" / "project.json")
    parser.add_argument("--hash-policy", choices=("exact", "newline-compatible"), default="exact",
                        help="Exact byte hashes by default; newline compatibility must be requested explicitly.")
    parser.add_argument("--output-dir", type=Path,
                        help="Report directory; relative paths resolve against the repository root.")
    args = parser.parse_args(argv)
    try:
        baseline = load_graph(args.config, hash_policy=args.hash_policy)
        config_path = args.config.resolve()
        project_root = config_path.parent.parent
        config = json.loads(config_path.read_text(encoding="utf-8"))
        output_setting = args.output_dir or config["paths"].get("results_resilience", "results/resilience")
        if not isinstance(output_setting, (str, Path)) or not str(output_setting):
            raise ValidationError("Output directory must be a nonempty path")
        output_directory = (project_root / output_setting).resolve()
        protected = [Path(baseline.report["package_directory"])]
        if config["paths"].get("raw_data"):
            protected.append((project_root / config["paths"]["raw_data"]).resolve())
        if any(output_directory == path or path in output_directory.parents for path in protected):
            raise ValidationError("Reports must not be written inside shared raw or processed inputs")
        write_reports(baseline, output_directory, project_root=project_root)
    except (ValidationError, OSError) as exc:
        print(f"Validation failed: {exc}", file=sys.stderr)
        return 1
    counts = baseline.report["counts"]
    print(f"Validation {baseline.report['status']}: {counts['lcc_nodes']:,} nodes, "
          f"{baseline.graph.number_of_edges():,} edges, "
          f"{len(baseline.focal_asns):,} focal ASes, {len(baseline.external_asns):,} external ASes, "
          f"{len(baseline.candidate_providers):,} candidate providers.")
    for warning in baseline.report["warnings"]:
        print(f"Warning [{warning['code']}]: {warning['message']}")
    print(f"Reports saved in {output_directory}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
