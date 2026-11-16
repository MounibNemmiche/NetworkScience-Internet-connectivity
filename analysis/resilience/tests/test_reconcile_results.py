"""Legacy reconciliation preserves original bytes and requires explicit evidence."""

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from analysis.resilience import reconcile_results as migration
from analysis.resilience import result_compatibility as compatibility
from analysis.resilience import run_resilience as workflow
from analysis.resilience.graph_loader import load_graph
from analysis.resilience.tests.test_run_resilience import config


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path, value):
    path.write_bytes((json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8"))


def _snapshot(folder):
    return {path.relative_to(folder): path.read_bytes() for path in folder.rglob("*") if path.is_file()}


def _legacy(config, tmp_path, *, original_ending="\n", changed_sources=None):
    if changed_sources is None:
        changed_sources = {
            "run_communities.py", "run_robustness.py", "run_resilience.py",
        }
    original_config = _read(config)
    config.write_bytes((json.dumps(original_config, indent=2) + "\n")
                       .replace("\n", original_ending).encode("utf-8"))
    original_bytes = config.read_bytes()
    original = tmp_path / "recovered-original.json"
    original.write_bytes(original_bytes)
    assert workflow.main(["--config", str(config)]) == 0
    output = config.parent.parent / "results/resilience"
    reviews = {}
    for name in compatibility.MANIFEST_NAMES:
        path = output / name
        manifest = _read(path)
        manifest.pop("configuration_compatibility")
        manifest["source_hashes"].pop("result_compatibility.py")
        for source in changed_sources & manifest["source_hashes"].keys():
            historical_sha = hashlib.sha256(("historical fixture: " + source).encode()).hexdigest()
            current_sha = manifest["source_hashes"][source]
            manifest["source_hashes"][source] = historical_sha
            reviews[source] = {
                "original_sha256": historical_sha,
                "compatible_sha256": current_sha,
                "reason": "Fixture review: wrapper metadata changed; numerical calls and outputs retained.",
            }
        if name == compatibility.MANIFEST_NAMES[-1]:
            for subrun in compatibility.MANIFEST_NAMES[:2]:
                digest = compatibility.hash_file(output / subrun)
                manifest["subrun_manifests"][subrun] = digest
                manifest["output_hashes"][subrun] = digest
        _write(path, manifest)
    review = tmp_path / "source-review.json"
    _write(review, reviews)
    current = _read(config)
    current["pagerank"].update({"tol": 1e-6, "max_iter": 500})
    # Change JSON formatting and line endings as well as unrelated A controls.
    config.write_bytes((json.dumps(current, indent=4, sort_keys=True) + "\n")
                       .replace("\n", "\r\n").encode("utf-8"))
    return SimpleNamespace(
        config=config, output=output, original=original, review=review,
        original_bytes=original_bytes, recovery="Recovered exact historical fixture configuration bytes.",
        before=_snapshot(output),
    )


@pytest.fixture
def legacy(config, tmp_path):
    return _legacy(config, tmp_path)


def _migrate(saved):
    return migration.reconcile_results(saved.config, saved.original, saved.review, saved.recovery)


def _verify(saved):
    return compatibility.verify_saved_results(saved.output, load_graph(saved.config), _read(saved.config))


@pytest.mark.parametrize("original_ending", ["\n", "\r\n"], ids=["original-LF", "original-CRLF"])
def test_explicit_legacy_reconciliation_keeps_every_existing_result_byte(config, tmp_path, original_ending):
    saved = _legacy(config, tmp_path, original_ending=original_ending)
    with pytest.raises(ValueError, match="explicit compatibility reconciliation"):
        _verify(saved)
    record = _migrate(saved)
    after = _snapshot(saved.output)
    assert set(after) == set(saved.before) | {Path(migration.SIDECAR_NAME)}
    assert all(after[name] == value for name, value in saved.before.items())
    assert record["experiments_recomputed"] is False
    assert record["operation"] == "compatibility_metadata_reconciliation"
    original = record["original_configuration"]
    assert original["utf8"].encode("utf-8") == saved.original_bytes
    assert original["sha256"] == hashlib.sha256(saved.original_bytes).hexdigest()
    assert original["recovery"] == saved.recovery
    assert record["validation_source_hashes"] == {
        "result_compatibility.py": compatibility.hash_file(Path(compatibility.__file__)),
    }
    assert record["migration_source_hashes"] == {
        "reconcile_results.py": compatibility.hash_file(Path(migration.__file__)),
    }
    for name in compatibility.MANIFEST_NAMES:
        manifest = _read(saved.output / name)
        binding = record["manifests"][name]
        assert binding["manifest_sha256"] == hashlib.sha256(saved.before[Path(name)]).hexdigest()
        assert manifest["config_sha256"] == original["sha256"]
        assert "configuration_compatibility" not in manifest
        for source, review in binding["source_reconciliations"].items():
            assert review == _read(saved.review)[source]
    assert set(_verify(saved)) == set(compatibility.MANIFEST_NAMES)


def test_existing_reconciliation_cannot_be_overwritten(legacy):
    _migrate(legacy)
    before = _snapshot(legacy.output)
    with pytest.raises(FileExistsError, match="Refusing to replace"):
        _migrate(legacy)
    assert _snapshot(legacy.output) == before


@pytest.mark.parametrize("problem", ["missing", "same-settings-different-bytes", "changed-settings"])
def test_original_configuration_requires_exact_historical_evidence(legacy, problem):
    if problem == "missing":
        absent = legacy.original.parent / "does-not-exist.json"
        legacy.original = absent
    elif problem == "same-settings-different-bytes":
        legacy.original.write_bytes(json.dumps(_read(legacy.original)).encode("utf-8"))
    else:
        original = _read(legacy.original)
        original["pagerank"]["damping"] = 0.9
        _write(legacy.original, original)
    with pytest.raises((OSError, ValueError)):
        _migrate(legacy)
    assert _snapshot(legacy.output) == legacy.before


def test_unknown_meaningful_configuration_changes_cannot_be_migrated(legacy):
    current = _read(legacy.config)
    current["future_analysis_setting"] = True
    _write(legacy.config, current)
    with pytest.raises(ValueError, match="effective Student B configurations differ"):
        _migrate(legacy)
    assert _snapshot(legacy.output) == legacy.before


@pytest.mark.parametrize("reviewed", [False, True])
def test_numerical_source_changes_cannot_be_reconciled(config, tmp_path, reviewed):
    saved = _legacy(config, tmp_path, changed_sources={"community_analysis.py"})
    if not reviewed:
        _write(saved.review, {})
    with pytest.raises(ValueError, match="orchestration wrappers|numerical source"):
        _migrate(saved)
    assert _snapshot(saved.output) == saved.before


@pytest.mark.parametrize("problem", ["missing", "wrong-old-hash", "wrong-current-hash", "empty-reason"])
def test_wrapper_review_must_match_both_sources_and_explain_the_change(legacy, problem):
    reviews = _read(legacy.review)
    source = "run_communities.py"
    if problem == "missing":
        reviews.pop(source)
    elif problem == "wrong-old-hash":
        reviews[source]["original_sha256"] = "0" * 64
    elif problem == "wrong-current-hash":
        reviews[source]["compatible_sha256"] = "0" * 64
    else:
        reviews[source]["reason"] = "  "
    _write(legacy.review, reviews)
    with pytest.raises(ValueError, match="source review|reviewed"):
        _migrate(legacy)
    assert _snapshot(legacy.output) == legacy.before


def test_unneeded_source_review_is_rejected(config, tmp_path):
    saved = _legacy(config, tmp_path, changed_sources={"run_resilience.py"})
    reviews = _read(saved.review)
    current = compatibility.hash_file(Path(workflow.__file__).parent / "run_communities.py")
    reviews["run_communities.py"] = {
        "original_sha256": current, "compatible_sha256": current, "reason": "Unchanged wrapper.",
    }
    _write(saved.review, reviews)
    with pytest.raises(ValueError, match="unchanged or unrecorded"):
        _migrate(saved)
    assert _snapshot(saved.output) == saved.before


def test_reconciliation_is_bound_to_exact_original_manifest_bytes(legacy):
    _migrate(legacy)
    path = legacy.output / compatibility.MANIFEST_NAMES[0]
    manifest = _read(path)
    manifest["created_at_utc"] = "altered historical timestamp"
    _write(path, manifest)
    with pytest.raises(ValueError, match="different historical manifest"):
        _verify(legacy)


def test_later_unreviewed_source_change_is_rejected(legacy, monkeypatch):
    _migrate(legacy)
    real_hash = compatibility.hash_file
    target = Path(workflow.__file__).parent / "run_communities.py"
    def changed_hash(path):
        return "0" * 64 if Path(path) == target else real_hash(path)
    monkeypatch.setattr(compatibility, "hash_file", changed_hash)
    with pytest.raises(ValueError, match="source run_communities.py changed"):
        _verify(legacy)


def test_later_compatibility_helper_change_invalidates_legacy_reconciliation(legacy, monkeypatch):
    _migrate(legacy)
    real_hash = compatibility.hash_file
    helper = Path(compatibility.__file__)
    def changed_hash(path):
        return "0" * 64 if Path(path) == helper else real_hash(path)
    monkeypatch.setattr(compatibility, "hash_file", changed_hash)
    with pytest.raises(ValueError):
        _verify(legacy)


@pytest.mark.parametrize("invalid_record", [None, [], False, 0, ""])
def test_malformed_legacy_reconciliation_fails_with_a_validation_error(legacy, invalid_record):
    _migrate(legacy)
    _write(legacy.output / migration.SIDECAR_NAME, invalid_record)
    with pytest.raises(ValueError):
        _verify(legacy)


@pytest.mark.parametrize("field,value", [
    ("original_sha256", "0" * 64), ("compatible_sha256", "0" * 64), ("reason", " "),
])
def test_tampered_source_review_cannot_authorize_reuse(legacy, field, value):
    _migrate(legacy)
    path = legacy.output / migration.SIDECAR_NAME
    record = _read(path)
    record["manifests"][compatibility.MANIFEST_NAMES[0]]["source_reconciliations"]["run_communities.py"][field] = value
    _write(path, record)
    with pytest.raises(ValueError, match="source run_communities.py changed"):
        _verify(legacy)


def test_reconciled_reuse_runs_no_experiments_and_preserves_all_run_history(legacy, monkeypatch):
    _migrate(legacy)
    before = _snapshot(legacy.output)
    def forbidden(*args, **kwargs):
        raise AssertionError("Reconciled reuse must preserve original runs and saved report files")
    monkeypatch.setattr(workflow.run_communities, "main", forbidden)
    monkeypatch.setattr(workflow.run_robustness, "main", forbidden)
    monkeypatch.setattr(workflow, "write_graph_reports", forbidden)
    monkeypatch.setattr(workflow, "write_combined_record", forbidden)
    assert workflow.main(["--config", str(legacy.config), "--reuse-communities", "--reuse-robustness"]) == 0
    assert _snapshot(legacy.output) == before


def test_reconciliation_cli_reports_missing_recovery_evidence_without_writing(legacy, capsys):
    assert migration.main([
        "--config", str(legacy.config), "--original-config", str(legacy.original),
        "--source-review", str(legacy.review), "--recovery", " ",
    ]) == 1
    assert "nonempty description" in capsys.readouterr().err
    assert _snapshot(legacy.output) == legacy.before


@pytest.mark.parametrize("reuse_flag", [None, "--reuse-communities", "--reuse-robustness"],
                         ids=["fresh-both", "fresh-robustness", "fresh-communities"])
def test_genuine_new_runs_do_not_inherit_stale_historical_bindings(legacy, monkeypatch, reuse_flag):
    _migrate(legacy)
    sidecar = legacy.output / migration.SIDECAR_NAME
    original_sidecar = sidecar.read_bytes()
    arguments = ["--config", str(legacy.config)]
    if reuse_flag:
        arguments.append(reuse_flag)
    assert workflow.main(arguments) == 0
    assert sidecar.read_bytes() == original_sidecar
    assert _verify(legacy)
    def forbidden(*args, **kwargs):
        raise AssertionError("Valid new results should be reusable after historical reconciliation")
    monkeypatch.setattr(workflow.run_communities, "main", forbidden)
    monkeypatch.setattr(workflow.run_robustness, "main", forbidden)
    before_reuse = _snapshot(legacy.output)
    assert workflow.main(["--config", str(legacy.config), "--reuse-communities", "--reuse-robustness"]) == 0
    assert _snapshot(legacy.output) == before_reuse
