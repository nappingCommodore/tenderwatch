"""Build & analyse the procurement relationship graph.

Reads the graph views from the SQLite DB (READ-ONLY), constructs a NetworkX
graph of Vendor <-> Department award flows (weighted by TRUSTED value, so the
Phase-1 corrupt cells never distort it), and:

  * ranks vendors / departments by weighted degree (money strength),
  * finds related-party clusters (vendor records sharing a PAN / GSTIN),
  * runs Louvain community detection to surface "capture rings" (vendors +
    departments that transact tightly together),
  * writes a GEXF for visual exploration in Gephi + CSVs of the findings.

Usage:  python scripts/graph_export.py [path/to/bihar_eproc.db]
"""

from __future__ import annotations

import csv
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

import networkx as nx

try:
    import community as community_louvain  # python-louvain
except ImportError:  # pragma: no cover
    community_louvain = None


def connect(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


# Gephi rejects zero-weight edges, so zero-value awards get a tiny positive
# weight while the TRUE value is preserved in the ``value_rs`` edge attribute.
ZERO_VALUE_WEIGHT = 1.0


def build_vendor_dept_graph(con: sqlite3.Connection) -> nx.Graph:
    """Undirected Vendor<->Department graph.

    Edge attributes:
      * ``value_rs``  -- true trusted Rs value of the awards (may be 0)
      * ``weight``    -- value_rs, or a tiny positive epsilon when the value is
                          0, so Gephi keeps the edge (it drops weight==0 edges)
      * ``n_awards``  -- number of awards on the edge
    """
    g = nx.Graph()
    zero_value = 0
    for r in con.execute("SELECT vendor_id, dept_id, n_awards, total_value "
                         "FROM v_graph_vendor_dept"):
        value = float(r["total_value"] or 0.0)
        if value <= 0:
            zero_value += 1
        v, d = f"V:{r['vendor_id']}", f"D:{r['dept_id']}"
        g.add_node(v, ntype="vendor", label=f"vendor {r['vendor_id']}")
        g.add_node(d, ntype="department", label=f"dept {r['dept_id']}")
        g.add_edge(v, d, weight=value if value > 0 else ZERO_VALUE_WEIGHT,
                   value_rs=value, n_awards=int(r["n_awards"] or 0))
    if zero_value:
        print(f"(kept {zero_value} zero-value edges with weight={ZERO_VALUE_WEIGHT})")
    for r in con.execute("SELECT vendor_id, name FROM dim_vendor"):
        n = f"V:{r['vendor_id']}"
        if n in g and r["name"]:
            g.nodes[n]["label"] = r["name"]
    for r in con.execute("SELECT department_id, name FROM dim_department"):
        n = f"D:{r['department_id']}"
        if n in g and r["name"]:
            g.nodes[n]["label"] = r["name"]
    return g


def related_party_clusters(con: sqlite3.Connection) -> list[dict]:
    """Connected components of vendor records sharing a PAN / GSTIN."""
    rp = nx.Graph()
    labels: dict[int, str] = {}
    for r in con.execute("SELECT vendor_id_1, name_1, vendor_id_2, name_2, "
                         "shared_pan, shared_gstin FROM v_vendor_related_party"):
        labels[r["vendor_id_1"]] = r["name_1"] or str(r["vendor_id_1"])
        labels[r["vendor_id_2"]] = r["name_2"] or str(r["vendor_id_2"])
        rp.add_edge(r["vendor_id_1"], r["vendor_id_2"],
                    key=r["shared_pan"] or r["shared_gstin"])
    clusters = []
    for comp in nx.connected_components(rp):
        if len(comp) < 2:
            continue
        keys = {rp.edges[e].get("key") for e in rp.subgraph(comp).edges}
        clusters.append({
            "vendor_ids": sorted(comp),
            "names": sorted({labels.get(v, str(v)) for v in comp}),
            "shared_keys": sorted(k for k in keys if k),
        })
    clusters.sort(key=lambda c: len(c["vendor_ids"]), reverse=True)
    return clusters


def louvain_communities(g: nx.Graph) -> dict[str, int]:
    if community_louvain is None:
        return {}
    return community_louvain.best_partition(g, weight="weight", random_state=42)


def main(argv: list[str]) -> int:
    db_path = argv[1] if len(argv) > 1 else "data/bihar_eproc.db"
    out = Path("data/graph")
    out.mkdir(parents=True, exist_ok=True)
    con = connect(db_path)

    g = build_vendor_dept_graph(con)
    print(f"graph: {g.number_of_nodes():,} nodes / {g.number_of_edges():,} edges")

    # --- weighted degree by TRUE Rs (value_rs) so the epsilon weights on
    #     zero-value edges never inflate the money ranking --------------------
    strength: dict[str, float] = defaultdict(float)
    for u, v, data in g.edges(data=True):
        val = float(data.get("value_rs", 0.0))
        strength[u] += val
        strength[v] += val
    top_vendors = sorted(
        ((n, s) for n, s in strength.items() if g.nodes[n]["ntype"] == "vendor"),
        key=lambda x: x[1], reverse=True)[:15]
    print("\nTop vendors by trusted Rs won (weighted degree):")
    for n, s in top_vendors:
        print(f"  {g.nodes[n]['label'][:34]:<34} Rs {s/1e7:,.1f} Cr  deg={g.degree(n)}")

    # --- related-party clusters --------------------------------------------
    clusters = related_party_clusters(con)
    print(f"\nrelated-party clusters (shared PAN/GSTIN): {len(clusters)}")
    for c in clusters[:8]:
        print(f"  {len(c['vendor_ids'])} ids | {', '.join(c['names'])[:70]} "
              f"| {', '.join(c['shared_keys'])[:30]}")

    # --- Louvain communities (capture rings) --------------------------------
    partition = louvain_communities(g)
    comm_out = out / "communities.csv"
    if partition:
        members: dict[int, list[str]] = defaultdict(list)
        for node, cid in partition.items():
            members[cid].append(node)
        stats = []
        for cid, nodes in members.items():
            vend = [n for n in nodes if g.nodes[n]["ntype"] == "vendor"]
            dept = [n for n in nodes if g.nodes[n]["ntype"] == "department"]
            value = sum(strength[n] for n in vend)
            stats.append((cid, len(vend), len(dept), value, nodes))
        stats.sort(key=lambda x: x[3], reverse=True)
        print(f"\nLouvain communities: {len(stats)}  (top capture rings by Rs):")
        for cid, nv, nd, value, _ in stats[:8]:
            print(f"  community {cid}: {nv} vendors / {nd} depts / Rs {value/1e7:,.1f} Cr")
        with comm_out.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["community_id", "node_id", "node_type", "label"])
            for cid, nodes in members.items():
                for n in nodes:
                    g.nodes[n]["community"] = cid
                    w.writerow([cid, n, g.nodes[n]["ntype"], g.nodes[n]["label"]])
    else:
        print("\n(Louvain unavailable: `pip install python-louvain`)")

    # --- exports ------------------------------------------------------------
    for n in g.nodes:
        g.nodes[n]["strength"] = float(strength.get(n, 0.0))
    gexf = out / "vendor_dept.gexf"
    nx.write_gexf(g, gexf)

    rp_csv = out / "related_party_clusters.csv"
    with rp_csv.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["cluster_no", "vendor_ids", "names", "shared_keys"])
        for i, c in enumerate(clusters, 1):
            w.writerow([i, "|".join(map(str, c["vendor_ids"])),
                        " | ".join(c["names"]), " | ".join(c["shared_keys"])])

    print(f"\nwrote: {gexf}")
    print(f"wrote: {rp_csv}")
    if partition:
        print(f"wrote: {comm_out}")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
