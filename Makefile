PYTHON = .venv\Scripts\python
.PHONY: qa lint fix test

qa: lint test
	@echo "[OK] QA completo: lint + tests pasaron"

lint:
	$(PYTHON) -m ruff check src/ tests/

fix:
	$(PYTHON) -m ruff check --fix src/ tests/
	$(PYTHON) -m ruff format src/ tests/

test:
	$(PYTHON) -m pytest tests/ -v -n auto