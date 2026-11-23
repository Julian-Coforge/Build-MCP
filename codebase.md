# codebase.md: What the Code Does and Why

> This file is kept up to date with every code change, in the same commit.
> It describes the code **as it is now**, not as planned. For the plan, see `PLAN.md`. For the rules the system must follow, see `REQUIREMENTS.md`.

**Last updated:** 2026-11-23, milestone M0 (repo hygiene, tooling, CI)

---

## 1. What this project is

An internal IT equipment request handler for the Week 7 lab:
- **A Python MCP server** exposes four tools over mock employee and policy data.
- **A ReAct agent** on the Claude API uses those tools to approve, deny or escalate free-text equipment requests.

Policy decisions are made by plain, tested Python functions. The LLM only interprets the request text, chooses which tools to call, and drafts the reply.

## 2. Current state

M0 is done. The project has tooling, CI and one config constant. No business logic exists yet; it starts in M1.

| File | What it does | Status |
|---|---|---|
| `src/it_requests/config.py` | Holds `REFERENCE_DATE = 2026-11-22`, the fixed "today" for every policy check | Tested |
| `server.py` | Brief step 1: a minimal MCP server named "Demo" with one trivial tool, `get_employee_id(employee_id: int) -> int`, which returns its input. Run directly, it serves streamable HTTP on port 8000. | Working; kept as step-1 evidence, superseded in M1 |
| `client.py` | Brief step 1: connects to `http://localhost:8000/mcp`, calls `get_employee_id` with `1234`, prints `{'result': 1234}` | Working; superseded in M1 |
| `REQUIREMENTS.md` | Requirements and policy rules | v1.1 |
| `PLAN.md` | Project plan | Approved 2026-11-23 |

## 3. Layout

```
Build-MCP/
├── src/it_requests/        # the installable package (all business logic lives here)
│   ├── __init__.py
│   ├── config.py           # shared constants (REFERENCE_DATE)
│   └── py.typed            # marks the package as typed for mypy
├── tests/
│   ├── unit/               # pure-function tests (fast, no network, no fakes needed)
│   └── integration/        # tools through MCP, agent loop with a fake LLM (from M4)
├── .github/workflows/ci.yml
├── pyproject.toml          # package metadata, pinned dev tools, tool configuration
├── Makefile                # `make check` = the same steps as CI
├── server.py, client.py    # brief step-1 demo (kept as evidence)
├── REQUIREMENTS.md, PLAN.md, codebase.md
└── .gitignore
```

**Why a `src/` layout:** tests import the *installed* package, not files that happen to sit next to them. A packaging mistake (a missing module, for example) then fails in tests instead of at demo time.

## 4. How to run

**One-time setup** (from the repo root, in your existing `.venv`):
```bash
source .venv/bin/activate
pip install -e ".[dev]"
```
`-e` installs the package in editable mode, so code changes apply without reinstalling.

**Checks, the same as CI:**
```bash
make check        # format check, lint, mypy, tests with coverage, dependency audit
make format       # auto-format with ruff
pytest            # tests only
```

**Step-1 demo server and client:**
```bash
python server.py          # terminal 1: serves on http://localhost:8000/mcp
python client.py          # terminal 2: prints {'result': 1234}
```

## 5. Design decisions

Each entry: the decision, and why.

1. **TDD throughout.** Every change starts with a failing test, committed as `test: ... (red)`, then the code that passes it, committed as `feat: ... (green)`.
   *Why:* the policy rules are exact, so tests written from REQUIREMENTS §8 pin the behaviour down before code exists. Separate red and green commits make the order visible in git history.

2. **Policy logic lives in pure functions, and the MCP layer is a thin wrapper.**
   *Why:* the brief asks for unit tests on the plain functions. Pure functions are also deterministic and need no fakes.

3. **`REFERENCE_DATE` is a constant, and the system clock is never read.**
   *Why:* eligibility is date arithmetic ("issued date plus 36 months"). Tests that used today's date would start failing on their own as time passes.

4. **Dev tools are pinned to exact versions in `pyproject.toml`.**
   *Why:* CI and your laptop then run the same ruff and mypy versions, so a check can't pass locally and fail in CI because of a tool update.

5. **ruff** for formatting and linting, **mypy `--strict`** on `src/`.
   *Why:* ruff is one fast tool that replaces black, isort and flake8. Strict typing catches wrong argument types in tool code before the agent hits them at runtime. Tests are not type-checked strictly, to keep them short.

6. **Coverage must be at least 90%** (`fail_under` in `pyproject.toml`).
   *Why:* success criterion S3 in PLAN.md. Enforced by the tool, so it can't quietly slip.

7. **CI order: format, lint, types, unit tests, integration tests, dependency audit.**
   *Why:* fast checks first, so the cheapest failure is reported first. CI never calls the Claude API, so it is free, deterministic and needs no secrets.

8. **The integration-test step accepts "no tests collected" for now** (`|| [ $? -eq 5 ]`).
   *Why:* pytest returns exit code 5 when it finds no tests. Integration tests arrive in M4. Any real test failure still fails the build. Remove the workaround once integration tests exist.

9. **Ruff rule SIM300 is turned off.**
   *Why:* it flags `config.REFERENCE_DATE == date(...)` as a "Yoda condition" because the name is upper-case, and suggests the less readable order.

## 6. Change log

| Date | Milestone | Change | Why |
|---|---|---|---|
| 2026-11-22 | — | `server.py` and `client.py` created with a trivial tool | Brief step 1: prove the SDK works before writing business logic |
| 2026-11-23 | Planning | `PLAN.md` and `codebase.md` added | Plan agreed before building |
| 2026-11-23 | M0 | `.gitignore` added first | `.venv/`, `__pycache__/` and later `.env` must never be committed |
| 2026-11-23 | M0 | `pyproject.toml`, `src/it_requests/`, first test and `config.py` | Package skeleton built test-first |
| 2026-11-23 | M0 | GitHub Actions CI and `Makefile` | Part 7: tests run on every push |
| 2026-11-23 | M0 | REQUIREMENTS v1.1 | Four consistency fixes from the plan review (see REQUIREMENTS §13) |

## 7. Known issues and fixes

None yet. Each entry will record the symptom, cause, evidence and fix.
