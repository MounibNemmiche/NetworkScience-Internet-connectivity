**Internet connectivity: project proposal and collaboration plan**

Student A and Student B | 30 October 2026

**The idea**

Study how networks registered in Italy depend on external Internet providers, then simulate what happens when selected providers are removed from the network. The project combines provider importance, community detection and network robustness around one question:

*How concentrated are the observed external transit relationships of ASes registered in Italy, and how does their topological connectivity change under targeted versus random provider removals?*

An Autonomous System (AS) is an independently administered network, identified by an ASN. Each node represents an AS; links represent inferred relationships between networks.

Working title: **Internet resilience and dependence on transit providers: a case study of Italy.**

This is a proposal for a two-person Network Science project. Italy and the role allocation below are starting choices to agree on together. The implementation and results are still to be produced.

**Why this is a practical choice**

- **The data is available to download.** Two CAIDA files provide the network relationships and organization metadata. Together they are about 6 MB compressed; the access check needed no account, API key, payment or scraping.
- **The question fits the course.** It gives a purpose to PageRank, communities and failure simulations, with results that can be explained through a few figures.
- **The scope is bounded.** Start with one country and one snapshot. Both students can develop separate analyses once the shared data package is ready.

Data access is straightforward. The parts that need care are interpreting the graph, implementing the community scores and keeping global graph computations manageable.

**1. The two downloads**

| Dataset | Direct download | What it provides |
|---|---|---|
| CAIDA AS Relationships, serial 2, 1 October 2026 | [20261001.as-rel2.txt.bz2](https://publicdata.caida.org/datasets/as-relationships/serial-2/20261001.as-rel2.txt.bz2) | AS links and inferred provider/customer or peer relationships; about 2 MB compressed |
| CAIDA AS to Organization Mapping, 1 October 2026 | [20261001.as-org2info.txt.gz](https://publicdata.caida.org/datasets/as-organizations/20261001.as-org2info.txt.gz) | AS names, organizations and country metadata; about 4 MB compressed |

Save both files under `data/raw/`, keeping their original names. Python's standard `bz2` and `gzip` modules can read the compressed, pipe-delimited text directly.

Both files passed a complete download and decompression check on 30 October 2026. That check read them in memory; the project still needs its own saved copies. Exact sizes and hashes are included below.

**2. Proposed division of work**

The roles can be chosen at kickoff. Each student owns an analytical part from implementation through interpretation. The workload does not need to be exactly 50/50; the priority is being able to make progress independently.

| Responsibility | Student A: data and provider importance | Student B: communities and robustness |
|---|---|---|
| Main question | Which providers are prominent, and which serve many Italian ASes? | How is the network organized, and how does connectivity change when providers fail? |
| Preparation | Download, clean and join the data; prepare the shared graph package | Develop scoring and simulation code on a small example graph while the real package is prepared |
| Analysis | Degree, PageRank, direct provider dependence and provider rankings | Louvain, community quality measures, random and targeted provider removals |
| Outputs | Data summary, provider tables, figures and interpretation | Community tables, failure curves, figures and interpretation |
| Final contribution | Assemble the presentation and explain the data/importance results | Supply and explain the communities/robustness section |

Both students agree on the research question and graph definition, review each other's results, and prepare the final presentation together.

**How independent work will work:** Student A prepares one shared data package. After both students verify it, each analysis reads that package and writes to its own results folder. Student B computes the removal ranking directly from the shared data, so the simulations do not wait for Student A's PageRank results. Student A's analysis does not require Student B's results either.

**3. Milestones and handoffs**

| Milestone | Student A | Student B | Ready when |
|---|---|---|---|
| Agree on the scope | Choose roles, country and shared settings together | Same | The graph definition and file format are agreed |
| Prepare the shared input | Build and validate package v1 | Prototype communities and failures on a small graph | Both can load the same real graph and identify the focal ASes |
| Run the analyses | Produce importance/dependency results | Produce community/robustness results | Each has checked tables, figures and short findings |
| Review and combine | Review failure assumptions; assemble the narrative | Review data coverage and provider interpretation; supply the second section | Figures answer the same research question with consistent definitions |
| Rehearse and submit | Explain the data and importance analysis | Explain communities and robustness | Both understand the overall result and can reproduce their part |

Set dates together around the current course deadline. Use a short check-in when the shared package is ready and another when the first figures exist. Discuss changes to shared inputs before making them.

**4. What the finished project should contain**

- Reproducible code, with instructions for downloading the data and running each analysis independently.
- A data-summary table and degree distribution.
- Provider rankings and direct-dependency figures.
- A community view and a table of all four course quality measures.
- Targeted-versus-random failure curves, including variation across random trials.
- Slides explaining the question, methods, results, limitations and each student's contribution.

A compact presentation can follow that same order. The current introduction describes code and slides as project outputs and asks contributors to focus on different aspects: [Part 0, PDF page 10](<../Lecture Slides 26-27/Part0_intro.pdf#page=10>) and [page 25](<../Lecture Slides 26-27/Part0_intro.pdf#page=25>). Confirm the submission date and presentation duration from the current course announcement.

Keep the core to one snapshot and one country. A second country or community algorithm can be added after the core is complete. Historical outage attribution, traffic volumes, detailed routing-policy simulation, exact global betweenness and an interactive dashboard are outside the initial scope.

**5. What the results can tell us**

This project measures resilience in an observed graph. CAIDA relationships are inferred and incomplete; registration country does not identify the physical location of all equipment. Links do not measure bandwidth or traffic, and an undirected path may be unusable under real routing policies. Removing an ASN is therefore a topological scenario, not a prediction of how many people would lose Internet access. See [CAIDA's relationship background](https://www.caida.org/catalog/datasets/as-relationships/).

A small effect is still a useful result. It may reflect redundancy, the optimistic reachability model, or both. Provider customer counts and PageRank measure different aspects of importance and need not produce the same ranking.

**First step:** download both files, build the shared package and check Italy's coverage. The technical specification below keeps the two analyses compatible.

---

**Implementation reference**

**A. Shared graph and data package**

Preserve the original relationship layer. The documented `provider|customer|-1` direction must remain intact; `0` denotes a peer link. The tested file had four fields per relationship row, including a source field. Preserve available evidence sources, check unexpected formats and review contradictory assignments. The organization file has separate AS and organization sections: join `ASN -> org_id -> organization/country` using their format headers. Follow the [relationship README](https://publicdata.caida.org/datasets/as-relationships/serial-2/README.txt) and [organization README](https://publicdata.caida.org/datasets/as-organizations/README.txt).

Use these definitions throughout:

| Item | Agreed definition |
|---|---|
| Global topology | Simple, undirected, unweighted graph of all observed relationship endpoints; both peer and provider/customer links contribute edges. Merge duplicate pairs and remove self-loops, recording counts. Keep relationship types separately. Do not add unrelated registry ASes as isolated observed nodes. |
| Analysis graph G | Largest connected component of the global topology; break any size tie by the smallest ASN in the component. Report exclusions, including excluded Italian ASes. Main claims refer to G. |
| Focal set F | Nodes in G with joined country `IT`. |
| External reference set E | Nodes in G with a valid, known country other than `IT`. Unknown-country nodes remain available as transit nodes but belong to neither F nor E. |
| Candidate providers P | Nodes in E with at least one observed direct provider-to-customer relationship to F. Peer links do not establish provider status. |

Freeze F and E at baseline. Require both to be nonempty and at least two candidate providers for the random/targeted comparison. If coverage fails, revisit the country together before the main runs. Keep the wider G for all experiments; filtering to Italian nodes or immediate neighbours would remove possible alternative paths. Smaller views are fine for illustrations with clearly labelled filters.

Student A prepares `data/processed/v1/` with:

| File | Contents |
|---|---|
| `nodes.csv` | `asn`, `asn_name`, `org_id`, `org_name`, `country`, `metadata_missing` for every global topology node |
| `relationships.csv` | `source_asn`, `target_asn`, `relationship`, `evidence_sources`; retain provider-to-customer direction and canonically order peer pairs |
| `edges.csv` | `source_asn`, `target_asn`; unique undirected pairs, smaller ASN first, no self-loops |
| `analysis_asns.csv` | ASNs belonging to G |
| `focal_asns.csv` | Baseline F |
| `external_asns.csv` | Baseline E |
| `manifest.json` | Input URLs, snapshot/retrieval dates, raw and processed hashes, schema version, counts, exclusions, missing metadata and cleaning decisions |

Use integer ASNs, UTF-8 text, uppercase country codes and empty fields for missing text. Invalid or unresolved country codes remain unknown. Normalize relationship values to `provider_to_customer` and `peer`; aggregate duplicate observations while retaining source labels. Both analyses construct G from the same `edges.csv` and `analysis_asns.csv`.

**B. Student A: provider importance and dependence**

Calculate degree and PageRank on G. For each candidate provider, count distinct focal customers and report `direct customer coverage = number of its focal customers / size of F`. These percentages can overlap because an AS can have several providers; they need not sum to 100%.

For each focal AS, also count observed direct providers, distinguishing domestic, external and unknown-country providers. Describe zero, one and multiple observed providers without assuming these counts alone establish resilience: peer connectivity and missing observations matter.

Compare the PageRank and direct-customer rankings, with named examples where metadata is available. State that PageRank measures importance in the undirected topology. Produce a degree distribution without inferring a power law from its appearance alone.

Save in `results/importance/`:

- `node_scores.csv`: `asn`, `degree`, `pagerank` for G.
- `provider_summary.csv`: `asn`, `org_name`, `country`, `focal_customer_count`, `focal_customer_fraction`, `degree`, `pagerank` for P.
- Figures, `findings.md` with captions and limitations, and `run_manifest.json` recording settings, input hashes, versions and runtime.

**C. Student B: communities and failure experiments**

Run Louvain with the shared seeds and score each partition. Use the partition with the highest modularity for the main visualization, breaking ties by seed. This checks seed sensitivity; it is not a comparison of different algorithms. Describe community sizes and where F and P fall, using a readable selected or community-level view.

Evaluate all four course measures on the same graph and partitions:

| Measure | Implementation convention |
|---|---|
| Modularity | Unweighted G, resolution 1. |
| Normalized cut | Let A be adjacency divided by its total sum, P its aggregation between communities, and p_i the row sums of P. Use `sum_i (p_i - P_ii) / p_i`, a sum rather than an average. Here P denotes the community probability matrix, distinct from the provider set above. |
| Course unsupervised NMI | Mutual information between community labels at the two ends of an edge, using that joint distribution P, divided by `H(p)`. This is not label-agreement NMI between partitions. Zero-probability terms contribute zero; zero entropy makes the normalized score undefined. |
| InfoMap measure | Two-level map-equation description length of each existing partition using the lecture's PageRank/random-walk setup. Record damping, teleportation convention and logarithm base. Do not substitute a separately optimized partition's score. Use sparse edge aggregates and analytical teleportation terms. |

Student B computes any PageRank needed for InfoMap locally using the shared settings. The course definitions are linked in section F below.

For failure experiments, derive P directly from the shared package. Fix the targeted order at baseline: descending distinct focal customer count, then descending degree in G, then ascending ASN. Remove the first k providers and their incident edges for each configured k.

For random trials, uniformly shuffle the same P with each fixed seed. Remove the first k providers in that order, reusing the order across k so failures accumulate. Start every trial from the same baseline graph and recompute connected components after each deletion.

| Output | Definition |
|---|---|
| Focal ASes retaining an external connection | Fraction of baseline F connected to at least one surviving node of baseline E. |
| Focal-to-external reachable pairs | Reachable pairs in `F × E` after removal, divided by `size of F × size of E` at baseline. Deleted external nodes contribute zero. |
| Largest component fraction | Largest remaining component size divided by the baseline number of nodes in G. |

Count reachable pairs by summing `focal nodes in component × surviving external nodes in component` over components. No all-pairs shortest paths are needed. All three baseline values equal 1 for connected G with nonempty F and E. Keep denominators fixed; if no external nodes survive, both focal measures equal 0.

Plot the targeted curve against the random median and 10th-90th percentile band. The band shows variation across simulations, not a confidence interval for actual outages. Remove ASNs individually; multiple ASNs owned by one organization are not automatically removed together.

Save in `results/resilience/`:

- `community_assignments.csv`: `seed`, `asn`, `community_id`.
- `community_quality.csv`: seed, community count, all four scores and conventions.
- `removal_order.csv`: strategy, trial/seed, position and ASN.
- `robustness_runs.csv`: strategy, trial/seed, removal count and the three connectivity measures.
- Figures, `findings.md` and `run_manifest.json`.

These runs require no files from `results/importance/`. Comparing the final results with Student A's PageRank ranking is optional.

**D. Settings, tools and working files**

| Parameter | Proposed starting value |
|---|---|
| Snapshot / focal country | `20261001` / `IT` |
| PageRank damping | `0.85`, uniform teleportation, unweighted graph |
| Louvain resolution / seeds | `1.0` / `11, 22, 33` |
| Maximum removed providers | `min(20, size of P)` |
| Random trials / seeds | 30 / integers 1001 through 1030 |
| Targeted ordering | Fixed at baseline as defined above |

Use Python, pandas, NetworkX, NumPy/SciPy and Matplotlib. Pin tested library versions and record convergence settings and runtime. Gephi is optional for illustrations; numerical results should be reproducible without it.

Proposed layout:

```text
internet-connectivity/
  README.md
  requirements.txt
  config/project.json
  data/raw/
  data/processed/v1/
  scripts/prepare_data.py
  analysis/importance/
  analysis/resilience/
  results/importance/
  results/resilience/
  presentation/
```

Student A owns `scripts/prepare_data.py` and `analysis/importance/`; Student B owns `analysis/resilience/`. Use separate branches or clearly owned folders. Coordinate changes to `config/project.json`, the environment and schema. Keep raw inputs unchanged and package v1 immutable; create v2 and rerun affected outputs if definitions change.

Document three entry points: prepare data, run importance, and run communities/robustness. Both analyses accept the shared configuration, read the prepared package and write only to their own results folder. Neither should import the other student's analysis or require their notebook to run first.

**E. Checks before interpreting results**

- Check hashes, unique ASNs, endpoint membership, metadata joins, duplicates, components and country coverage. Validate provider direction against raw rows and the README, and verify every member of P serves a distinct focal customer.
- Confirm PageRank converges and sums to approximately 1; inspect leading names and degrees for parsing errors.
- Check every partition assigns every node in G exactly once. Compare modularity with the library result. Verify course NMI and InfoMap on a tiny, manually checkable example and retain the calculation.
- Test removals on two small clusters connected through a provider node. Removing that node should separate them. Check baseline values equal 1, cumulative removals never increase the three measures, and random/targeted runs use the same candidate pool and removal counts.
- Confirm seeds reproduce removal orders. Benchmark one Louvain run and one random trial before the full experiment.

Use sparse representations and avoid dense global matrices or exact all-pairs betweenness. If the pilot is too slow, reduce and document the trial/removal budget in the shared configuration. Keep the declared analysis graph intact.

**F. Data records and references**

The download check returned HTTP 200 for both files. Compressed sizes were 2,015,308 bytes for relationships and 4,024,948 bytes for organizations. The relationship file contained 657,444 non-comment records; this is not a cleaned unique-edge count. Organization rows include two record types and must not all be counted as ASes.

SHA-256 hashes of the compressed downloads:

```text
20261001.as-rel2.txt.bz2
8810a457ebdffd85c7e86782a8a76e85a86d623bd52bc52e622a3c4ad5c0d521

20261001.as-org2info.txt.gz
e010e11531e097d02cef583f666f578e7723ec5db58b1c8cb43d4c8e6f416c0d
```

Compare hashes after downloading and investigate differences before combining results. Preserve dataset names, dates, URLs and retrieval dates in the README. CAIDA's [public data terms](https://www.caida.org/about/legal/aua/public_aua/) require citation and reporting publications using the data, including publicly available presentations; use the [publication reporting form](https://www.caida.org/catalog/datasets/publications/report-publication/) when applicable.

| Data reference | Purpose |
|---|---|
| [AS Relationships description](https://www.caida.org/catalog/datasets/as-relationships/) | Interpretation, methodology and citation guidance |
| [Serial 2 README](https://publicdata.caida.org/datasets/as-relationships/serial-2/README.txt) | Format and known data issues |
| [Relationship archive](https://publicdata.caida.org/datasets/as-relationships/serial-2/) | Other dated snapshots |
| [AS to Organization Mapping description](https://www.caida.org/catalog/datasets/as-organizations/) | Metadata methodology and citation guidance |
| [Organization README](https://publicdata.caida.org/datasets/as-organizations/README.txt) | Parsing the AS and organization sections |
| [Organization archive](https://publicdata.caida.org/datasets/as-organizations/) | Other dated mappings |

| Method reference | Purpose |
|---|---|
| [NetworkX PageRank](https://networkx.org/documentation/stable/reference/algorithms/generated/networkx.algorithms.link_analysis.pagerank_alg.pagerank.html) | Parameters, convergence and undirected inputs |
| [NetworkX Louvain](https://networkx.org/documentation/stable/reference/algorithms/generated/networkx.algorithms.community.louvain.louvain_communities.html) | Resolution and seeds |
| [NetworkX modularity](https://networkx.org/documentation/stable/reference/algorithms/generated/networkx.algorithms.community.quality.modularity.html) | Check the modularity calculation |
| [Map Equation resources](https://www.mapequation.org/) and [Infomap documentation](https://www.mapequation.org/infomap/) | Objective and scoring conventions |
| [NetworkX connected components](https://networkx.org/documentation/stable/reference/algorithms/generated/networkx.algorithms.components.connected_components.html) | Component-based connectivity calculations |
| [NetworkX graph exports](https://networkx.org/documentation/stable/reference/readwrite/index.html) and [Gephi](https://gephi.org/) | Optional graph visualization workflow |

Course references use PDF page numbers. Relative links work when the lecture folder is available beside the project folder; the filenames and pages also identify the readings when this Markdown file is shared on its own.

| Course file and pages | Topic |
|---|---|
| [Part0_intro.pdf, page 10](<../Lecture Slides 26-27/Part0_intro.pdf#page=10>) and [page 25](<../Lecture Slides 26-27/Part0_intro.pdf#page=25>) | Outputs and contributor responsibilities |
| [Part1_centrality.pdf, page 53](<../Lecture Slides 26-27/Part1_centrality.pdf#page=53>) and [page 116](<../Lecture Slides 26-27/Part1_centrality.pdf#page=116>) | Degree distributions and PageRank |
| [Part2_community.pdf, page 17](<../Lecture Slides 26-27/Part2_community.pdf#page=17>) and [page 48](<../Lecture Slides 26-27/Part2_community.pdf#page=48>) | Community probability matrix, modularity and normalized cut |
| [Part2_community.pdf, page 66](<../Lecture Slides 26-27/Part2_community.pdf#page=66>) and [page 73](<../Lecture Slides 26-27/Part2_community.pdf#page=73>) | InfoMap and course unsupervised NMI |
| [Part2_community.pdf, page 133](<../Lecture Slides 26-27/Part2_community.pdf#page=133>) | Community-analysis expectations |
| [Part3_layout.pdf, page 31](<../Lecture Slides 26-27/Part3_layout.pdf#page=31>) | Readable network views |
| [Part5_homophily.pdf, pages 30-33](<../Lecture Slides 26-27/Part5_homophily.pdf#page=30>) | Robustness and targeted removals |
