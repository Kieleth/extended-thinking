# Storage Layer Split

Two stores, one boundary:

- **Vector store (ChromaDB)**: semantic retrieval over raw chunks. Similarity search, no graph.
- **Graph store (Kuzu)**: typed concepts and bitemporal edges. Cypher queries, structural walks.

`StorageLayer.default()` wires both. Algorithms read from the graph store
via `AlgorithmContext.kg`; wisdom synthesis optionally pulls from vectors.

SQLite ConceptStore is legacy. Do not add new writes to it.
