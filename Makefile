# Run from an activated virtualenv (source .venv/bin/activate).
# `make check` runs the same steps as CI, in the same fast-to-slow order.
.PHONY: install format lint type test audit check

install:
	python -m pip install -e ".[dev]"

format:
	ruff format .

lint:
	ruff format --check .
	ruff check .

type:
	mypy

test:
	pytest --cov

audit:
	pip-audit --skip-editable

check: lint type test audit
