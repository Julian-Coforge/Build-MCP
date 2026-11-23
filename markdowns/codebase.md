# codebase.md: What the Code Does and Why

> This file is kept up to date with every code change, in the same commit.
> It describes the code **as it is now**, not as planned. For the plan, see `PLAN.md`. For the rules the system must follow, see `REQUIREMENTS.md`.

**Last updated:** 2026-11-23, milestone M1 (mock employee data, `get_employee_info` as a pure function, JSON logging)

---

## 1. What this project is

An internal IT equipment request handler for the Week 7 lab:
- **A Python MCP server** exposes four tools over mock employee and policy data.
- **A ReAct agent** on the Claude API uses those tools to approve, deny or escalate free-text equipment requests.

Policy decisions are made by plain, tested Python functions. The LLM only interprets the request text, chooses which tools to call, and drafts the reply.

## 2. Current state

M0 is done. In M1, the mock employee data, its validating loader and `get_employee_info` exist as plain functions with tests, as does the JSON logging module. Nothing is exposed over MCP yet. The MCP tool wrapper comes next.

| File | What it does | Status |
|---|---|---|
| `src/it_requests/config.py` | `REFERENCE_DATE = 2026-11-22`, the fixed "today" for every policy check. `DATA_DIR` and `EMPLOYEES_FILE`, resolved from the file's own location. | Tested |
| `data/employees.json` | The six employees from REQUIREMENTS §8, with fixed asset IDs. Top-level `data_version` (`employees-v1`) and `effective_date` (`2026-11-22`). | Tested (loads and validates) |
| `src/it_requests/data.py` | Pydantic models (`EquipmentItem`, `Employee`, `EmployeeDirectory`), the `Role`, `Status` and `CatalogItem` literals, the `ROLES` and `CATALOG_ITEMS` tuples, `EMPLOYEE_ID_PATTERN`, `DataValidationError` and `load_employees(path)`. | Tested, 100% coverage |
| `src/it_requests/employees.py` | `normalize_employee_id`, `tenure_months` and `get_employee_info`: the pure function behind the future MCP tool (REQUIREMENTS §7.1). | Tested, 100% coverage |
| `src/it_requests/observability.py` | `request_id_var`, `REDACTED_KEYS`, `JsonFormatter`, `log_event` and `configure_logging`: structured JSON logging for the server and, later, the agent (PLAN §5). | Tested, 100% coverage; not yet called by any component |
| `examples/step1/server.py`, `client.py` | Brief step 1: a minimal "Demo" MCP server with a trivial `get_employee_id` tool, plus a client that calls it. | Kept unchanged as submission evidence |

### `data.py` in detail

- **Models:** every model uses `ConfigDict(extra="forbid", frozen=True)`. `equipment` and `employees` are tuples.
- **Field rules:** `employee_id` must match `EMPLOYEE_ID_PATTERN` (`^E[0-9]{4}$`). `name`, `asset_id` and `data_version` must be non-empty. Roles, statuses and items must come from the literals. Dates must be valid ISO dates.
- **Cross-record rules**, checked in a `model_validator` on `EmployeeDirectory`:
  - no duplicate `employee_id`
  - no duplicate `asset_id` across all employees
  - no `issued_date` before that employee's `hire_date`
  - no `hire_date` or `issued_date` after `effective_date`
- **`EmployeeDirectory.get(employee_id)`:** exact match only. It returns `None` when the ID is not found.
- **`load_employees(path)`:** a missing or unreadable file, malformed JSON and any validation failure all raise `DataValidationError`. The message names the file path, and the original error is chained (`from err`).

### `employees.py` in detail

- **`normalize_employee_id(raw)`:** strips whitespace and upper-cases. Returns the ID if it fully matches `EMPLOYEE_ID_PATTERN`, otherwise `None`.
- **`tenure_months(hire_date, as_of)`:** whole calendar months completed. A month counts once `as_of` reaches the same day of the month. For example, 2021-06-14 to 2026-11-14 is 65 months, and to 2026-11-13 is 64.
- **`get_employee_info(employee_id, *, directory, as_of=REFERENCE_DATE)`:**
  - **Success:** returns `employee_id`, `name`, `role`, `hire_date` (ISO string), `tenure_months`, `status`, `equipment` and `data_version`. Each `equipment` entry has `item`, `asset_id` and `issued_date`, sorted by `(issued_date, asset_id)`.
  - **Bad format:** returns `{"error": "INVALID_EMPLOYEE_ID", "employee_id": <raw input>}`.
  - **Valid format, unknown ID:** returns `{"error": "EMPLOYEE_NOT_FOUND", "employee_id": <normalised ID>}`.
  - **Status:** reported as it is, not judged. `on_leave` is a normal success, because judging status is the eligibility tool's job.

### `observability.py` in detail

- **`log_event(logger, event, level=INFO, **fields)`:** the one helper every call site uses. It passes `fields` to the formatter through `extra={"fields": ...}`.
- **`JsonFormatter`:** writes each record as one line of JSON.
  - **Standard keys:** `ts`, `level`, `component` and `event`, plus `request_id`, which is taken from `request_id_var`. `ts` is UTC ISO-8601 with milliseconds and a `Z`, for example `2026-11-23T10:15:02.123Z`.
  - **Custom fields:** written after redaction. The standard keys are applied last, so a custom field can't overwrite them.
  - **Exceptions:** add `exc_type` and `exc_message`, with no traceback, so each record stays on one line.
  - **Non-JSON values:** dates and other values that JSON can't hold are written as strings (`json.dumps(default=str)`).
- **Redaction:** any field whose key matches `REDACTED_KEYS` (`name`, `api_key`, `authorization`, `password`, `token`), ignoring case, is written as `"[REDACTED]"`.
- **`configure_logging(component, *, log_file=None, level=INFO)`:**
  - Returns the named logger, writing to stderr, and also to `log_file` if one is given. Parent folders are created.
  - It closes and replaces existing handlers, so calling it twice never duplicates log lines.
  - Sets `propagate = False`.
- **Not yet included:** `trace_id` and `span_id` (PLAN §5). They arrive with tracing in M5.

## 3. Layout

```
Build-MCP/
├── src/it_requests/        # the installable package (all business logic lives here)
│   ├── __init__.py
│   ├── config.py           # shared constants (REFERENCE_DATE, DATA_DIR, EMPLOYEES_FILE)
│   ├── data.py             # Pydantic data models and load_employees
│   ├── employees.py        # get_employee_info and its helpers (pure)
│   ├── observability.py    # JSON logging: log_event, configure_logging
│   └── py.typed            # marks the package as typed for mypy
├── data/
│   └── employees.json      # mock employees (REQUIREMENTS §8)
├── tests/
│   ├── unit/               # test_config, test_data, test_employees, test_observability
│   └── integration/        # tools through MCP, agent loop with a fake LLM (from M4)
├── examples/step1/         # brief step-1 demo (do not modify)
├── markdowns/              # REQUIREMENTS, PLAN, codebase, MILESTONES, prompts/
├── .github/workflows/ci.yml
├── pyproject.toml          # package metadata, dependencies, tool configuration
├── Makefile                # `make check` = the same steps as CI
├── CLAUDE.md               # instructions for coding agents
└── .gitignore
```

**Why a `src/` layout:** tests import the *installed* package, not files that happen to sit next to them. A packaging mistake (a missing module, for example) then fails in tests instead of at demo time.

## 4. How to run

**One-time setup** (from the repo root, in your existing `.venv`):
```bash
source .venv/bin/activate
pip install -e ".[dev]"
```
`-e` installs the package in editable mode, so code changes apply without reinstalling. Re-run it after a dependency is added to `pyproject.toml`.

**Checks, the same as CI:**
```bash
make check        # format check, lint, mypy, tests with coverage, dependency audit
make format       # auto-format with ruff
pytest            # tests only
```

**Try the lookup from Python:**
```python
from it_requests import config
from it_requests.data import load_employees
from it_requests.employees import get_employee_info

directory = load_employees(config.EMPLOYEES_FILE)
get_employee_info(" e1001 ", directory=directory)  # E1001's record, tenure_months 65
```

**Step-1 demo server and client:**
```bash
python examples/step1/server.py   # terminal 1: serves on http://localhost:8000/mcp
python examples/step1/client.py   # terminal 2: prints {'result': 1234}
```

## 5. Design decisions

Each entry: the decision, and why.

1. **TDD throughout.** Every change starts with a failing test, committed as `test: ...` (red), then the code that passes it, committed as `feat: ...` or `fix: ...` (green).
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

10. **Bad data fails loudly at load time.** Any problem in `employees.json` raises `DataValidationError` before anything runs.
    *Why:* a silently bad record would surface later as a wrong approval, the worst outcome in this system (PLAN §3).

11. **Models use `extra="forbid"` and `frozen=True`, and use tuples instead of lists.**
    *Why:* `extra="forbid"` turns a mistyped field name such as `hire_dte` into an error instead of a silently missing value. `frozen=True` plus tuples make the loaded data truly read-only, so no tool can change shared state, and one load can be shared safely by tests.

12. **Cross-record checks live on `EmployeeDirectory`, not `Employee`.**
    *Why:* uniqueness needs every record at once, and the date limit needs the top-level `effective_date`. A validator on `Employee` sees only one employee.

13. **`ROLES` and `CATALOG_ITEMS` are derived from the `Literal` types with `typing.get_args`.**
    *Why:* one source of truth. Adding a catalog item is a one-line change, and the type checker and the runtime list cannot disagree.

14. **One `EMPLOYEE_ID_PATTERN` (`^E[0-9]{4}$`), defined in `data.py` and imported by `employees.py`.**
    *Why:* the data check and the input check then accept exactly the same IDs. `[0-9]` instead of `\d`, because `\d` also matches non-ASCII digits (see §7).

15. **Tool functions return errors as dicts and never raise** (`INVALID_EMPLOYEE_ID`, `EMPLOYEE_NOT_FOUND`).
    *Why:* REQUIREMENTS §7. The agent receives tool output as text, so a returned error lets the model reason about it, for example to ask for a valid ID or escalate under E1. A raised exception would only show up as an opaque tool failure.

16. **`INVALID_EMPLOYEE_ID` echoes the raw input, and `EMPLOYEE_NOT_FOUND` echoes the normalised ID.**
    *Why:* for a bad format, the model needs to see exactly what it sent. For a valid but unknown ID, the normalised form is the useful one.

17. **Equipment is sorted by `(issued_date, asset_id)` in the response.**
    *Why:* deterministic output whatever the order in the data file, so tests, logs and traces stay comparable. `asset_id` breaks ties between items issued on the same day.

18. **Every successful response includes `data_version`.**
    *Why:* lineage (PLAN §3). Any decision can be traced back to the exact data it was made from.

19. **`pydantic>=2.14,<3` is declared as a direct dependency.**
    *Why:* `data.py` imports it directly. Relying on it arriving only as a dependency of `mcp` would break if `mcp` ever dropped or changed it.

20. **Logging uses the standard library only, with one JSON object per line.**
    *Why:* no new dependency, and logs can be searched with `grep` or `jq` (PLAN §5).

21. **`request_id` lives in a `ContextVar`, not a global.**
    *Why:* each asyncio task sees its own value, so two MCP requests handled at the same time never log each other's ID.

22. **Redaction happens in the formatter, and ignores the case of the key.**
    *Why:* one place to enforce "never log names or secrets". A call site can't forget it, and `Authorization` can't slip past on spelling alone.

23. **Logs go to stderr, never stdout.**
    *Why:* an MCP server on the stdio transport uses stdout for protocol messages, so a log line there would corrupt the stream.

24. **`configure_logging` replaces handlers instead of adding to them, and sets `propagate = False`.**
    *Why:* calling it again (in tests, or on an in-process restart) must not double every line. Propagating to the root logger could print each record a second time in another format.

## 6. Change log

| Date | Milestone | Change | Why |
|---|---|---|---|
| 2026-11-22 | — | `server.py` and `client.py` created with a trivial tool | Brief step 1: prove the SDK works before writing business logic |
| 2026-11-23 | Planning | `PLAN.md` and `codebase.md` added | Plan agreed before building |
| 2026-11-23 | M0 | `.gitignore` added first | `.venv/`, `__pycache__/` and later `.env` must never be committed |
| 2026-11-23 | M0 | `pyproject.toml`, `src/it_requests/`, first test and `config.py` | Package skeleton built test-first |
| 2026-11-23 | M0 | GitHub Actions CI and `Makefile` | Part 7: tests run on every push |
| 2026-11-23 | M0 | REQUIREMENTS v1.1 | Four consistency fixes from the plan review (see REQUIREMENTS §13) |
| 2026-11-23 | M0 | `CLAUDE.md` added; docs moved to `markdowns/`; step-1 demo moved to `examples/step1/` | One set of instructions for coding agents; a tidy repo root |
| 2026-11-23 | M1 | Failing tests for the employee data loader (`test_data.py`) | TDD red step |
| 2026-11-23 | M1 | `data/employees.json`, `data.py`, `DATA_DIR`/`EMPLOYEES_FILE`, pydantic pin; test for `issued_date` after `effective_date` | Mock data that fails loudly on bad records |
| 2026-11-23 | M1 | Failing tests for `get_employee_info` (`test_employees.py`) | TDD red step |
| 2026-11-23 | M1 | `employees.py`: `normalize_employee_id`, `tenure_months`, `get_employee_info` | REQUIREMENTS §7.1 as a pure function, before any MCP wrapping |
| 2026-11-23 | M1 | Shared `EMPLOYEE_ID_PATTERN` restricted to ASCII digits, with a reproduction test | Fix for non-ASCII digits being accepted (see §7) |
| 2026-11-23 | M1 | Failing tests for JSON logging (`test_observability.py`) | TDD red step |
| 2026-11-23 | M1 | `observability.py`: `JsonFormatter`, `log_event`, `configure_logging`, `request_id_var`, redaction | One structured logging path for the server and agent (PLAN §5) |

## 7. Known issues and fixes

### 7.1 Employee IDs with non-ASCII digits were accepted (fixed)

- **Symptom:** `data.py` accepted the ID `"E١٠٠١"`, which is written with Arabic-Indic digits.
- **Cause:** the `Employee` model's pattern was `^E\d{4}$`, and `\d` matches digits from every script. `employees.py` already used `[0-9]` in its own copy of the pattern, so the two checks disagreed.
- **Evidence:** a reproduction case, `test_invalid_data_raises[non_ascii_digit_employee_id]`, failed with `DID NOT RAISE DataValidationError`.
- **Fix:** one shared `EMPLOYEE_ID_PATTERN = r"^E[0-9]{4}$"` in `data.py`, imported by `employees.py`. The test now passes.
- **Still open (docs):** `markdowns/prompts/M1.md` lines 126 and 175 still say `^E\d{4}$`.
