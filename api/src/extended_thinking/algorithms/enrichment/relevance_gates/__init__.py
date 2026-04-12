"""Enrichment relevance-gate plugins (ADR 011 v2).

MVP gate (lands in Phase B.3): embedding_cosine — fast, zero external
calls beyond the local vectorizer. llm_judge lands in Phase C; it
wires through et_write_rationale so its verdicts become Rationale
nodes citing both the user concept and the candidate KnowledgeNode.
"""
