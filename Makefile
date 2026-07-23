UV ?= uv
.PHONY: qa lint format fix test

qa: lint format test
	@echo "[OK] QA complete: check + format + tests passed"

lint:
	$(UV) run ruff check .

format:
	$(UV) run ruff format --check .

fix:
	$(UV) run ruff check --fix .
	$(UV) run ruff format .

test:
	$(UV) run pytest tests/ -v -n auto
