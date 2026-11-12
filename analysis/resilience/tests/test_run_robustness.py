"""Artifact integrity and failure handling for the full robustness entry point."""

import csv
import hashlib
import json

import pytest

from analysis.resilience import run_robustness as runner
from analysis.resilience.tests.test_graph_loader import _package


@pytest.fixture
def config(tmp_path, monkeypatch):
    path = _package(tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["robustness"] = {"max_removed_providers": 2, "random_trials": 3,
                          "random_seeds": [1001, 1002, 1003],
                          "targeted_ordering": "focal_customer_count_desc, degree_desc, asn_asc"}
    path.write_text(json.dumps(data), encoding="utf-8")
    def placeholders(summary, *, output_directory, **kwargs):
        output_directory.mkdir(parents=True)
        (output_directory / "robustness_loss_overview.png").write_bytes(b"explicit I/O test placeholder")
    monkeypatch.setattr(runner, "render_robustness_figures", placeholders)
    return path


def test_cli_writes_all_trials_sensitivity_and_correct_hashes(config):
    folder = config.parent.parent
    before = {path.name: path.read_bytes() for path in (folder / "data/processed/v1").iterdir()}
    assert runner.main(["--config", str(config)]) == 0
    out = folder / "results/resilience"
    manifest = json.loads((out / "robustness_run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["primary"]["run_rows"] == 12
    assert manifest["primary"]["order_rows"] == 12
    assert manifest["sensitivity"]["settings"]["candidate_count"] == 2
    assert manifest["sensitivity"]["excluded_candidate_asns"] == [30]
    for name, expected in manifest["output_hashes"].items():
        assert hashlib.sha256((out / name).read_bytes()).hexdigest() == expected
    for filename in ("robustness_runs.csv", "removal_order.csv", "robustness_summary.csv"):
        assert (out / filename).is_file()
        assert (out / "sensitivity/eu_excluded" / filename).is_file()
    assert before == {path.name: path.read_bytes() for path in (folder / "data/processed/v1").iterdir()}
    assert not list(out.glob(".robustness-staging-*"))


def test_repeated_cli_reproduces_raw_results(config):
    assert runner.main(["--config", str(config)]) == 0
    out = config.parent.parent / "results/resilience"
    first = {name: (out / name).read_bytes() for name in ("robustness_runs.csv", "removal_order.csv", "robustness_summary.csv")}
    assert runner.main(["--config", str(config)]) == 0
    assert first == {name: (out / name).read_bytes() for name in first}


def test_sensitivity_compares_common_budget_when_candidate_counts_differ(config):
    data = json.loads(config.read_text(encoding="utf-8"))
    data["robustness"]["max_removed_providers"] = 20
    config.write_text(json.dumps(data), encoding="utf-8")
    assert runner.main(["--config", str(config)]) == 0
    out = config.parent.parent / "results/resilience"
    report = (out / "robustness_findings.md").read_text(encoding="utf-8")
    assert "common removal count k=2" in report


@pytest.mark.parametrize("mutation", [lambda data: data["robustness"].update(random_trials=2),
                                      lambda data: data["robustness"].update(random_seeds=[1, 1, 2]),
                                      lambda data: data["robustness"].update(targeted_ordering="pagerank")])
def test_invalid_settings_do_not_publish(config, mutation):
    data = json.loads(config.read_text(encoding="utf-8"))
    mutation(data)
    config.write_text(json.dumps(data), encoding="utf-8")
    assert runner.main(["--config", str(config)]) == 1
    assert not (config.parent.parent / "results/resilience/robustness_run_manifest.json").exists()


def test_shared_inputs_cannot_be_output_directory(config):
    assert runner.main(["--config", str(config), "--output-dir", "data/processed/v1"]) == 1


def test_modified_input_before_publish_is_rejected(config, monkeypatch):
    package = config.parent.parent / "data/processed/v1"
    original = runner.render_robustness_figures
    def change_file(*args, **kwargs):
        original(*args, **kwargs)
        with (package / "nodes.csv").open("ab") as handle:
            handle.write(b"\n")
    monkeypatch.setattr(runner, "render_robustness_figures", change_file)
    assert runner.main(["--config", str(config)]) == 1
    out = config.parent.parent / "results/resilience"
    assert not (out / "robustness_run_manifest.json").exists()
    assert not list(out.glob(".robustness-staging-*"))
