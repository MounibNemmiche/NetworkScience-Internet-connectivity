# Community scores: definitions and verification

`community_metrics.py` evaluates a supplied hard partition with the four measures
from the course. It does not discover communities, optimize an Infomap partition
or compare partitions against reference labels. Reuse one prepared scorer for
the three Louvain partitions in the next task.

## Use

From the repository root:

```python
import networkx as nx
from analysis.resilience.community_metrics import CommunityScorer, score_partition

G = nx.path_graph(4)
groups = [{0, 1}, {2, 3}]

scorer = CommunityScorer(
    G, damping=0.85, residual_tolerance=1e-12, max_iter=10000
)
scores = scorer.score(groups)
print(scores.modularity, scores.normalized_cut)
print(scores.course_nmi, scores.infomap_bits)
record = scores.to_dict()

# A convenience function prepares a new scorer for a single partition.
same_scores = score_partition(G, groups, damping=0.85)
```

Later code can pass `load_graph(...).graph` directly. The scorer captures the
adjacency at construction; subsequent graph mutations do not alter its scores.
Edge attributes, including `weight`, are ignored. Runtime dependencies are
NetworkX, NumPy and SciPy.

Each graph node must occur exactly once in nonempty community groups. Missing,
unknown, repeated or overlapping nodes are rejected. The input must be a simple,
undirected NetworkX graph with at least one edge. Directed graphs, multigraphs,
self-loops, empty graphs and edgeless graphs raise `CommunityScoreError`.

## Degree-based probability model

Let W be the unweighted adjacency matrix, m the edge count, and C the binary
community-membership matrix. Define:

```text
A = W / (2m)
P = C A C^T
p_i = sum_j P_ij
```

P is the joint distribution of community labels at the two ends of an oriented
edge. Each undirected edge contributes both orientations. Its marginal p is
community degree volume divided by 2m. These probabilities are used for
modularity, normalized cut and the course NMI. They are distinct from the
teleporting random-walk probabilities used for InfoMap.

| Measure | Formula and direction | Course reference |
|---|---|---|
| Modularity | `Q = sum_i (P_ii - p_i^2)`; larger is better; resolution 1 | Part 2, PDF pp. 17 and 48 |
| Normalized cut | `Ncut = sum_i (p_i - P_ii) / p_i`; smaller is better | Part 2, PDF p. 48 |
| Course NMI | `I = sum_ij P_ij log2(P_ij / (p_i p_j))`; `NMI = I / H(p)` | Part 2, PDF pp. 72-73 |

Normalized cut is a sum, not a mean. If any community has zero degree volume,
`normalized_cut` is `None`, with an explanation in `undefined_reasons`.
Zero-probability entropy and mutual-information terms contribute zero. When
H(p) is zero, as for a one-community partition, `course_nmi` is `None` with its
reason. JSON export therefore uses `null` for these undefined values.

The course NMI measures dependence between starting and ending community labels.
It is not a label-agreement NMI between two partitions. Statistical independence
gives zero mutual information; the statement on PDF p. 73 that independence gives
one contradicts its displayed formula. The implementation follows the formula.

A high course NMI need not indicate assortative communities. For an alternating
partition `{0, 2}/{1, 3}` of a four-node cycle, each edge crosses communities.
Knowing the starting label determines the ending label, so NMI is 1 even though
modularity is -0.5 and normalized cut is 2. Interpret the measures together.

## InfoMap: recorded teleportation

The score is the two-level map equation of the same supplied partition. The
implementation follows the exit-rate formula on Part 2, PDF p. 66. That formula
includes teleportation in the recorded flow.

With column-stochastic convention, let `M = W diag(degree)^(-1)` and solve:

```text
r = alpha M r + (1 - alpha) 1/n
```

The default alpha is 0.85, following the shared project settings. The slide leaves
the logarithm base unspecified; this implementation uses base 2 and reports bits
per random-walk step. Neither alpha nor the log base is presented as a separate
course requirement.

For community i, let `r_i` be its total visit probability, `n_i` its node count,
and `z_i` its vector of node visit probabilities. Without dangling nodes:

```text
q_i = alpha * sum_{u in i, v outside i} r_u W_vu / degree(u)
      + (1 - alpha) * r_i * (1 - n_i/n)

f(x) = -sum_j x_j log2(x_j / sum(x))
L = f(q) + sum_i f([q_i, z_i])
```

The second exit-rate term records teleportation that leaves the community.
Teleportation landing inside the same community does not count as an exit.
Zero exit rates contribute zero; for a one-community partition L reduces to the
entropy of the stationary node visits.

The lecture's degree normalization is undefined for isolates. This implementation
supports isolates by declaring a uniform dangling-node redistribution extension:
the link-following probability at a dangling node is distributed uniformly over
all nodes. If `d_i` is stationary mass on dangling nodes in community i, its
additional exit term is `alpha * d_i * (1 - n_i/n)`. This redistribution is also
recorded. All four scores still require the graph to contain at least one edge.

The stationary solver checks the actual total L1 residual
`||T(r) - r||_1 <= residual_tolerance`; the threshold is not multiplied by n.
Failure to satisfy it within `max_iter` raises `CommunityScoreError` rather than
returning an unchecked vector. Damping must satisfy `0 <= alpha < 1`.

Diagnostics are available in `scorer.walk` and `scores.walk`, including the
read-only node-probability mapping, iterations, normalization, L1 residual and
the a posteriori L1 error bound `residual / (1 - alpha)`. `to_dict()` records
compact diagnostics and conventions without duplicating the full node mapping.

## Hand-checkable examples

### Path on four nodes, split into adjacent pairs

For `0-1-2-3` and communities `{0,1}/{2,3}`:

```text
P = [[1/3, 1/6], [1/6, 1/3]]
p = [1/2, 1/2]
Q = 1/6
Ncut = 2/3
NMI = (2/3) log2(4/3) + (1/3) log2(2/3)
    = 0.08170416594551039
```

At alpha 0.85, stationary probabilities are `10/57` at each endpoint and
`37/114` at each middle node. Each community's exit rate is `10/57`.
Each within-community codebook has probabilities `20/77, 37/77, 20/77`, including
its exit symbol. Therefore:

```text
L = 20/57 + (77/57) H(20/77, 37/77, 20/77)
  = 2.4020292159603818 bits per step
```

This irregular graph checks the stationary calculation as well as the scores;
uniform visits or degree-only stationary probabilities would give wrong results
for the declared teleporting walk.

### Two disconnected edges, one community per edge

Take edges `{0,1}` and `{2,3}`, with those pairs as the communities. Visits are
uniform, modularity is 0.5, Ncut is zero and course NMI is 1. There are no crossing
graph edges, but recorded teleportation produces `q_i = 0.0375` for each module
at alpha 0.85. Within-community codebook probabilities are `3/43, 20/43, 20/43`:

```text
L = 0.075 + 1.075 H(3/43, 20/43, 20/43)
  = 1.467434328863306 bits per step
```

Dropping recorded teleportation would give a different score. This example checks
that the chosen convention matches the lecture's exit formula.

## Sparse computation and verification

The scorer stores sparse adjacency and edge/community aggregates. It never
constructs a dense n-by-n transition matrix or a dense k-by-k community matrix.
Preparation uses O(n+m) memory and O(t(n+m)) walk work for t iterations. Each
partition scans O(n+m) node/edge data and uses O(n+m+k) storage, where k is the
number of communities; sparse duplicate aggregation can add sorting work.
The same prepared walk is reused across partitions.

Run the hand-check verification from the repository root:

```powershell
python -m analysis.resilience.verify_community_metrics
```

To include a full-size graph integration check:

```powershell
python -m analysis.resilience.verify_community_metrics --check-real-graph
```

The verification command writes `verification.json`, `verification.md` and
`checked_scores.csv` under `results/resilience/metric_checks/`. Default execution
checks the tiny examples only. The full-size option checks implementation on the
loaded real graph; it does not replace the later Louvain analysis or optimize
partitions. The saved records identify which checks actually ran.

Course source:
[Part2_community.pdf](<../../../../Lecture Slides 26-27/Part2_community.pdf>), PDF
pp. 17, 48, 64-66 and 72-73. Local lecture files live outside the repository;
these page references also identify the material when the repository is shared
without the course folder.
