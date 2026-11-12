# Student B: communities and robustness

The complete numerical workflow has been run and validated on the recorded v1 scenario. The country-label sensitivity is kept separate; shared sign-off is not inferred.

The baseline contains 80,966 ASes, 657,444 undirected edges, 1,087 Italian ASes and 107 candidate external providers.

Louvain seed 22 was selected by highest modularity: 98 communities, Q=0.516034. Every partition was evaluated with the four course measures.

The failure analysis contains 651 primary metric rows: one targeted sequence and 30 random sequences, each including baseline and 20 cumulative removal steps. The actual budget and all seeds are recorded in the manifest.

## Read the results

- [Community findings and figures](community_findings.md).
- [Provider-removal findings and sensitivity](robustness_findings.md).
- [Graph validation](graph_validation.md), where available.
- [Metric verification](metric_checks/verification.md), where available.

## Reproduce

From the repository root, after installing `analysis/resilience/requirements.txt`:

```powershell
python -m analysis.resilience.run_resilience --hash-policy newline-compatible
```

To retain existing community outputs, use `--reuse-communities`; their baseline, configuration, source and output hashes must match. This option never bypasses validation of changed or incomplete community results.

`input_contract.json` records the exact input bytes, LF-normalized hashes, baseline sets and provider order. Newline compatibility accepts only demonstrated LF/CRLF equivalence. Neither runner modifies the shared package or Student A's code/results.

## Interpretation and limits

The main run preserves the supplied classification. The alternate run excludes non-assigned country labels from E/P and keeps those nodes as transit nodes. Both support inspection before the students agree on a shared convention.

The provider-removal order is derived from direct Italian-customer counts, baseline degree and ASN. It is independent of saved importance rankings.

Results describe inferred topological connectivity, not traffic, bandwidth or people losing Internet access. One snapshot and one focal country are analysed.
