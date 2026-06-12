.PHONY: install test lint build coverage

install:
	pip install -e ".[dev]"

test:
	pytest --cov=boomi_auditor --cov-report=term-missing --cov-fail-under=80

coverage:
	pytest --cov=boomi_auditor --cov-report=html --cov-fail-under=80
	@echo "Open htmlcov/index.html for the report"

lint:
	ruff check .

build:
	python -m build
