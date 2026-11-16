"""Shared, conservative cache compatibility checks for Student B's saved runs.

Configuration identity is parsed JSON, with only Student A's PageRank stopping
controls removed. Array order and unknown keys remain significant. Raw run
hashes remain provenance; this policy never replaces them with current hashes.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path


POLICY = "student-b-effective-config-v1"
RECONCILIATION_FILE = "compatibility_reconciliation.json"
MANIFEST_NAMES = ("community_run_manifest.json", "robustness_run_manifest.json", "run_manifest.json")
RECONCILABLE_SOURCES = frozenset(("run_communities.py", "run_robustness.py", "run_resilience.py"))
REQUIRED_OUTPUTS = {
    MANIFEST_NAMES[0]: {"community_assignments.csv", "community_quality.csv", "community_summary.csv",
                        "community_edges.csv", "community_provider_membership.csv", "seed_sensitivity.csv",
                        "community_findings.md"},
    MANIFEST_NAMES[1]: {"robustness_runs.csv", "removal_order.csv", "robustness_summary.csv", "robustness_findings.md"},
    MANIFEST_NAMES[2]: {MANIFEST_NAMES[0], MANIFEST_NAMES[1], "findings.md", "input_contract.json"},
}
REQUIRED_SOURCES = {
    MANIFEST_NAMES[0]: {"graph_loader.py", "community_metrics.py", "community_analysis.py",
                        "community_plots.py", "run_communities.py"},
    MANIFEST_NAMES[1]: {"graph_loader.py", "robustness.py", "robustness_plots.py", "run_robustness.py"},
    MANIFEST_NAMES[2]: {"run_resilience.py"},
}


def hash_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _json_bytes(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode("utf-8")


def canonical_configuration(config: dict) -> bytes:
    """Canonical UTF-8 JSON; ignore tol/max_iter, apply the actual threshold default.

    Integer spellings of floating-point controls have the same representation.
    Integer-only budgets/seeds retain their types. All other keys (including
    future settings and input declarations) are deliberately checked as supplied.
    """
    if not isinstance(config, dict):
        raise ValueError("Configuration must be a JSON object")
    effective = copy.deepcopy(config)
    pagerank = effective.get("pagerank")
    if isinstance(pagerank, dict):
        for name in ("tol", "max_iter"):
            pagerank.pop(name, None)
        if type(pagerank.get("damping")) in (int, float):
            pagerank["damping"] = float(pagerank["damping"])
    louvain = effective.get("louvain")
    if isinstance(louvain, dict):
        louvain.setdefault("threshold", 1e-7)
        for name in ("resolution", "threshold"):
            if type(louvain.get(name)) in (int, float):
                louvain[name] = float(louvain[name])
    return _json_bytes(effective)


def configuration_record(config: dict) -> dict:
    canonical = canonical_configuration(config)
    return {"policy": POLICY, "effective_config": json.loads(canonical),
            "effective_sha256": hashlib.sha256(canonical).hexdigest()}


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _configuration_matches(record, config):
    _require(isinstance(record, dict) and record.get("policy") == POLICY,
             "Missing or unsupported configuration compatibility policy")
    recorded = record.get("effective_config")
    _require(isinstance(recorded, dict), "Missing effective configuration snapshot")
    canonical = canonical_configuration(recorded)
    _require(_json_bytes(recorded) == canonical, "Noncanonical effective configuration snapshot")
    _require(hashlib.sha256(canonical).hexdigest() == record.get("effective_sha256"),
             "Effective configuration snapshot hash mismatch")
    _require(canonical == canonical_configuration(config), "Effective Student B configuration differs; rerun this analysis")


def _reconciliation(output, supplied):
    if supplied is None:
        path = output / RECONCILIATION_FILE
        supplied = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    _require(isinstance(supplied, dict), "Invalid compatibility reconciliation record")
    if supplied:
        _require(supplied.get("schema_version") == 1
                 and supplied.get("operation") == "compatibility_metadata_reconciliation"
                 and supplied.get("experiments_recomputed") is False,
                 "Invalid compatibility reconciliation record")
        _require(isinstance(supplied.get("manifests"), dict), "Missing reconciliation manifest bindings")
        _require(supplied.get("validation_source_hashes") == {
            "result_compatibility.py": hash_file(Path(__file__))},
            "Compatibility validation source changed since reconciliation")
    return supplied


def verify_manifest(path, output, baseline, config, *, reconciliation=None, check_source=True):
    """Check configuration, baseline, sources and outputs without changing files."""
    path, output = Path(path).resolve(), Path(output).resolve()
    _require(path.parent == output and path.name in MANIFEST_NAMES, "Invalid result manifest path")
    data = json.loads(path.read_text(encoding="utf-8"))
    expected_status = "complete_for_recorded_v1_scenario" if path.name == MANIFEST_NAMES[2] else "complete"
    _require(isinstance(data, dict) and data.get("status") == expected_status,
             f"{path.name}: incomplete or invalid run manifest")
    _require(isinstance(data.get("output_hashes"), dict) and REQUIRED_OUTPUTS[path.name] <= data["output_hashes"].keys(),
             f"{path.name}: missing required output hashes")
    _require(isinstance(data.get("source_hashes"), dict) and REQUIRED_SOURCES[path.name] <= data["source_hashes"].keys(),
             f"{path.name}: missing required source hashes")
    _require(data.get("baseline_fingerprint") == baseline.report["baseline_fingerprint"],
             f"{path.name}: input baseline differs; rerun this analysis")
    record = data.get("configuration_compatibility")
    # A reconciliation describes one immutable legacy run. A later genuine run
    # carries its own canonical snapshot and source hashes, so the historical
    # sidecar must neither authorize nor obstruct it.
    reconciliation = _reconciliation(output, reconciliation) if record is None else {}
    entry = reconciliation.get("manifests", {}).get(path.name, {})
    _require(isinstance(entry, dict), f"{path.name}: invalid reconciliation manifest entry")
    if entry:
        _require(isinstance(entry, dict) and entry.get("manifest_sha256") == hash_file(path),
                 f"{path.name}: reconciliation is bound to a different historical manifest")
    if record is not None:
        _configuration_matches(record, config)
        _require("result_compatibility.py" in data["source_hashes"], f"{path.name}: missing compatibility source hash")
    elif data.get("config_sha256") != baseline.report["config_sha256"]:
        _require(bool(entry), f"{path.name}: legacy configuration differs; explicit compatibility reconciliation required")
        original = reconciliation.get("original_configuration", {})
        _require(isinstance(original, dict) and isinstance(original.get("utf8"), str)
                 and isinstance(original.get("recovery"), str) and bool(original["recovery"].strip()),
                 "Missing original configuration recovery evidence")
        original_hash = hashlib.sha256(original["utf8"].encode("utf-8")).hexdigest()
        _require(original_hash == original.get("sha256") == data.get("config_sha256"),
                 f"{path.name}: original configuration bytes do not match historical hash")
        original_config = json.loads(original["utf8"])
        _configuration_matches(reconciliation.get("configuration_compatibility"), original_config)
        _configuration_matches(reconciliation["configuration_compatibility"], config)
    for name, wanted in data["output_hashes"].items():
        file = (output / name).resolve()
        _require(file.is_relative_to(output) and file.is_file() and hash_file(file) == wanted,
                 f"{path.name}: missing or changed output {name}")
    if path.name == MANIFEST_NAMES[2]:
        expected_subruns = {name: hash_file(output / name) for name in MANIFEST_NAMES[:2]}
        _require(data.get("subrun_manifests") == expected_subruns, "Combined subrun manifest hashes differ")
    if check_source:
        reviews = entry.get("source_reconciliations", {})
        _require(isinstance(reviews, dict), "Invalid source reconciliation mapping")
        for name, wanted in data["source_hashes"].items():
            _require(Path(name).name == name and "\\" not in name and "/" not in name,
                     f"{path.name}: invalid source filename")
            current = hash_file(Path(__file__).parent / name)
            if current != wanted:
                review = reviews.get(name, {})
                _require(name in RECONCILABLE_SOURCES and isinstance(review, dict)
                         and review.get("original_sha256") == wanted and review.get("compatible_sha256") == current
                         and isinstance(review.get("reason"), str) and bool(review["reason"].strip()),
                         f"{path.name}: source {name} changed; rerun or explicitly reconcile a reviewed metadata-only change")
    return data


def verify_saved_results(output, baseline, config, *, reconciliation=None):
    """Shared preflight for reuse and the independent numerical verifier."""
    return {name: verify_manifest(Path(output) / name, output, baseline, config,
                                  reconciliation=reconciliation) for name in MANIFEST_NAMES}
