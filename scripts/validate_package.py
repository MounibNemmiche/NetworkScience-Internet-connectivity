import csv
from pathlib import Path

d = Path("data/processed/v1")

# Load nodes
nodes = {}
missing_meta = 0
for row in csv.DictReader(open(d / "nodes.csv", encoding="utf-8")):
    asn = int(row["asn"])
    nodes[asn] = row
    if row["metadata_missing"] == "True":
        missing_meta += 1

# Load edges and verify all endpoints are in nodes
edge_asns = set()
for row in csv.DictReader(open(d / "edges.csv", encoding="utf-8")):
    edge_asns.add(int(row["source_asn"]))
    edge_asns.add(int(row["target_asn"]))

orphan_edges = edge_asns - set(nodes.keys())
orphan_nodes = set(nodes.keys()) - edge_asns

print(f"Nodes in nodes.csv: {len(nodes)}")
print(f"Unique ASNs in edges: {len(edge_asns)}")
ok = chr(9989)
fail = chr(10060)
print(f"Edge endpoints missing from nodes.csv: {len(orphan_edges)} {ok if len(orphan_edges)==0 else fail}")
print(f"Nodes with no edges: {len(orphan_nodes)} {ok if len(orphan_nodes)==0 else fail}")
print(f"Nodes with missing metadata: {missing_meta}")

# Country distribution of focal set
focal_countries = {}
for row in csv.DictReader(open(d / "focal_asns.csv", encoding="utf-8")):
    asn = int(row["asn"])
    c = nodes[asn]["country"]
    focal_countries[c] = focal_countries.get(c, 0) + 1
all_IT = list(focal_countries.keys()) == ["IT"]
print(f"Focal set countries: {focal_countries} {ok if all_IT else fail}")

# Verify P: each candidate provider serves at least one focal customer
rels_p2c = {}
for row in csv.DictReader(open(d / "relationships.csv", encoding="utf-8")):
    if row["relationship"] == "provider_to_customer":
        src = int(row["source_asn"])
        tgt = int(row["target_asn"])
        if src not in rels_p2c:
            rels_p2c[src] = set()
        rels_p2c[src].add(tgt)

focal_set = set()
for row in csv.DictReader(open(d / "focal_asns.csv", encoding="utf-8")):
    focal_set.add(int(row["asn"]))
ext_set = set()
for row in csv.DictReader(open(d / "external_asns.csv", encoding="utf-8")):
    ext_set.add(int(row["asn"]))

p_count = 0
for provider, customers in rels_p2c.items():
    if provider in ext_set:
        focal_customers = customers & focal_set
        if len(focal_customers) > 0:
            p_count += 1

match_107 = p_count == 107
print(f"Verified candidate providers from relationships: {p_count} (expected 107) {ok if match_107 else fail}")
