# Community-score verification

All 7 retained small-graph examples passed their scalar checks and the independent NetworkX modularity comparison.

Conventions: unweighted graph; modularity resolution 1; summed Ncut; edge-end community NMI; two-level map equation of the supplied partition; damping 0.85; uniform, recorded teleportation; base-2 logarithms. Isolates use uniform dangling redistribution.

| Case | Modularity | Ncut | Course NMI | InfoMap, bits/step |
|---|---:|---:|---:|---:|
| path4_paired | 0.166666666667 | 0.666666666667 | 0.0817041659455 | 2.40202921596 |
| cycle4_adjacent | 0 | 1 | 0 | 2.87744375108 |
| cycle4_alternating | -0.5 | 2 | 1 | 3.84789163274 |
| cycle4_one_community | 0 | 0 | undefined | 2 |
| cycle4_singletons | -0.25 | 4 | 0.5 | 3.88698307939 |
| disconnected_edges | 0.5 | 0 | 1 | 1.46743432886 |
| edge_and_isolate | 0 | undefined | undefined | 1.40592932403 |

## Retained manual calculations

Here H denotes base-2 Shannon entropy.

- **path4_paired**: `r=(10/57,37/114,37/114,10/57); q_i=10/57; L=20/57+(77/57)H(20/77,20/77,37/77)`.
- **cycle4_adjacent**: `r_u=1/4; q_i=1/4; L=1/2+(3/2)log2(3)`.
- **cycle4_alternating**: `r_u=1/4; q_i=37/80; L=37/40+(77/40)H(37/77,20/77,20/77)`.
- **cycle4_one_community**: `H(community)=0, so NMI undefined; q=0; L=H(r)=2`.
- **cycle4_singletons**: `r_u=1/4; q_i=77/320; L=(77/80)*2+(157/80)H(77/157,80/157)`.
- **disconnected_edges**: `No crossing edges; recorded teleport gives q_i=3/80; L=3/40+(43/40)H(3/43,20/43,20/43)`.
- **edge_and_isolate**: `Uniform dangling redistribution: r=(20/43,20/43,3/43); q_i=2/43; zero edge-volume community makes Ncut undefined`.

The alternating cycle partition has NMI 1 and negative modularity. Course NMI captures dependence, including bipartite structure; it is not an assortativity score. Disconnected components still have nonzero InfoMap exit rates because teleportation is recorded.

The pytest suite supplies a separate dense-matrix oracle: a stationary linear solve, direct transition exit probabilities and direct codebook entropies. This command retains the scalar examples; it does not invoke pytest.

## Full-graph check

Passed on 80,966 nodes and 657,444 edges using one fixed community containing every node. This verifies execution at the project graph size; it is not a community-discovery result.

Modularity and Ncut are zero; NMI is undefined because community entropy is zero. InfoMap is 13.813940202 bits/step, equal to the independently summed node-visit entropy.

PageRank: 112 iterations; total L1 residual 6.88012e-13; tolerance 1e-12; L1 error bound 4.58675e-12.

The stationary vector was computed locally from G. No Student A importance result was read. The input country-label and checksum-portability warnings remain in the JSON record and do not change this topology-only check.

## Sources and reproduction

Definitions: `Part2_community.pdf`, PDF pp. 17, 48, 66 and 72-73. The lecture's InfoMap exit expression counts teleportation. Its NMI formula gives zero under independence; the contradictory explanatory sentence is not used.

Run from the repository root:

```powershell
python -m analysis.resilience.verify_community_metrics --check-real-graph
```

The JSON record contains expected and observed values, walk diagnostics, source hashes and environment versions. `checked_scores.csv` contains the small-graph comparisons.
