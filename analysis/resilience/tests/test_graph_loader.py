"""Small, independently specified data contracts for Student B's graph loader."""

import csv
import hashlib
import json

import networkx as nx
import pytest

from analysis.resilience.graph_loader import ValidationError, load_graph
from analysis.resilience.validate_graph import main


HEADERS = {
    "nodes.csv": ["asn", "asn_name", "org_id", "org_name", "country", "metadata_missing"],
    "edges.csv": ["source_asn", "target_asn"],
    "relationships.csv": ["source_asn", "target_asn", "relationship", "evidence_sources"],
    "analysis_asns.csv": ["asn"],
    "focal_asns.csv": ["asn"],
    "external_asns.csv": ["asn"],
}


def _write_csv(path, header, rows, *, newline="\n"):
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, lineterminator=newline)
        writer.writerow(header)
        writer.writerows(rows)


def _refresh_hashes(config):
    folder = config.parent.parent / "data/processed/v1"
    manifest_path = folder / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["processed_file_hashes"] = {
        name: hashlib.sha256((folder / name).read_bytes()).hexdigest() for name in HEADERS
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")


def _rewrite(config, filename, mutate):
    path = config.parent.parent / "data/processed/v1" / filename
    with path.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.reader(stream))
    mutate(rows)
    _write_csv(path, rows[0], rows[1:])
    _refresh_hashes(config)


def _package(tmp_path, *, countries=None, edges=None, analysis=None):
    """Create an internally declared package, independent of preparation code."""
    countries = countries or {1: "IT", 2: "IT", 10: "US", 20: "NA", 30: "EU", 40: ""}
    # Every tuple is (provider, customer) unless neither endpoint is Italian.
    directed = edges or [(10, 1), (10, 2), (20, 1), (20, 2), (30, 1), (10, 30), (20, 40)]
    topology = sorted({tuple(sorted(edge)) for edge in directed})
    analysis = set(countries) if analysis is None else set(analysis)
    focal = {asn for asn in analysis if countries[asn] == "IT"}
    external = {asn for asn in analysis if countries[asn] not in ("", "IT")}
    folder = tmp_path / "data/processed/v1"
    folder.mkdir(parents=True)
    config = tmp_path / "config/project.json"
    config.parent.mkdir()
    config.write_text(json.dumps({
        "snapshot": "20261001", "focal_country": "IT",
        "paths": {"processed_data": "data/processed/v1", "results_resilience": "results/resilience"},
    }), encoding="utf-8")
    nodes = [
        [asn, f"AS{asn}" if country else "", f"org{asn}" if country else "",
         f"Organization {asn}" if country else "", country, not bool(country)]
        for asn, country in sorted(countries.items())
    ]
    relationships = []
    for source, target in directed:
        if countries[target] == "IT":
            relationships.append([source, target, "provider_to_customer", "bgp"])
        else:
            relationships.append([min(source, target), max(source, target), "peer", "bgp"])
    rows = {
        "nodes.csv": nodes,
        "edges.csv": topology,
        "relationships.csv": relationships,
        "analysis_asns.csv": [[asn] for asn in sorted(analysis)],
        "focal_asns.csv": [[asn] for asn in sorted(focal)],
        "external_asns.csv": [[asn] for asn in sorted(external)],
    }
    for name, data in rows.items():
        _write_csv(folder / name, HEADERS[name], data)
    (folder / "manifest.json").write_text(json.dumps({
        "schema_version": "1.0", "snapshot": "20261001", "focal_country": "IT",
        "counts": {}, "processed_file_hashes": {},
    }), encoding="utf-8")
    _refresh_hashes(config)
    return config


def test_valid_bundle_preserves_countries_direction_and_frozen_baseline(tmp_path):
    baseline = load_graph(_package(tmp_path))
    assert set(baseline.graph) == {1, 2, 10, 20, 30, 40}
    assert baseline.graph.number_of_edges() == 7
    assert not baseline.graph.is_directed()
    assert nx.is_connected(baseline.graph)
    assert baseline.nodes[20].country == "NA"
    assert baseline.nodes[30].country == "EU"
    assert baseline.focal_asns == frozenset({1, 2})
    assert baseline.external_asns == frozenset({10, 20, 30})
    assert baseline.candidate_providers == frozenset({10, 20, 30})
    assert baseline.provider_customers[10] == frozenset({1, 2})
    assert baseline.provider_customers[30] == frozenset({1})
    assert baseline.targeted_order == (10, 20, 30)
    assert baseline.baseline_degrees[10] == baseline.baseline_degrees[20] == 3
    assert "EU" in json.dumps(baseline.report)
    with pytest.raises(nx.NetworkXError):
        baseline.graph.remove_node(10)
    with pytest.raises(TypeError):
        baseline.provider_customers[10] = frozenset()
    mutable = baseline.graph.copy()
    mutable.remove_node(10)
    assert 10 in baseline.graph


def test_loading_is_deterministic_even_if_csv_rows_are_reordered(tmp_path):
    config = _package(tmp_path)
    first = load_graph(config)
    for name in HEADERS:
        _rewrite(config, name, lambda rows: rows.__setitem__(slice(1, None), rows[:0:-1]))
    second = load_graph(config)
    assert list(second.graph) == list(first.graph)
    assert list(second.graph.edges()) == list(first.graph.edges())
    assert second.targeted_order == first.targeted_order


def test_targeting_prioritizes_customers_then_degree_then_asn(tmp_path):
    config = _package(
        tmp_path,
        countries={1: "IT", 2: "IT", 10: "US", 20: "NA", 30: "EU", 40: "",
                   60: "GB", 70: "", 80: "", 90: ""},
        edges=[(10, 1), (10, 2), (20, 1), (20, 2), (30, 1), (10, 30),
               (20, 40), (20, 60), (30, 70), (30, 80), (30, 90)],
    )
    baseline = load_graph(config)
    assert baseline.baseline_degrees[30] > baseline.baseline_degrees[20]
    assert baseline.baseline_degrees[20] > baseline.baseline_degrees[10]
    assert baseline.targeted_order == (20, 10, 30)


@pytest.mark.parametrize("new_header", [["source_asn"], ["source_asn", "source_asn"]])
def test_invalid_csv_schema_is_rejected(tmp_path, new_header):
    config = _package(tmp_path)
    _rewrite(config, "edges.csv", lambda rows: rows.__setitem__(0, new_header))
    with pytest.raises(ValidationError):
        load_graph(config)


@pytest.mark.parametrize("filename", ["nodes.csv", "analysis_asns.csv", "focal_asns.csv", "external_asns.csv", "edges.csv", "relationships.csv"])
def test_duplicate_records_fail_instead_of_being_silently_merged(tmp_path, filename):
    config = _package(tmp_path)
    _rewrite(config, filename, lambda rows: rows.append(rows[1].copy()))
    with pytest.raises(ValidationError):
        load_graph(config)


@pytest.mark.parametrize("replacement", [[1, 1], [10, 1], [1, 999]])
def test_selfloop_noncanonical_and_missing_endpoint_edges_fail(tmp_path, replacement):
    config = _package(tmp_path)
    _rewrite(config, "edges.csv", lambda rows: rows.__setitem__(1, replacement))
    with pytest.raises(ValidationError):
        load_graph(config)


@pytest.mark.parametrize("replacement", [[1, "invalid"], [1, "1.5"], [1, "-1"]])
def test_bad_asn_values_fail(tmp_path, replacement):
    config = _package(tmp_path)
    _rewrite(config, "edges.csv", lambda rows: rows.__setitem__(1, replacement))
    with pytest.raises(ValidationError):
        load_graph(config)


@pytest.mark.parametrize("filename,values", [
    ("focal_asns.csv", [[1], [10]]),
    ("external_asns.csv", [[1], [10], [20], [30]]),
    ("external_asns.csv", [[10], [20]]),
    ("focal_asns.csv", [[1], [2], [999]]),
])
def test_focal_external_sets_must_exactly_match_metadata_and_graph(tmp_path, filename, values):
    config = _package(tmp_path)
    _rewrite(config, filename, lambda rows: rows.__setitem__(slice(1, None), values))
    with pytest.raises(ValidationError):
        load_graph(config)


def test_peer_link_does_not_establish_provider_status(tmp_path):
    config = _package(tmp_path)
    def make_peer(rows):
        for row in rows[1:]:
            if row[:2] == ["30", "1"]:
                row[:] = ["1", "30", "peer", "bgp"]
    _rewrite(config, "relationships.csv", make_peer)
    baseline = load_graph(config)
    assert baseline.candidate_providers == frozenset({10, 20})
    assert 30 in baseline.external_asns and 30 in baseline.graph


@pytest.mark.parametrize("bad_row", [
    [10, 1, "unknown_1", "bgp"],
    [1, 10, "provider_to_customer", "bgp"],
    [1, 10, "peer", "bgp"],
    [1, 40, "peer", "bgp"],
    [1, 999, "peer", "bgp"],
])
def test_invalid_conflicting_or_unmatched_relationships_fail(tmp_path, bad_row):
    config = _package(tmp_path)
    _rewrite(config, "relationships.csv", lambda rows: rows.append(bad_row))
    with pytest.raises(ValidationError):
        load_graph(config)


def test_relationship_topology_must_cover_every_edge(tmp_path):
    config = _package(tmp_path)
    _rewrite(config, "relationships.csv", lambda rows: rows.pop())
    with pytest.raises(ValidationError):
        load_graph(config)


@pytest.mark.parametrize("tie", [False, True])
def test_connected_but_wrong_analysis_component_is_rejected(tmp_path, tie):
    countries = {1: "IT", 10: "US", 20: "NA", 2: "IT", 30: "EU", 50: "GB"}
    edges = [(10, 1), (20, 1), (30, 2), (50, 2)]
    if not tie:
        countries[40] = ""
        edges.append((10, 40))
    config = _package(tmp_path, countries=countries, edges=edges, analysis={2, 30, 50})
    with pytest.raises(ValidationError):
        load_graph(config)


def test_largest_component_tie_uses_smallest_asn(tmp_path):
    config = _package(
        tmp_path,
        countries={1: "IT", 10: "US", 20: "NA", 2: "IT", 30: "EU", 50: "GB"},
        edges=[(10, 1), (20, 1), (30, 2), (50, 2)], analysis={1, 10, 20},
    )
    baseline = load_graph(config)
    assert baseline.analysis_asns == frozenset({1, 10, 20})
    assert baseline.candidate_providers == frozenset({10, 20})


def test_checksum_tampering_fails_even_with_newline_compatibility(tmp_path):
    config = _package(tmp_path)
    path = config.parent.parent / "data/processed/v1/nodes.csv"
    path.write_bytes(path.read_bytes().replace(b"AS10", b"AS99"))
    for policy in ("exact", "newline-compatible"):
        with pytest.raises(ValidationError):
            load_graph(config, hash_policy=policy)


def test_newline_only_change_needs_explicit_compatibility_policy(tmp_path):
    config = _package(tmp_path)
    path = config.parent.parent / "data/processed/v1/focal_asns.csv"
    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    with pytest.raises(ValidationError):
        load_graph(config)
    baseline = load_graph(config, hash_policy="newline-compatible")
    assert baseline.focal_asns == frozenset({1, 2})
    assert "newline" in json.dumps(baseline.report).lower()


def test_manifest_counts_and_snapshot_are_checked(tmp_path):
    config = _package(tmp_path)
    path = config.parent.parent / "data/processed/v1/manifest.json"
    manifest = json.loads(path.read_text())
    manifest["counts"]["lcc_nodes"] = 999
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValidationError):
        load_graph(config)
    manifest["counts"] = {}
    manifest["snapshot"] = "20260901"
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValidationError):
        load_graph(config)


@pytest.mark.parametrize("field,value", [
    ("focal_country", []), ("snapshot", 20261001), ("raw_data", 123),
])
def test_malformed_configuration_types_raise_validation_error(tmp_path, field, value):
    config = _package(tmp_path)
    settings = json.loads(config.read_text())
    target = settings["paths"] if field == "raw_data" else settings
    target[field] = value
    config.write_text(json.dumps(settings))
    with pytest.raises(ValidationError):
        load_graph(config)


def test_malformed_raw_hash_manifest_raises_validation_error(tmp_path):
    config = _package(tmp_path)
    path = config.parent.parent / "data/processed/v1/manifest.json"
    manifest = json.loads(path.read_text())
    manifest["raw_file_hashes"] = []
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValidationError):
        load_graph(config)


def test_explicit_assigned_country_policy_keeps_eu_as_transit_only(tmp_path):
    config = _package(tmp_path)
    _rewrite(config, "external_asns.csv", lambda rows: rows.__setitem__(
        slice(1, None), [row for row in rows[1:] if row != ["30"]],
    ))
    path = config.parent.parent / "data/processed/v1/manifest.json"
    manifest = json.loads(path.read_text())
    manifest["country_policy"] = "assigned_alpha2"
    manifest["counts"] = {"candidate_providers": 2, "external_asns": 2, "unknown_country_asns": 2}
    path.write_text(json.dumps(manifest))
    baseline = load_graph(config)
    assert baseline.nodes[30].country == "EU"
    assert 30 in baseline.graph
    assert baseline.external_asns == frozenset({10, 20})
    assert baseline.candidate_providers == frozenset({10, 20})
    assert baseline.targeted_order == (10, 20)
    assert baseline.report["country_policy"] == "assigned_alpha2"


def test_unknown_manifest_country_policy_is_rejected(tmp_path):
    config = _package(tmp_path)
    path = config.parent.parent / "data/processed/v1/manifest.json"
    manifest = json.loads(path.read_text())
    manifest["country_policy"] = "guess_from_names"
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValidationError):
        load_graph(config)


def test_missing_declared_counts_are_reported_as_unverified(tmp_path):
    baseline = load_graph(_package(tmp_path))
    warning = next(item for item in baseline.report["warnings"]
                   if item["code"] == "missing_manifest_counts")
    assert "lcc_nodes" in warning["fields"]
    assert baseline.report["counts"]["lcc_nodes"] == 6
    assert baseline.report["ready_for_final_robustness"] is False


def test_cli_success_then_invalid_input_fails_without_overwriting_reports(tmp_path, capsys):
    config = _package(tmp_path)
    folder = config.parent.parent / "data/processed/v1"
    input_bytes = {path.name: path.read_bytes() for path in folder.iterdir()}
    assert main(["--config", str(config)]) == 0
    output = tmp_path / "results/resilience"
    previous = {path.name: path.read_bytes() for path in output.iterdir()}
    assert set(previous) == {"graph_validation.json", "graph_validation.md", "candidate_providers.csv"}
    report = json.loads(previous["graph_validation.json"])
    assert report["counts"]["lcc_nodes"] == 6
    assert report["ready_for_community_analysis"] is True
    assert report["ready_for_final_robustness"] is False
    with (output / "candidate_providers.csv").open(encoding="utf-8", newline="") as stream:
        assert [int(row["asn"]) for row in csv.DictReader(stream)] == [10, 20, 30]
    assert {path.name: path.read_bytes() for path in folder.iterdir()} == input_bytes
    _rewrite(config, "edges.csv", lambda rows: rows.append(rows[1].copy()))
    capsys.readouterr()
    assert main(["--config", str(config)]) == 1
    assert "Validation failed" in capsys.readouterr().err
    assert {path.name: path.read_bytes() for path in output.iterdir()} == previous


@pytest.mark.parametrize("output", [
    "data/processed/v1", "data/processed/v1/reports", "data/raw/reports",
])
def test_cli_rejects_output_inside_shared_inputs(tmp_path, capsys, output):
    config = _package(tmp_path)
    settings = json.loads(config.read_text())
    settings["paths"]["raw_data"] = "data/raw"
    config.write_text(json.dumps(settings))
    package_folder = tmp_path / "data/processed/v1"
    original = {path.name: path.read_bytes() for path in package_folder.iterdir()}
    assert main(["--config", str(config), "--output-dir", output]) == 1
    assert "Reports must not be written inside shared raw or processed inputs" in capsys.readouterr().err
    assert {path.name: path.read_bytes() for path in package_folder.iterdir()} == original
    assert not list((tmp_path / "data").rglob("graph_validation.*"))


def test_saved_report_uses_relative_path_without_mutating_runtime_path(tmp_path):
    from analysis.resilience.validate_graph import write_reports

    config = _package(tmp_path)
    baseline = load_graph(config)
    absolute_package = baseline.report["package_directory"]
    output = tmp_path / "results/resilience"
    write_reports(baseline, output, project_root=tmp_path)
    report = json.loads((output / "graph_validation.json").read_text())
    assert report["package_directory"] == "data/processed/v1"
    assert report["package_directory_scope"] == "repository-relative"
    assert baseline.report["package_directory"] == absolute_package
    assert str(tmp_path) not in (output / "graph_validation.json").read_text()


def test_saved_report_hides_external_package_parent_directories(tmp_path):
    from analysis.resilience.validate_graph import write_reports

    config = _package(tmp_path)
    baseline = load_graph(config)
    output = tmp_path / "reports"
    write_reports(baseline, output, project_root=tmp_path / "different-project")
    report = json.loads((output / "graph_validation.json").read_text())
    assert report["package_directory"] == "v1"
    assert report["package_directory_scope"] == "directory name only; outside the project root"
    assert str(tmp_path) not in (output / "graph_validation.json").read_text()
