# Communities and resilience

This workflow validates the shared AS graph, evaluates four course community
scores, compares seeded Louvain partitions and simulates cumulative provider
removals. It reads no saved importance rankings and changes no shared data.

## Complete workflow

Use Python 3.12 and run from the repository root:

```powershell
python -m pip install -r analysis/resilience/requirements.lock.txt
python -m pytest analysis/resilience/tests -q
python -m analysis.resilience.run_resilience --hash-policy newline-compatible
```

The lock records a tested Windows environment. `requirements.txt` lists the
primary runtime and test dependencies; install Python separately.

The entry point validates inputs, runs communities, runs primary and country-label
sensitivity experiments, and records findings, an input contract and a combined
manifest under `results/resilience/`.

`--reuse-communities` and `--reuse-robustness` retain existing results only after
their baseline, effective configuration, source and output hashes pass verification.
With both flags, validation leaves the original reports and manifests unchanged.
Changed or incomplete results are rejected.

### Configuration compatibility and original run history

`student-b-effective-config-v1` parses JSON, removes only `pagerank.tol` and
`pagerank.max_iter`, fills the omitted Louvain threshold with its runtime default
`1e-7`, and serializes sorted keys as compact ASCII-escaped UTF-8 JSON. Damping,
resolution and threshold use floating-point representation. Whitespace, object
key order and LF/CRLF endings do not affect this fingerprint. Array order,
integer-only settings and all other keys remain significant, including unknown
future settings. This conservative policy retains damping, teleportation,
weighting, Louvain controls, robustness controls and input declarations.

New run manifests retain the original byte-level `config_sha256` and add a
`configuration_compatibility` snapshot and fingerprint. The baseline fingerprint
still binds all six processed inputs through their LF-normalized hashes; source
and output hashes still require exact bytes. Saved-result verification and reuse
call the same compatibility validator.

Legacy runs with a different configuration byte hash require explicit recovery
of the original configuration. To reconcile them without rerunning experiments:

```powershell
python -m analysis.resilience.reconcile_results --original-config <recovered-config.json> --source-review <reviewed-sources.json> --recovery "Describe the Git revision and exact LF/CRLF variant recovered"
python -m analysis.resilience.run_resilience --hash-policy newline-compatible --reuse-communities --reuse-robustness
```

The recovered bytes must match every historical configuration hash. The review
JSON maps each changed orchestration wrapper to `original_sha256`,
`compatible_sha256` and a nonempty `reason`. Only `run_communities.py`,
`run_robustness.py` and `run_resilience.py` can receive reviewed metadata-only
exceptions; numerical source changes cannot be reconciled. The command verifies
the effective settings, baseline and all saved hashes before creating
`compatibility_reconciliation.json`. It refuses to replace an existing sidecar.

The sidecar binds exact original manifest bytes, recovered configuration bytes,
reviewed source transitions and the compatibility validator's hash. It records
`experiments_recomputed: false`; original timestamps, hashes, runtimes and results
stay intact. Later genuine runs use their own canonical records and exact source
hashes independently of this historical sidecar.

## Individual commands

```powershell
python -m analysis.resilience.validate_graph --hash-policy newline-compatible
python -m analysis.resilience.verify_community_metrics
python -m analysis.resilience.run_communities --hash-policy newline-compatible
python -m analysis.resilience.run_robustness --hash-policy newline-compatible
python -m analysis.resilience.export_gephi
```

The default configuration is `config/project.json`. Paths in it resolve from the
repository root. `--config` supports an alternative project configuration;
runner output directories must remain outside raw and processed inputs.

Saved graph reports use repository-relative paths. Runtime safety checks retain
the original absolute input paths in memory. External package locations are
reported by directory name only.

## Input validation

The loader checks schemas, types, hashes, unique node sets, canonical edges,
endpoint coverage, connected components, country memberships and the consistency
of relationship records with the topology. Unknown-country nodes remain in the
graph, and Namibia's `NA` label is preserved as text.

The main candidate pool contains external providers with direct Italian customers.
The targeted order sorts by distinct Italian-customer count descending, baseline
degree descending and ASN ascending. It is fixed before the first removal.

Exact hashes are the default. `newline-compatible` accepts only verified LF/CRLF
equivalence, preserving the original shared package. `.gitattributes` disables
automatic line-ending conversion for the resilience source and saved artifacts
so their recorded hashes survive a Git checkout.

## Community analysis

The graph is simple, undirected and unweighted. Louvain runs use the configured
seeds 11, 22 and 33, resolution 1 and explicit `weight=None`. The highest
modularity selects the partition; exact ties prefer the smaller seed.

All four scores evaluate each supplied partition on the full graph:

- Modularity at the configured resolution.
- Normalized cut summed over all communities.
- The course edge-end community NMI; this is not partition-label agreement.
- The map equation with recorded uniform teleportation and damping 0.85.

The map equation scores Louvain partitions; it does not optimize an Infomap
partition. Its stationary probabilities are computed independently of saved
importance results, with an explicit residual check.

[COMMUNITY_METRICS.md](COMMUNITY_METRICS.md) gives formulas, conventions,
undefined cases and hand-checked examples. ARI and variation of information
measure partition agreement across seeds separately from the course NMI.

## Provider-removal experiments

The configured experiment compares one targeted sequence with 30 seeded random
permutations for k=0..20. Every trial starts from the same baseline, and each
step removes a provider ASN with its incident edges.

For each remaining connected component C, let f_C and e_C be its Italian and
external node counts. Using fixed baseline denominators, the exported measures
are the fraction of Italian ASes with any external connection, the fraction of
reachable Italian-to-external pairs and the largest-component fraction.

The primary scenario preserves supplied country classifications. A separate
scenario excludes non-assigned country labels from external/provider sets while
retaining those nodes as transit. Its candidate pool, order and denominators
are recorded independently.

Bounds, baseline values, monotonicity and complete removal-order consistency
are checked before export. Random bands are the 10th-90th percentiles of
simulation outcomes, not confidence intervals for real outages.

## Outputs and verification

- `community_assignments.csv`, `community_quality.csv` and community summaries.
- `candidate_providers.csv`, `removal_order.csv`, complete runs and summaries.
- `sensitivity/eu_excluded/` with the alternate scenario's tables.
- `figures/` with PNG/PDF quantitative plots.
- [Gephi views](../../results/resilience/gephi/README.md) with six native captures.
- `run_manifest.json`, subrun manifests and `input_contract.json` with lineage.

After saving a unit-test run, verify the recorded exports with:

```powershell
python -m pytest analysis/resilience/tests -q --junitxml=results/resilience/tests.junit.xml
python -m analysis.resilience.verify_results
```

To preserve historical test and validation reports, save the new JUnit file
separately and pass `--junitxml <new-tests.junit.xml>` and
`--report-dir <new-audit-directory>` to `verify_results`. This audit checks saved
partitions and component samples without rerunning Louvain or removal experiments.

The verifier checks output/source hashes, community coverage and quotient counts,
all saved removal orders, denominators and percentile bindings. It independently
recalculates NetworkX modularity and selected component snapshots. The report
states which checks cover all rows and which use sampled independent calculations.

The fresh-environment reproduction record covers the original eleven numerical
data tables. Runtime values and presentation metadata are not byte-level scientific
comparisons. Current tests additionally cover portable path reporting.
