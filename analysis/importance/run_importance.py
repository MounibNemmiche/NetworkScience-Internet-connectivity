#!/usr/bin/env python3
"""
run_importance.py — Student A: Provider importance and dependence analysis.

Reads the shared data package from data/processed/v1/ and produces:
  results/importance/node_scores.csv
  results/importance/provider_summary.csv
  results/importance/focal_dependency.csv
  results/importance/figures/  (degree distribution, rankings, dependency)
  results/importance/findings.md
  results/importance/run_manifest.json
"""

import csv
import hashlib
import json
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import networkx as nx
import numpy as np

# ── paths & config ───────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = PROJECT_ROOT / "config" / "project.json"

with open(CONFIG_PATH, encoding="utf-8") as f:
    CONFIG = json.load(f)

DATA_DIR = PROJECT_ROOT / CONFIG["paths"]["processed_data"]
OUT_DIR = PROJECT_ROOT / CONFIG["paths"]["results_importance"]
FIG_DIR = OUT_DIR / "figures"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ── 1. Load data package ────────────────────────────────────────────────────

def load_package():
    """Load the shared data package CSVs into memory."""

    # edges
    edges = []
    with open(DATA_DIR / "edges.csv", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            edges.append((int(row["source_asn"]), int(row["target_asn"])))

    # analysis ASNs (LCC)
    analysis_asns = set()
    with open(DATA_DIR / "analysis_asns.csv", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            analysis_asns.add(int(row["asn"]))

    # focal ASNs
    focal = set()
    with open(DATA_DIR / "focal_asns.csv", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            focal.add(int(row["asn"]))

    # external ASNs
    external = set()
    with open(DATA_DIR / "external_asns.csv", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            external.add(int(row["asn"]))

    # nodes metadata
    nodes_meta = {}
    with open(DATA_DIR / "nodes.csv", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            nodes_meta[int(row["asn"])] = row

    # relationships (we need p2c for provider analysis)
    relationships = []
    with open(DATA_DIR / "relationships.csv", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            relationships.append(row)

    return edges, analysis_asns, focal, external, nodes_meta, relationships


# ── 2. Build graph G ────────────────────────────────────────────────────────

def build_graph(edges, analysis_asns):
    """Build undirected graph G from edges, restricted to LCC nodes."""
    G = nx.Graph()
    G.add_nodes_from(analysis_asns)
    for a, b in edges:
        if a in analysis_asns and b in analysis_asns:
            G.add_edge(a, b)
    return G


# ── 3. Compute degree and PageRank ──────────────────────────────────────────

def compute_scores(G, damping):
    """Return dict asn -> {degree, pagerank}."""
    print(f"  Computing PageRank (damping={damping})...")
    pr = nx.pagerank(G, alpha=damping)

    # Validate
    pr_sum = sum(pr.values())
    print(f"  PageRank sum: {pr_sum:.6f} (expected ~1.0)")
    assert abs(pr_sum - 1.0) < 1e-4, f"PageRank sum too far from 1: {pr_sum}"

    degrees = dict(G.degree())

    scores = {}
    for node in G.nodes():
        scores[node] = {
            "asn": node,
            "degree": degrees[node],
            "pagerank": pr[node],
        }
    return scores


# ── 4. Provider summary ────────────────────────────────────────────────────

def compute_provider_summary(relationships, focal, external, scores, nodes_meta):
    """
    Candidate providers P: nodes in E with at least one p2c link to F.
    For each: focal customer count, coverage fraction, degree, pagerank.
    """
    # Build provider -> focal customers map
    provider_focal_customers = defaultdict(set)
    for r in relationships:
        if r["relationship"] != "provider_to_customer":
            continue
        provider = int(r["source_asn"])
        customer = int(r["target_asn"])
        if provider in external and customer in focal:
            provider_focal_customers[provider].add(customer)

    focal_size = len(focal)
    rows = []
    for asn, customers in sorted(provider_focal_customers.items(),
                                  key=lambda x: (-len(x[1]), -scores.get(x[0], {}).get("degree", 0), x[0])):
        meta = nodes_meta.get(asn, {})
        s = scores.get(asn, {"degree": 0, "pagerank": 0.0})
        rows.append({
            "asn": asn,
            "org_name": meta.get("org_name", ""),
            "country": meta.get("country", ""),
            "focal_customer_count": len(customers),
            "focal_customer_fraction": round(len(customers) / focal_size, 6),
            "degree": s["degree"],
            "pagerank": s["pagerank"],
        })

    return rows


# ── 5. Focal AS dependency ──────────────────────────────────────────────────

def compute_focal_dependency(relationships, focal, external, nodes_meta):
    """
    For each focal AS: count observed direct providers, split by
    domestic (IT), external, and unknown-country.
    """
    # All p2c where customer is focal
    focal_providers = defaultdict(list)  # focal_asn -> list of provider ASNs
    for r in relationships:
        if r["relationship"] != "provider_to_customer":
            continue
        provider = int(r["source_asn"])
        customer = int(r["target_asn"])
        if customer in focal:
            focal_providers[customer].append(provider)

    rows = []
    for asn in sorted(focal):
        providers = focal_providers.get(asn, [])
        domestic = 0
        ext = 0
        unknown = 0
        for p in providers:
            meta = nodes_meta.get(p, {})
            country = meta.get("country", "")
            if country == "IT":
                domestic += 1
            elif country:
                ext += 1
            else:
                unknown += 1

        meta = nodes_meta.get(asn, {})
        rows.append({
            "asn": asn,
            "asn_name": meta.get("asn_name", ""),
            "org_name": meta.get("org_name", ""),
            "total_providers": len(providers),
            "domestic_providers": domestic,
            "external_providers": ext,
            "unknown_providers": unknown,
        })

    return rows


# ── 6. Figures ──────────────────────────────────────────────────────────────

def plot_degree_distribution(G, fig_dir):
    """Degree distribution: histogram + log-log CCDF."""
    degrees = [d for _, d in G.degree()]
    deg_arr = np.array(degrees)

    fig, axes = plt.subplots(1, 2, figsize=(14, 5))

    # (a) Histogram (log-scale y)
    ax = axes[0]
    max_deg = int(deg_arr.max())
    bins = np.logspace(0, np.log10(max_deg + 1), 50)
    ax.hist(deg_arr, bins=bins, edgecolor="black", alpha=0.7, color="#4C72B0")
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("Degree", fontsize=12)
    ax.set_ylabel("Count", fontsize=12)
    ax.set_title("(a) Degree distribution (log-log histogram)", fontsize=12)
    ax.grid(True, alpha=0.3)

    # (b) CCDF
    ax = axes[1]
    sorted_deg = np.sort(deg_arr)[::-1]
    ccdf = np.arange(1, len(sorted_deg) + 1) / len(sorted_deg)
    ax.plot(sorted_deg, ccdf, linewidth=1.2, color="#C44E52")
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("Degree", fontsize=12)
    ax.set_ylabel("P(X ≥ x)", fontsize=12)
    ax.set_title("(b) Complementary CDF", fontsize=12)
    ax.grid(True, alpha=0.3)

    fig.suptitle(f"Degree distribution of analysis graph G (n={G.number_of_nodes():,}, m={G.number_of_edges():,})",
                 fontsize=13, fontweight="bold")
    plt.tight_layout()
    path = fig_dir / "degree_distribution.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved {path.name}")
    return path


def plot_provider_rankings(provider_summary, fig_dir):
    """Scatter: PageRank vs focal customer count, labeled top providers."""
    if not provider_summary:
        return None

    asns = [r["asn"] for r in provider_summary]
    cust_counts = [r["focal_customer_count"] for r in provider_summary]
    pageranks = [r["pagerank"] for r in provider_summary]
    names = [r["org_name"] for r in provider_summary]

    fig, ax = plt.subplots(figsize=(10, 7))
    ax.scatter(cust_counts, pageranks, s=50, alpha=0.7, edgecolors="black", linewidths=0.5, color="#4C72B0")

    # Label top 10 by customer count
    top_n = 10
    for i in range(min(top_n, len(provider_summary))):
        label = names[i] if len(names[i]) <= 25 else names[i][:22] + "..."
        ax.annotate(f"AS{asns[i]}\n{label}",
                    (cust_counts[i], pageranks[i]),
                    fontsize=7, ha="left", va="bottom",
                    xytext=(5, 3), textcoords="offset points",
                    arrowprops=dict(arrowstyle="-", color="gray", lw=0.5))

    ax.set_xlabel("Focal customer count", fontsize=12)
    ax.set_ylabel("PageRank", fontsize=12)
    ax.set_title("Provider ranking: PageRank vs focal customer count", fontsize=13, fontweight="bold")
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    path = fig_dir / "provider_rankings.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved {path.name}")
    return path


def plot_provider_coverage_bar(provider_summary, fig_dir, top_n=20):
    """Horizontal bar chart of top providers by focal customer fraction."""
    data = provider_summary[:top_n]
    if not data:
        return None

    labels = [f"AS{r['asn']} — {r['org_name'][:30]}" for r in data]
    fractions = [r["focal_customer_fraction"] * 100 for r in data]

    fig, ax = plt.subplots(figsize=(10, max(6, top_n * 0.35)))
    y_pos = range(len(labels))
    ax.barh(y_pos, fractions, color="#4C72B0", edgecolor="black", alpha=0.8)
    ax.set_yticks(y_pos)
    ax.set_yticklabels(labels, fontsize=8)
    ax.invert_yaxis()
    ax.set_xlabel("Focal customer coverage (%)", fontsize=12)
    ax.set_title(f"Top {len(data)} external providers by Italian AS customer coverage",
                 fontsize=13, fontweight="bold")
    ax.grid(True, axis="x", alpha=0.3)

    for i, v in enumerate(fractions):
        ax.text(v + 0.3, i, f"{v:.1f}%", va="center", fontsize=8)

    plt.tight_layout()
    path = fig_dir / "provider_coverage.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved {path.name}")
    return path


def plot_focal_dependency_distribution(focal_dep, fig_dir):
    """Distribution of number of direct providers per focal AS."""
    totals = [r["total_providers"] for r in focal_dep]
    externals = [r["external_providers"] for r in focal_dep]

    fig, axes = plt.subplots(1, 2, figsize=(14, 5))

    # (a) Total providers
    ax = axes[0]
    max_val = max(totals) if totals else 1
    bins = np.arange(0, max_val + 2) - 0.5
    ax.hist(totals, bins=bins, edgecolor="black", alpha=0.7, color="#4C72B0")
    ax.set_xlabel("Number of direct providers (all)", fontsize=12)
    ax.set_ylabel("Number of Italian ASes", fontsize=12)
    ax.set_title("(a) Total observed providers per focal AS", fontsize=12)
    ax.grid(True, alpha=0.3, axis="y")

    # Annotate counts for 0, 1, 2+ providers
    from collections import Counter
    cnt = Counter(totals)
    zero = cnt.get(0, 0)
    one = cnt.get(1, 0)
    multi = sum(v for k, v in cnt.items() if k >= 2)
    ax.text(0.95, 0.95, f"0 providers: {zero}\n1 provider: {one}\n2+ providers: {multi}",
            transform=ax.transAxes, ha="right", va="top", fontsize=9,
            bbox=dict(boxstyle="round,pad=0.4", facecolor="wheat", alpha=0.8))

    # (b) External providers
    ax = axes[1]
    max_val = max(externals) if externals else 1
    bins = np.arange(0, max_val + 2) - 0.5
    ax.hist(externals, bins=bins, edgecolor="black", alpha=0.7, color="#C44E52")
    ax.set_xlabel("Number of direct external providers", fontsize=12)
    ax.set_ylabel("Number of Italian ASes", fontsize=12)
    ax.set_title("(b) External providers per focal AS", fontsize=12)
    ax.grid(True, alpha=0.3, axis="y")

    cnt_ext = Counter(externals)
    zero_e = cnt_ext.get(0, 0)
    one_e = cnt_ext.get(1, 0)
    multi_e = sum(v for k, v in cnt_ext.items() if k >= 2)
    ax.text(0.95, 0.95, f"0 ext providers: {zero_e}\n1 ext provider: {one_e}\n2+ ext providers: {multi_e}",
            transform=ax.transAxes, ha="right", va="top", fontsize=9,
            bbox=dict(boxstyle="round,pad=0.4", facecolor="wheat", alpha=0.8))

    fig.suptitle(f"Direct provider dependency of Italian ASes (|F|={len(focal_dep)})",
                 fontsize=13, fontweight="bold")
    plt.tight_layout()
    path = fig_dir / "focal_dependency.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved {path.name}")
    return path


# ── 7. Findings ─────────────────────────────────────────────────────────────

def write_findings(G, scores, provider_summary, focal_dep, focal, fig_dir, out_dir):
    """Write findings.md with captions and limitations."""
    # Top 10 by PageRank
    top_pr = sorted(scores.values(), key=lambda x: -x["pagerank"])[:10]
    # Top 10 by degree
    top_deg = sorted(scores.values(), key=lambda x: -x["degree"])[:10]

    # Stats
    deg_arr = np.array([s["degree"] for s in scores.values()])
    pr_arr = np.array([s["pagerank"] for s in scores.values()])

    from collections import Counter
    total_providers_counts = Counter(r["total_providers"] for r in focal_dep)

    lines = [
        "# Findings: Provider importance and dependence",
        "",
        f"**Analysis graph G**: {G.number_of_nodes():,} nodes, {G.number_of_edges():,} edges (single connected component).",
        f"**Focal set F (IT)**: {len(focal):,} ASes.",
        f"**Candidate providers P**: {len(provider_summary)} external ASes with at least one provider-to-customer link to F.",
        "",
        "---",
        "",
        "## Degree distribution",
        "",
        f"- Mean degree: {deg_arr.mean():.1f}",
        f"- Median degree: {np.median(deg_arr):.0f}",
        f"- Max degree: {deg_arr.max():,}",
        f"- Min degree: {deg_arr.min()}",
        "",
        "The degree distribution is highly right-skewed with most nodes having low degree",
        "and a small number of hubs with very high degree. No formal power-law fit is",
        "attempted here; the visual shape is consistent with a heavy-tailed distribution",
        "but this alone does not establish a power law.",
        "",
        f"![Degree distribution](figures/degree_distribution.png)",
        "",
        "---",
        "",
        "## PageRank",
        "",
        f"- Damping factor: {CONFIG['pagerank']['damping']}",
        f"- Teleportation: {CONFIG['pagerank']['teleportation']}",
        f"- Weighted: {CONFIG['pagerank']['weighted']}",
        f"- Sum of PageRank: {pr_arr.sum():.6f}",
        "",
        "### Top 10 nodes by PageRank",
        "",
        "| Rank | ASN | Degree | PageRank |",
        "|------|-----|--------|----------|",
    ]
    for i, s in enumerate(top_pr, 1):
        lines.append(f"| {i} | AS{s['asn']} | {s['degree']:,} | {s['pagerank']:.6f} |")

    lines += [
        "",
        "### Top 10 nodes by degree",
        "",
        "| Rank | ASN | Degree | PageRank |",
        "|------|-----|--------|----------|",
    ]
    for i, s in enumerate(top_deg, 1):
        lines.append(f"| {i} | AS{s['asn']} | {s['degree']:,} | {s['pagerank']:.6f} |")

    lines += [
        "",
        "---",
        "",
        "## Provider rankings",
        "",
        "### Top 15 external providers by focal customer count",
        "",
        "| Rank | ASN | Organization | Country | Focal customers | Coverage | Degree | PageRank |",
        "|------|-----|-------------|---------|-----------------|----------|--------|----------|",
    ]
    for i, r in enumerate(provider_summary[:15], 1):
        lines.append(
            f"| {i} | AS{r['asn']} | {r['org_name'][:40]} | {r['country']} "
            f"| {r['focal_customer_count']} | {r['focal_customer_fraction']*100:.1f}% "
            f"| {r['degree']:,} | {r['pagerank']:.6f} |"
        )

    lines += [
        "",
        "Coverage percentages can overlap (an Italian AS may have multiple external providers).",
        "",
        "PageRank and focal customer count measure different aspects: PageRank reflects",
        "topological centrality in the full undirected graph, while focal customer count",
        "is a direct measure of how many Italian ASes list this provider. The two rankings",
        "need not agree.",
        "",
        f"![Provider rankings](figures/provider_rankings.png)",
        "",
        f"![Provider coverage](figures/provider_coverage.png)",
        "",
        "---",
        "",
        "## Focal AS dependency",
        "",
        f"- Focal ASes with 0 observed providers: {total_providers_counts.get(0, 0)}",
        f"- Focal ASes with exactly 1 provider: {total_providers_counts.get(1, 0)}",
        f"- Focal ASes with 2+ providers: {sum(v for k,v in total_providers_counts.items() if k>=2)}",
        "",
        "These counts reflect *observed* direct provider relationships only.",
        "Peer connectivity, private peering, and incomplete BGP visibility mean that",
        "a single observed provider does not imply a single point of failure.",
        "",
        f"![Focal dependency](figures/focal_dependency.png)",
        "",
        "---",
        "",
        "## Limitations",
        "",
        "- CAIDA relationships are inferred from BGP data and are incomplete.",
        "- Registration country does not identify physical equipment location.",
        "- Links carry no bandwidth/traffic information; undirected paths may be",
        "  unusable under real routing policies.",
        "- PageRank is computed on the undirected topology; it measures structural",
        "  centrality, not traffic importance.",
        "- Provider counts per focal AS reflect observed relationships only;",
        "  missing observations may undercount actual providers.",
        "",
    ]

    path = out_dir / "findings.md"
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"  Saved findings.md")
    return path


# ── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    t0 = time.perf_counter()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    FIG_DIR.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print("PHASE 3: Provider importance and dependence (Student A)")
    print("=" * 60)

    # ── Load ─────────────────────────────────────────────────────
    print("\n[1/6] Loading data package...")
    edges, analysis_asns, focal, external, nodes_meta, relationships = load_package()
    print(f"  Edges: {len(edges):,}")
    print(f"  G nodes: {len(analysis_asns):,}, F: {len(focal):,}, E: {len(external):,}")

    # ── Build graph ──────────────────────────────────────────────
    print("\n[2/6] Building graph G...")
    G = build_graph(edges, analysis_asns)
    print(f"  G: {G.number_of_nodes():,} nodes, {G.number_of_edges():,} edges")
    assert nx.is_connected(G), "G is not connected!"

    # ── Degree + PageRank ────────────────────────────────────────
    print("\n[3/6] Computing degree and PageRank...")
    damping = CONFIG["pagerank"]["damping"]
    scores = compute_scores(G, damping)

    # Top 5 by PageRank
    top5_pr = sorted(scores.values(), key=lambda x: -x["pagerank"])[:5]
    print("  Top 5 by PageRank:")
    for s in top5_pr:
        meta = nodes_meta.get(s["asn"], {})
        name = meta.get("org_name", meta.get("asn_name", "?"))
        print(f"    AS{s['asn']} ({name}): deg={s['degree']:,}, pr={s['pagerank']:.6f}")

    # Save node_scores.csv
    scores_path = OUT_DIR / "node_scores.csv"
    with open(scores_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["asn", "degree", "pagerank"])
        w.writeheader()
        for asn in sorted(scores):
            w.writerow(scores[asn])
    print(f"  Saved node_scores.csv ({len(scores):,} rows)")

    # ── Provider summary ─────────────────────────────────────────
    print("\n[4/6] Computing provider summary...")
    provider_summary = compute_provider_summary(relationships, focal, external, scores, nodes_meta)
    print(f"  Candidate providers |P|: {len(provider_summary)}")

    prov_path = OUT_DIR / "provider_summary.csv"
    with open(prov_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=[
            "asn", "org_name", "country", "focal_customer_count",
            "focal_customer_fraction", "degree", "pagerank"])
        w.writeheader()
        w.writerows(provider_summary)
    print(f"  Saved provider_summary.csv")

    # ── Focal dependency ─────────────────────────────────────────
    print("\n[5/6] Computing focal AS dependency...")
    focal_dep = compute_focal_dependency(relationships, focal, external, nodes_meta)

    dep_path = OUT_DIR / "focal_dependency.csv"
    with open(dep_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=[
            "asn", "asn_name", "org_name", "total_providers",
            "domestic_providers", "external_providers", "unknown_providers"])
        w.writeheader()
        w.writerows(focal_dep)
    print(f"  Saved focal_dependency.csv ({len(focal_dep)} rows)")

    # ── Figures ──────────────────────────────────────────────────
    print("\n[6/6] Generating figures...")
    plot_degree_distribution(G, FIG_DIR)
    plot_provider_rankings(provider_summary, FIG_DIR)
    plot_provider_coverage_bar(provider_summary, FIG_DIR)
    plot_focal_dependency_distribution(focal_dep, FIG_DIR)

    # ── Findings ─────────────────────────────────────────────────
    write_findings(G, scores, provider_summary, focal_dep, focal, FIG_DIR, OUT_DIR)

    # ── Manifest ─────────────────────────────────────────────────
    elapsed = time.perf_counter() - t0

    input_hashes = {}
    for name in ["edges.csv", "analysis_asns.csv", "focal_asns.csv", "external_asns.csv",
                  "nodes.csv", "relationships.csv"]:
        input_hashes[name] = sha256_file(DATA_DIR / name)

    output_hashes = {}
    for name in ["node_scores.csv", "provider_summary.csv", "focal_dependency.csv", "findings.md"]:
        output_hashes[name] = sha256_file(OUT_DIR / name)

    manifest = {
        "schema_version": "1.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "runtime_seconds": round(elapsed, 2),
        "config": {
            "snapshot": CONFIG["snapshot"],
            "focal_country": CONFIG["focal_country"],
            "pagerank": CONFIG["pagerank"],
        },
        "input_hashes": input_hashes,
        "output_hashes": output_hashes,
        "library_versions": {
            "networkx": nx.__version__,
            "numpy": np.__version__,
        },
        "counts": {
            "graph_nodes": G.number_of_nodes(),
            "graph_edges": G.number_of_edges(),
            "focal_asns": len(focal),
            "external_asns": len(external),
            "candidate_providers": len(provider_summary),
        },
    }

    manifest_path = OUT_DIR / "run_manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    # ── Done ─────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("ANALYSIS COMPLETE")
    print("=" * 60)
    print(f"  Output: {OUT_DIR}")
    print(f"  Runtime: {elapsed:.1f}s")


if __name__ == "__main__":
    main()
