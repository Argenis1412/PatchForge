PYTHON = .venv\Scripts\python
.PHONY: qa lint format fix test

qa: lint format test
	@echo "[OK] QA complete: check + format + tests passed"

lint:
	$(PYTHON) -m ruff check .

format:
	$(PYTHON) -m ruff format --check .

fix:
	$(PYTHON) -m ruff check --fix .
	$(PYTHON) -m ruff format .

test:
	$(PYTHON) -m pytest tests/ -v -n auto