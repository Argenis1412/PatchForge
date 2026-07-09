.PHONY: qa lint fix test

qa: lint test
	@echo "[OK] QA completo: lint + tests pasaron"

lint:
	python -m ruff check src/ tests/

fix:
	python -m ruff check --fix src/ tests/
	python -m ruff format src/ tests/

test:
	python -m pytest tests/ -v -n auto --dist loadgroup -m "not serial"
	python -m pytest tests/ -v -m "serial"