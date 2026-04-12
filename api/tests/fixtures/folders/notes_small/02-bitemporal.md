# Bitemporal Edges

Every edge in the knowledge graph tracks:

- `t_valid_from`: when the fact became true in the world.
- `t_valid_to`: when the fact stopped being true (NULL = still valid).
- `t_created`: when the system learned the fact (transaction time).

Point-in-time query pattern:

```
MATCH (a)-[r]->(b)
WHERE r.t_valid_from <= $as_of
  AND (r.t_valid_to IS NULL OR r.t_valid_to > $as_of)
```

Reference: Graphiti / Zep (arxiv 2501.13956).
