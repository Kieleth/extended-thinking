SCHEMA_SRC     = schema/extended_thinking.yaml
# Generated Python artifacts live inside the package so the wheel ships them.
# JSON Schema + TS types also land here as reference outputs; the wheel
# carries them too (see api/pyproject.toml [tool.setuptools.package-data]).
GENERATED_DIR  = api/src/extended_thinking/_schema

.PHONY: schema schema-pydantic schema-json schema-ts schema-kuzu schema-kuzu-ddl schema-kuzu-types schema-check setup dev-api dev-web dev test lint seed clean-schema at at-vcr at-live at-record at-update-snapshots

# ── Schema pipeline ──────────────────────────────────────────────────

schema: schema-pydantic schema-kuzu schema-json schema-ts  ## Generate all schema artifacts
	@echo "Schema artifacts generated in $(GENERATED_DIR)/"

schema-pydantic:  ## LinkML → Pydantic models
	@mkdir -p $(GENERATED_DIR)
	gen-pydantic $(SCHEMA_SRC) --black > $(GENERATED_DIR)/models.py

schema-json:  ## LinkML → JSON Schema
	@mkdir -p $(GENERATED_DIR)
	gen-json-schema $(SCHEMA_SRC) > $(GENERATED_DIR)/schema.json

schema-ts:  ## JSON Schema → TypeScript types
	@mkdir -p $(GENERATED_DIR)
	cd web && npx json2ts ../$(GENERATED_DIR)/schema.json > ../$(GENERATED_DIR)/types.ts 2>/dev/null || echo "TypeScript generation skipped (web not installed yet)"

# ── Kuzu codegen (ADR 013) ────────────────────────────────────────────
# LinkML → Kuzu DDL + typed Python accessors. Pydantic models must be
# generated first because kuzu_types imports from them.

schema-kuzu: schema-pydantic schema-kuzu-ddl schema-kuzu-types  ## LinkML → Kuzu DDL + typed accessors

schema-kuzu-ddl:  ## LinkML → Kuzu DDL (CREATE TABLE statements)
	python scripts/gen_kuzu.py

schema-kuzu-types:  ## LinkML → typed Python accessors (pydantic bridge)
	python scripts/gen_kuzu_types.py

schema-check:  ## Regenerate and fail if committed artifacts are stale
	@$(MAKE) schema-pydantic >/dev/null
	@$(MAKE) schema-kuzu-ddl >/dev/null
	@$(MAKE) schema-kuzu-types >/dev/null
	@$(MAKE) schema-json >/dev/null
	@if ! git diff --exit-code --quiet -- $(GENERATED_DIR); then \
	    echo "ERROR: generated schema artifacts are stale. Run 'make schema' and commit:"; \
	    git diff --stat -- $(GENERATED_DIR); \
	    exit 1; \
	fi
	@echo "schema-check: generated artifacts up to date"

clean-schema:  ## Remove generated schema artifacts
	rm -f $(GENERATED_DIR)/models.py $(GENERATED_DIR)/schema.json \
	      $(GENERATED_DIR)/types.ts $(GENERATED_DIR)/kuzu_ddl.py \
	      $(GENERATED_DIR)/kuzu_types.py

# ── Setup ────────────────────────────────────────────────────────────

setup:  ## Full setup from clean checkout
	python -m venv .venv
	.venv/bin/pip install --upgrade pip
	.venv/bin/pip install -e "api/[dev]"
	$(MAKE) schema
	@echo "Setup complete. Activate with 'source .venv/bin/activate', then run 'make dev-api'."

# ── Dev ──────────────────────────────────────────────────────────────

dev-api:  ## Start FastAPI dev server
	cd api && uvicorn extended_thinking.api.main:app --reload --port 8000

dev-web:  ## Start Next.js dev server
	cd web && npm run dev

dev:  ## Instructions for starting both
	@echo "Run in separate terminals:"
	@echo "  make dev-api   # FastAPI on :8000"
	@echo "  make dev-web   # Next.js on :3000"

# ── Test / Lint ──────────────────────────────────────────────────────

test:  ## Run all tests
	cd api && python -m pytest tests/ -v

lint:  ## Lint all code
	cd api && ruff check src/ tests/
	cd web && npm run lint

# ── Acceptance Tests ─────────────────────────────────────────────────

at:  ## Run acceptance suite (fast path, default)
	cd api && python -m pytest tests/acceptance/ -m acceptance

at-vcr:  ## Run acceptance suite with cassette replay
	cd api && python -m pytest tests/acceptance/ -m "acceptance or vcr"

at-live:  ## Run acceptance suite against live LLM API
	cd api && LIVE_API=1 python -m pytest tests/acceptance/ -m "acceptance or live"

at-record:  ## Regenerate VCR cassettes (needs real API keys)
	cd api && python -m pytest tests/acceptance/ -m vcr --record-mode=rewrite

at-update-snapshots:  ## Accept new snapshot outputs
	cd api && python -m pytest tests/acceptance/ --snapshot-update

# ── Seed ─────────────────────────────────────────────────────────────

seed:  ## Seed demo data via API
	curl -s -X POST http://localhost:8000/api/seed | python -m json.tool
