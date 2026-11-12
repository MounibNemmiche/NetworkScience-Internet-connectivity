# Shared graph loading and validation

Status: **passed_with_warnings**. The supplied baseline passed structural checks.

The graph is available for community development. Final robustness runs still require agreement on shared input conventions; this loader does not certify that agreement.

Snapshot: `20261001`. Focal country: `IT`.
Source commit: `3d0113fd67a00545eb8732d8402e6964e8f12cd2`.
Baseline fingerprint: `abfe3cfdfeae63524c7e5e755cd6e8992783f6108de23186c86de21af6316a80`.

| Quantity | Count |
|---|---:|
| global_topology_nodes | 80,966 |
| global_topology_edges | 657,444 |
| unique_undirected_edges | 657,444 |
| connected_components | 1 |
| lcc_nodes | 80,966 |
| focal_asns_IT | 1,087 |
| external_asns | 79,433 |
| unknown_country_asns | 446 |
| excluded_from_lcc | 0 |
| excluded_focal_asns | 0 |
| candidate_providers | 107 |
| aggregated_relationships | 657,444 |

## Items to resolve

- **unresolved_country_labels**: Non-country labels are preserved in the supplied external set. Agree on their treatment and version any changed package before final robustness runs.
  - `EU`: 18 nodes; ASNs 249, 250, 764, 777, 1121, 1124, 1126, 1234, 1271, 1273, 1342, 1732, 1770, 1835, 1961, 2578, 6168, 19178.
  - Affected candidate providers: 1273.
- **crlf_hash_portability**: These CSVs use CRLF. Git newline conversion can change byte hashes; agree on a portable artifact policy before final shared runs.

## Checks and reproducibility

- CSV schema and types.
- manifest hashes and declared derived counts.
- unique nodes and ASN sets.
- canonical nonduplicate nonloop edges.
- endpoint coverage.
- largest component and tie-break.
- focal/external country membership.
- relationship/topology consistency.
- distinct direct provider customers.
- stable baseline ordering.

Processed-package validation; raw hashes are checked when files are available. Raw relationship direction and organization joins are not re-audited here.

Country values are loaded as text: Namibia's `NA` is preserved. Unknown-country nodes remain in the topology. Supplied focal/external assignments are preserved, including any flagged regional labels.

The JSON report records byte and LF-normalized hashes, the hash policy, configuration, manifest and loader hashes, runtime and environment. The fingerprint identifies the six input CSVs after LF normalization, snapshot and classification policy; it does not claim that two differently ordered CSVs have the same fingerprint.

Environment: Python 3.12.10, NetworkX 3.6.1. Runtime: 10.571 seconds.

`candidate_providers.csv` records the derived candidate pool and baseline ordering. It is an input summary, not a removal experiment.
