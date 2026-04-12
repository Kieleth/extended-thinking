# ADR 004: Configurable AI Models per Tier

**Status:** Accepted
**Date:** 2026-04-11
**Depends on:** ADR 001 (Pluggable Memory)

## Context

Extraction and wisdom generation had hardcoded models: Haiku 4.5 for extraction, Opus 4.6 for synthesis. This made sense as defaults but locked out legitimate alternatives: users running Sonnet for quality, GPT-4o / GPT-5 when they have OpenAI keys, local models via ollama, or cheaper models during development.

Hardcoding also violated the OSS contract — contributors and users should be able to tune the system without editing source.

## Decision

Two LLM tiers, each independently configurable via environment variables:

| Tier | Default | Purpose | Call frequency |
|------|---------|---------|----------------|
| Extraction | `claude-haiku-4-5-20251001` | Concept extraction per batch | Many (one per ~20 chunks) |
| Wisdom | `claude-opus-4-6` | Cross-cluster insight synthesis | Rare (one per `et_insight`) |

Each tier has two env vars:

- `ET_EXTRACTION_PROVIDER` / `ET_WISDOM_PROVIDER` — which AI provider to use (`anthropic`, `openai`, or empty for auto-detect)
- `ET_EXTRACTION_MODEL` / `ET_WISDOM_MODEL` — which model within that provider

Defaults assume Anthropic is available. If you have OpenAI keys instead, override:

```env
ET_EXTRACTION_PROVIDER=openai
ET_EXTRACTION_MODEL=gpt-4o-mini
ET_WISDOM_PROVIDER=openai
ET_WISDOM_MODEL=gpt-4o
```

## Why two tiers, not one

Extraction is a high-volume, pattern-matching task. It runs many times per sync (one call per ~20 chunks). Quality-per-dollar matters more than raw intelligence. Haiku-tier models fit.

Wisdom is a low-volume, reasoning-heavy task. One call per insight, operating on a structured prompt with graph context, source paths, and cross-cluster requirements. Wrong model choice here produces shallow or hallucinated insights. Opus-tier models fit.

Collapsing to one tier either overpays for extraction (Opus on every batch) or underpowers wisdom (Haiku trying to reason across clusters with source grounding). The split is economic and architectural.

Supersession detection (ADR 002) rides the extraction tier — it's produced by the same prompt and doesn't justify its own tier.

## Where models are resolved

Extractor (`processing/extractor.py`) and pipeline (`processing/pipeline_v2.py::generate_wisdom`) both read `settings.extraction_model` and `settings.wisdom_model` at call time. No caller needs to pass a model name — it's automatic from config. Callers CAN override via function kwargs if they need to (e.g., unit tests pinning a specific model).

The provider registry (`ai/registry.py`) handles the "which provider" resolution. Setting `ET_EXTRACTION_PROVIDER=openai` routes extraction through `OpenAIProvider`, and the `extraction_model` name is passed to whichever provider is selected.

## Not a plugin (yet)

Models could be modeled as plugins per ADR 003, but models aren't algorithms — they're tools that algorithms use. Treating them as config rather than plugins keeps the surface lean. If someone later writes a true "extraction strategy" plugin (e.g., one that uses a fine-tuned classifier instead of an LLM), that plugin can define its own model handling.

## What `et_stats` shows

```
🤖 Models:
   Extraction: claude-haiku-4-5-20251001
   Wisdom:     claude-opus-4-6
```

Makes the active config visible at a glance. Users editing `.env` can immediately verify the change took effect.

## Consequences

**Positive:**
- Anyone with an OpenAI key can run ET without an Anthropic account.
- Cheaper extraction for experimentation (`ET_EXTRACTION_MODEL=claude-haiku-3-5-20240620`, older and cheaper).
- Wisdom can be upgraded to stronger models as they release, without code changes.
- Contributors who prefer one provider don't need workarounds.

**Negative:**
- Prompts are tuned for Claude's JSON compliance. Swapping to a weaker model (some open-source LLMs) may produce more parse failures.
- Different providers have different capabilities (prompt caching, streaming, tool use). Our extraction/wisdom code uses only the lowest-common-denominator API (`complete()`), so we don't exploit provider-specific features.
- Users who set only one provider's API key will see errors if they name a model from a different provider. Error messages need to be clear.

## What we don't do

- Fallback cascades ("try Opus, if unavailable try Sonnet"). One call, one model. Fallbacks hide problems.
- Auto model selection based on task complexity. Users choose their tradeoffs explicitly.
- Per-tenant model config (we're single-user / single-process). Add if we ever deploy multi-tenant.

## References

- ADR 001: Pluggable Memory — same philosophy (config, not code)
- OpenAI and Anthropic model docs (pricing, context windows)
