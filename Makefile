# Run from an activated virtualenv (source .venv/bin/activate).
# `make check` runs the same steps as CI, in the same fast-to-slow order.
.PHONY: install format lint type test test-live audit check serve client-demo reset-reviews agent-once cost-report agent agent-fault eval

install:
	python -m pip install -e ".[dev]"

format:
	ruff format .

lint:
	ruff format --check .
	ruff check .

type:
	mypy

# Fast tests only: fakes, no network, no paid calls. The same selection CI runs.
test:
	pytest -m "not slow" --cov

# PAID (about $0.004): one real T1 run on Haiku against the live server. Run `make serve`
# in another terminal first. Skips, with the reason shown, if the key or server is missing.
test-live:
	pytest -m "slow" -rs tests/live

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

# One PAID model call (Haiku by default): Claude picks one tool, run on the live server.
agent-once:
	python scripts/agent_once.py

# Spend so far from logs/cost_ledger.jsonl: total, by env, stage and request, and budget left.
cost-report:
	python -m it_requests.cost_report

# One PAID agent run with the full printed trace. Pick a case: make agent CASE=T3 (default T1).
CASE ?= T1
agent:
	python scripts/run_agent.py --case $(CASE)

# PAID, labelled fault-injection demo (REQUIREMENTS §11): plants a delivery promise in the
# accepted draft so reflection must catch it. Saved as traces/<case>-fault.txt.
agent-fault:
	python scripts/run_agent.py --case $(CASE) --inject-fault

# PAID eval: T1-T4 x 3 on Sonnet by default. Prints the estimate and asks before spending.
eval:
	python scripts/run_eval.py
