# Internet connectivity and resilience

This Network Science project studies how ASes registered in Italy depend on
external providers and how their observed topological connectivity changes
under targeted and random provider removals.

The shared snapshot is 1 October 2026. The analysis uses a simple, undirected,
unweighted graph with 80,966 AS nodes and 657,444 edges. The supplied baseline
contains 1,087 Italian ASes and 107 candidate external providers.

## Project components

- [Data preparation](scripts/prepare_data.py) and
  [package validation](scripts/validate_package.py).
- [Provider importance and dependence](analysis/importance/run_importance.py).
- [Importance findings and data summary](results/importance/findings.md).
- [Community scores, Louvain and provider-removal experiments](analysis/resilience/README.md).
- [Community results](results/resilience/community_findings.md) and
  [robustness results](results/resilience/robustness_findings.md).
- [Gephi screenshots, captions and editable project](results/resilience/gephi/README.md).
- [Numerical validation](results/resilience/final_validation.md).

## Run the complete workflows

### 1. Data Preparation and Package Validation (Student A)

The raw CAIDA files are processed to build the immutable shared package:

```powershell
python scripts/prepare_data.py
python scripts/validate_package.py
```

### 2. Provider Importance and Dependence Analysis (Student A)

Computes degree, PageRank (with stationary residual convergence verification), local Italian transit customer counts, multihoming statistics, and exports figures and findings:

```powershell
python -m pip install -r requirements.txt
python analysis/importance/run_importance.py
```

Outputs are saved under `results/importance/`:
- `data_summary.csv` — overall network and metadata summary metrics.
- `node_scores.csv` — global degree and PageRank for all 80,966 nodes in G.
- `provider_summary.csv` — candidate external providers with customer counts, fractions, and intra-set ranks.
- `focal_dependency.csv` — observed upstream transit provider counts per Italian AS.
- `findings.md` — detailed comparative discussion, case studies, and limitations.
- `figures/` — degree distribution (log-log & CCDF), ranking scatters, rank comparison bar chart, coverage, and dependency histograms.

### 3. Community Detection and Resilience Analysis (Student B)

Use Python 3.12 and run from the repository root:

```powershell
python -m venv .venv
.venv/Scripts/python -m pip install -r analysis/resilience/requirements.lock.txt
.venv/Scripts/python -m pytest analysis/resilience/tests -q
.venv/Scripts/python -m analysis.resilience.run_resilience --hash-policy newline-compatible
```

On Linux or macOS, replace `.venv/Scripts/python` with `.venv/bin/python`.
The lock records the tested Windows environment; the shorter
`analysis/resilience/requirements.txt` lists the primary dependencies.
The root requirements file belongs to the existing importance workflow.

The default configuration is [config/project.json](config/project.json).
The prepared inputs are under `data/processed/v1/`. Newline compatibility accepts
only a proven LF/CRLF difference from a declared input hash. Source and result
bytes under the resilience directories are preserved by `.gitattributes`.

## Recorded results

Louvain was evaluated with seeds 11, 22 and 33 at resolution 1. The selected
seed 22 has 98 communities and modularity 0.516034. All partitions were scored
with modularity, summed normalized cut, the course edge-end NMI and the recorded
teleportation map equation.

At 20 targeted provider removals, 96.23% of Italian ASes retain a connection to
at least one external AS, compared with a 99.26% random median. The retained
Italian-to-external pair fraction is 90.08% versus 98.07%; the largest-component
fraction is 93.79% versus 98.83%. Random summaries use 30 seeded permutations.

The main scenario preserves the supplied country labels. A separate
EU-excluded sensitivity keeps the topology unchanged and uses 106 candidate
providers. Results and denominators for the two scenarios remain separate.

These are inferred topological paths, not measured traffic, routing-policy
validity, bandwidth or estimates of people disconnected. Country registration
does not establish equipment location. Full assumptions, seed comparisons and
limitations accompany the result tables and figures.

## Data sources

- [CAIDA AS Relationships, serial 2](https://publicdata.caida.org/datasets/as-relationships/serial-2/20261001.as-rel2.txt.bz2).
- [CAIDA AS-to-Organization Mapping](https://publicdata.caida.org/datasets/as-organizations/20261001.as-org2info.txt.gz).

The shared configuration and package manifest record the source URLs, snapshot,
input hashes and preparation settings. Derived outputs under
`results/resilience/` include configuration, source and output hashes for
reproducibility.
