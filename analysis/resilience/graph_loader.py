"""Load the shared package without changing its graph or country assignments.

Only NetworkX and the Python standard library are required. No Student A analysis
or saved importance result is imported. Country-policy questions remain explicit
in the report; this loader never silently reclassifies the supplied baseline.
"""

from __future__ import annotations

import csv
import hashlib
import json
import platform
import re
import subprocess
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Iterator, Mapping

import networkx as nx


SCHEMAS = {
    "nodes.csv": ("asn", "asn_name", "org_id", "org_name", "country", "metadata_missing"),
    "relationships.csv": ("source_asn", "target_asn", "relationship", "evidence_sources"),
    "edges.csv": ("source_asn", "target_asn"),
    "analysis_asns.csv": ("asn",),
    "focal_asns.csv": ("asn",),
    "external_asns.csv": ("asn",),
}

# ISO 3166-1 assigned alpha-2 labels, kept local for offline validation.
# Reference: https://www.iso.org/iso-3166-country-codes.html
# Reserved/regional labels (including EU) are not silently treated as countries.
COUNTRY_CODES = frozenset("""
AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL
BM BN BO BQ BR BS BT BV BW BY BZ CA CC CD CF CG CH CI CK CL CM CN CO CR CU
CV CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR GA
GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT HU ID
IE IL IM IN IO IQ IR IS IT JE JM JO JP KE KG KH KI KM KN KP KR KW KY KZ LA
LB LC LI LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR
MS MT MU MV MW MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG
PH PK PL PM PN PR PS PT PW PY QA RE RO RS RU RW SA SB SC SD SE SG SH SI SJ
SK SL SM SN SO SR SS ST SV SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO TR TT
TV TW TZ UA UG UM US UY UZ VA VC VE VG VI VN VU WF WS YE YT ZA ZM ZW
""".split())


class ValidationError(ValueError):
    """The package is inconsistent or cannot be verified under the chosen policy."""


@dataclass(frozen=True)
class NodeMetadata:
    asn: int
    asn_name: str
    org_id: str
    org_name: str
    country: str
    metadata_missing: bool


@dataclass(frozen=True)
class GraphBaseline:
    graph: nx.Graph
    nodes: Mapping[int, NodeMetadata]
    analysis_asns: frozenset[int]
    focal_asns: frozenset[int]
    external_asns: frozenset[int]
    candidate_providers: frozenset[int]
    provider_customers: Mapping[int, frozenset[int]]
    baseline_degrees: Mapping[int, int]
    targeted_order: tuple[int, ...]
    report: dict


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def _json(path: Path) -> dict:
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValidationError(f"Cannot read JSON {path}: {exc}") from exc
    _require(isinstance(result, dict), f"{path}: expected a JSON object")
    return result


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise ValidationError(f"Cannot hash {path}: {exc}") from exc
    return digest.hexdigest()


def _verify_csv_hash(path: Path, expected: str, policy: str) -> dict:
    _require(isinstance(expected, str) and re.fullmatch(r"[0-9a-f]{64}", expected) is not None,
             f"{path.name}: missing or malformed manifest SHA-256")
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise ValidationError(f"Cannot read {path}: {exc}") from exc
    actual = _sha256(content)
    lf = content.replace(b"\r\n", b"\n")
    crlf = lf.replace(b"\n", b"\r\n")
    compatible = expected in {_sha256(lf), _sha256(crlf)}
    exact = actual == expected
    _require(exact or (policy == "newline-compatible" and compatible),
             f"{path.name}: SHA-256 mismatch (expected {expected}, got {actual}). "
             "Only a proven LF/CRLF difference may use hash_policy='newline-compatible'.")
    return {
        "expected_sha256": expected,
        "actual_sha256": actual,
        "lf_sha256": _sha256(lf),
        "match": "exact" if exact else "newline_only",
        "contains_crlf": b"\r\n" in content,
    }


def _records(directory: Path, name: str) -> Iterator[tuple[int, dict[str, str]]]:
    path = directory / name
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle, strict=True)
            fields = reader.fieldnames or []
            _require(len(fields) == len(set(fields)) and set(fields) == set(SCHEMAS[name]),
                     f"{name}: invalid schema; expected {SCHEMAS[name]}, got {fields}")
            for row in reader:
                _require(None not in row and all(value is not None for value in row.values()),
                         f"{name}:{reader.line_num}: wrong number of columns")
                yield reader.line_num, row
    except (OSError, UnicodeError, csv.Error) as exc:
        raise ValidationError(f"Cannot parse {name}: {exc}") from exc


def _asn(value: str, location: str) -> int:
    _require(re.fullmatch(r"[0-9]+", value) is not None,
             f"{location}: ASN must be an unsigned integer, got {value!r}")
    number = int(value)
    _require(0 < number < 2**32, f"{location}: ASN outside 1..4294967295")
    return number


def _asn_set(directory: Path, name: str) -> frozenset[int]:
    result: set[int] = set()
    for line, row in _records(directory, name):
        asn = _asn(row["asn"], f"{name}:{line}")
        _require(asn not in result, f"{name}:{line}: duplicate ASN {asn}")
        result.add(asn)
    return frozenset(result)


def _git_commit(root: Path) -> str | None:
    # A per-command ownership exception is restricted to this checkout. It does
    # not change global Git configuration or run remote commands.
    try:
        completed = subprocess.run(
            ["git", "-c", f"safe.directory={root.as_posix()}", "-C", str(root),
             "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5, check=False,
        )
        return completed.stdout.strip() if completed.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def load_graph(config_path: str | Path, *, hash_policy: str = "exact") -> GraphBaseline:
    """Validate and return an immutable baseline for Student B's analyses.

    ``exact`` requires the manifest's byte hashes. ``newline-compatible`` is an
    explicit provisional alternative accepting only demonstrable LF/CRLF changes.
    Relative config paths resolve against the caller's working directory; paths
    inside the configuration resolve against the repository root (config/..).
    """
    started = time.perf_counter()
    _require(hash_policy in {"exact", "newline-compatible"}, "Unknown hash policy")
    config_path = Path(config_path).resolve()
    root = config_path.parent.parent
    config = _json(config_path)
    paths = config.get("paths")
    _require(isinstance(paths, dict), "Configuration paths must be an object")
    processed_setting = paths.get("processed_data")
    _require(isinstance(processed_setting, str) and bool(processed_setting),
             "Configuration paths.processed_data must be a nonempty string")
    directory = (root / processed_setting).resolve()
    focal_country = config.get("focal_country")
    snapshot = config.get("snapshot")
    _require(isinstance(focal_country, str) and focal_country in COUNTRY_CODES,
             "Configuration focal_country is not an assigned alpha-2 code")
    _require(isinstance(snapshot, str) and re.fullmatch(r"[0-9]{8}", snapshot) is not None,
             "Configuration snapshot must be an eight-digit string")
    manifest_path = directory / "manifest.json"
    manifest = _json(manifest_path)
    _require(manifest.get("schema_version") == "1.0", "Unsupported manifest schema_version")
    _require(manifest.get("snapshot") == snapshot, "Config/manifest snapshot mismatch")
    _require(manifest.get("focal_country") == focal_country, "Config/manifest focal_country mismatch")
    country_policy = manifest.get("country_policy", "nonempty_nonfocal")
    _require(isinstance(country_policy, str) and country_policy in {"nonempty_nonfocal", "assigned_alpha2"},
             "Manifest country_policy must be nonempty_nonfocal or assigned_alpha2")
    expected_hashes = manifest.get("processed_file_hashes", {})
    _require(isinstance(expected_hashes, dict), "Manifest processed_file_hashes must be an object")
    hashes = {name: _verify_csv_hash(directory / name, expected_hashes.get(name), hash_policy)
              for name in SCHEMAS}

    nodes: dict[int, NodeMetadata] = {}
    for line, row in _records(directory, "nodes.csv"):
        location = f"nodes.csv:{line}"
        asn = _asn(row["asn"], location)
        _require(asn not in nodes, f"{location}: duplicate ASN {asn}")
        country = row["country"]
        _require(country == "" or re.fullmatch(r"[A-Z]{2}", country) is not None,
                 f"{location}: country must be empty or two uppercase letters")
        _require(row["metadata_missing"] in {"True", "False"},
                 f"{location}: metadata_missing must be True or False")
        nodes[asn] = NodeMetadata(asn, row["asn_name"], row["org_id"], row["org_name"],
                                  country, row["metadata_missing"] == "True")
    _require(bool(nodes), "nodes.csv is empty")

    edges: set[tuple[int, int]] = set()
    endpoints: set[int] = set()
    for line, row in _records(directory, "edges.csv"):
        location = f"edges.csv:{line}"
        a, b = (_asn(row[key], location) for key in ("source_asn", "target_asn"))
        _require(a != b, f"{location}: self-loop at ASN {a}")
        _require(a < b, f"{location}: edge must be canonical (smaller ASN first)")
        _require(a in nodes and b in nodes, f"{location}: endpoint missing from nodes.csv")
        _require((a, b) not in edges, f"{location}: duplicate edge {(a, b)}")
        edges.add((a, b))
        endpoints.update((a, b))
    _require(bool(edges), "edges.csv is empty")
    _require(endpoints == set(nodes), "nodes.csv includes nodes with no observed edges")
    full_graph = nx.Graph()
    full_graph.add_nodes_from(sorted(nodes))
    full_graph.add_edges_from(sorted(edges))
    components = list(nx.connected_components(full_graph))
    lcc = min(components, key=lambda component: (-len(component), min(component)))

    analysis = _asn_set(directory, "analysis_asns.csv")
    focal = _asn_set(directory, "focal_asns.csv")
    external = _asn_set(directory, "external_asns.csv")
    _require(analysis == lcc, "analysis_asns.csv is not the largest connected component "
             "(ties must use the smallest ASN)")
    _require(bool(focal) and bool(external), "Focal and external sets must both be nonempty")
    _require(focal <= analysis and external <= analysis, "Focal/external ASN outside analysis graph")
    _require(not focal & external, "Focal and external sets overlap")
    expected_focal = frozenset(asn for asn in analysis if nodes[asn].country == focal_country)
    # Legacy packages retain their original assignments. A future package may
    # explicitly declare assigned_alpha2 without erasing regional metadata.
    expected_external = frozenset(asn for asn in analysis
                                  if nodes[asn].country and nodes[asn].country != focal_country
                                  and (country_policy == "nonempty_nonfocal"
                                       or nodes[asn].country in COUNTRY_CODES))
    _require(focal == expected_focal, "focal_asns.csv disagrees with country metadata")
    _require(external == expected_external, "external_asns.csv disagrees with the package country policy")

    relation_pairs: set[tuple[int, int]] = set()
    relation_types: Counter[str] = Counter()
    customers: dict[int, set[int]] = defaultdict(set)
    for line, row in _records(directory, "relationships.csv"):
        location = f"relationships.csv:{line}"
        a, b = (_asn(row[key], location) for key in ("source_asn", "target_asn"))
        kind = row["relationship"]
        _require(kind in {"peer", "provider_to_customer"}, f"{location}: unknown relationship {kind!r}")
        _require(a != b, f"{location}: self-loop relationship")
        _require(a in nodes and b in nodes, f"{location}: endpoint missing from nodes.csv")
        _require(kind != "peer" or a < b, f"{location}: peer pair must be canonical")
        pair = (min(a, b), max(a, b))
        _require(pair not in relation_pairs,
                 f"{location}: duplicate or contradictory relationship for pair {pair}")
        _require(pair in edges, f"{location}: relationship has no matching topology edge")
        relation_pairs.add(pair)
        relation_types[kind] += 1
        if kind == "provider_to_customer" and a in external and b in focal:
            customers[a].add(b)
    _require(relation_pairs == edges, "edges.csv contains topology edges without relationships")
    providers = frozenset(customers)
    _require(len(providers) >= 2, "At least two candidate providers are needed")
    if "relationship_type_counts" in manifest:
        _require(manifest["relationship_type_counts"] == dict(relation_types),
                 "Manifest relationship_type_counts disagree with relationships.csv")

    graph = full_graph if analysis == set(nodes) else full_graph.subgraph(sorted(analysis)).copy()
    degrees = dict(graph.degree())
    targeted_order = tuple(sorted(providers, key=lambda asn: (-len(customers[asn]), -degrees[asn], asn)))
    excluded_focal = sorted(asn for asn in set(nodes) - analysis if nodes[asn].country == focal_country)
    counts = {
        "global_topology_nodes": len(nodes),
        "global_topology_edges": len(edges),
        "unique_undirected_edges": len(edges),
        "connected_components": len(components),
        "lcc_nodes": len(analysis),
        f"focal_asns_{focal_country}": len(focal),
        "external_asns": len(external),
        "unknown_country_asns": len(analysis - focal - external),
        "excluded_from_lcc": len(nodes) - len(analysis),
        "excluded_focal_asns": len(excluded_focal),
        "candidate_providers": len(providers),
        "aggregated_relationships": sum(relation_types.values()),
    }
    declared_counts = manifest.get("counts", {})
    _require(isinstance(declared_counts, dict), "Manifest counts must be an object")
    for key, observed in counts.items():
        if key in declared_counts:
            _require(type(declared_counts[key]) is int and declared_counts[key] == observed,
                     f"Manifest count {key}: expected {declared_counts[key]!r}, observed {observed}")
    if "excluded_focal_asns_list" in manifest:
        _require(manifest["excluded_focal_asns_list"] == excluded_focal,
                 "Manifest excluded_focal_asns_list disagrees with graph")

    warnings = []
    missing_counts = sorted(set(counts) - set(declared_counts))
    if missing_counts:
        warnings.append({"code": "missing_manifest_counts", "message":
                         "Some independently computed counts are not declared in the manifest; "
                         "their agreement with preparation outputs cannot be checked.",
                         "fields": missing_counts})
    unresolved: dict[str, list[int]] = defaultdict(list)
    for asn in sorted(analysis):
        country = nodes[asn].country
        if country and country not in COUNTRY_CODES:
            unresolved[country].append(asn)
    if unresolved:
        warnings.append({"code": "unresolved_country_labels", "message":
                         ("Non-country labels are preserved in the supplied external set. "
                          "Agree on their treatment and version any changed package before final robustness runs."
                          if country_policy == "nonempty_nonfocal" else
                          "Non-country labels are preserved in metadata and excluded from the external "
                          "set under the package's explicitly declared assigned_alpha2 policy."),
                         "asns_by_label": dict(unresolved),
                         "affected_candidate_providers": sorted(providers &
                             {asn for values in unresolved.values() for asn in values})})
    newline_only = [name for name, detail in hashes.items() if detail["match"] == "newline_only"]
    if newline_only:
        warnings.append({"code": "newline_compatible_hashes", "message":
                         "Explicit newline compatibility was used; byte hashes differ from the manifest.",
                         "files": newline_only})
    crlf_files = [name for name, detail in hashes.items() if detail["contains_crlf"]]
    if crlf_files:
        warnings.append({"code": "crlf_hash_portability", "message":
                         "These CSVs use CRLF. Git newline conversion can change byte hashes; "
                         "agree on a portable artifact policy before final shared runs.", "files": crlf_files})

    raw_hashes = {}
    raw_dir_setting = config["paths"].get("raw_data")
    declared_raw_hashes = manifest.get("raw_file_hashes", {})
    _require(isinstance(declared_raw_hashes, dict), "Manifest raw_file_hashes must be an object")
    if raw_dir_setting is not None:
        _require(isinstance(raw_dir_setting, str) and bool(raw_dir_setting),
                 "Configuration paths.raw_data must be a nonempty string")
        raw_directory = (root / raw_dir_setting).resolve()
        for name, expected in declared_raw_hashes.items():
            _require(isinstance(name, str) and Path(name).name == name,
                     "Manifest raw filename must not contain a directory")
            _require(isinstance(expected, str) and re.fullmatch(r"[0-9a-f]{64}", expected) is not None,
                     f"{name}: malformed raw manifest SHA-256")
            raw_path = raw_directory / name
            if raw_path.is_file():
                actual = _file_hash(raw_path)
                _require(actual == expected, f"{name}: raw SHA-256 mismatch")
                raw_hashes[name] = {"status": "exact", "actual_sha256": actual}
            else:
                raw_hashes[name] = {"status": "not_available", "expected_sha256": expected}

    fingerprint = _sha256(json.dumps({
        "snapshot": snapshot, "focal_country": focal_country,
        "country_policy": country_policy,
        "lf_hashes": {name: detail["lf_sha256"] for name, detail in hashes.items()},
    }, sort_keys=True).encode("utf-8"))
    report = {
        "status": "passed_with_warnings" if warnings else "passed",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "ready_for_community_analysis": True,
        "ready_for_final_robustness": False,
        "final_robustness_note": "Loading/validation does not certify that both students have agreed "
                                 "and frozen the country and artifact policies. Resolve those before final runs.",
        "baseline_fingerprint": fingerprint,
        "source_commit": _git_commit(root),
        "package_directory": str(directory),
        "schema_version": manifest["schema_version"],
        "snapshot": snapshot,
        "focal_country": focal_country,
        "country_policy": country_policy,
        "hash_policy": hash_policy,
        "config_sha256": _file_hash(config_path),
        "manifest_sha256": _file_hash(manifest_path),
        "loader_sha256": _file_hash(Path(__file__)),
        "processed_hashes": hashes,
        "raw_hashes": raw_hashes,
        "counts": counts,
        "relationship_type_counts": dict(relation_types),
        "metadata_missing_global": sum(node.metadata_missing for node in nodes.values()),
        "excluded_focal_asns": excluded_focal,
        "warnings": warnings,
        "checks": ["CSV schema and types", "manifest hashes and declared derived counts",
                   "unique nodes and ASN sets", "canonical nonduplicate nonloop edges",
                   "endpoint coverage", "largest component and tie-break",
                   "focal/external country membership", "relationship/topology consistency",
                   "distinct direct provider customers", "stable baseline ordering"],
        "scope": "Processed-package validation; raw hashes are checked when files are available. "
                 "Raw relationship direction and organization joins are not re-audited here.",
        "environment": {"python": platform.python_version(), "networkx": nx.__version__},
        "runtime_seconds": round(time.perf_counter() - started, 3),
    }
    return GraphBaseline(
        graph=nx.freeze(graph), nodes=MappingProxyType(dict(sorted(nodes.items()))),
        analysis_asns=analysis, focal_asns=focal, external_asns=external,
        candidate_providers=providers,
        provider_customers=MappingProxyType({asn: frozenset(customers[asn]) for asn in sorted(providers)}),
        baseline_degrees=MappingProxyType(degrees), targeted_order=targeted_order, report=report,
    )
