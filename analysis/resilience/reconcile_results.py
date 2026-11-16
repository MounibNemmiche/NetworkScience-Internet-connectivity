"""Record a reviewed cache compatibility migration without recomputing experiments.

Original manifests and outputs remain untouched. The sidecar binds the recovered
configuration and each reviewed orchestration-source change to their exact bytes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from .graph_loader import load_graph
from .result_compatibility import configuration_record, hash_file, verify_saved_results


SIDECAR_NAME = "compatibility_reconciliation.json"
MANIFEST_NAMES = (
    "community_run_manifest.json", "robustness_run_manifest.json", "run_manifest.json",
)
REVIEWABLE_SOURCES = frozenset({
    "run_communities.py", "run_robustness.py", "run_resilience.py",
})


def _object(path: Path) -> dict:
    value = json.loads(path.read_bytes().decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return value


def reconcile_results(config_path, original_config_path, source_review_path, recovery):
    """Validate a legacy run and create its reconciliation sidecar exclusively.

    ``source_review_path`` contains a mapping from changed wrapper filenames to
    ``original_sha256``, ``compatible_sha256`` and a nonempty review ``reason``.
    Numerical sources cannot be exempted. Existing sidecars are never replaced.
    """
    config_path = Path(config_path).resolve()
    config = _object(config_path)
    if not isinstance(recovery, str) or not recovery.strip():
        raise ValueError("A nonempty description of original configuration recovery is required")
    paths = config.get("paths")
    if not isinstance(paths, dict):
        raise ValueError("Configuration paths must be an object")
    output_setting = paths.get("results_resilience")
    if not isinstance(output_setting, str) or not output_setting:
        raise ValueError("Configuration paths.results_resilience must be a nonempty string")
    root = config_path.parent.parent
    output = (root / output_setting).resolve()
    baseline = load_graph(config_path, hash_policy="newline-compatible")
    protected = [Path(baseline.report["package_directory"]).resolve()]
    if paths.get("raw_data"):
        protected.append((root / paths["raw_data"]).resolve())
    if any(output == path or output.is_relative_to(path) for path in protected):
        raise ValueError("Compatibility metadata cannot be written into shared inputs")
    destination = output / SIDECAR_NAME
    if destination.exists():
        raise FileExistsError(f"Refusing to replace existing reconciliation history: {destination}")

    original_bytes = Path(original_config_path).read_bytes()
    original_text = original_bytes.decode("utf-8")
    original = json.loads(original_text)
    if not isinstance(original, dict):
        raise ValueError("Original configuration must be a JSON object")
    original_sha = hashlib.sha256(original_bytes).hexdigest()
    original_record = configuration_record(original)
    if original_record != configuration_record(config):
        raise ValueError("Original and current effective Student B configurations differ")

    reviews = _object(Path(source_review_path))
    if not set(reviews) <= REVIEWABLE_SOURCES:
        raise ValueError("Source reviews may cover only the three orchestration wrappers")
    used_reviews = set()
    manifests = {}
    source_directory = Path(__file__).parent
    for name in MANIFEST_NAMES:
        manifest_path = output / name
        manifest = _object(manifest_path)
        if manifest.get("config_sha256") != original_sha:
            raise ValueError(f"{name}: recovered configuration bytes do not match the historical hash")
        sources = manifest.get("source_hashes")
        if not isinstance(sources, dict) or not sources:
            raise ValueError(f"{name}: missing source hashes")
        exceptions = {}
        for source_name, old_sha in sources.items():
            if not isinstance(source_name, str) or Path(source_name).name != source_name:
                raise ValueError(f"{name}: invalid source filename")
            current_sha = hash_file(source_directory / source_name)
            if current_sha == old_sha:
                continue
            if source_name not in REVIEWABLE_SOURCES:
                raise ValueError(f"{name}: numerical source {source_name} changed; reconciliation is forbidden")
            review = reviews.get(source_name)
            if not isinstance(review, dict):
                raise ValueError(f"{name}: missing explicit source review for {source_name}")
            for key, expected in (("original_sha256", old_sha), ("compatible_sha256", current_sha)):
                if (not isinstance(review.get(key), str)
                        or re.fullmatch(r"[0-9a-f]{64}", review[key]) is None
                        or review[key] != expected):
                    raise ValueError(f"{name}: reviewed {key} does not match {source_name}")
            reason = review.get("reason")
            if not isinstance(reason, str) or not reason.strip():
                raise ValueError(f"{name}: source review for {source_name} requires a reason")
            exceptions[source_name] = {
                "original_sha256": old_sha, "compatible_sha256": current_sha, "reason": reason,
            }
            used_reviews.add(source_name)
        manifests[name] = {
            "manifest_sha256": hash_file(manifest_path), "source_reconciliations": exceptions,
        }
    if set(reviews) != used_reviews:
        raise ValueError("Source review includes unchanged or unrecorded sources")
    record = {
        "schema_version": 1,
        "operation": "compatibility_metadata_reconciliation",
        "experiments_recomputed": False,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "original_configuration": {
            "utf8": original_text, "sha256": original_sha, "recovery": recovery,
        },
        "configuration_compatibility": original_record,
        "validation_source_hashes": {
            "result_compatibility.py": hash_file(source_directory / "result_compatibility.py"),
        },
        "migration_source_hashes": {"reconcile_results.py": hash_file(Path(__file__))},
        "manifests": manifests,
    }
    # Apply precisely the same input, configuration, source and output guards as
    # reuse and saved-result verification, before writing any metadata.
    verify_saved_results(output, baseline, config, reconciliation=record)
    serialized = json.dumps(record, indent=2, sort_keys=True, allow_nan=False) + "\n"
    with destination.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(serialized)
    return record


def main(argv=None):
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=root / "config/project.json")
    parser.add_argument("--original-config", type=Path, required=True)
    parser.add_argument("--source-review", type=Path, required=True)
    parser.add_argument("--recovery", required=True)
    args = parser.parse_args(argv)
    try:
        record = reconcile_results(args.config, args.original_config, args.source_review, args.recovery)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(f"Compatibility reconciliation failed: {exc}", file=sys.stderr)
        return 1
    print(f"Compatibility reconciliation recorded for {len(record['manifests'])} original manifests; "
          "experiments recomputed: false.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
