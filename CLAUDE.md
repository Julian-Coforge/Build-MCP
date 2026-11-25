# CLAUDE.md

Instructions for any coding agent working in this repo. Read this before every task.

## Project

An IT equipment request handler for a course assignment (Week 7 lab), with two parts:
- **A Python MCP server** exposes four tools over mock data: `get_employee_info`, `get_policy_limits`, `check_request_eligibility` and `flag_for_human_review`.
- **A ReAct agent** on the Claude API uses those tools to approve, deny or escalate free-text equipment requests, then reflects on its own draft reply.

**Core principle:** deterministic, tested Python makes every policy decision. The LLM only interprets the request text, picks which tools to call, and drafts the reply. When the data doesn't clearly support approve or deny, the agent escalates. It never guesses.

## Read these first

| File | What it is |
|---|---|
| `markdowns/REQUIREMENTS.md` | The source of truth: rule IDs (R-*), escalation codes (E1 to E11), the decision table, constraints C1 to C7, tool contracts (§7) and mock data (§8) |
| `markdowns/PLAN.md` | Architecture, logging, tracing, cost controls, testing approach and milestones |
| `markdowns/codebase.md` | What the code does now, and why |
| `markdowns/MILESTONES.md` | Current stage |
| `markdowns/prompts/M*.md` | The task prompts for each milestone |

If a task conflicts with REQUIREMENTS.md, stop and say so. Don't pick one yourself.

## Layout

```
src/it_requests/     installable package; all business logic lives here
  config.py          shared constants (REFERENCE_DATE, data paths)
tests/unit/          pure-function tests: fast, no network, no fakes needed
tests/integration/   tools through MCP (in-process Client); agent loop with a fake LLM
data/                mock JSON data (employees, policy); review_queue.json is runtime-only and gitignored
scripts/             runnable demos (client_demo.py, later run_eval.py)
examples/step1/      brief step-1 demo. Evidence for the submission: do not modify.
markdowns/           all project docs
```

## Rules

1. **TDD, always.**
   - Write the tests first, run them, and show the failures.
   - Then **STOP and wait for the user to reply `go`**. The user commits the failing tests as a separate "red" commit.
   - After `go`, write the minimum code that passes, then refactor with the tests green.
   - Every bug fix starts with a test that reproduces the bug.
2. **No git commands.** No commit, add, branch, checkout, push, stash or reset. The user does all git.
3. **Explain every decision.** Add a short comment saying *why* at each non-obvious choice, and give a one-line reason for each decision in your reply.
4. **Run `make check` before you say a task is done.** It must pass. Report the result honestly, failures included.
5. **Pure functions for policy logic.** No I/O, no system clock (take `as_of`, default `config.REFERENCE_DATE` = 2026-11-22), no globals. `server.py` holds thin MCP wrappers only.
6. **Tool errors are returned as data**, for example `{"error": "EMPLOYEE_NOT_FOUND", ...}`, never raised to the agent (REQUIREMENTS §7).
7. **Stay in scope.** Do only what the prompt asks. Don't touch `examples/step1/` or the docs in `markdowns/`. The user's main Claude session keeps `codebase.md` and `MILESTONES.md` up to date.

## Test standard (Arrange-Act-Assert)

Every test follows the `aaa-unit-testing` standard. `tests/unit/test_test_style.py` enforces rules 1, 2 and the parametrize/logic rules on every push.

1. **One behaviour, one `assert` per test.** If it needs two asserts, it is two tests. `pytest.raises`, or an `_exploding` fake that raises if called, counts as the assert.
2. **The name is the spec:** `test_<unit>_<condition>_<expected>`, in domain words. No "and", "works" or "correct" in names.
3. **Arrange / Act / Assert are visibly separate**, with a blank line between phases when setup is more than one line.
4. **Fakes over mocks.** Dependencies (LLM `create`, MCP client, clock, tracer, file paths) are injected; tests pass small deterministic fakes. No `patch` of module internals.
5. **Fast by default.** Anything real, networked or paid is marked `slow` (and `eval` if it calls the Claude API). `make check` and CI run `-m "not slow"`. Real-dependency twins are named `test_real_...`.
6. **Known bugs are `xfail(strict=True, reason=...)`.** Never skip or delete a test to get green.
7. **Deterministic:** no real time, randomness, network or shared state in fast tests. Use `tmp_path`, injected clocks and fresh fixtures.
8. **Edges on purpose:** empty input, exact boundary, oversized values, invalid input, repeat calls (idempotency), input not mutated.
9. **Shared data at module top in CAPS** (with a comment on why it gives the outcome); helpers prefixed `_`. No `if`/`for` in test bodies: use `@pytest.mark.parametrize(..., ids=...)` or a helper.
10. **Group by unit** with `# ---------- unit ----------` comments, happy path first, slow tests last.

## Commands

```bash
pip install -e ".[dev]"     # once, in the activated .venv
make check                  # format check, lint, mypy --strict, tests + coverage (≥90%), pip-audit: same as CI
make format                 # auto-format with ruff
pytest tests/unit/test_x.py # run one test file
make serve                  # run the MCP server on http://127.0.0.1:8000/mcp (from M1)
make client-demo            # call the server from the demo client (from M1)
```

## Code standards

- Python 3.14, with type hints everywhere. `mypy --strict` must pass on `src/`.
- Formatting and linting with ruff (line length 100).
- MCP SDK is `mcp==2.3.0`. Use `from mcp.server import MCPServer` and `from mcp import Client`. `Client(server)` connects in-process, which is what the tests use.
- Annotate tools `-> dict[str, Any]`. With bare `dict`, the SDK returns no structured output.
- Pin new dependencies in `pyproject.toml` with a comment saying why each one is needed.

## Logging and secrets

- Log through `it_requests.observability.log_event`: one line of JSON per event, carrying `request_id`.
- **Never log** API keys, credentials, environment variables or employee names (log `employee_id` only). The formatter redacts the known keys. Don't work around it.
- `ANTHROPIC_API_KEY` lives only in `.env`, which is gitignored. Never put it in code, tests, logs or printed output.

## LLM cost (hard budget: under $5 in total)

- **Tests and CI never call the real Claude API.** Use a fake client.
- Development runs use `claude-haiku-5-5`. Evaluation and demo runs use `claude-sonnet-5-5`. Do not use Opus for the agent.
- Every model call goes through the one LLM wrapper, which records tokens and cost in `logs/cost_ledger.jsonl` (from M5).
- The budget gate refuses to run at $4.00 spent. Never bypass it or raise it without the user's say-so.
- Don't start a live (paid) run unless the user asks for one.
