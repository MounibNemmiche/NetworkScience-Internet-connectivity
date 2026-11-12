"""The combined entry point rejects stale caches and never requires A's results."""

import hashlib
import json

import pytest

from analysis.resilience import run_resilience as workflow
from analysis.resilience.tests.test_graph_loader import _package


@pytest.fixture
def config(tmp_path, monkeypatch):
    path = _package(tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    data.update({"louvain": {"resolution": 1.0, "seeds": [11, 22, 33]},
                 "pagerank": {"damping": 0.85, "teleportation": "uniform", "weighted": False},
                 "robustness": {"max_removed_providers": 2, "random_trials": 2,
                                "random_seeds": [1001, 1002],
                                "targeted_ordering": "focal_customer_count_desc, degree_desc, asn_asc"}})
    path.write_text(json.dumps(data), encoding="utf-8")
    def community_plots(summary, edges, *, output_directory, **kwargs):
        output_directory.mkdir(parents=True)
        (output_directory / "community_sizes.png").write_bytes(b"I/O placeholder")
    def robustness_plots(summary, *, output_directory, **kwargs):
        output_directory.mkdir(parents=True)
        (output_directory / "robustness_loss_overview.png").write_bytes(b"I/O placeholder")
    monkeypatch.setattr(workflow.run_communities, "render_community_figures", community_plots)
    monkeypatch.setattr(workflow.run_robustness, "render_robustness_figures", robustness_plots)
    return path


def test_full_entrypoint_has_combined_manifest_and_input_contract(config):
    assert workflow.main(["--config", str(config)]) == 0
    out = config.parent.parent / "results/resilience"
    manifest = json.loads((out / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["requires_shared_signoff"] is True
    assert manifest["primary_robustness_rows"] == manifest["sensitivity_robustness_rows"] == 9
    assert manifest["community_assignment_rows"] == 18
    for name, expected in manifest["output_hashes"].items():
        assert hashlib.sha256((out / name).read_bytes()).hexdigest() == expected
    assert (out / "graph_validation.md").is_file()
    contract = json.loads((out / "input_contract.json").read_text(encoding="utf-8"))
    assert contract["candidate_provider_asns"] == [10, 20, 30]
    assert not (config.parent.parent / "results/importance").exists()


def test_verified_reuse_runs_neither_algorithm_again(config, monkeypatch):
    assert workflow.main(["--config", str(config)]) == 0
    def forbidden(*args, **kwargs):
        raise AssertionError("Validated cached analysis must not run again")
    monkeypatch.setattr(workflow.run_communities, "main", forbidden)
    monkeypatch.setattr(workflow.run_robustness, "main", forbidden)
    assert workflow.main(["--config", str(config), "--reuse-communities", "--reuse-robustness"]) == 0


def test_changed_output_rejected_before_reuse(config):
    assert workflow.main(["--config", str(config)]) == 0
    out = config.parent.parent / "results/resilience"
    previous = (out / "run_manifest.json").read_bytes()
    with (out / "community_assignments.csv").open("ab") as handle:
        handle.write(b"\n")
    assert workflow.main(["--config", str(config), "--reuse-communities", "--reuse-robustness"]) == 1
    assert (out / "run_manifest.json").read_bytes() == previous


def test_incomplete_manifest_has_clear_failure(config):
    out = config.parent.parent / "results/resilience"
    out.mkdir(parents=True)
    (out / "community_run_manifest.json").write_text('{"status":"complete"}', encoding="utf-8")
    assert workflow.main(["--config", str(config), "--reuse-communities"]) == 1


def test_shared_data_rejected_as_combined_output(config):
    assert workflow.main(["--config", str(config), "--output-dir", "data/processed/v1"]) == 1
