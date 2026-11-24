# Run from an activated virtualenv (source .venv/bin/activate).
# `make check` runs the same steps as CI, in the same fast-to-slow order.
.PHONY: install format lint type test audit check serve client-demo reset-reviews

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

# Start the MCP server on http://127.0.0.1:8000/mcp (blocks; stop with Ctrl+C).
serve:
	python -m it_requests.server

# Call the running server over HTTP. Run `make serve` in another terminal first.
client-demo:
	python scripts/client_demo.py

# Delete the review queue, so the demo starts clean and its first ticket is REV-0001.
reset-reviews:
	rm -f data/review_queue.json
