# ADR 010: Batteries Included for the D+I Layer

**Status:** Accepted
**Date:** 2026-04-12
**Depends on:** ADR 001 (Pluggable Memory)
**Reaffirms and extends:** the MemoryProvider protocol

## Context

ADR 001 positioned extended-thinking as a thinking layer on top of any memory system via the `MemoryProvider` protocol. The original framing emphasized adaptation: "memory is commoditized, plug in your preferred system (MemPalace, Obsidian, Zep, etc.)."

Practice has diverged from that framing in one important way. Most external memory systems have weak ingestion surfaces. When we wrap them, we end up reimplementing chunking, filtering, source metadata, dedup, and content-type classification inside our providers anyway. The built-in providers (`folder`, `claude_code`) have gotten more capable over time — they handle format specifics, apply content filters, and feed the pipeline with clean data.

Meanwhile, the user ecosystem has broadened. Claude Code is one source; ChatGPT exports, Cursor conversations, Copilot chat history, and generic markdown notes are equally valid. Users who don't use MemPalace shouldn't feel they're missing anything.

## Decision

Commit to both sides of the D+I layer explicitly:

**1. Open protocol stays.** `MemoryProvider` remains the public contract. Anyone can write an adapter for a memory system we don't ship, register it via the existing registry mechanism, and it composes with our pipeline without privileged access.

**2. Shipped defaults should be genuinely good.** The built-in providers are not placeholders or reference implementations. They are production-quality ingestors that a user can rely on exclusively. We invest in them as first-class product surface.

In practice: every format a typical user has on disk — Claude Code sessions, markdown notes, ChatGPT exports, Cursor conversations, VSCode Copilot chat, etc. — gets a dedicated built-in provider that handles its quirks. AutoProvider aggregates whatever it finds.

## What "good" means for built-in providers

1. **Format-aware parsing.** Not generic JSON or text extraction — each provider understands its source's conventions (system messages, tool calls, metadata envelopes).

2. **Provenance is rich.** Source paths, timestamps, conversation/project context, tool usage all surface as `MemoryChunk.metadata` so downstream algorithms (provenance tracking, source-type filtering) can reason about them.

3. **Content-type classification.** Each chunk is labeled (conversation, note, spec, code, etc.) so the content filter can do its job and the wisdom prompts know what they're looking at.

4. **Robust to partial data.** Users will have incomplete exports, truncated files, mixed schemas across versions. Providers degrade gracefully — never crash on a single malformed entry.

5. **Idempotent ingestion.** Re-running a sync doesn't duplicate. Chunk IDs are stable across runs.

6. **Documented.** Each provider has a docstring explaining the source format, file locations, and any quirks (e.g., "ChatGPT exports come as a zip; we look for conversations.json inside").

## Non-decisions (deliberately out of scope)

- **No capture layer.** We don't record conversations as they happen. We ingest what tools already store on disk.
- **No provider for commercial cloud services that require API keys to read user data.** Users who want Notion/Linear/etc. ingestion install those provider packages separately; we don't bundle auth-requiring integrations in the core distribution.
- **No preference between built-in providers.** AutoProvider aggregates all detected sources with equal standing. The user's own thinking is wherever it is.

## Implementation approach

Migrate incrementally rather than rebuild. The existing `folder` and `claude_code` providers are already solid. New providers get added one at a time:

1. `providers/chatgpt_export.py` — the most portable standard (users download a zip from chatgpt.com/settings).
2. `providers/cursor.py` — conversations stored locally by the Cursor editor.
3. `providers/copilot_chat.py` — GitHub Copilot Chat history in VSCode.
4. `providers/generic_openai_chat.py` — catch-all for OpenAI-format JSON exports.

Each is a separate ADR-level decision (format details, parsing choices, metadata schema) recorded in its own file when implemented.

## Consequences

**Positive:**
- Honest about the practice: we ARE doing ingestion work, so doing it well is the right bar.
- Wider user base reachable out-of-box. A user with just a ChatGPT export and some markdown notes gets full ET value without setting up a separate memory system.
- The MemoryProvider protocol is reaffirmed as the extension point. Third-party providers remain first-class; our defaults are just fuller.
- Quality bar for built-ins makes reviews clearer ("does this provider meet the six criteria above?").

**Negative:**
- More maintenance surface. Each new provider is a format that changes over time (ChatGPT's export schema has already changed twice since 2023). We commit to keeping them working.
- Temptation to build capture/sync features. We resist (see non-decisions above).

## References

- ADR 001 (Pluggable Memory) — original protocol decision; this ADR reaffirms and extends.
- Upcoming: per-provider ADRs for each new ingestor as they ship.
