#!/usr/bin/env python3
"""
prepare_data.py — Build the shared data package from raw CAIDA files.

Reads:
  data/raw/20261001.as-rel2.txt.bz2       (or decompressed in processed/v1)
  data/raw/20261001.as-org2info.txt.gz     (or decompressed in processed/v1)

Produces in data/processed/v1/:
  nodes.csv, relationships.csv, edges.csv,
  analysis_asns.csv, focal_asns.csv, external_asns.csv, manifest.json
"""

import csv
import hashlib
import json
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import networkx as nx
import numpy as np

# ── paths ────────────────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config" / "project.json"

with open(CONFIG_PATH, encoding="utf-8") as f:
    CONFIG = json.load(f)

RAW_DIR = PROJECT_ROOT / CONFIG["paths"]["raw_data"]
OUT_DIR = PROJECT_ROOT / CONFIG["paths"]["processed_data"]
FOCAL_COUNTRY = CONFIG["focal_country"]


# ── helpers ──────────────────────────────────────────────────────────────────

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_lines(path: Path):
    """Yield stripped lines from a text file, skipping blanks."""
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield line


# ── 1. Parse the organization file ──────────────────────────────────────────

def parse_org_file(path: Path):
    """
    Returns:
        orgs:  dict  org_id  -> {org_name, country, source}
        as_to_org: dict  asn(int) -> {aut_name, org_id, opaque_id, source}
    """
    orgs: dict[str, dict] = {}
    as_to_org: dict[int, dict] = {}
    section = None  # "org" or "aut"

    for line in read_lines(path):
        if line.startswith("#"):
            if "format:org_id" in line:
                section = "org"
            elif "format:aut" in line:
                section = "aut"
            continue

        parts = line.split("|")

        if section == "org" and len(parts) >= 5:
            org_id, _changed, org_name, country, source = parts[0], parts[1], parts[2], parts[3], parts[4]
            country = country.strip().upper() if country.strip() else ""
            orgs[org_id] = {
                "org_name": org_name.strip(),
                "country": country,
                "source": source.strip(),
            }

        elif section == "aut" and len(parts) >= 6:
            try:
                asn = int(parts[0])
            except ValueError:
                continue
            _changed = parts[1]
            aut_name = parts[2].strip()
            org_id = parts[3].strip()
            opaque_id = parts[4].strip()
            source = parts[5].strip()
            as_to_org[asn] = {
                "aut_name": aut_name,
                "org_id": org_id,
                "opaque_id": opaque_id,
                "source": source,
            }

    return orgs, as_to_org


# ── 2. Parse the relationships file ─────────────────────────────────────────

def parse_relationships(path: Path):
    """
    Returns:
        rels: list of dicts with source_asn, target_asn, relationship, evidence_sources
              For provider-to-customer (-1): source_asn is the provider.
        input_clique: set of ASNs
        ixp_ases: set of ASNs
    """
    rels: list[dict] = []
    input_clique: set[int] = set()
    ixp_ases: set[int] = set()

    for line in read_lines(path):
        if line.startswith("#"):
            if line.startswith("# input clique:"):
                tokens = line.replace("# input clique:", "").split()
                input_clique = {int(t) for t in tokens}
            elif line.startswith("# IXP ASes:"):
                tokens = line.replace("# IXP ASes:", "").split()
                ixp_ases = {int(t) for t in tokens}
            continue

        parts = line.split("|")
        if len(parts) < 3:
            continue

        try:
            asn1 = int(parts[0])
            asn2 = int(parts[1])
            rel_code = int(parts[2])
        except ValueError:
            continue

        evidence = parts[3].strip() if len(parts) >= 4 else ""

        if rel_code == -1:
            # asn1 is provider of asn2
            rels.append({
                "source_asn": asn1,
                "target_asn": asn2,
                "relationship": "provider_to_customer",
                "evidence_sources": evidence,
            })
        elif rel_code == 0:
            # peer: canonically order with smaller ASN first
            a, b = (asn1, asn2) if asn1 < asn2 else (asn2, asn1)
            rels.append({
                "source_asn": a,
                "target_asn": b,
                "relationship": "peer",
                "evidence_sources": evidence,
            })
        else:
            # Unexpected relationship code — keep it, flag it
            rels.append({
                "source_asn": asn1,
                "target_asn": asn2,
                "relationship": f"unknown_{rel_code}",
                "evidence_sources": evidence,
            })

    return rels, input_clique, ixp_ases


# ── 3. Build undirected edges ────────────────────────────────────────────────

def build_edges(rels: list[dict]):
    """
    Produce unique undirected pairs (smaller ASN first), no self-loops.
    Also count duplicates and self-loops.
    """
    edge_set: set[tuple[int, int]] = set()
    self_loops = 0
    duplicates = 0

    for r in rels:
        a, b = r["source_asn"], r["target_asn"]
        if a == b:
            self_loops += 1
            continue
        pair = (min(a, b), max(a, b))
        if pair in edge_set:
            duplicates += 1
        else:
            edge_set.add(pair)

    return sorted(edge_set), self_loops, duplicates


# ── 4. Build graph, extract LCC, focal / external sets ──────────────────────

def build_graph_and_sets(edges, orgs, as_to_org, focal_country):
    """
    Build undirected graph G from edges.
    Extract LCC (largest connected component, tie-break by smallest ASN).
    Classify nodes as focal (IT), external (known country != IT), or unknown.
    """
    G_full = nx.Graph()
    G_full.add_edges_from(edges)

    print(f"  Global topology: {G_full.number_of_nodes()} nodes, {G_full.number_of_edges()} edges")

    # Find all connected components
    components = list(nx.connected_components(G_full))
    print(f"  Connected components: {len(components)}")

    # Find LCC — tie-break by smallest ASN in the component
    components.sort(key=lambda c: (-len(c), min(c)))
    lcc_nodes = components[0]
    print(f"  LCC: {len(lcc_nodes)} nodes")

    # Excluded nodes
    excluded = set(G_full.nodes()) - lcc_nodes

    # Build a mapping asn -> country
    def get_country(asn: int) -> str:
        info = as_to_org.get(asn)
        if info is None:
            return ""
        org_id = info["org_id"]
        org = orgs.get(org_id)
        if org is None:
            return ""
        return org["country"]

    # Classify LCC nodes
    focal = set()
    external = set()
    unknown_country = set()
    for asn in lcc_nodes:
        c = get_country(asn)
        if c == focal_country:
            focal.add(asn)
        elif c:
            external.add(asn)
        else:
            unknown_country.add(asn)

    # Count excluded Italian ASes
    excluded_focal = set()
    for asn in excluded:
        if get_country(asn) == focal_country:
            excluded_focal.add(asn)

    return lcc_nodes, focal, external, unknown_country, excluded, excluded_focal


# ── 5. Build nodes table ────────────────────────────────────────────────────

def build_nodes_table(all_asns, orgs, as_to_org):
    """Build list-of-dicts for nodes.csv covering the full global topology."""
    rows = []
    for asn in sorted(all_asns):
        info = as_to_org.get(asn)
        if info is None:
            rows.append({
                "asn": asn,
                "asn_name": "",
                "org_id": "",
                "org_name": "",
                "country": "",
                "metadata_missing": True,
            })
            continue

        org_id = info["org_id"]
        org = orgs.get(org_id)
        country = org["country"] if org else ""
        org_name = org["org_name"] if org else ""

        rows.append({
            "asn": asn,
            "asn_name": info["aut_name"],
            "org_id": org_id,
            "org_name": org_name,
            "country": country,
            "metadata_missing": False,
        })

    return rows


# ── 6. Aggregate relationships ──────────────────────────────────────────────

def aggregate_relationships(rels: list[dict]):
    """
    Aggregate duplicate relationship observations.
    Key: (source_asn, target_asn, relationship)
    Value: combined evidence sources.
    """
    agg: dict[tuple, list[str]] = defaultdict(list)
    for r in rels:
        key = (r["source_asn"], r["target_asn"], r["relationship"])
        if r["evidence_sources"]:
            agg[key].append(r["evidence_sources"])

    rows = []
    for (src, tgt, rel), sources in sorted(agg.items()):
        rows.append({
            "source_asn": src,
            "target_asn": tgt,
            "relationship": rel,
            "evidence_sources": ";".join(sorted(set(sources))),
        })
    return rows


# ── 7. Identify candidate providers ─────────────────────────────────────────

def find_candidate_providers(agg_rels, focal, external):
    """
    P = nodes in E with at least one provider-to-customer link to F.
    A provider-to-customer row has source_asn = provider, target_asn = customer.
    """
    providers: dict[int, set[int]] = defaultdict(set)  # provider -> set of focal customers
    for r in agg_rels:
        if r["relationship"] != "provider_to_customer":
            continue
        provider, customer = r["source_asn"], r["target_asn"]
        if provider in external and customer in focal:
            providers[provider].add(customer)
        # Also check reverse: customer is provider of focal
        # No — in our normalized data, source_asn IS the provider.
        # But we should also check if focal AS is provider of external (won't count).
    return providers


# ── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    t0 = time.perf_counter()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # Determine file paths (prefer decompressed in processed/v1)
    rel_decompressed = OUT_DIR / "20261001.as-rel2.txt"
    org_decompressed = OUT_DIR / "20261001.as-org2info.txt"

    if not rel_decompressed.exists() or not org_decompressed.exists():
        print("ERROR: Decompressed data files not found in", OUT_DIR)
        print("Run data/extract.py first.")
        sys.exit(1)

    print("=" * 60)
    print("PHASE 2: Building shared data package")
    print("=" * 60)

    # ── Step 1: Parse organization file ──────────────────────────
    print("\n[1/7] Parsing organization file...")
    orgs, as_to_org = parse_org_file(org_decompressed)
    print(f"  Organizations: {len(orgs)}")
    print(f"  AS-to-Org mappings: {len(as_to_org)}")

    # ── Step 2: Parse relationships file ─────────────────────────
    print("\n[2/7] Parsing relationships file...")
    rels, input_clique, ixp_ases = parse_relationships(rel_decompressed)
    print(f"  Raw relationship records: {len(rels)}")
    print(f"  Input clique ASes: {len(input_clique)}")
    print(f"  IXP ASes: {len(ixp_ases)}")

    # Count relationship types
    rel_types = defaultdict(int)
    for r in rels:
        rel_types[r["relationship"]] += 1
    for rt, cnt in sorted(rel_types.items()):
        print(f"    {rt}: {cnt}")

    # ── Step 3: Build undirected edges ───────────────────────────
    print("\n[3/7] Building undirected edge list...")
    edges, self_loops, duplicates = build_edges(rels)
    print(f"  Unique undirected edges: {len(edges)}")
    print(f"  Self-loops removed: {self_loops}")
    print(f"  Duplicate pairs merged: {duplicates}")

    # ── Step 4: Build graph and extract sets ─────────────────────
    print("\n[4/7] Building graph and extracting LCC, focal/external sets...")
    all_graph_nodes = set()
    for a, b in edges:
        all_graph_nodes.add(a)
        all_graph_nodes.add(b)

    lcc_nodes, focal, external, unknown_country, excluded, excluded_focal = \
        build_graph_and_sets(edges, orgs, as_to_org, FOCAL_COUNTRY)

    print(f"  Focal ASes (IT) in G: {len(focal)}")
    print(f"  External ASes in G: {len(external)}")
    print(f"  Unknown-country ASes in G: {len(unknown_country)}")
    print(f"  Excluded from LCC: {len(excluded)} nodes")
    print(f"  Excluded Italian ASes: {len(excluded_focal)}")

    # Validate preconditions
    assert len(focal) > 0, "No focal ASes found — check country code"
    assert len(external) > 0, "No external ASes found"

    # ── Step 5: Aggregate relationships ──────────────────────────
    print("\n[5/7] Aggregating relationships...")
    agg_rels = aggregate_relationships(rels)
    print(f"  Aggregated relationship rows: {len(agg_rels)}")

    # ── Step 6: Find candidate providers ─────────────────────────
    print("\n[6/7] Identifying candidate providers P...")
    provider_customers = find_candidate_providers(agg_rels, focal, external)
    print(f"  Candidate providers |P|: {len(provider_customers)}")
    assert len(provider_customers) >= 2, \
        f"Only {len(provider_customers)} candidate providers — need ≥ 2 for random/targeted comparison"

    # Top providers by focal customer count
    top = sorted(provider_customers.items(), key=lambda x: -len(x[1]))[:10]
    print("  Top 10 providers by focal customer count:")
    for asn, customers in top:
        info = as_to_org.get(asn, {})
        org_id = info.get("org_id", "")
        org = orgs.get(org_id, {})
        name = org.get("org_name", info.get("aut_name", "?"))
        print(f"    AS{asn} ({name}): {len(customers)} focal customers")

    # ── Step 7: Write output files ───────────────────────────────
    print("\n[7/7] Writing output files...")

    # nodes.csv — all nodes in global topology
    nodes_table = build_nodes_table(all_graph_nodes, orgs, as_to_org)
    nodes_path = OUT_DIR / "nodes.csv"
    with open(nodes_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["asn", "asn_name", "org_id", "org_name", "country", "metadata_missing"])
        w.writeheader()
        w.writerows(nodes_table)
    print(f"  nodes.csv: {len(nodes_table)} rows")

    # relationships.csv — aggregated
    rels_path = OUT_DIR / "relationships.csv"
    with open(rels_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["source_asn", "target_asn", "relationship", "evidence_sources"])
        w.writeheader()
        w.writerows(agg_rels)
    print(f"  relationships.csv: {len(agg_rels)} rows")

    # edges.csv — unique undirected pairs
    edges_path = OUT_DIR / "edges.csv"
    with open(edges_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["source_asn", "target_asn"])
        w.writerows(edges)
    print(f"  edges.csv: {len(edges)} rows")

    # analysis_asns.csv — LCC
    analysis_path = OUT_DIR / "analysis_asns.csv"
    with open(analysis_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["asn"])
        for asn in sorted(lcc_nodes):
            w.writerow([asn])
    print(f"  analysis_asns.csv: {len(lcc_nodes)} rows")

    # focal_asns.csv
    focal_path = OUT_DIR / "focal_asns.csv"
    with open(focal_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["asn"])
        for asn in sorted(focal):
            w.writerow([asn])
    print(f"  focal_asns.csv: {len(focal)} rows")

    # external_asns.csv
    external_path = OUT_DIR / "external_asns.csv"
    with open(external_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["asn"])
        for asn in sorted(external):
            w.writerow([asn])
    print(f"  external_asns.csv: {len(external)} rows")

    # manifest.json
    elapsed = time.perf_counter() - t0

    # Compute hashes of output files
    output_hashes = {}
    for name in ["nodes.csv", "relationships.csv", "edges.csv",
                  "analysis_asns.csv", "focal_asns.csv", "external_asns.csv"]:
        output_hashes[name] = sha256_file(OUT_DIR / name)

    manifest = {
        "schema_version": "1.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "runtime_seconds": round(elapsed, 2),
        "snapshot": CONFIG["snapshot"],
        "focal_country": FOCAL_COUNTRY,
        "data_sources": CONFIG["data_sources"],
        "raw_file_hashes": {
            CONFIG["data_sources"]["relationships"]["filename"]:
                sha256_file(RAW_DIR / CONFIG["data_sources"]["relationships"]["filename"]),
            CONFIG["data_sources"]["organizations"]["filename"]:
                sha256_file(RAW_DIR / CONFIG["data_sources"]["organizations"]["filename"]),
        },
        "processed_file_hashes": output_hashes,
        "counts": {
            "organizations_parsed": len(orgs),
            "as_to_org_mappings": len(as_to_org),
            "raw_relationship_records": len(rels),
            "aggregated_relationships": len(agg_rels),
            "unique_undirected_edges": len(edges),
            "self_loops_removed": self_loops,
            "duplicate_pairs_merged": duplicates,
            "global_topology_nodes": len(all_graph_nodes),
            "global_topology_edges": len(edges),
            "connected_components": None,  # filled below
            "lcc_nodes": len(lcc_nodes),
            "focal_asns_IT": len(focal),
            "external_asns": len(external),
            "unknown_country_asns": len(unknown_country),
            "excluded_from_lcc": len(excluded),
            "excluded_focal_asns": len(excluded_focal),
            "candidate_providers": len(provider_customers),
            "input_clique_ases": len(input_clique),
            "ixp_ases": len(ixp_ases),
        },
        "relationship_type_counts": dict(rel_types),
        "excluded_focal_asns_list": sorted(excluded_focal),
        "input_clique": sorted(input_clique),
        "ixp_ases": sorted(ixp_ases),
        "cleaning_decisions": [
            "Self-loops removed from edge list",
            "Duplicate undirected pairs merged; relationship aggregation preserves all source labels",
            "Peer links canonically ordered (smaller ASN first)",
            "Provider-to-customer direction preserved as in raw data: source_asn = provider",
            "Unknown/empty country codes left as empty string, not assigned to focal or external",
            "Only ASes appearing as relationship endpoints included in global topology (no isolated registry ASes added)",
            "IXP ASes and input clique ASes kept as normal nodes; noted in manifest for reference",
        ],
    }

    # Fill connected components count
    G_full = nx.Graph()
    G_full.add_edges_from(edges)
    manifest["counts"]["connected_components"] = nx.number_connected_components(G_full)

    manifest_path = OUT_DIR / "manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    print(f"  manifest.json: written")

    # ── Summary ──────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("DATA PACKAGE COMPLETE")
    print("=" * 60)
    print(f"  Output directory: {OUT_DIR}")
    print(f"  Global topology:  {len(all_graph_nodes):,} nodes, {len(edges):,} edges")
    print(f"  Analysis graph G: {len(lcc_nodes):,} nodes (LCC)")
    print(f"  Focal set F (IT): {len(focal):,} ASes")
    print(f"  External set E:   {len(external):,} ASes")
    print(f"  Candidate P:      {len(provider_customers):,} providers")
    print(f"  Components:       {manifest['counts']['connected_components']}")
    print(f"  Runtime:          {elapsed:.1f}s")


if __name__ == "__main__":
    main()
