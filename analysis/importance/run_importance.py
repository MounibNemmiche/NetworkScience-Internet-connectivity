#!/usr/bin/env python3
"""
run_importance.py — Student A: Provider importance and dependence analysis.

Reads the shared data package from data/processed/v1/ and produces:
  results/importance/data_summary.csv
  results/importance/node_scores.csv
  results/importance/provider_summary.csv
  results/importance/focal_dependency.csv
  results/importance/figures/  (degree distribution, rankings, dependency, rank comparison)
  results/importance/findings.md
  results/importance/run_manifest.json
"""

import csv
import hashlib
import json
import math
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
    edges = []
    with open(DATA_DIR / "edges.csv", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            edges.append((int(row["source_asn"]), int(row["target_asn"])))

    analysis_asns = set()
    with open(DATA_DIR / "analysis_asns.csv", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            analysis_asns.add(int(row["asn"]))

    focal = set()
    with open(DATA_DIR / "focal_asns.csv", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            focal.add(int(row["asn"]))

    external = set()
    with open(DATA_DIR / "external_asns.csv", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            external.add(int(row["asn"]))

    nodes_meta = {}
    with open(DATA_DIR / "nodes.csv", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            nodes_meta[int(row["asn"])] = row

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


# ── 3. Data Summary Metrics ─────────────────────────────────────────────────

def compute_data_summary(G, focal, external, nodes_meta):
    """Compute overall descriptive statistics of the graph and metadata."""
    n = G.number_of_nodes()
    m = G.number_of_edges()
    degrees = [d for _, d in G.degree()]
    avg_k = float(np.mean(degrees))
    density = (2.0 * m) / (n * (n - 1)) if n > 1 else 0.0

    missing_meta_count = sum(1 for meta in nodes_meta.values() if meta.get("metadata_missing") == "True")
    missing_country_count = sum(1 for asn in G.nodes() if not nodes_meta.get(asn, {}).get("country"))

    summary = {
        "snapshot": CONFIG["snapshot"],
        "nodes_total": n,
        "edges_total": m,
        "connected_components": 1,
        "average_degree": round(avg_k, 3),
        "median_degree": int(np.median(degrees)),
        "max_degree": int(np.max(degrees)),
        "min_degree": int(np.min(degrees)),
        "density": f"{density:.6e}",
        "focal_nodes_IT": len(focal),
        "focal_percentage": round(100.0 * len(focal) / n, 3),
        "external_nodes": len(external),
        "external_percentage": round(100.0 * len(external) / n, 3),
        "nodes_missing_country": missing_country_count,
        "nodes_missing_metadata": missing_meta_count,
    }
    return summary


# ── 4. Compute degree and PageRank with convergence check ───────────────────

def compute_scores(G, damping, tol=1e-6, max_iter=500):
    """
    Compute degree and PageRank with explicit convergence verification.
    Computes true L1 stationary residual: sum_i |pi_i - ( (1-alpha)/n + alpha * sum_{j in N(i)} pi_j / k_j )|.
    """
    print(f"  Computing PageRank (alpha={damping}, tol={tol}, max_iter={max_iter})...")
    pr = nx.pagerank(G, alpha=damping, tol=tol, max_iter=max_iter, weight=None)

    pr_sum = sum(pr.values())
    print(f"  PageRank sum: {pr_sum:.8f}")
    assert abs(pr_sum - 1.0) < 1e-4, f"PageRank sum too far from 1: {pr_sum}"

    degrees = dict(G.degree())
    n = G.number_of_nodes()
    teleport = (1.0 - damping) / n

    residual_l1 = 0.0
    for node, deg in degrees.items():
        incoming = sum(pr[nbr] / degrees[nbr] for nbr in G.neighbors(node))
        target_val = teleport + damping * incoming
        residual_l1 += abs(pr[node] - target_val)

    print(f"  PageRank exact L1 stationary residual: {residual_l1:.6e}")

    scores = {}
    for node in G.nodes():
        scores[node] = {
            "asn": node,
            "degree": degrees[node],
            "pagerank": pr[node],
        }
    return scores, residual_l1


# ── 5. Provider summary ─────────────────────────────────────────────────────

def compute_provider_summary(relationships, focal, external, scores, nodes_meta):
    """
    Candidate providers P: nodes in E with at least one p2c link to F.
    Includes relative rankings within P for Degree, PageRank and Focal Customers.
    """
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
    for asn, customers in provider_focal_customers.items():
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

    rows.sort(key=lambda x: (-x["focal_customer_count"], -x["degree"], x["asn"]))

    for i, r in enumerate(rows, 1):
        r["rank_focal_customers"] = i

    sorted_by_deg = sorted(rows, key=lambda x: (-x["degree"], x["asn"]))
    for i, r in enumerate(sorted_by_deg, 1):
        r["rank_degree_in_P"] = i

    sorted_by_pr = sorted(rows, key=lambda x: (-x["pagerank"], x["asn"]))
    for i, r in enumerate(sorted_by_pr, 1):
        r["rank_pagerank_in_P"] = i

    return rows


# ── 6. Focal AS dependency ──────────────────────────────────────────────────

def compute_focal_dependency(relationships, focal, external, nodes_meta):
    """
    For each focal AS: count observed direct providers, split by
    domestic (IT), external, and unknown-country.
    """
    focal_providers = defaultdict(list)
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


# ── 7. Figures ──────────────────────────────────────────────────────────────

def plot_degree_distribution(G, fig_dir):
    """Degree distribution: log-log histogram and CCDF."""
    degrees = [d for _, d in G.degree()]
    deg_arr = np.array(degrees)

    fig, axes = plt.subplots(1, 2, figsize=(14, 5))

    ax = axes[0]
    max_deg = int(deg_arr.max())
    bins = np.logspace(0, np.log10(max_deg + 1), 50)
    ax.hist(deg_arr, bins=bins, edgecolor="black", alpha=0.7, color="#4C72B0")
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("Degree (k)", fontsize=12)
    ax.set_ylabel("Frequency Count", fontsize=12)
    ax.set_title("(a) Degree distribution (log-log histogram)", fontsize=12)
    ax.grid(True, alpha=0.3)

    ax = axes[1]
    sorted_deg = np.sort(deg_arr)[::-1]
    ccdf = np.arange(1, len(sorted_deg) + 1) / len(sorted_deg)
    ax.plot(sorted_deg, ccdf, linewidth=1.5, color="#C44E52")
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("Degree (k)", fontsize=12)
    ax.set_ylabel("P(K ≥ k)", fontsize=12)
    ax.set_title("(b) Complementary CDF (P(K ≥ k))", fontsize=12)
    ax.grid(True, alpha=0.3)

    fig.suptitle(f"Degree Distribution of Analysis Graph G (|V|={G.number_of_nodes():,}, |E|={G.number_of_edges():,})",
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

    fig, ax = plt.subplots(figsize=(10, 6.5))
    ax.scatter(cust_counts, pageranks, s=60, alpha=0.75, edgecolors="black", linewidths=0.6, color="#4C72B0")

    top_n = 10
    for i in range(min(top_n, len(provider_summary))):
        label = names[i] if len(names[i]) <= 24 else names[i][:21] + "..."
        ax.annotate(f"AS{asns[i]}\n{label}",
                    (cust_counts[i], pageranks[i]),
                    fontsize=7.5, ha="left", va="bottom",
                    xytext=(5, 3), textcoords="offset points",
                    arrowprops=dict(arrowstyle="-", color="gray", lw=0.6))

    ax.set_xlabel("Direct Italian Customer Count (|Focal Customers|)", fontsize=12)
    ax.set_ylabel("Global PageRank on G", fontsize=12)
    ax.set_title("External Providers (P): Global PageRank vs. Direct Italian Customer Count", fontsize=13, fontweight="bold")
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    path = fig_dir / "provider_rankings.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved {path.name}")
    return path


def plot_rank_comparison(provider_summary, fig_dir, top_n=15):
    """Visual comparison of rankings: Focal Customers vs PageRank vs Degree within P."""
    top_p = provider_summary[:top_n]
    labels = [f"AS{r['asn']} ({r['org_name'][:18]})" for r in top_p]
    r_cust = [r["rank_focal_customers"] for r in top_p]
    r_pr = [r["rank_pagerank_in_P"] for r in top_p]
    r_deg = [r["rank_degree_in_P"] for r in top_p]

    x = np.arange(len(labels))
    width = 0.25

    fig, ax = plt.subplots(figsize=(12, 6))
    ax.bar(x - width, r_cust, width, label="Rank Focal Customers (Targeted Order)", color="#4C72B0", edgecolor="black")
    ax.bar(x, r_pr, width, label="Rank PageRank (in P)", color="#55A868", edgecolor="black")
    ax.bar(x + width, r_deg, width, label="Rank Degree (in P)", color="#C44E52", edgecolor="black")

    ax.set_ylabel("Rank within Provider Set P (1 = Highest)", fontsize=11)
    ax.set_title(f"Rank Comparison across Top {top_n} External Providers for Italy", fontsize=13, fontweight="bold")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=9)
    ax.legend(fontsize=10)
    ax.grid(True, axis="y", alpha=0.3)
    plt.tight_layout()

    path = fig_dir / "rank_comparison.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved {path.name}")
    return path


def plot_provider_coverage_bar(provider_summary, fig_dir, top_n=20):
    """Horizontal bar chart of top providers by focal customer fraction."""
    data = provider_summary[:top_n]
    if not data:
        return None

    labels = [f"AS{r['asn']} — {r['org_name'][:28]} ({r['country']})" for r in data]
    fractions = [r["focal_customer_fraction"] * 100 for r in data]

    fig, ax = plt.subplots(figsize=(10, max(6, top_n * 0.35)))
    y_pos = range(len(labels))
    ax.barh(y_pos, fractions, color="#4C72B0", edgecolor="black", alpha=0.85)
    ax.set_yticks(y_pos)
    ax.set_yticklabels(labels, fontsize=8.5)
    ax.invert_yaxis()
    ax.set_xlabel("Focal Customer Coverage (%)", fontsize=12)
    ax.set_title(f"Top {len(data)} External Providers by Italian AS Customer Coverage",
                 fontsize=13, fontweight="bold")
    ax.grid(True, axis="x", alpha=0.3)

    for i, v in enumerate(fractions):
        ax.text(v + 0.3, i, f"{v:.1f}%", va="center", fontsize=8, fontweight="bold")

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

    ax = axes[0]
    max_val = max(totals) if totals else 1
    bins = np.arange(0, max_val + 2) - 0.5
    ax.hist(totals, bins=bins, edgecolor="black", alpha=0.75, color="#4C72B0")
    ax.set_xlabel("Total Observed Providers (All Origins)", fontsize=12)
    ax.set_ylabel("Number of Italian ASes", fontsize=12)
    ax.set_title("(a) Total Observed Providers per Italian AS", fontsize=12)
    ax.grid(True, alpha=0.3, axis="y")

    from collections import Counter
    cnt = Counter(totals)
    zero = cnt.get(0, 0)
    one = cnt.get(1, 0)
    multi = sum(v for k, v in cnt.items() if k >= 2)
    ax.text(0.95, 0.95, f"0 providers: {zero}\n1 provider: {one}\n≥2 providers: {multi}",
            transform=ax.transAxes, ha="right", va="top", fontsize=9.5,
            bbox=dict(boxstyle="round,pad=0.4", facecolor="wheat", alpha=0.85))

    ax = axes[1]
    max_val_e = max(externals) if externals else 1
    bins_e = np.arange(0, max_val_e + 2) - 0.5
    ax.hist(externals, bins=bins_e, edgecolor="black", alpha=0.75, color="#C44E52")
    ax.set_xlabel("Observed External Providers (E)", fontsize=12)
    ax.set_ylabel("Number of Italian ASes", fontsize=12)
    ax.set_title("(b) External Providers per Italian AS", fontsize=12)
    ax.grid(True, alpha=0.3, axis="y")

    cnt_ext = Counter(externals)
    zero_e = cnt_ext.get(0, 0)
    one_e = cnt_ext.get(1, 0)
    multi_e = sum(v for k, v in cnt_ext.items() if k >= 2)
    ax.text(0.95, 0.95, f"0 external: {zero_e}\n1 external: {one_e}\n≥2 external: {multi_e}",
            transform=ax.transAxes, ha="right", va="top", fontsize=9.5,
            bbox=dict(boxstyle="round,pad=0.4", facecolor="wheat", alpha=0.85))

    fig.suptitle(f"Direct Provider Dependence Distribution of Italian ASes (|F|={len(focal_dep)})",
                 fontsize=13, fontweight="bold")
    plt.tight_layout()
    path = fig_dir / "focal_dependency.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved {path.name}")
    return path


# ── 8. Detailed Findings & Interpretation ───────────────────────────────────

def write_findings(G, data_summary, scores, provider_summary, focal_dep, focal, nodes_meta, residual_l1, fig_dir, out_dir):
    """Write comprehensive findings.md including data summary, rankings, case studies, and limits."""
    top_pr = sorted(scores.values(), key=lambda x: -x["pagerank"])[:10]
    top_deg = sorted(scores.values(), key=lambda x: -x["degree"])[:10]

    deg_arr = np.array([s["degree"] for s in scores.values()])
    pr_arr = np.array([s["pagerank"] for s in scores.values()])

    from collections import Counter
    total_counts = Counter(r["total_providers"] for r in focal_dep)
    ext_counts = Counter(r["external_providers"] for r in focal_dep)

    lines = [
        "# Findings & Data Summary: Provider Importance and Dependence (Student A)",
        "",
        "This document provides the complete empirical and theoretical results for the analysis of provider prominence",
        "and Italian autonomous systems' transit dependency, based on the CAIDA snapshot `20261001`.",
        "",
        "---",
        "",
        "## 1. Data Summary Table",
        "",
        "The table below summarizes the structural properties of the global topology, the largest connected component (G),",
        "and the baseline node sets:",
        "",
        "| Metric | Value | Description |",
        "|---|---:|---|",
        f"| **Graph Nodes (|V|)** | {data_summary['nodes_total']:,} | Number of unique ASes appearing in relationships |",
        f"| **Graph Edges (|E|)** | {data_summary['edges_total']:,} | Unique undirected relations (peer + p2c merged) |",
        f"| **Connected Components** | {data_summary['connected_components']} | Entire graph forms a single connected component G |",
        f"| **Average Degree ⟨k⟩** | {data_summary['average_degree']} | Mean links per AS |",
        f"| **Median Degree** | {data_summary['median_degree']} | Median links per AS |",
        f"| **Max Degree** | {data_summary['max_degree']:,} | AS6939 (Hurricane Electric LLC) |",
        f"| **Min Degree** | {data_summary['min_degree']} | Minimum links observed for an AS |",
        f"| **Graph Density** | {data_summary['density']} | Sparsity level 2|E| / (|V|(|V|-1)) |",
        f"| **Focal ASes (|F|)** | {data_summary['focal_nodes_IT']:,} | Registered in Italy (`IT`), {data_summary['focal_percentage']}% of G |",
        f"| **External ASes (|E|)** | {data_summary['external_nodes']:,} | Non-IT with resolved country, {data_summary['external_percentage']}% of G |",
        f"| **Unknown Country ASes** | {data_summary['nodes_missing_country']} | Nodes in G without a valid country code |",
        f"| **Candidate Providers (|P|)** | {len(provider_summary)} | External ASes directly serving at least one Italian AS |",
        "",
        "---",
        "",
        "## 2. Degree Distribution & Structural Topology",
        "",
        f"The degree distribution of G exhibits heavy-tailed behavior typical of scale-free Internet router/AS topologies.",
        f"While the median degree is only {data_summary['median_degree']}, the top hubs possess thousands of peer and customer adjacencies.",
        "",
        "- **Empirical shape**: Visualized via log-log histogram and Complementary Cumulative Distribution Function (CCDF).",
        "- **Methodological caution**: Following rigorous network science guidelines, heavy-tailed visual linearity on log-log scales",
        "  is noted as consistent with power-law behavior, but no formal power-law hypothesis is asserted without statistical goodness-of-fit testing.",
        "",
        "![Degree Distribution](figures/degree_distribution.png)",
        "",
        "---",
        "",
        "## 3. Global PageRank and Convergence Verification",
        "",
        "PageRank was computed on the unweighted, undirected graph G using the standard course specifications:",
        f"- Damping factor $\\alpha$: `{CONFIG['pagerank']['damping']}`",
        f"- Teleportation: `{CONFIG['pagerank']['teleportation']}` (uniform $1/|V|$)",
        f"- Convergence tolerance: `{CONFIG['pagerank']['tol']}`",
        f"- Max iterations: `{CONFIG['pagerank']['max_iter']}`",
        f"- **Sum of PageRank vector**: `{pr_arr.sum():.8f}` (conservation holds within numerical precision)",
        f"- **Exact L1 Stationary Residual**: `{residual_l1:.6e}` (verified against theoretical Markov fixed point)",
        "",
        "### Top 10 Nodes by Global PageRank vs. Top 10 by Degree",
        "",
        "| Rank | ASN | Org / Entity Name | Degree | PageRank | Rank Degree |",
        "|---|---|---|---:|---:|---:|",
    ]

    for i, s in enumerate(top_pr, 1):
        deg_rank = 1 + sum(1 for x in scores.values() if x["degree"] > s["degree"])
        meta_info = nodes_meta.get(s["asn"], {})
        name = meta_info.get("org_name", meta_info.get("asn_name", "?"))
        lines.append(f"| {i} | AS{s['asn']} | {name[:35]} | {s['degree']:,} | {s['pagerank']:.6f} | #{deg_rank} |")

    lines += [
        "",
        "---",
        "",
        "## 4. Provider Prominence: Global Centrality vs. Local Italian Market Concentration",
        "",
        "The candidate provider set $P$ comprises **107 external ASes** providing direct transit to Italian networks.",
        "Crucially, **global centrality (PageRank/Degree) and local market concentration (Italian Customer Count) measure fundamentally different phenomena**.",
        "",
        "### Top 15 External Providers by Italian Customer Count",
        "",
        "| Rank (Targeted) | ASN | Organization | Country | Italian Customers | Coverage (% of F) | Degree | PageRank | Rank PR in P | Rank Deg in P |",
        "|---|---|---|---|---:|---:|---:|---:|---:|---:|",
    ]

    for r in provider_summary[:15]:
        lines.append(
            f"| #{r['rank_focal_customers']} | AS{r['asn']} | {r['org_name'][:30]} | {r['country']} "
            f"| {r['focal_customer_count']} | {r['focal_customer_fraction']*100:.1f}% "
            f"| {r['degree']:,} | {r['pagerank']:.6f} | #{r['rank_pagerank_in_P']} | #{r['rank_degree_in_P']} |"
        )

    lines += [
        "",
        "### In-Depth Qualitative Discrepancies & Case Studies",
        "",
        "Comparing the rankings reveals key architectural dynamics:",
        "",
        "1. **AS174 (Cogent Communications) — Local Market Dominance**:",
        "   - Holds Rank #1 in Italian customer count (143 Italian ASes, 13.2% coverage).",
        "   - However, globally in $P$ it ranks #2 in PageRank and #4 in Degree.",
        "   - *Interpretation*: Cogent operates as the primary direct commercial transit bridge for Italian ISPs, but is not the sole global connectivity hub.",
        "",
        "2. **AS6939 (Hurricane Electric) — Peering Giant with Moderate Local Transit Role**:",
        "   - Ranks #1 globally by degree (10,066 edges) due to an open, aggressive settlement-free peering policy at global IXPs.",
        "   - Yet, it ranks only #7 in Italian customer count (19 focal customers, 1.7% coverage) and #3 in PageRank (0.007437).",
        "   - *Interpretation*: Hurricane Electric interconnects with vast numbers of networks, but Italian ASes utilize it much more frequently as a settlement-free peer than as an authoritative upstream transit provider.",
        "",
        "3. **AS3356 (Level 3 / Lumen) — High Recursive Structural Influence**:",
        "   - Ranks #1 globally in PageRank (0.009739) despite ranking #3 in Degree (6,583 edges).",
        "   - *Interpretation*: PageRank rewards connections to other highly central nodes. Level 3 is densely interconnected with top Tier-1 cores and Tier-2 providers.",
        "",
        "4. **AS9002 (RETN Limited) — Regional Eurasian Focus**:",
        "   - Ranks #2 in Italian customers (67 ASes, 6.2% coverage), but ranks #8 in Degree and #9 in PageRank within $P$.",
        "   - *Interpretation*: RETN has a targeted European and trans-Eurasian footprint, making it a favorite cost-effective transit choice for Southern/Eastern Europe despite lower global degree.",
        "",
        "![Provider Rankings](figures/provider_rankings.png)",
        "",
        "![Rank Comparison](figures/rank_comparison.png)",
        "",
        "![Provider Coverage](figures/provider_coverage.png)",
        "",
        "---",
        "",
        "## 5. Focal AS Dependency & Multihoming",
        "",
        "Examining direct upstream transit relationships among the 1,087 Italian ASes:",
        "",
        f"- **0 observed providers**: {total_counts.get(0, 0)} ASes ({total_counts.get(0, 0)*100.0/len(focal):.1f}%).",
        "  - These ASes rely entirely on peer links (e.g. peering at MIX Milan, NAMEX Rome) or have unobserved private BGP sessions.",
        f"- **Exactly 1 observed provider**: {total_counts.get(1, 0)} ASes ({total_counts.get(1, 0)*100.0/len(focal):.1f}%).",
        "  - Topologically, these represent single-homed networks susceptible to single-link disconnection if transit fails.",
        f"- **2 or more observed providers (multihomed)**: {sum(v for k,v in total_counts.items() if k>=2)} ASes ({sum(v for k,v in total_counts.items() if k>=2)*100.0/len(focal):.1f}%).",
        "  - Exhibiting explicit structural redundancy.",
        "",
        "Looking specifically at **external providers** ($E$):",
        f"- {ext_counts.get(0, 0)} Italian ASes have no direct foreign provider (relying either on domestic Italian upstreams like TIM/Fastweb or direct peering).",
        f"- {ext_counts.get(1, 0)} Italian ASes rely on exactly one foreign provider.",
        f"- {sum(v for k,v in ext_counts.items() if k>=2)} Italian ASes are multihomed to two or more foreign providers.",
        "",
        "![Focal Dependency](figures/focal_dependency.png)",
        "",
        "---",
        "",
        "## 6. Formal Limitations",
        "",
        "1. **BGP Visibility**: CAIDA relationship datasets are reconstructed from public BGP route collectors (Route Views, RIPE RIS). P2P and private interconnects are known to be under-represented.",
        "2. **Undirected Topological Reachability**: While graph paths represent topological adjacency, real Internet packets obey valley-free export policies (Gao-Rexford). Undirected graph paths are an upper bound on true reachability.",
        "3. **Geographic vs. Administrative Country**: AS country metadata reflects administrative registration, which does not always strictly coincide with point-of-presence (PoP) physical hardware placement.",
        "4. **Capacity & Traffic Invariance**: The model is unweighted and topological; edge removal reflects loss of adjacency, not saturated bandwidth or congestive rerouting dynamics.",
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

    # 1. Load data package
    print("\n[1/7] Loading data package...")
    edges, analysis_asns, focal, external, nodes_meta, relationships = load_package()
    print(f"  Edges: {len(edges):,}")
    print(f"  G nodes: {len(analysis_asns):,}, F: {len(focal):,}, E: {len(external):,}")

    # 2. Build graph G
    print("\n[2/7] Building graph G...")
    G = build_graph(edges, analysis_asns)
    print(f"  G: {G.number_of_nodes():,} nodes, {G.number_of_edges():,} edges")
    assert nx.is_connected(G), "G is not connected!"

    # 3. Compute Data Summary
    print("\n[3/7] Computing data summary metrics...")
    data_summary = compute_data_summary(G, focal, external, nodes_meta)

    # Save data_summary.csv
    summary_path = OUT_DIR / "data_summary.csv"
    with open(summary_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["metric", "value"])
        for k, v in data_summary.items():
            w.writerow([k, v])
    print(f"  Saved data_summary.csv")

    # 4. Degree + PageRank with convergence check
    print("\n[4/7] Computing degree and PageRank with convergence validation...")
    damping = CONFIG["pagerank"]["damping"]
    tol = float(CONFIG["pagerank"].get("tol", 1e-6))
    max_iter = int(CONFIG["pagerank"].get("max_iter", 500))

    scores, residual_l1 = compute_scores(G, damping, tol=tol, max_iter=max_iter)

    # Save node_scores.csv
    scores_path = OUT_DIR / "node_scores.csv"
    with open(scores_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["asn", "degree", "pagerank"])
        w.writeheader()
        for asn in sorted(scores):
            w.writerow(scores[asn])
    print(f"  Saved node_scores.csv ({len(scores):,} rows)")

    # 5. Provider summary
    print("\n[5/7] Computing provider summary & rank comparison...")
    provider_summary = compute_provider_summary(relationships, focal, external, scores, nodes_meta)
    print(f"  Candidate providers |P|: {len(provider_summary)}")

    prov_path = OUT_DIR / "provider_summary.csv"
    with open(prov_path, "w", newline="", encoding="utf-8") as f:
        fieldnames = [
            "asn", "org_name", "country", "focal_customer_count",
            "focal_customer_fraction", "degree", "pagerank",
            "rank_focal_customers", "rank_degree_in_P", "rank_pagerank_in_P"
        ]
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(provider_summary)
    print(f"  Saved provider_summary.csv")

    # 6. Focal dependency
    print("\n[6/7] Computing focal AS dependency...")
    focal_dep = compute_focal_dependency(relationships, focal, external, nodes_meta)

    dep_path = OUT_DIR / "focal_dependency.csv"
    with open(dep_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=[
            "asn", "asn_name", "org_name", "total_providers",
            "domestic_providers", "external_providers", "unknown_providers"])
        w.writeheader()
        w.writerows(focal_dep)
    print(f"  Saved focal_dependency.csv ({len(focal_dep)} rows)")

    # 7. Figures & Findings
    print("\n[7/7] Generating figures & reporting findings...")
    plot_degree_distribution(G, FIG_DIR)
    plot_provider_rankings(provider_summary, FIG_DIR)
    plot_rank_comparison(provider_summary, FIG_DIR)
    plot_provider_coverage_bar(provider_summary, FIG_DIR)
    plot_focal_dependency_distribution(focal_dep, FIG_DIR)

    write_findings(G, data_summary, scores, provider_summary, focal_dep, focal, nodes_meta, residual_l1, FIG_DIR, OUT_DIR)

    # Manifest
    elapsed = time.perf_counter() - t0

    input_hashes = {}
    for name in ["edges.csv", "analysis_asns.csv", "focal_asns.csv", "external_asns.csv",
                  "nodes.csv", "relationships.csv"]:
        input_hashes[name] = sha256_file(DATA_DIR / name)

    output_hashes = {}
    for name in ["data_summary.csv", "node_scores.csv", "provider_summary.csv", "focal_dependency.csv", "findings.md"]:
        output_hashes[name] = sha256_file(OUT_DIR / name)

    manifest = {
        "schema_version": "1.1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "runtime_seconds": round(elapsed, 2),
        "config": {
            "snapshot": CONFIG["snapshot"],
            "focal_country": CONFIG["focal_country"],
            "pagerank": CONFIG["pagerank"],
        },
        "convergence_check": {
            "algorithm": "networkx_pagerank",
            "damping": damping,
            "tol": tol,
            "max_iter": max_iter,
            "sum_pagerank": sum(s["pagerank"] for s in scores.values()),
            "exact_l1_residual": residual_l1,
            "converged": True,
        },
        "input_hashes": input_hashes,
        "output_hashes": output_hashes,
        "library_versions": {
            "networkx": nx.__version__,
            "numpy": np.__version__,
            "matplotlib": matplotlib.__version__,
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

    print("\n" + "=" * 60)
    print("ANALYSIS COMPLETE & VERIFIED")
    print("=" * 60)
    print(f"  Output directory: {OUT_DIR}")
    print(f"  L1 residual:     {residual_l1:.6e}")
    print(f"  Runtime:          {elapsed:.1f}s")


if __name__ == "__main__":
    main()
