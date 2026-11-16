"""Cache compatibility accepts unrelated A settings and rejects changed B inputs."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from analysis.resilience import result_compatibility as compatibility
from analysis.resilience import run_resilience as workflow
from analysis.resilience import verify_results as verifier
from analysis.resilience.graph_loader import load_graph
from analysis.resilience.tests.test_graph_loader import HEADERS, _rewrite
from analysis.resilience.tests.test_run_resilience import config


MANIFESTS = ("community_run_manifest.json", "robustness_run_manifest.json", "run_manifest.json")


def _read_config(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _saved(config):
    assert workflow.main(["--config", str(config)]) == 0
    output = config.parent.parent / "results/resilience"
    return output, {name: (output / name).read_bytes() for name in MANIFESTS}


def _no_experiments(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Compatible saved results must not rerun either experiment")
    monkeypatch.setattr(workflow.run_communities, "main", forbidden)
    monkeypatch.setattr(workflow.run_robustness, "main", forbidden)


def _reject_both(output, baseline, changed):
    for name in MANIFESTS[:2]:
        with pytest.raises(ValueError):
            workflow._verified_manifest(output / name, output, baseline, changed)
    with pytest.raises(ValueError):
        verifier.verify_saved_results(output, baseline, changed)


@pytest.mark.parametrize("ending", ["\n", "\r\n"])
@pytest.mark.parametrize("stopping", [
    {"tol": 1e-6, "max_iter": 500},
    {"tol": 1e-12, "max_iter": 5000},
    {},
])
def test_unrelated_stopping_settings_and_json_formatting_reuse_results(config, monkeypatch, ending, stopping):
    original = _read_config(config)
    original["pagerank"].update({"tol": 1e-5, "max_iter": 100})
    config.write_text(json.dumps(original), encoding="utf-8")
    output, before = _saved(config)
    original["pagerank"].pop("tol")
    original["pagerank"].pop("max_iter")
    original["pagerank"].update(stopping)
    changed = dict(reversed(list(original.items())))
    config.write_bytes((json.dumps(changed, sort_keys=True, indent=4) + "\n").replace("\n", ending).encode("utf-8"))
    baseline = load_graph(config)
    assert baseline.report["config_sha256"] != json.loads(before[MANIFESTS[0]])["config_sha256"]
    for name in MANIFESTS[:2]:
        assert workflow._verified_manifest(output / name, output, baseline, changed)["status"] == "complete"
    assert verifier.verify_saved_results(output, baseline, changed)
    _no_experiments(monkeypatch)
    assert workflow.main(["--config", str(config), "--reuse-communities", "--reuse-robustness"]) == 0
    assert all((output / name).read_bytes() == before[name] for name in MANIFESTS)


def test_canonical_configuration_copies_input_and_normalizes_runtime_defaults(config):
    original = _read_config(config)
    before = deepcopy(original)
    defaulted = deepcopy(original)
    defaulted["louvain"]["threshold"] = 1e-7
    defaulted["louvain"]["resolution"] = 1
    defaulted["pagerank"].update({"tol": 1e-6, "max_iter": 500})
    assert compatibility.canonical_configuration(original) == compatibility.canonical_configuration(defaulted)
    record = compatibility.configuration_record(original)
    assert hashlib.sha256(compatibility.canonical_configuration(original)).hexdigest() == record["effective_sha256"]
    assert original == before


CONFIG_MUTATIONS = [
    ("pagerank", "damping", 0.9),
    ("pagerank", "teleportation", "personalized"),
    ("pagerank", "weighted", True),
    ("louvain", "resolution", 1.2),
    ("louvain", "seeds", [11, 22, 44]),
    ("louvain", "threshold", 1e-6),
    ("robustness", "max_removed_providers", 1),
    ("robustness", "random_trials", 1),
    ("robustness", "random_seeds", [1001, 1003]),
    ("robustness", "targeted_ordering", "degree_desc, asn_asc"),
    ("paths", "processed_data", "data/processed/v2"),
    ("paths", "raw_data", "data/other_raw"),
    (None, "snapshot", "20261002"),
    (None, "focal_country", "FR"),
    (None, "data_sources", {"relationships": {"sha256": "0" * 64}}),
    (None, "future_analysis_setting", True),
]


@pytest.mark.parametrize("section,key,value", CONFIG_MUTATIONS,
                         ids=[f"{section or 'project'}.{key}" for section, key, _ in CONFIG_MUTATIONS])
def test_meaningful_configuration_changes_rejected_consistently(config, monkeypatch, section, key, value):
    output, before = _saved(config)
    baseline = load_graph(config)
    changed = _read_config(config)
    (changed if section is None else changed[section])[key] = value
    _reject_both(output, baseline, changed)
    config.write_text(json.dumps(changed), encoding="utf-8")
    _no_experiments(monkeypatch)
    assert workflow.main(["--config", str(config), "--reuse-communities", "--reuse-robustness"]) == 1
    assert all((output / name).read_bytes() == before[name] for name in MANIFESTS)


def _change_metadata(config):
    _rewrite(config, "nodes.csv", lambda rows: rows[1].__setitem__(3, "Changed organization"))


def _change_relationship(config):
    # Reverse an existing provider/customer relationship without changing G.
    _rewrite(config, "relationships.csv", lambda rows: rows[1].__setitem__(slice(0, 2), rows[1][1::-1]))


def _change_topology(config):
    # AS30 remains connected through AS10; the two other providers remain.
    _rewrite(config, "edges.csv", lambda rows: rows.__setitem__(slice(1, None), [row for row in rows[1:] if row != ["1", "30"]]))
    _rewrite(config, "relationships.csv", lambda rows: rows.__setitem__(slice(1, None), [row for row in rows[1:] if row[:2] != ["30", "1"]]))


def _change_focal_membership(config):
    _rewrite(config, "nodes.csv", lambda rows: next(row for row in rows[1:] if row[0] == "1").__setitem__(4, "US"))
    _rewrite(config, "focal_asns.csv", lambda rows: rows.remove(["1"]))
    _rewrite(config, "external_asns.csv", lambda rows: rows.append(["1"]))


def _change_external_membership(config):
    _rewrite(config, "nodes.csv", lambda rows: next(row for row in rows[1:] if row[0] == "40").__setitem__(4, "GB"))
    _rewrite(config, "external_asns.csv", lambda rows: rows.append(["40"]))


def _change_analysis_nodes(config):
    # Delete the leaf AS40, including its metadata and observed edge/relationship.
    _rewrite(config, "nodes.csv", lambda rows: rows.__setitem__(slice(1, None), [row for row in rows[1:] if row[0] != "40"]))
    _rewrite(config, "analysis_asns.csv", lambda rows: rows.remove(["40"]))
    for name in ("edges.csv", "relationships.csv"):
        _rewrite(config, name, lambda rows: rows.__setitem__(slice(1, None), [row for row in rows[1:] if "40" not in row[:2]]))


@pytest.mark.parametrize("mutate", [
    _change_metadata, _change_relationship, _change_topology,
    _change_focal_membership, _change_external_membership, _change_analysis_nodes,
], ids=["node_metadata", "provider_relationships", "topology", "focal_membership", "external_membership", "analysis_nodes"])
def test_changed_valid_inputs_rejected_even_after_package_hashes_updated(config, monkeypatch, mutate):
    output, before = _saved(config)
    old = load_graph(config)
    mutate(config)
    baseline = load_graph(config)
    assert baseline.report["baseline_fingerprint"] != old.report["baseline_fingerprint"]
    _reject_both(output, baseline, _read_config(config))
    _no_experiments(monkeypatch)
    assert workflow.main(["--config", str(config), "--reuse-communities", "--reuse-robustness"]) == 1
    assert all((output / name).read_bytes() == before[name] for name in MANIFESTS)


def test_processed_input_line_endings_keep_same_baseline(config, monkeypatch):
    output, before = _saved(config)
    old = load_graph(config)
    folder = config.parent.parent / "data/processed/v1"
    for name in HEADERS:
        path = folder / name
        path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    # Preserve the declared package hashes: newline-compatible must prove equivalence.
    baseline = load_graph(config, hash_policy="newline-compatible")
    assert baseline.report["baseline_fingerprint"] == old.report["baseline_fingerprint"]
    assert verifier.verify_saved_results(output, baseline, _read_config(config))
    _no_experiments(monkeypatch)
    assert workflow.main(["--config", str(config), "--hash-policy", "newline-compatible",
                          "--reuse-communities", "--reuse-robustness"]) == 0
    assert all((output / name).read_bytes() == before[name] for name in MANIFESTS)


@pytest.mark.parametrize("source", ["graph_loader.py", "community_analysis.py", "result_compatibility.py"])
def test_source_hash_protection_remains_active(config, monkeypatch, source):
    output, _ = _saved(config)
    baseline = load_graph(config)
    current_hash = compatibility.hash_file
    source_directory = Path(workflow.__file__).parent
    def changed_hash(path):
        path = Path(path)
        return "0" * 64 if path == source_directory / source else current_hash(path)
    monkeypatch.setattr(compatibility, "hash_file", changed_hash)
    with pytest.raises(ValueError):
        workflow._verified_manifest(output / MANIFESTS[0], output, baseline, _read_config(config))
    with pytest.raises(ValueError):
        verifier.verify_saved_results(output, baseline, _read_config(config))


@pytest.mark.parametrize("filename", ["community_assignments.csv", "robustness_runs.csv"])
def test_output_hash_protection_remains_active(config, filename):
    output, _ = _saved(config)
    path = output / filename
    path.write_bytes(path.read_bytes() + b"\n")
    manifest = MANIFESTS[0] if filename.startswith("community") else MANIFESTS[1]
    baseline = load_graph(config)
    with pytest.raises(ValueError):
        workflow._verified_manifest(output / manifest, output, baseline, _read_config(config))
    with pytest.raises(ValueError):
        verifier.verify_saved_results(output, baseline, _read_config(config))


def test_canonical_record_tampering_rejected(config):
    output, _ = _saved(config)
    path = output / MANIFESTS[0]
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["configuration_compatibility"]["effective_config"]["pagerank"]["damping"] = 0.9
    path.write_text(json.dumps(manifest), encoding="utf-8")
    baseline = load_graph(config)
    with pytest.raises(ValueError):
        workflow._verified_manifest(path, output, baseline, _read_config(config))


def test_new_manifest_keeps_raw_config_hash_and_canonical_record(config):
    output, _ = _saved(config)
    expected_raw = hashlib.sha256(config.read_bytes()).hexdigest()
    expected = compatibility.configuration_record(_read_config(config))
    for name in MANIFESTS:
        manifest = json.loads((output / name).read_text(encoding="utf-8"))
        assert manifest["config_sha256"] == expected_raw
        assert manifest["configuration_compatibility"] == expected
        assert "result_compatibility.py" in manifest["source_hashes"]


def test_verifier_main_rejects_changed_damping_before_reading_numerical_outputs(config, monkeypatch, tmp_path):
    output, before = _saved(config)
    changed = _read_config(config)
    changed["pagerank"]["damping"] = 0.9
    config.write_text(json.dumps(changed), encoding="utf-8")
    def forbidden(*args, **kwargs):
        raise AssertionError("The verifier must reject incompatible settings during shared preflight")
    monkeypatch.setattr(verifier, "_csv", forbidden)
    report_directory = tmp_path / "separate-audit"
    with pytest.raises(ValueError, match="configuration differs"):
        verifier.main(["--config", str(config), "--report-dir", str(report_directory)])
    assert not report_directory.exists()
    assert all((output / name).read_bytes() == before[name] for name in MANIFESTS)


def test_verifier_main_accepts_stopping_changes_and_saves_a_separate_audit(config, tmp_path):
    output, before = _saved(config)
    changed = _read_config(config)
    changed["pagerank"].update({"tol": 1e-10, "max_iter": 1000})
    config.write_text(json.dumps(changed, indent=2), encoding="utf-8")
    # A minimal JUnit fixture exercises separate report input without replacing
    # a historical test artifact. It is not evidence for the project's test run.
    junit_fixture = tmp_path / "junit-fixture.xml"
    junit_fixture.write_text('<testsuites><testsuite tests="1" failures="0" errors="0"/></testsuites>', encoding="utf-8")
    report_directory = tmp_path / "separate-audit"
    assert verifier.main(["--config", str(config), "--report-dir", str(report_directory),
                          "--junitxml", str(junit_fixture)]) == 0
    report = json.loads((report_directory / "final_validation.json").read_text(encoding="utf-8"))
    assert report["status"] == "passed"
    assert report["configuration_compatibility"]["experiments_recomputed"] is False
    assert all((output / name).read_bytes() == before[name] for name in MANIFESTS)
