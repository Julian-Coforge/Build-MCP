# codebase.md: What the Code Does and Why

> This file is kept up to date with every code change, in the same commit.
> It describes the code **as it is now**, not as planned. For the plan, see `PLAN.md`. For the rules the system must follow, see `REQUIREMENTS.md`.

**Last updated:** 2026-11-23, milestone M2 (policy data, `get_policy_limits` as a pure function and as the second MCP tool). M1 and M2 are verified.

---

## 1. What this project is

An internal IT equipment request handler for the Week 7 lab:
- **A Python MCP server** exposes four tools over mock employee and policy data.
- **A ReAct agent** on the Claude API uses those tools to approve, deny or escalate free-text equipment requests.

Policy decisions are made by plain, tested Python functions. The LLM only interprets the request text, chooses which tools to call, and drafts the reply.

## 2. Current state

M0 is done. In M1, the mock employee data, its validating loader and `get_employee_info` exist as plain functions with tests, as does the JSON logging module. The real MCP server exposes one tool, `get_employee_info`, over streamable HTTP, and `scripts/client_demo.py` calls it. In M2, the role-entitlement policy (`data/policy.json`) and its validating loader exist with tests, as does `get_policy_limits`, as a pure function and as the server's second MCP tool. The other two tools come in M3 and M4.

| File | What it does | Status |
|---|---|---|
| `src/it_requests/config.py` | `REFERENCE_DATE = 2026-11-22`, the fixed "today" for every policy check. `DATA_DIR`, `EMPLOYEES_FILE` and `POLICY_FILE`, resolved from the file's own location. | Tested |
| `data/employees.json` | The six employees from REQUIREMENTS §8, with fixed asset IDs. Top-level `data_version` (`employees-v1`) and `effective_date` (`2026-11-22`). | Tested (loads and validates) |
| `data/policy.json` | The role-entitlement matrix from REQUIREMENTS §4.1: 4 roles × 6 items, each with `max` and `refresh_months`. Top-level `data_version` (`policy-v1`) and `effective_date`. | Tested (loads and validates) |
| `src/it_requests/data.py` | Pydantic models (`EquipmentItem`, `Employee`, `EmployeeDirectory`), the `Role`, `Status` and `CatalogItem` literals, the `ROLES` and `CATALOG_ITEMS` tuples, `EMPLOYEE_ID_PATTERN`, `DataValidationError` and `load_employees(path)`. Policy models (`ItemLimit`, `PolicyTable`) and `load_policy(path)`. | Tested, 100% coverage |
| `src/it_requests/employees.py` | `normalize_employee_id`, `tenure_months` and `get_employee_info`: the pure function behind the MCP tool (REQUIREMENTS §7.1). | Tested, 100% coverage |
| `src/it_requests/policy.py` | `normalize_role` and `get_policy_limits`: the pure function behind the MCP tool (REQUIREMENTS §7.2). | Tested, 100% coverage |
| `src/it_requests/observability.py` | `request_id_var`, `REDACTED_KEYS`, `JsonFormatter`, `log_event` and `configure_logging`: structured JSON logging for the server and, later, the agent (PLAN §5). | Tested, 100% coverage; used by `server.py` |
| `src/it_requests/server.py` | `build_server(directory, policy_table, *, as_of)` registers the `get_employee_info` and `get_policy_limits` tools as thin wrappers. Both log through one helper, `_call_and_log`. `main()` configures logging, loads both data files, logs `server.start` and serves on `http://127.0.0.1:8000/mcp`. | Integration-tested in-process; `main()` checked by hand |
| `scripts/client_demo.py` | Connects over HTTP, lists the tools, calls `get_employee_info` for `E1001`, `" e1001 "` and `E9999`, then calls `get_policy_limits` for `standard`, `Manager` and `intern` (screenshot for deliverable #2). | Checked by hand |
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
- **Loaders:** `load_employees(path)` and `load_policy(path)` both call one private helper, `_load_model(path, model, what)`. A missing or unreadable file, malformed JSON and any validation failure all raise `DataValidationError`. The message names the file path, and the original error is chained (`from err`).
- **`ItemLimit`:** has two fields, `max` and `refresh_months`.
  - `max` is an int of 0 or more.
  - `refresh_months` is a positive int (the refresh interval), `"none"` (entitled, but no refresh cycle: R-LIMIT) or `None` (not entitled: R-ROLE).
  - A `model_validator` enforces the v1.1 pairing: `max == 0` requires `None`, and `max > 0` requires a positive int or `"none"`, never `None`.
- **`PolicyTable`:**
  - Fields: `data_version`, `effective_date` and `roles: dict[Role, dict[CatalogItem, ItemLimit]]`.
  - The literal key types reject unknown roles and items. A `model_validator` rejects missing ones, and its error names the missing role or item.
  - `limits_for(role)` returns a copy of that role's limits.

### `employees.py` in detail

- **`normalize_employee_id(raw)`:** strips whitespace and upper-cases. Returns the ID if it fully matches `EMPLOYEE_ID_PATTERN`, otherwise `None`.
- **`tenure_months(hire_date, as_of)`:** whole calendar months completed. A month counts once `as_of` reaches the same day of the month. For example, 2021-06-14 to 2026-11-14 is 65 months, and to 2026-11-13 is 64.
- **`get_employee_info(employee_id, *, directory, as_of=REFERENCE_DATE)`:**
  - **Success:** returns `employee_id`, `name`, `role`, `hire_date` (ISO string), `tenure_months`, `status`, `equipment` and `data_version`. Each `equipment` entry has `item`, `asset_id` and `issued_date`, sorted by `(issued_date, asset_id)`.
  - **Bad format:** returns `{"error": "INVALID_EMPLOYEE_ID", "employee_id": <raw input>}`.
  - **Valid format, unknown ID:** returns `{"error": "EMPLOYEE_NOT_FOUND", "employee_id": <normalised ID>}`.
  - **Status:** reported as it is, not judged. `on_leave` is a normal success, because judging status is the eligibility tool's job.

### `policy.py` in detail

- **`normalize_role(raw)`:** strips whitespace and lower-cases. Returns the matching role from `ROLES`, typed as `Role`, or `None` if there is no match.
- **`get_policy_limits(role, *, policy)`:**
  - **Success:** returns `{"role", "limits", "data_version"}`. `limits` has all six items in `CATALOG_ITEMS` order, each as `{"max", "refresh_months"}`.
  - **`refresh_months`:** passed through unchanged: an int, `"none"` or `None`, which becomes `null` in JSON.
  - **Unknown role:** returns `{"error": "UNKNOWN_ROLE", "role": <raw input>}`.

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

### `server.py` in detail

- **`build_server(directory, policy_table, *, as_of=REFERENCE_DATE)`:** a factory. Tests inject their own data, and importing the module never reads files or opens ports. The parameter is called `policy_table` because `policy` is the module name.
- **`_call_and_log(tool, call, *, data_version, inputs, result_fields)`:** the one helper both tools use.
  - Times the call and logs one `tool.call` event, then returns the result unchanged, including error dicts. `is_error` therefore stays `False` for tool errors.
  - The event fields are `tool`, the inputs, any result-derived fields, `status` (`ok` or the error code), `data_version` and `duration_ms`.
- **The `get_employee_info` tool:**
  - Logs `employee_id`, the raw input exactly as sent.
  - Logs `resolved_employee_id`: the normalised ID on success, or `null` on any error.
  - Never logs the employee's name.
- **The `get_policy_limits` tool:** logs `role`, the raw input, and `data_version` `policy-v1`.
- **Tool descriptions:** each tool's docstring, word for word from REQUIREMENTS §7.1 and §7.2.
- **`main()`:**
  - Configures logging, to stderr and `logs/app.jsonl`, and loads `data/employees.json` and `data/policy.json`.
  - Logs `server.start`, with `data_version`, `policy_version`, `employee_count`, `host` and `port`.
  - Runs streamable HTTP on `127.0.0.1:8000`.
- **Ways to start it:** `make serve`, `python -m it_requests.server` or the installed `it-requests-server` command.

## 3. Layout

```
Build-MCP/
├── src/it_requests/        # the installable package (all business logic lives here)
│   ├── __init__.py
│   ├── config.py           # shared constants (REFERENCE_DATE, DATA_DIR, EMPLOYEES_FILE, POLICY_FILE)
│   ├── data.py             # Pydantic data models, load_employees and load_policy
│   ├── employees.py        # get_employee_info and its helpers (pure)
│   ├── policy.py           # get_policy_limits and normalize_role (pure)
│   ├── observability.py    # JSON logging: log_event, configure_logging
│   ├── server.py           # MCP server: build_server (thin tool wrappers) and main
│   └── py.typed            # marks the package as typed for mypy
├── data/
│   ├── employees.json      # mock employees (REQUIREMENTS §8)
│   └── policy.json         # role entitlements (REQUIREMENTS §4.1)
├── tests/
│   ├── unit/               # test_config, test_data, test_policy_data, test_employees, test_policy, test_observability
│   └── integration/        # conftest (server, server_log), test_server_employee_tool, test_server_policy_tool
├── scripts/
│   └── client_demo.py      # calls the running server over HTTP
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

**MCP server and demo client** (two terminals):
```bash
make serve          # terminal 1: serves on http://127.0.0.1:8000/mcp, logs to stderr and logs/app.jsonl
make client-demo    # terminal 2: lists tools, calls get_employee_info and get_policy_limits
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

8. **Coverage is measured across both test layers and checked once.** In CI, the unit step runs with `--cov-fail-under=0`, and the integration step uses `--cov-append` and applies the 90% threshold. (This replaced the earlier "no tests collected" workaround, `|| [ $? -eq 5 ]`, which was removed once integration tests existed.)
   *Why:* `server.py` is only exercised by integration tests. Unit tests alone reach 87%, so a unit-only check would fail even though the combined coverage is 100%.

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

25. **The server is built by a factory, `build_server`, not created at module level.**
    *Why:* tests inject data, and importing the module has no side effects (no file reads, no open ports).

26. **Tool functions are annotated `-> dict[str, Any]`, not bare `dict`.**
    *Why:* with bare `dict`, mcp 2.3.0 returns only text and no `structured_content` (verified).

27. **The tool docstring is one long line (`# noqa: E501`).**
    *Why:* the docstring is the description the model sees, and it must equal REQUIREMENTS §7.1 exactly. Wrapping it would add line breaks.

28. **Integration tests use `Client(server)` in-process.**
    *Why:* they cover the real protocol, schema and structured output, but CI needs no open port. This settles the open question in PLAN decision 4: SDK 2.3 supports in-process clients, so the stdio fallback isn't needed.

29. **The server binds to `127.0.0.1` only.**
    *Why:* it is a local mock server and must never be reachable from the network.

30. **`main()` is excluded from coverage (`# pragma: no cover`).**
    *Why:* it starts a blocking network server. Everything it calls is tested separately, and it was checked by hand with `make serve` and `make client-demo`.

31. **`duration_ms` uses `time.perf_counter()`.**
    *Why:* it is monotonic, so a system clock change can't produce a negative or wrong duration.

32. **The `max`/`refresh_months` pairing is enforced in `ItemLimit`'s validator.**
    *Why:* the eligibility logic relies on `null` meaning "not entitled" and `"none"` meaning "no refresh cycle" (REQUIREMENTS §7.2, v1.1). A contradictory pair, such as max 0 with refresh 24, would otherwise load fine and give a wrong decision later.

33. **The policy must be complete: every role defines every catalog item.**
    *Why:* a gap would leave the eligibility check with no rule to apply. It is caught at load, not at request time.

34. **Both loaders share one helper, `_load_model`.**
    *Why:* the error handling exists once, so every data file fails the same way. A fix in the helper applies to all loaders. It was added as a refactor after the tests were green, and the existing `load_employees` tests passed unchanged.

35. **`limits_for` returns a copy of the role's limits.**
    *Why:* `frozen=True` stops fields being reassigned, but a dict inside a model can still be changed. The copy keeps the shared table safe from callers.

36. **`ROLES` and `CATALOG_ITEMS` are typed `tuple[Role, ...]` and `tuple[CatalogItem, ...]`, not `tuple[str, ...]`.**
    *Why:* code that loops over them gets `Role` and `CatalogItem` values. mypy then accepts them as dict keys and return values without a `cast`, which `normalize_role` and `get_policy_limits` rely on.

37. **`get_policy_limits` builds `limits` in `CATALOG_ITEMS` order and passes `refresh_months` through unchanged.**
    *Why:* the output is the same on every call. `"none"` (R-LIMIT) and `null` (R-ROLE) stay distinct, so the model never has to infer entitlement from `max` (REQUIREMENTS §7.2).

38. **Every tool logs through one helper, `_call_and_log`.**
    *Why:* each wrapper stays a few lines, and the `tool.call` shape can't drift between tools. It was added as a refactor after the tests were green. Result-dependent fields, such as `resolved_employee_id`, come in through a `result_fields` callback, so the helper stays generic.

39. **`get_employee_info` logs both the raw `employee_id` and `resolved_employee_id`.**
    *Why:* the raw value shows exactly what the agent sent, including a bad ID. The resolved value lets one `jq` query find every lookup of an employee, however messy the input was. It is `null` on any error, never a guess.

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
| 2026-11-23 | M1 | Failing integration tests for the `get_employee_info` MCP tool (`tests/integration/`) | TDD red step |
| 2026-11-23 | M1 | `server.py` (`build_server`, `main`), `scripts/client_demo.py`, `it-requests-server` script, `make serve` / `make client-demo`, CI coverage across both layers; `tests/integration/.gitkeep` removed | `get_employee_info` exposed over MCP and proven end to end over HTTP |
| 2026-11-23 | M2 | Failing tests for the policy data file and loader (`test_policy_data.py`) | TDD red step |
| 2026-11-23 | M2 | `data/policy.json`, `ItemLimit`, `PolicyTable`, `load_policy`, `POLICY_FILE`; both loaders refactored onto `_load_model` | Role entitlements as validated data that fails loudly |
| 2026-11-23 | M2 | Failing tests for `get_policy_limits` (`test_policy.py`) | TDD red step |
| 2026-11-23 | M2 | `policy.py`: `normalize_role`, `get_policy_limits`; `ROLES` and `CATALOG_ITEMS` typed with their literals | REQUIREMENTS §7.2 as a pure function, before any MCP wrapping |
| 2026-11-23 | M2 | Failing integration tests for the `get_policy_limits` tool and `resolved_employee_id` logging; `server_log` fixture moved to `conftest.py` | TDD red step |
| 2026-11-23 | M2 | `get_policy_limits` MCP tool; `resolved_employee_id` in `get_employee_info` logs; shared `_call_and_log` helper; `policy_version` in `server.start`; client demo calls the policy tool | Second tool exposed over MCP and proven end to end over HTTP |

## 7. Known issues and fixes

### 7.1 Employee IDs with non-ASCII digits were accepted (fixed)

- **Symptom:** `data.py` accepted the ID `"E١٠٠١"`, which is written with Arabic-Indic digits.
- **Cause:** the `Employee` model's pattern was `^E\d{4}$`, and `\d` matches digits from every script. `employees.py` already used `[0-9]` in its own copy of the pattern, so the two checks disagreed.
- **Evidence:** a reproduction case, `test_invalid_data_raises[non_ascii_digit_employee_id]`, failed with `DID NOT RAISE DataValidationError`.
- **Fix:** one shared `EMPLOYEE_ID_PATTERN = r"^E[0-9]{4}$"` in `data.py`, imported by `employees.py`. The test now passes.
- **Docs:** `markdowns/prompts/M1.md` corrected to `^E[0-9]{4}$` (2026-11-23).

### 7.2 Log values seen in the M1 demo (expected, not bugs)

- **`"request_id": null` on every line.** A request ID belongs to one equipment request handled by the agent, and the agent doesn't exist until M5. In M5 the agent sets `request_id_var` and sends the ID to the server in the MCP call metadata, so server lines will carry it from then on.
- **`tool.call` logs the raw input (`" e1001 "`), not the normalised ID.** This is deliberate (see the comment in `server.py`): a bad ID must be visible exactly as sent. The gap is that a successful call doesn't show which employee it resolved to. Fixed in M2: `tool.call` lines for `get_employee_info` now include `resolved_employee_id` (the matched ID, or null on an error).

### 7.3 M1 verification (2026-11-23)

Checked by the main Claude session on a copy of the repo at commit `58d9674`, Python 3.14.6:
- `ruff format --check`, `ruff check` and `mypy --strict` all pass.
- 63 unit tests and 5 integration tests pass. Combined coverage is 100% (unit tests alone: 87%).
- The git history shows a red commit before each green commit for all four steps. In one case (`8a18ce1`), a new test was committed together with the implementation instead of separately.
- The tool description matches REQUIREMENTS §7.1 exactly, and no `tool.call` line contains an employee name.

### 7.4 M2 verification (2026-11-23)

Checked by the main Claude session on a copy of branch `m2-get-policy-limits` at `b60659c`, Python 3.14.6:
- `ruff format --check`, `ruff check` and `mypy --strict` all pass.
- 106 unit tests and 14 integration tests pass. Coverage is 100%.
- Each step has a red commit before its green commit.
- Both tool descriptions match REQUIREMENTS §7.1 and §7.2 exactly. `"none"` and `null` reach the client as distinct values.

