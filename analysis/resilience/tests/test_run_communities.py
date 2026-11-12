"""End-to-end output checks on tiny packages; plotting alone is replaced for I/O tests."""

import csv
import hashlib
import json
from pathlib import Path

import networkx as nx
import pytest

from analysis.resilience import run_communities
from analysis.resilience.graph_loader import load_graph
from analysis.resilience.tests.test_community_metrics import _dense_reference
from analysis.resilience.tests.test_graph_loader import _package


def _read_csv(path):
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def _snapshot(directory):
    return {path.relative_to(directory).as_posix(): path.read_bytes()
            for path in directory.rglob("*") if path.is_file()}


@pytest.fixture
def config(tmp_path):
    path = _package(tmp_path)
    contents = json.loads(path.read_text(encoding="utf-8"))
    contents["louvain"] = {"resolution": 1.0, "seeds": [11, 22, 33]}
    contents["pagerank"] = {"damping": 0.85, "teleportation": "uniform", "weighted": False}
    path.write_text(json.dumps(contents), encoding="utf-8")
    return path


@pytest.fixture
def fast_plot_exports(monkeypatch):
    def placeholders(summary_rows, edge_rows, *, selected_seed, output_directory):
        """Exercise artifact I/O only; these are deliberately not rendered figures."""
        output_directory.mkdir(parents=True)
        paths = []
        for name in ("community_sizes.png", "community_composition.png", "community_connectivity.png"):
            path = output_directory / name
            path.write_bytes(b"test-only plotting placeholder")
            paths.append(path)
        return paths

    monkeypatch.setattr(run_communities, "render_community_figures", placeholders)
    return placeholders


def test_cli_exports_all_assignments_scores_and_verifiable_manifest(config, tmp_path, fast_plot_exports):
    project_root = config.parent.parent
    package = project_root / "data/processed/v1"
    before = _snapshot(package)
    config_before = config.read_bytes()
    output = tmp_path / "custom-output"
    assert run_communities.main(["--config", str(config), "--output-dir", str(output)]) == 0
    assert _snapshot(package) == before
    assert config.read_bytes() == config_before
    assert not list(output.glob(".community-staging-*"))
    manifest = json.loads((output / "community_run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "complete"
    assert manifest["scope"] == "baseline communities only; no provider removals"
    assert manifest["assignment_rows"] == 18
    assert manifest["input_counts"]["lcc_nodes"] == 6
    assert manifest["input_counts"]["global_topology_edges"] == 7
    assert manifest["input_counts"]["candidate_providers"] == 3
    assert manifest["settings"]["weighted"] is False
    assert manifest["settings"]["seeds"] == [11, 22, 33]
    assert manifest["settings"]["backend"] == "networkx"
    assert len(manifest["runs"]) == 3
    assert len(manifest["seed_sensitivity"]) == 3
    for relative, expected in manifest["output_hashes"].items():
        assert hashlib.sha256((output / relative).read_bytes()).hexdigest() == expected
    source_directory = run_communities.__file__
    for name, expected in manifest["source_hashes"].items():
        assert hashlib.sha256((Path(source_directory).parent / name).read_bytes()).hexdigest() == expected

    baseline = load_graph(config)
    assignments = _read_csv(output / "community_assignments.csv")
    assert len(assignments) == 18
    quality = _read_csv(output / "community_quality.csv")
    assert len(quality) == 3
    assert sum(row["selected"] == "True" for row in quality) == 1
    assert int(next(row["seed"] for row in quality if row["selected"] == "True")) == manifest["selected_seed"]
    for row in quality:
        seed = int(row["seed"])
        chosen = [entry for entry in assignments if int(entry["seed"]) == seed]
        assert len(chosen) == len(baseline.graph)
        assert {int(entry["asn"]) for entry in chosen} == set(baseline.graph)
        communities = {}
        for entry in chosen:
            communities.setdefault(int(entry["community_id"]), set()).add(int(entry["asn"]))
        groups = list(communities.values())
        assert len(groups) == int(row["community_count"])
        expected = _dense_reference(baseline.graph, groups)
        assert float(row["modularity"]) == pytest.approx(nx.community.modularity(baseline.graph, groups, weight=None))
        for metric in ("modularity", "normalized_cut", "course_nmi", "infomap_bits"):
            if expected[metric] is None:
                assert row[metric] == ""
            else:
                assert float(row[metric]) == pytest.approx(expected[metric], abs=1e-10)
    summary = _read_csv(output / "community_summary.csv")
    assert sum(int(row["node_count"]) for row in summary) == len(baseline.graph)
    assert sum(int(row["focal_asn_count"]) for row in summary) == len(baseline.focal_asns)
    assert sum(int(row["candidate_provider_count"]) for row in summary) == len(baseline.candidate_providers)
    assert (output / "community_findings.md").is_file()
    assert len(_read_csv(output / "community_provider_membership.csv")) == len(baseline.candidate_providers)


def test_cli_uses_configured_default_output_directory(config, fast_plot_exports):
    assert run_communities.main(["--config", str(config)]) == 0
    output = config.parent.parent / "results/resilience"
    assert (output / "community_run_manifest.json").is_file()
    assert not list(output.glob(".community-staging-*"))


@pytest.mark.parametrize("mutation", [
    lambda contents: contents["louvain"].update(resolution=2),
    lambda contents: contents["louvain"].update(seeds=[11, 11]),
])
def test_invalid_run_settings_preserve_existing_results(config, fast_plot_exports, mutation):
    assert run_communities.main(["--config", str(config)]) == 0
    output = config.parent.parent / "results/resilience"
    before = _snapshot(output)
    contents = json.loads(config.read_text(encoding="utf-8"))
    mutation(contents)
    config.write_text(json.dumps(contents), encoding="utf-8")
    assert run_communities.main(["--config", str(config)]) == 1
    assert _snapshot(output) == before
    assert not list(output.glob(".community-staging-*"))


@pytest.mark.parametrize("input_directory", ["data/processed/v1", "data/raw"])
def test_shared_input_directories_cannot_receive_outputs(config, fast_plot_exports, input_directory):
    contents = json.loads(config.read_text(encoding="utf-8"))
    contents["paths"]["raw_data"] = "data/raw"
    config.write_text(json.dumps(contents), encoding="utf-8")
    protected = config.parent.parent / input_directory
    protected.mkdir(parents=True, exist_ok=True)
    before = _snapshot(protected)
    assert run_communities.main(["--config", str(config), "--output-dir", str(protected)]) == 1
    assert _snapshot(protected) == before
    assert not list(protected.glob(".community-staging-*"))


def test_failed_export_removes_stage_and_preserves_previous_results(config, monkeypatch):
    output = config.parent.parent / "results/resilience"
    output.mkdir(parents=True)
    sentinel = output / "community_quality.csv"
    sentinel.write_text("previous complete result", encoding="utf-8")
    before = _snapshot(output)
    stages = []

    def failed_plot(summary_rows, edge_rows, *, selected_seed, output_directory):
        stages.append(output_directory.parent)
        output_directory.mkdir()
        (output_directory / "partial.png").write_bytes(b"partial export")
        raise OSError("intentional export failure")

    monkeypatch.setattr(run_communities, "render_community_figures", failed_plot)
    assert run_communities.main(["--config", str(config)]) == 1
    assert _snapshot(output) == before
    assert stages and all(not stage.exists() for stage in stages)
    assert not list(output.glob(".community-staging-*"))


def test_input_change_during_export_is_not_published(config, monkeypatch, fast_plot_exports):
    output = config.parent.parent / "results/resilience"
    output.mkdir(parents=True)
    (output / "community_quality.csv").write_text("prior result", encoding="utf-8")
    before = _snapshot(output)

    def concurrent_change(summary_rows, edge_rows, *, selected_seed, output_directory):
        result = fast_plot_exports(summary_rows, edge_rows, selected_seed=selected_seed,
                                   output_directory=output_directory)
        edge_path = config.parent.parent / "data/processed/v1/edges.csv"
        edge_path.write_bytes(edge_path.read_bytes() + b"\n")
        return result

    monkeypatch.setattr(run_communities, "render_community_figures", concurrent_change)
    assert run_communities.main(["--config", str(config)]) == 1
    assert _snapshot(output) == before
    assert not list(output.glob(".community-staging-*"))
