"""Generated schema artifacts (LinkML → Pydantic + Kuzu DDL/types).

Codegen outputs for the malleus-rooted extended-thinking ontology. Do
not hand-edit — run `make schema` at the repo root to regenerate.

  - `models`       Pydantic types (gen-pydantic)
  - `kuzu_ddl`     Kuzu CREATE NODE/REL TABLE statements
  - `kuzu_types`   Pydantic ↔ Kuzu row bridge (to_kuzu_row / from_kuzu_row)

Private module (leading underscore): external consumers should import
the classes via `extended_thinking.models` / `extended_thinking.storage`
surfaces, not directly from here.
"""
