# Choosing a KG Store

Candidates weighed: SQLite, Kuzu, Neo4j.

- **SQLite**: familiar, easy to ship, but bitemporal queries become JOINs and the graph part is synthetic.
- **Kuzu**: embedded graph DB with Cypher, temporal properties on edges, column-store backend. Our pick.
- **Neo4j**: server-required, heavy for a CLI tool's default setup.

Decision: **Kuzu** for the knowledge graph, ChromaDB stays for vectors.
