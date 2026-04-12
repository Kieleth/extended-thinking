# Security Policy

## Threat Model

Extended-thinking is a local-first synthesis layer over memory systems. It reads the user's Claude Code sessions, markdown folders, and optional memory providers (MemPalace, Mem0, Graphiti). It calls Anthropic and OpenAI APIs for concept extraction and wisdom synthesis.

It is designed to run on a trusted single-user workstation, inside a trusted venv. All data stays local unless the user explicitly connects external providers.

### What's protected

- **Secrets never committed.** API keys live in `~/.config/extended-thinking/secrets.toml` (mode 0600) or environment variables. Credentials in `config.toml` or project config raise `RuntimeError` at load (ADR 012 secrets guard).
- **Cassettes scrub credentials.** VCR cassettes committed under `api/tests/fixtures/cassettes/` have `authorization` and `x-api-key` headers replaced with `REDACTED`.
- **No silent exception swallowing.** `tests/test_invariants.py::test_no_silent_exception_swallowing` AST-scans the codebase; every `except` block must log or re-raise.
- **API key validation at startup.** Requesting an AI provider with no keys configured raises `RuntimeError`, not silent None (`test_invariants.py::test_provider_registry_raises_without_keys`).
- **Concept extraction parser is defensive.** LLM output with invalid categories is dropped; garbage input returns `[]` rather than crashing (`test_invariants.py::test_extraction_parser_{rejects_bad_categories,handles_garbage}`).
- **Wisdom refuses to hallucinate.** When the concept graph doesn't support an insight, the pipeline returns `nothing_new`/`nothing_novel` rather than generating ungrounded claims (product invariant #1, enforced in `pipeline_v2.generate_wisdom`).
- **Provider sandbox.** `ClaudeCodeProvider`, `FolderProvider`, and friends read but never write to their sources. Write-back to providers is disabled to prevent echo loops.
- **Namespace scoping.** Memory-synthesis writes use `namespace="memory"`; programmatic consumers default to `"default"` (ADR 013 C2). No cross-namespace contamination on default queries.

### What's NOT protected

- **Prompt injection.** An adversarial document in your indexed folder could try to manipulate extraction or wisdom output. Extended-thinking does not sanitize inputs beyond the extractor's JSON-structure validation. Treat the concept graph as advisory, not authoritative.
- **External provider API calls.** When you enable a hosted memory provider (Mem0 cloud, Zep), your data leaves your machine. See that provider's security policy.
- **Multi-user deployment.** Not a supported scenario. The single `~/.extended-thinking/` data dir and single secrets file assume one user.
- **Stored data at rest.** Kuzu and ChromaDB files are not encrypted. Filesystem encryption (FileVault, dm-crypt) is your responsibility.
- **LLM output grounding is statistical, not formal.** The grounding check refuses insights the graph cannot support, but "support" is pattern-matching over the concept graph. A sufficiently adversarial prompt could still produce a misleading (but technically grounded) synthesis.

## Reporting Vulnerabilities

Report security issues via [GitHub Security Advisories](https://github.com/Kieleth/extended-thinking/security/advisories).

Do not open public issues for security vulnerabilities.

## Dependency Security

Extended-thinking depends on Anthropic, OpenAI, Kuzu, ChromaDB, FastAPI, and their transitive deps. We do not do our own CVE tracking; we follow upstream. If a CVE affects a pinned minor version, bump the pin in `pyproject.toml` and cut a patch release.
