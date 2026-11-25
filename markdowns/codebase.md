# codebase.md: What the Code Does and Why

> This file is kept up to date with every code change, in the same commit.
> It describes the code **as it is now**, not as planned. For the plan, see `PLAN.md`. For the rules the system must follow, see `REQUIREMENTS.md`.

**Last updated:** 2026-11-25, milestone M7 in progress (the deterministic half of reflection in `agent/reflection.py`, the `reflection_verdict` tool and the reflection prompt; failing tests for `Reflector.reflect`, the model's reflection call). M1 to M6 are verified.

---

## 1. What this project is

An internal IT equipment request handler for the Week 7 lab:
- **A Python MCP server** exposes four tools over mock employee and policy data.
- **A ReAct agent** on the Claude API uses those tools to approve, deny or escalate free-text equipment requests.

Policy decisions are made by plain, tested Python functions. The LLM only interprets the request text, chooses which tools to call, and drafts the reply.

## 2. Current state

M0 is done. In M1, the mock employee data, its validating loader and `get_employee_info` exist as plain functions with tests, as does the JSON logging module. The real MCP server exposes one tool, `get_employee_info`, over streamable HTTP, and `scripts/client_demo.py` calls it. In M2, the role-entitlement policy (`data/policy.json`) and its validating loader exist with tests, as does `get_policy_limits`, as a pure function and as the server's second MCP tool. In M3, `check_request_eligibility` exists as a pure function and as the server's third MCP tool. In M4, `flag_for_human_review` exists as a plain function with injected dependencies (the queue path and the current time), with tests, and is the server's fourth MCP tool. An integration test walks the T2 and T3 tool sequences through the real protocol. In M5, the agent package exists with its pure cost functions and budget gate (`agent/costs.py`). OpenTelemetry tracing writes spans to JSON-lines files, and every log line carries the active `trace_id` and `span_id`. `LLMClient` (`agent/llm.py`) is the one wrapper every model call goes through: it records tokens, cost, latency and a span for each call, and is tested only with a fake. The agent connects to the MCP server as a client, gives Claude the server's tools and runs the one tool call Claude picks for a hardcoded request (brief step 5a): tested end to end with the fake and the in-process server. `scripts/agent_once.py` makes the real, paid call; it has not been run yet. `python -m it_requests.cost_report` prints spend from the ledger.

| File | What it does | Status |
|---|---|---|
| `src/it_requests/config.py` | `REFERENCE_DATE = 2026-11-22`, the fixed "today" for every policy check. `REPO_ROOT`, and from it `DATA_DIR`, `EMPLOYEES_FILE`, `POLICY_FILE`, `REVIEW_QUEUE_FILE`, `LOGS_DIR`, `APP_LOG_FILE`, `COST_LEDGER_FILE` and `TRACES_DIR`. The models (`DEV_MODEL`, `EVAL_MODEL`), the price table (`ModelPrices`, `PRICES_PER_MTOK`, `PRICES_CHECKED`, Haiku long-prompt prices) and the budget limits (`BUDGET_USD_DEFAULT`, `BUDGET_WARN_USD`, `MAX_TOKENS_PER_CALL`). | Tested |
| `data/employees.json` | The six employees from REQUIREMENTS §8, with fixed asset IDs. Top-level `data_version` (`employees-v1`) and `effective_date` (`2026-11-22`). | Tested (loads and validates) |
| `data/policy.json` | The role-entitlement matrix from REQUIREMENTS §4.1: 4 roles × 6 items, each with `max` and `refresh_months`. Top-level `data_version` (`policy-v1`) and `effective_date`. | Tested (loads and validates) |
| `src/it_requests/data.py` | Pydantic models (`EquipmentItem`, `Employee`, `EmployeeDirectory`), the `Role`, `Status` and `CatalogItem` literals, the `ROLES` and `CATALOG_ITEMS` tuples, `EMPLOYEE_ID_PATTERN`, `DataValidationError` and `load_employees(path)`. Policy models (`ItemLimit`, `PolicyTable`) and `load_policy(path)`. | Tested, 100% coverage |
| `src/it_requests/employees.py` | `normalize_employee_id`, `tenure_months` and `get_employee_info`: the pure function behind the MCP tool (REQUIREMENTS §7.1). | Tested, 100% coverage |
| `src/it_requests/policy.py` | `normalize_role` and `get_policy_limits`: the pure function behind the MCP tool (REQUIREMENTS §7.2). | Tested, 100% coverage |
| `src/it_requests/eligibility.py` | `add_months` and `check_request_eligibility`: the REQUIREMENTS §4.3 algorithm, the only source of truth for eligibility. | Tested, 100% coverage |
| `src/it_requests/reviews.py` | `ESCALATION_CODES`, `parse_reason` and `flag_for_human_review`: the only side-effecting tool, which appends review tickets to a JSON queue file (REQUIREMENTS §2.3, §7.4). | Tested, 100% coverage (including the write-failure path and temp-file cleanup); used by `server.py` |
| `src/it_requests/agent/costs.py` | `cost_usd`, `ledger_total`, `check_budget` and `budget_from_env`, with `UnknownModelError`, `LedgerCorruptError` and `BudgetExceededError`: pure cost maths and the budget gate (PLAN §4). | Tested, 100% coverage; not yet called by any component |
| `src/it_requests/agent/llm.py` | `LLMClient` (the one wrapper for paid model calls) and the `CreateMessage` protocol. Each call writes a cost-ledger line, an `llm.call` span and an `llm.call` log line. | Tested with a fake, 100% coverage; used by `single_call.py` |
| `src/it_requests/agent/tools.py` | `with_thought`, `FINAL_ANSWER_TOOL`, `strip_thought`, `parse_final_answer`: the ReAct tool definitions. | Unit-tested on the real server schemas, 100% coverage |
| `src/it_requests/agent/loop.py` | `run_agent`: the ReAct loop, with guards, the step cap and the E11 fallback. `AgentRequest`, `AgentResult`, `Step`, `GuardEvent`. | 81 integration tests with the fake model and the real in-process server, 99% coverage; one live twin (`tests/live`, paid, opt-in) |
| `src/it_requests/agent/trace_format.py` | `format_trace`: the printed Thought / Action / Observation / Final Answer trace. Pure. | Unit-tested, 100% coverage |
| `src/it_requests/agent/prompts.py` | `PROMPT_VERSION` (`m7-v1`), `SYSTEM_PROMPT_SINGLE_CALL` (M5), `SYSTEM_PROMPT_REACT` (rules copied from REQUIREMENTS §5, §6, §6.1, §10, plus the working method) and `SYSTEM_PROMPT_REFLECTION` (the six §11 questions word for word, C4, C5, the reply-only rule). | `test_prompts.py` checks every code, constraint, §11 question and key tool name, and no per-request content |
| `src/it_requests/agent/reflection.py` | The deterministic half of reflection: `code_findings`, `unsupported_dates`, `remove_sentences_with`, `REFLECTION_TOOL`, `ReflectionVerdict`, `parse_verdict`. Pure. | Unit-tested with observations from the real tool functions, 100% coverage |
| `src/it_requests/agent/mcp_bridge.py` | `MCPToolClient` protocol, `to_claude_tools` (MCP tool list to Claude tool definitions) and `call_tool` (one tool call with `request_id` and `traceparent` in `_meta`), plus the shared span helpers `traceparent` and `span_safe_arguments`. | Tested, 100% coverage |
| `src/it_requests/agent/single_call.py` | `run_single_tool_call`: one model call, then the one tool call Claude picks, run on the server, inside an `agent.run` trace. | Integration-tested with the fake and the in-process server, 100% coverage |
| `src/it_requests/agent/guards.py` | `check_tool_call` (C6, C7 before a call), `check_final_answer` (C1, C2, C3, required calls, C7 on the answer) and `violation_message`. Pure. | Unit-tested against results from the real tool functions, 100% coverage |
| `src/it_requests/cost_report.py` | `summarise`, `format_report` and `main`: spend from the cost ledger, in total and by env, stage and request, plus the budget left. | Tested, 100% coverage |
| `scripts/agent_once.py` | The live, paid entry point. Loads `.env`, runs the budget gate first, then makes one real Haiku call against the running server and prints the tool, arguments, result status, cost and trace path. | Not unit-tested (thin wiring of tested parts); type-checks with `mypy --strict`; not yet run |
| `scripts/run_agent.py` | One live, paid ReAct run for `--case T1` or `--text "..."`: budget gate first, prints `format_trace` and the cost, saves `traces/<case or request_id>.txt`. | Thin wiring; `mypy --strict` clean; not yet run |
| `scripts/run_eval.py` | Live, paid eval: `--cases` (default T1-T4) x `--repeat` (default 3) on `--model` (default Sonnet). Prints the run count and estimate, asks `Continue? [y/N]`, checks the budget gate before every run, writes `evals/results/<UTC>-<model>.jsonl`, prints the pass table and total cost. | Thin wiring; `mypy --strict` clean; not yet run |
| `src/it_requests/evals.py` | `load_cases`, `grade`, `summarise`, `format_summary`, `resolve_model`, `estimate_usd`. | Unit-tested, 100% coverage |
| `evals/cases.json` | T1 to T12 (`cases-v1`): request text, expected decision and code, required tools. | Loaded and checked by `test_evals.py` |
| `.env.example` | Placeholder `ANTHROPIC_API_KEY` and `BUDGET_USD=4.00`. Copied to the gitignored `.env`. | Committed; no real key |
| `src/it_requests/tracing.py` | `JsonLinesSpanExporter` and `make_tracer`: OpenTelemetry spans written as JSON lines to `traces/<request_id>.jsonl` (PLAN §6). | Tested, 97% coverage (an unused `force_flush` return); not yet called by any component |
| `src/it_requests/observability.py` | `request_id_var`, `REDACTED_KEYS`, `JsonFormatter`, `log_event` and `configure_logging`: structured JSON logging for the server and, later, the agent (PLAN §5). | Tested, 100% coverage; used by `server.py` |
| `src/it_requests/server.py` | `build_server(directory, policy_table, *, as_of)` registers all four tools (`get_employee_info`, `get_policy_limits`, `check_request_eligibility`, `flag_for_human_review`) as thin wrappers. Both log through one helper, `_call_and_log`. `main()` configures logging, loads both data files, logs `server.start` and serves on `http://127.0.0.1:8000/mcp`. | Integration-tested in-process (including `request_id` from `_meta`); `main()` checked by hand |
| `scripts/client_demo.py` | Connects over HTTP, lists the tools, calls `get_employee_info` for `E1001`, `" e1001 "` and `E9999`, then calls `get_policy_limits` for `standard`, `Manager` and `intern`, then calls `check_request_eligibility` for five cases, one per kind of verdict plus an unknown item, then calls `flag_for_human_review` three times: a new ticket, the same call again (duplicate) and an invalid reason (screenshot for deliverable #2). | Checked by hand |
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

### `eligibility.py` in detail

- **`add_months(d, months)`:** adds calendar months. If the target day doesn't exist, it uses that month's last day (§4.2), found with `calendar.monthrange`. For example, 2024-01-31 + 1 is 2024-02-29.
- **`check_request_eligibility(employee_id, item, *, directory, policy, as_of=REFERENCE_DATE)`:** runs the §4.3 steps in order, and the first step that applies wins.
  1. Bad ID format: `INVALID_EMPLOYEE_ID`, echoing the raw input.
  2. Unknown employee: `EMPLOYEE_NOT_FOUND`, echoing the normalised ID.
  3. Item not in the catalog, checked after trimming and lower-casing: `UNKNOWN_ITEM`, echoing the raw item.
  4. Status not `active`: `NEEDS_REVIEW`, rule R-STATUS. This returns before the policy is read.
  5. Role `max` is 0: `NOT_ELIGIBLE`, rule R-ROLE.
  6. Fewer units held than `max`: `ELIGIBLE`, rule R-NEW.
  7. `refresh_months` is `"none"`: `NOT_ELIGIBLE`, rule R-LIMIT.
  8. The oldest unit's refresh date is on or before `as_of`: `ELIGIBLE`, rule R-REFRESH-OK. `replaces_asset_id` names the unit to return.
  9. Otherwise: `NOT_ELIGIBLE`, rule R-REFRESH, with `next_eligible_date`.
- **Oldest unit:** the held unit with the earliest `issued_date`, with ties broken by `asset_id`.
- **Verdict keys:** every verdict has the same 15 keys:
  - `employee_id`, `item`, `status` and `rule_id`
  - `role` and `employment_status`
  - `units_held`, `max` and `refresh_months`
  - `oldest_issued_date`, `refresh_date` and `next_eligible_date`
  - `replaces_asset_id`
  - `as_of` and `data_versions` (both file versions)

  Keys that don't apply are `null`, and all dates are ISO strings.

### `reviews.py` in detail

- **`ESCALATION_CODES`:** `E1` to `E11` (REQUIREMENTS §5).
- **`parse_reason(reason)`:** the reason must be a code from `E1` to `E11` (any case), a colon, then a non-empty explanation. It returns `(CODE, explanation)`, or `None`.
  - The check is a full-match regex with an explicit 1–11 alternation, so `E11` is never read as `E1`, and `E12`, `E0` and `E1E2` fail.
- **`flag_for_human_review(employee_id, request, reason, *, queue_path, now)`:**
  - **Validation first:** an empty request returns `INVALID_REQUEST`. A bad reason returns `INVALID_REASON`. Nothing is read or written until both pass.
  - **Employee ID:** stripped, but not validated and not required to exist, so `E1` escalations can still be recorded.
  - **Duplicates:** the same employee (compared ignoring case and surrounding spaces) with the same request (compared stripped) returns the existing ticket with `duplicate: True`, and nothing is written.
  - **New ticket:** appended with all seven §2.3 fields. `review_id` is `REV-` plus the next four-digit number, and `created_at` is `now.isoformat()`.
  - **Returns:** `{"review_id", "status": "pending", "reason_code", "duplicate"}`.
- **Queue file:**
  - The format is `{"tickets": [...]}`. A missing file means an empty queue. The file and its folder are created on the first write.
  - Writes are atomic: a temp file in the same folder is fsynced, then moved over the queue with `os.replace`. The temp file is removed if anything fails.
  - An unreadable file, or one with the wrong shape (no `tickets` list, or a ticket missing a string field), returns `QUEUE_UNAVAILABLE`, and the file is left untouched.
  - A failed write also returns `QUEUE_UNAVAILABLE`.

### `agent/costs.py` in detail

- **`cost_usd(model, input_tokens, output_tokens, cache_read_tokens=0, cache_write_tokens=0)`:**
  - Prices each token kind separately from `config.PRICES_PER_MTOK`, and rounds to 6 decimals.
  - `input_tokens` means uncached input only, as the API reports it.
  - If a Haiku prompt's total input (uncached + cache read + cache write) is above 100K tokens, the whole call uses `HAIKU_LONG_PRICES`. Exactly 100K uses the normal prices.
  - An unknown model raises `UnknownModelError`. That is a programming error, not a tool result.
- **`read_ledger(path)`:**
  - Returns every entry of the JSONL ledger. A missing file gives no entries, and blank lines are skipped.
  - Any line that isn't a JSON object with a numeric `cost_usd` raises `LedgerCorruptError` naming the line number. `true` counts as non-numeric.
- **`ledger_total(path)`:** the sum of `cost_usd` over `read_ledger(path)`. The budget gate and the cost report therefore apply the same checks.
- **`check_budget(spent, *, budget, warn_at)`:** returns `"warn"` at or above `warn_at`, otherwise `"ok"`. It raises `BudgetExceededError` at or above `budget`. It does no logging; the caller logs.
- **`budget_from_env(environ)`:**
  - Reads `BUDGET_USD`, defaulting to $4.00.
  - A non-numeric, negative, `nan` or `inf` value raises `ValueError`. `0` is allowed, and means "refuse every run".

### `observability.py` in detail

- **`log_event(logger, event, level=INFO, **fields)`:** the one helper every call site uses. It passes `fields` to the formatter through `extra={"fields": ...}`.
- **`JsonFormatter`:** writes each record as one line of JSON.
  - **Standard keys:** `ts`, `level`, `component` and `event`, plus `request_id`, which is taken from `request_id_var`, and `trace_id` and `span_id`, taken from the active OpenTelemetry span (hex, or `null` outside a span). `ts` is UTC ISO-8601 with milliseconds and a `Z`, for example `2026-11-23T10:15:02.123Z`.
  - **Custom fields:** written after redaction. The standard keys are applied last, so a custom field can't overwrite them.
  - **Exceptions:** add `exc_type` and `exc_message`, with no traceback, so each record stays on one line.
  - **Non-JSON values:** dates and other values that JSON can't hold are written as strings (`json.dumps(default=str)`).
- **Redaction:** any field whose key matches `REDACTED_KEYS` (`name`, `api_key`, `authorization`, `password`, `token`), ignoring case, is written as `"[REDACTED]"`.
- **`configure_logging(component, *, log_file=None, level=INFO)`:**
  - Returns the named logger, writing to stderr, and also to `log_file` if one is given. Parent folders are created.
  - It closes and replaces existing handlers, so calling it twice never duplicates log lines.
  - Sets `propagate = False`.

### `agent/llm.py` in detail

- **`CreateMessage`:** a `Protocol` for the one call shape used, `create(**kwargs) -> Message`. mypy `--strict` rejects passing the SDK's `client.messages.create` directly, because its overloads include streaming. The caller adapts it with a three-line non-streaming function, shown in the docstring.
- **`LLMClient(*, create, model, env, ledger_path, tracer, logger, clock)`:** everything is injected. `clock` is a monotonic timer, used for latency only.
- **`call(*, request_id, run_id, stage, system, messages, tools) -> Message`:**
  - **Sends:** `model`, `max_tokens=MAX_TOKENS_PER_CALL` (4000), `system`, `messages`, `tools`, `tool_choice={"type": "auto"}` and `thinking={"type": "adaptive"}`.
  - **Records:** opens an `llm.call` span with `model`, `prompt_version` and `stage`, then adds the four token counts, `cost_usd`, `latency_ms` and `stop_reason`.
  - **Ledger:** appends one line to the cost ledger with `ts`, `request_id`, `run_id`, `stage`, `model`, `prompt_version`, `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_write_tokens`, `cost_usd`, `latency_ms`, `env` and `stop_reason`. `null` cache counts are recorded as 0.
  - **Log:** logs `llm.call` at INFO with the same numbers. It never logs messages, the system prompt, tools or the request text.
  - **Failure:** if `create` raises, the span is marked `ERROR`, no ledger line is written, and the exception propagates. There is no retry, because the SDK already retries.
  - **`system`** may be a string or a list of text blocks; either is passed through unchanged.
- **`system_blocks(text)`:** one text block with `cache_control: {"type": "ephemeral"}`. The cache prefix runs tools, then system, then messages, so this one breakpoint caches the tools and the system prompt for every loop step after the first.

### `agent/tools.py` in detail

- **`with_thought(claude_tools)`:** deep-copies each tool, puts a required string `thought` first in `properties` and `required`, sets `additionalProperties: false` in the schema and `strict: true` on the tool. `strict` is a tool-level field in the Messages API, not a schema key.
- **`FINAL_ANSWER_TOOL`:** `final_answer`, strict. Properties `thought`, `decision` (enum approve/deny/escalate), `rule_id` and `review_id` (string or null), `reason_code` (E1 to E11 from `reviews.ESCALATION_CODES`, or null) and `draft_reply`. All are required; null means "not applicable".
- **`strip_thought(tool_input)`:** returns `(thought, arguments)` without mutating the input. A missing `thought` becomes `""`.
- **`parse_final_answer(tool_input)`:** returns a `FinalAnswer`, or raises `ValueError` on a bad `decision` or a non-string field. It checks types only; `check_final_answer` decides whether the answer is allowed.

### `agent/mcp_bridge.py` and `agent/single_call.py` in detail

- **`to_claude_tools(listed)`:** maps each MCP tool to `{name, description, input_schema}`. A missing description becomes `""`. MCP-only fields such as `title` and `output_schema` are dropped.
- **`call_tool(client, name, arguments, *, request_id, traceparent)`:**
  - Calls the server with `meta={"request_id": ..., "traceparent": ...}`, and returns `structured_content`.
  - `is_error`, or no structured content, returns `{"error": "TOOL_CALL_FAILED", "tool": name}`.
  - A tool's own error dict passes through unchanged.
- **`run_single_tool_call(request_text, *, request_id, llm, mcp_client, tracer, logger)`:**
  - Sets `request_id_var` for the run and opens an `agent.run` root span, with `request_id`, a fresh `run_id`, `prompt_version` and `model`.
  - Logs `run.start`, and logs the request text at DEBUG only.
  - Lists the server's tools and makes one `llm.call` (stage `single_call`).
  - Takes the first `tool_use` block. If there is none, it returns `{"error": "NO_TOOL_CALL", ...}` and nothing is sent to the server.
  - Otherwise it runs that tool in a `tool.<name>` child span, sending that span's `traceparent`. The span records `tool`, `arguments` (as JSON, with `request` and `reason` redacted), `result_status` and `data_version` when present. It never records the result.
  - Logs `run.end` with the tool, result status, `cost_usd` and tokens, and sets `total_cost_usd` and `total_tokens` on the root span.
  - Returns `request_id`, `run_id`, `tool`, `arguments`, `result_status`, `result` and `cost_usd`.

### `agent/guards.py` in detail

- **`Observation(tool, arguments, result)`** and **`FinalAnswer(*, decision, rule_id, review_id=None, reason_code=None, draft_reply=None)`:** frozen dataclasses. `FinalAnswer` is keyword-only, and `rule_id` must be given but may be `None`.
- **`check_tool_call(tool, observations)`:** returns `"C6_STEP_LIMIT"` when 8 investigation calls are already made (failed ones count; the flag does not), `"C7_ALREADY_FLAGGED"` when a flag already returned a `review_id`, else `None`.
- **`check_final_answer(answer, observations)`:** returns every violation, in the order C1, C2, C3, `REQUIRED_CALLS`, C7.
  - Approve and deny read the **latest** successful eligibility result (`status`, `rule_id`).
  - Deny and escalate need the rule ID or review ID in `draft_reply` as a whole token, so `R-REFRESH` is not matched inside `R-REFRESH-OK`.
  - Escalate compares against the first flag that returned a `review_id`. E7 also needs a successful `get_employee_info` and `check_request_eligibility` before that flag.
  - Error results never satisfy `REQUIRED_CALLS`.
- **`violation_message(codes)`:** one sentence per code, joined, for the loop to send back to the model. An unknown code raises `KeyError`, because that is a bug in the loop.
- **Not checked yet:** that an E7 check returned `NOT_ELIGIBLE` (§5), and that the answer's `reason_code` matches the flag's.

### `agent/loop.py` in detail

- **`run_agent(request, *, llm, mcp_client, tracer, logger, max_model_calls=12)`:**
  - Sends the tools as `with_thought(to_claude_tools(...)) + [FINAL_ANSWER_TOOL]`, the system prompt as `system_blocks(SYSTEM_PROMPT_REACT)`, and a first user message of `"Request ID: ...\n\n"` plus the request text.
  - Each turn makes one `llm.call` (stage `react_step_<n>`) with a snapshot of the messages. The assistant content goes back unchanged, so thinking blocks keep their signature. All `tool_result` blocks go back in one user message.
  - **MCP tool:** `check_tool_call` first. A blocked call gets an `is_error` tool_result with `violation_message`, a `guard.blocked` WARN line, and no server call. Otherwise the thought is stripped, the server is called inside a `tool.<name>` span, and a `Step` is recorded. A tool's own error dict is sent as normal data, not `is_error`.
  - **`final_answer`:** `parse_final_answer`, then `check_final_answer`. A rejection (including `INVALID_FINAL_ANSWER`) goes back as `is_error`. One correction is allowed.
  - **No tool_use:** the reply is `"Call final_answer to finish."`, which counts as a model call.
- **Fallback (E11), run by the code:** the model-call limit, a second rejected final answer, or a turn after a C6 block that doesn't call `flag_for_human_review`. If a ticket exists, it is reused with its own code (C7). Otherwise the code flags with `"E11: <why>."`, using the last `employee_id` argument, else the first `E####` in the request text, else `UNKNOWN`. The reply is fixed text with the `review_id`. `agent.fallback` is logged at WARN.
- **Spans:** `agent.run` (request_id, run_id, prompt_version, model; at the end decision, rule_id or reason_code, review_id, fallback, total_cost_usd, total_tokens), with `llm.call` and `tool.*` children. Tool spans carry the redacted arguments, `result_status` and `data_version`, but **not the thought**: it can repeat the employee's name.
- **Logs:** `run.start`, `react.action`, `react.observation` (status only), `guard.blocked`, `agent.fallback`, `run.end` at INFO or WARN. The request text, thoughts, arguments and draft reply are at DEBUG only.

### `agent/trace_format.py` in detail

- **`format_trace(result)`:** `Thought:`, `Action: tool({json})` and `Observation: {full result}` per step, a `GUARD: blocked <tool> (<codes>)` line after the step it followed, the final thought, then `Final Answer: APPROVE (rule R-NEW)` or `ESCALATE (E6, review REV-0001)` (labelled `Final Answer (fallback)` on a fallback) and `Reply:`. The full observation is fine here: this text is printed for the graded trace, not logged.

### `agent/reflection.py` in detail

- **`code_findings(answer, observations)`:** returns codes in `CODE_FINDINGS` order:
  - `C4_UNSUPPORTED_DATE`: a `YYYY-MM-DD` in the reply that appears in the JSON text of no successful observation. Error results support nothing.
  - `DENY_MISSING_NEXT_DATE`: a denial whose latest successful eligibility result (`guards.latest_verdict`) has a `next_eligible_date` that the reply leaves out (§11 q4).
  - `ESCALATE_MISSING_REVIEW_ID`: an escalation whose reply doesn't cite `review_id` (§11 q5; the C3 guard already blocks this).
- **`unsupported_dates(reply, observations)`:** the dates behind C4, in order of appearance, without duplicates.
- **`remove_sentences_with(reply, tokens)`:** splits on `. `, `! `, `? ` and newlines (so `$1.50` stays whole), drops each sentence that holds a token along with its separator, and returns the rest stripped.
- **`REFLECTION_TOOL`:** `reflection_verdict`, strict. `thought`, `answers` (`q1` to `q6`, each a required boolean), `verdict` (`CONFIRMED` or `REVISED`), and `revised_reply` and `what_changed` (string or null). Every property is required.
- **`parse_verdict(tool_input)`:** returns a `ReflectionVerdict` (with a copy of `answers`), or raises `ValueError` on any shape problem, a `REVISED` verdict with a null or blank reply, or a `CONFIRMED` verdict that carries a reply.

### `cost_report.py` in detail

- **`summarise(entries, *, budget)`:** pure. Returns a `CostSummary` with `total`, `calls`, `by_env`, `by_stage`, `by_request`, `budget` and `remaining`. A missing key is grouped under `"unknown"`. `remaining` can be negative, so an overspend shows.
- **`format_report(summary)`:** the text report, to 4 decimals, with the biggest spend first in each section.
- **`main(*, ledger_path=None, environ=None)`:** reads the ledger with `read_ledger`, takes the budget from `BUDGET_USD`, and prints the report. The parameters let tests use a tmp ledger.

### `tracing.py` in detail

- **`JsonLinesSpanExporter(path)`:** appends one JSON line per finished span. Each line has:
  - `name`, `trace_id` (32 hex characters), `span_id` (16 hex) and `parent_span_id` (`null` for the root span)
  - `start` and `end` (UTC ISO-8601) and `duration_ms`
  - `status` (`UNSET`, `OK` or `ERROR`) and `attributes`

  It creates the parent folder, and opens the file for each export, so no handle stays open.
- **`make_tracer(path)`:** returns `(tracer, provider)`.
  - It builds a new `TracerProvider` with a `SimpleSpanProcessor` and the exporter, and never sets the global provider.
  - The tracer is typed as the OpenTelemetry API's `Tracer`.
  - The caller shuts the provider down at the end of a run.

### `server.py` in detail

- **`build_server(directory, policy_table, *, as_of=REFERENCE_DATE, review_queue_path=REVIEW_QUEUE_FILE, clock=_utc_now)`:** a factory. Tests inject their own data, and importing the module never reads files or opens ports. The parameter is called `policy_table` because `policy` is the module name. Tests pass a `tmp_path` queue and a fixed clock.
- **`_call_and_log(tool, call, *, ctx, data_version, inputs, result_fields, status_of)`:** the one helper every tool uses.
  - Sets `request_id_var` from the call's `_meta` (`_request_id(ctx)`) for the length of the call, then resets it, so every `tool.call` line carries the agent's `request_id`. Only a string is accepted.
  - Times the call and logs one `tool.call` event, then returns the result unchanged, including error dicts. `is_error` therefore stays `False` for tool errors.
  - The event fields are `tool`, the inputs, any result-derived fields, `status`, `data_version` and `duration_ms`.
  - `status_of` decides the logged status. The default is `ok` or the error code.
- **`request_id` from `_meta` (MCP SDK 2.3):** each tool takes a `ctx: Context[Any, Any]` parameter. The SDK injects it and leaves it out of the input schema, so the model sees the same arguments. `ctx.request_context.meta` is a dict; over `Client` it always holds the SDK's own `io.modelcontextprotocol/*` keys, so `request_id` is read with `.get`.
- **The `get_employee_info` tool:**
  - Logs `employee_id`, the raw input exactly as sent.
  - Logs `resolved_employee_id`: the normalised ID on success, or `null` on any error.
  - Never logs the employee's name.
- **The `get_policy_limits` tool:** logs `role`, the raw input, and `data_version` `policy-v1`.
- **The `check_request_eligibility` tool:**
  - Logs `employee_id` and `item` (raw), plus `resolved_employee_id` and `rule_id`. Both are `null` on any error.
  - Logs the verdict as `status` (`ELIGIBLE`, `NOT_ELIGIBLE` or `NEEDS_REVIEW`), or the error code.
  - Logs `data_version` as a dict of both file versions, matching the result's `data_versions`.
- **The `flag_for_human_review` tool:**
  - Calls `reviews.flag_for_human_review` with `queue_path=review_queue_path` and `now=clock()`.
  - Logs `employee_id` (raw), `reason_code`, `review_id` and `duplicate`. The last three are all `null` on an error.
  - `status` is `ok` or the error code.
  - Never logs `request`, or the explanation part of the reason.
- **Tool descriptions:** each tool's docstring, word for word from REQUIREMENTS §7.1 to §7.4.
- **`main()`:**
  - Configures logging, to stderr and `logs/app.jsonl`, and loads `data/employees.json` and `data/policy.json`.
  - Logs `server.start`, with `data_version`, `policy_version`, `employee_count`, `review_queue` (the path relative to the repo root, never the contents), `host` and `port`.
  - Runs streamable HTTP on `127.0.0.1:8000`.
- **Ways to start it:** `make serve`, `python -m it_requests.server` or the installed `it-requests-server` command.

## 3. Layout

```
Build-MCP/
├── src/it_requests/        # the installable package (all business logic lives here)
│   ├── __init__.py
│   ├── config.py           # shared constants: dates, paths, models, prices, budget limits
│   ├── data.py             # Pydantic data models, load_employees and load_policy
│   ├── employees.py        # get_employee_info and its helpers (pure)
│   ├── policy.py           # get_policy_limits and normalize_role (pure)
│   ├── eligibility.py      # check_request_eligibility and add_months (pure)
│   ├── reviews.py          # flag_for_human_review and parse_reason (writes the review queue)
│   ├── agent/
│   │   ├── __init__.py
│   │   ├── costs.py        # cost_usd, ledger_total, check_budget, budget_from_env (pure)
│   │   ├── llm.py          # LLMClient: every model call, with ledger, span and log
│   │   ├── prompts.py      # PROMPT_VERSION and system prompts
│   │   ├── guards.py       # C1-C3, C6, C7 and required-call checks (pure)
│   │   ├── loop.py         # run_agent: the ReAct loop, guards, step cap, E11 fallback
│   │   ├── tools.py        # thought on every tool, final_answer, parse helpers
│   │   ├── reflection.py   # code checks on the draft, the reflection_verdict tool
│   │   ├── trace_format.py # the printed Thought/Action/Observation trace
│   │   ├── mcp_bridge.py   # MCP tools to Claude tool definitions; call one tool
│   │   └── single_call.py  # run_single_tool_call (brief step 5a)
│   ├── cost_report.py      # python -m it_requests.cost_report
│   ├── observability.py    # JSON logging: log_event, configure_logging
│   ├── tracing.py          # OpenTelemetry spans to traces/<request_id>.jsonl
│   ├── server.py           # MCP server: build_server (thin tool wrappers) and main
│   └── py.typed            # marks the package as typed for mypy
├── data/
│   ├── employees.json      # mock employees (REQUIREMENTS §8)
│   └── policy.json         # role entitlements (REQUIREMENTS §4.1)
├── tests/
│   ├── conftest.py         # FakeMessages: a fake Anthropic messages API (real SDK types)
│   ├── unit/               # test_config, test_data, test_policy_data, test_employees, test_policy, test_eligibility, test_reviews, test_observability, test_costs, test_tracing, test_llm, test_mcp_bridge, test_cost_report, test_guards, test_tools, test_prompts, test_trace_format, test_evals, test_reflection, test_test_style (AAA style guard)
│   ├── integration/        # conftest (server, server_log, call_tool, listed_tools, tool_call_records), test_server_employee_tool, test_server_policy_tool, test_server_eligibility_tool, test_server_review_tool, test_server_all_tools, test_agent_single_call, test_agent_loop, test_server_meta
│   └── live/               # test_real_agent: one PAID T1 run on Haiku (slow, eval); `make test-live` only
├── scripts/
│   ├── client_demo.py      # calls the running server over HTTP
│   ├── agent_once.py       # one live, paid agent run (brief step 5a)
│   ├── run_agent.py        # one live, paid ReAct run with the printed trace
│   └── run_eval.py         # live, paid eval with a pass/fail table
├── evals/
│   ├── cases.json          # T1 to T12 (REQUIREMENTS §9)
│   └── results/            # one JSONL file per eval run
├── examples/step1/         # brief step-1 demo (do not modify)
├── markdowns/              # REQUIREMENTS, PLAN, codebase, MILESTONES, prompts/
├── .github/workflows/ci.yml
├── pyproject.toml          # package metadata, dependencies, tool configuration
├── Makefile                # `make check` = the same steps as CI
├── CLAUDE.md               # instructions for coding agents
├── .env.example          # placeholder keys; copy to the gitignored .env
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
pytest            # fast tests only (addopts deselects `slow`, so nothing paid runs)
make test-live    # PAID (~$0.004): one real T1 run; needs `make serve` and ANTHROPIC_API_KEY
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
make reset-reviews  # optional: empty the review queue so the demo's first ticket is REV-0001
make client-demo    # terminal 2: lists tools, calls all four tools
```

**Agent, one live tool call** (paid: well under $0.01 on Haiku, an estimate; needs `.env` with a real `ANTHROPIC_API_KEY`):
```bash
cp .env.example .env    # once, then put your key in .env
make serve              # terminal 1
make agent-once         # terminal 2: budget gate, one Haiku call, one tool call on the server
make cost-report        # spend so far from logs/cost_ledger.jsonl
```
`python scripts/agent_once.py --model claude-sonnet-5-5 --env eval` uses Sonnet. The trace goes to `traces/REQ-0501.jsonl`.

**Agent, full ReAct run and eval** (paid; the server must be running):
```bash
make agent CASE=T3      # one run of a case (Haiku by default), printed trace, traces/T3.txt
python scripts/run_agent.py --text "E1002: can I get a headset?" --model sonnet
make reset-reviews      # optional: start the eval with an empty review queue
make eval               # T1-T4 x 3 on Sonnet: shows the estimate (about $1.02) and asks first
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

40. **The eligibility steps run in the exact §4.3 order, and the first match wins.**
    *Why:* the order is part of the rules. For example, an on-leave employee asking for an item their role can't have gets `NEEDS_REVIEW`, because a human must look at any non-active employee first. Tests pin each ordering case.

41. **The "oldest unit" tie is broken by `asset_id`.**
    *Why:* E1003 holds two monitors issued on the same day. Without a tie-break, the unit the employee is told to return could change between runs, or when the data file is reordered.

42. **`R-REFRESH-OK` returns `replaces_asset_id`.**
    *Why:* REQUIREMENTS §10 says a refresh approval is a replacement, and the reply must name the unit being returned. It never raises the employee above `max`.

43. **Every verdict has one fixed set of 15 keys, with `null` where a key doesn't apply.**
    *Why:* the agent, the tests and the logs never have to check whether a key is present.

44. **`data_versions` names both data files.**
    *Why:* the verdict depends on the employee data and on the policy. Lineage (PLAN §3) must be able to trace it back to both.

45. **An `assert` documents the invariant before the refresh step.**
    *Why:* by step 8, `max > 0` and units held equal `max`, so a unit exists. The `ItemLimit` validator guarantees `refresh_months` is a positive int. The assert states this for mypy and for readers. No valid data can trigger it, so it doesn't conflict with "errors are returned as data".

46. **The eligibility tool logs the verdict as `status`, through a `status_of` callback on `_call_and_log`.**
    *Why:* `jq 'select(.status=="NOT_ELIGIBLE")'` finds every denial without reading tool results. It isn't the helper's default, because `get_employee_info`'s result also has a `status` key (the employment status), which must not be logged as a tool status. The callback extends the shared helper instead of duplicating it.

47. **The eligibility tool logs `data_version` as a dict of both versions.**
    *Why:* the verdict depends on both data files. The dict matches the result's `data_versions`, so a log line and a result can be compared directly.

48. **`flag_for_human_review` takes `queue_path` and `now` as arguments.**
    *Why:* tests use `tmp_path` and a fixed time, so they are deterministic and never touch the real queue. The server passes the real path and the real time.

49. **Arguments are validated before the queue is read or written.**
    *Why:* REQUIREMENTS §7.4. A rejected call leaves no trace, so a bad argument can never create a half-valid ticket.

50. **The duplicate key is employee plus request, compared ignoring case and surrounding spaces on the ID, and stripped on the request.**
    *Why:* §7.4 makes the tool idempotent. A retried call, for example after a timeout, returns the same ticket. This also protects C7 (at most one flag per request). The first ticket's reason code wins.

51. **The queue is written atomically: a temp file, fsync, then `os.replace`.**
    *Why:* a crash mid-write leaves either the old file or the new one, never half a JSON file. The temp file is in the same folder, because a replace across filesystems isn't guaranteed to be atomic.

52. **A corrupt queue file is reported as `QUEUE_UNAVAILABLE` and never overwritten.**
    *Why:* "fixing" it by rewriting would destroy every existing ticket. A human must look at it. The shape of every ticket is checked when the file is read, so the duplicate check can't crash on a hand-edited file.

53. **The employee ID is stored stripped, not byte-for-byte as sent.**
    *Why:* the M4 prompt says so, and stray spaces would otherwise break duplicate detection. REQUIREMENTS §2.3 says "stored as given". This was flagged in the red step, and the decision was to go ahead with stripping. "As given" is read as "even if the employee doesn't exist".

54. **The server gets `clock` as a function, not a fixed time.**
    *Why:* one server stamps many tickets, so it must read the time at each call. Tests pass `lambda: FIXED_NOW`. The default, `_utc_now`, is the only line not covered by tests, and was checked in a live run.

55. **The review tool never logs the request text or the reason's explanation.**
    *Why:* both are free text that can hold names, medical details (E9) or other personal data. Logs are kept longer and read more widely than the queue (PLAN §5). Tests check the raw log text, on both the success and the error path.

56. **`server.start` logs the queue path relative to the repo root.**
    *Why:* an absolute path would put the local home folder, and with it the OS username, into logs and screenshots.

57. **`make reset-reviews` deletes `data/review_queue.json`.**
    *Why:* the demo then starts clean, so its first ticket is `REV-0001` and the duplicate call is easy to see. The queue is gitignored runtime output.

58. **Prices live in `config.py` as frozen `ModelPrices` records, with the date they were checked.**
    *Why:* named fields can't be read from the wrong position, as a bare tuple could. Prices change, and the ledger is only as right as this table, so the date shows how stale it might be (PLAN §4).

59. **The cost functions are pure: no network, no SDK and no logging.**
    *Why:* every cost and budget rule is unit-tested without spending anything. The LLM wrapper and the run start-up do the I/O and the logging.

60. **A corrupt ledger line raises `LedgerCorruptError` instead of being skipped.**
    *Why:* if the ledger can't be read, total spend is unknown. Skipping lines would undercount spend and could let runs continue past the budget, so the gate refuses to run until a human fixes the file.

61. **The budget gate refuses at exactly the budget (`>=`).**
    *Why:* the run that would cross the line is refused, so real spend stays near $4.00, not well above it. The warning also starts at exactly $3.00. The M5 prompt asked for that, whereas PLAN §4 says "passes $3.00".

62. **`budget_from_env` rejects `nan` and `inf`.**
    *Why:* both parse as floats and aren't negative, but either would switch the gate off: `spent >= nan` is always False, and no spend reaches `inf`.

63. **The Haiku long-prompt tier counts cached tokens toward the 100K threshold, and keeps the normal cache prices.**
    *Why:* the tier depends on the whole prompt's size. The price source gives long-prompt input and output prices only, so the cache prices are unchanged. This was flagged in the red step.

64. **`opentelemetry-sdk` is pinned to exactly the installed `opentelemetry-api` version (1.45.2).**
    *Why:* the SDK and the API are released together, and a mismatched pair can fail at import or drop spans. The API comes in with `mcp`.

65. **Traces go to JSON-lines files, through our own small exporter.**
    *Why:* a prototype needs no tracing backend, and the files can be read with `jq` and used in screenshots (PLAN §6). Pointing at Jaeger later is a one-line exporter change.

66. **`make_tracer` builds a new provider each time, and never sets the global one.**
    *Why:* the global provider can be set only once per process, so tests would depend on their order. The tracer is passed in explicitly, like `as_of` and `clock`.

67. **The exporter appends instead of overwriting.**
    *Why:* re-running the same request keeps its earlier traces. Each run is told apart by its own `trace_id`.

68. **`SimpleSpanProcessor`, not a batching processor.**
    *Why:* each span is written as soon as it ends, so a crash mid-run still leaves every finished span on disk. The volume is a handful of spans per run, so batching would gain nothing.

69. **Log lines carry `trace_id` and `span_id` in the same hex format as the trace files.**
    *Why:* a log line can be matched to its span (PLAN §5). The IDs are read from OpenTelemetry's current-span context, which is a ContextVar like `request_id_var`, so the logger needs no tracer passed in. Outside a span both are `null`.

70. **Every model call goes through `LLMClient`.**
    *Why:* cost tracking and tracing can't be bypassed. The ledger the budget gate sums is complete only if no code calls the SDK directly (PLAN §2).

71. **`LLMClient` takes a `create` function typed by a small `CreateMessage` protocol, not the SDK client.**
    *Why:* tests pass `FakeMessages.create`, and mypy `--strict` rejects the SDK's overloaded `messages.create` (its overloads include streaming). The caller adapts the SDK with a non-streaming function. Checked with mypy in a scratch file before choosing.

72. **`tool_choice` is `auto`, with adaptive thinking.**
    *Why:* forced `tool_choice` returns a 400 on Sonnet 5.5, and the same code must run on Haiku and Sonnet. The system prompt asks for exactly one tool instead.

73. **A failed call writes no ledger line and is not retried.**
    *Why:* no tokens are billed for a failed request, so recording one would overstate spend. The SDK already retries 429 and 5xx twice, so a second retry layer would multiply waits and calls.

74. **The ledger, span and log carry numbers only, never prompt content.**
    *Why:* messages and the request text can hold names and other personal data (CLAUDE.md, PLAN §5). Tests check the raw log text for a name and the system prompt.

75. **`PROMPT_VERSION` goes on every ledger line, span and log line.**
    *Why:* any change in cost or behaviour can be traced to the prompt that caused it. It is bumped on every prompt edit.

76. **The test fake builds real `anthropic.types.Message` objects, and lives in `tests/conftest.py`.**
    *Why:* a field-name mistake (for example `usage.cache_read_tokens`) fails the test, where a dict or `MagicMock` would quietly return a value. It sits at the top of `tests/`, so unit and integration tests can share it.

77. **The agent sends `request_id` and a W3C `traceparent` in each MCP call's `_meta`.**
    *Why:* PLAN §5 asked M5 to verify that SDK 2.3 supports this. It does: `RequestParamsMeta` is an open TypedDict, and an integration test proves the values are sent. The `traceparent` names the tool span, so a server span added later joins under the call that caused it. The server doesn't read `_meta` yet.

78. **Spans and logs never hold a tool's result, and free-text arguments (`request`, `reason`) are redacted in spans.**
    *Why:* `get_employee_info` returns the employee's name, and a request is the employee's own words. An integration test reads E1002's name from the data file and checks it appears nowhere in the trace, ledger or logs.

79. **A reply with no `tool_use` returns `NO_TOOL_CALL` and sends nothing to the server.**
    *Why:* the model must not decide anything in M5. If it answers in prose, nothing is run on its behalf, and the outcome is reported as data.

80. **`usage_numbers` is shared by `LLMClient` and the run summary.**
    *Why:* the cost a run reports (`run.end`, the result's `cost_usd` and the root span) always equals its ledger line. A test checks they match.

81. **`read_ledger` was split out of `ledger_total`.**
    *Why:* the cost report needs the entries, not just the sum, and both must apply the same corrupt-line checks. It was a refactor with tests green, and the existing `ledger_total` tests passed unchanged.

82. **`scripts/agent_once.py` runs the budget gate before creating the Anthropic client.**
    *Why:* a refused run must spend nothing. The script stays thin and outside the package: every part it wires together is tested, and it type-checks with `mypy --strict`. It is run by hand only, because it is a paid call.

83. **`python-dotenv` loads `.env`, and the API key is never read by our code.**
    *Why:* `anthropic.Anthropic()` reads `ANTHROPIC_API_KEY` itself, so the key never passes through code that could print or log it (CLAUDE.md). `.env.example` is committed with placeholders only.

84. **The guards use the latest eligibility result, and only successful results satisfy required calls.**
    *Why:* if two checks disagree, an older `ELIGIBLE` must not back an approval; the agent escalates instead of picking the convenient verdict. An error result is not data, so it can't count as the lookup. C6 still counts every call, failed or not, so a model retrying a bad ID reaches the limit.

85. **Rule and review IDs must appear in the reply as whole tokens.**
    *Why:* `R-REFRESH` is a substring of `R-REFRESH-OK`. A plain `in` check would pass a denial that cites the wrong rule.

86. **`check_final_answer` returns all violations in a fixed order.**
    *Why:* the model can fix every problem in one retry, and tests compare exact lists.

87. **`strict: true` replaces forced `tool_choice`, and `thought` is the first property.**
    *Why:* Sonnet 5.5 rejects forced tool choice, and strict mode still guarantees schema-valid arguments. The model writes properties in order, so it states its reason before filling in the arguments.

88. **The system prompt copies the written rules and holds no per-request content.**
    *Why:* the model applies REQUIREMENTS word for word instead of inventing rules. Identical text on every run is what makes it cacheable, and `system_blocks` caches it with the tools.

89. **`parse_final_answer` raises `ValueError` instead of returning an error dict.**
    *Why:* it is called by the loop, not exposed as a tool, so CLAUDE.md rule 6 doesn't apply. A refusal or a `max_tokens` cut-off can break the schema even in strict mode; the loop catches the error and tells the model.

90. **The code, not the model, escalates when a run can't end cleanly (the E11 fallback).**
    *Why:* "never guess" must hold even when the model misbehaves. Hitting the model-call limit, a second rejected answer, or ignoring the step limit all end in one ticket and a fixed, safe reply, with no further paid call.

91. **Thoughts go to DEBUG logs and the printed trace, never to spans.**
    *Why:* a thought can repeat the employee's name, and trace files are kept on disk like logs (CLAUDE.md). The prompt asked for the thought on the tool span; this was flagged and accepted at the red step.

92. **Each model call gets a snapshot of the messages, and the assistant content is sent back unchanged.**
    *Why:* the API checks thinking-block signatures on the next turn, so blocks must not be rebuilt. The snapshot also keeps the fake's recorded calls accurate.

93. **`traceparent` and `span_safe_arguments` moved from `single_call.py` to `mcp_bridge.py`.**
    *Why:* the loop and the single call now share one copy of the redaction rule.

94. **The server reads `request_id` through an injected `Context` parameter.**
    *Why:* it is the SDK 2.3 way to reach a request's `_meta`, and the SDK leaves `Context` out of the input schema, so what the model sees is unchanged (pinned by `test_server_meta.py`). The ContextVar is reset after each call, so a later call without meta can't inherit the ID.

95. **Eval logic lives in `it_requests.evals`, not in the scripts.**
    *Why:* grading decides what "pass" means, so it must be tested. The scripts only wire live services to tested functions, like `agent_once.py`.

96. **A fallback run never passes, and only successful tool calls count as required calls.**
    *Why:* a fallback means the model didn't finish on its own, even if the code's E11 escalation happens to match. Counting only successful calls is the same rule the guards use.

97. **`run_eval.py` asks before spending and checks the budget gate before every run.**
    *Why:* a 12-run Sonnet eval costs about $1 (estimate), a quarter of the budget. The prompt keeps a human in the loop, and a per-run gate stops cleanly with the results so far on disk.

98. **Reflection is split: code checks what it can, the model judges the rest.**
    *Why:* invented dates, a missing next date and a missing review ID can be checked exactly, with no cost and no variation between runs. The model's pass is kept for what code can't judge (C5 promises, whether every item was answered), and is told to fix the code findings.

99. **A date counts as supported if it appears anywhere in a successful result's JSON text.**
    *Why:* dates live in nested fields (`equipment[].issued_date`, `next_eligible_date`, `as_of`). Searching the text needs no list of date fields to keep in step with the tools.

100. **`parse_verdict` rejects a `CONFIRMED` verdict that carries a revised reply.**
    *Why:* the two contradict each other, so neither can be trusted. The caller then keeps the original draft, which the guards have already accepted.

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
| 2026-11-23 | M3 | Failing tests for `check_request_eligibility` and `add_months` (`test_eligibility.py`) | TDD red step |
| 2026-11-23 | M3 | `eligibility.py`: `add_months`, `check_request_eligibility` | REQUIREMENTS §4.3 as a pure function, before any MCP wrapping |
| 2026-11-23 | M3 | Failing integration tests for the `check_request_eligibility` tool (`test_server_eligibility_tool.py`); tool-list test expects three tools | TDD red step |
| 2026-11-23 | M3 | `check_request_eligibility` MCP tool; `status_of` option on `_call_and_log`; client demo eligibility section | Third tool exposed over MCP and proven end to end over HTTP |
| 2026-11-24 | M4 | Failing tests for `flag_for_human_review` and `parse_reason` (`test_reviews.py`) | TDD red step |
| 2026-11-24 | M4 | `reviews.py`: `ESCALATION_CODES`, `parse_reason`, `flag_for_human_review` with an atomic JSON queue; test parameter renamed from `request` to `request_text` | REQUIREMENTS §7.4 as a plain function with injected dependencies |
| 2026-11-24 | M4 | Failing integration tests for the `flag_for_human_review` tool and an all-tools walk-through; `review_queue_path` and fixed-clock fixtures | TDD red step |
| 2026-11-24 | M4 | `flag_for_human_review` MCP tool (injected queue path and clock), `REVIEW_QUEUE_FILE`, `make reset-reviews`, client demo review section | All four tools exposed over MCP and proven end to end over HTTP |
| 2026-11-24 | M5 | Failing tests for cost maths and the budget gate (`test_costs.py`) | TDD red step |
| 2026-11-24 | M5 | `config.py`: `REPO_ROOT`, log and trace paths, models, price table, budget limits. New `agent/` package with `costs.py` | Cost transparency and the budget gate, before any paid call |
| 2026-11-24 | M5 | Failing tests for the span exporter and log-trace correlation (`test_tracing.py`, `test_observability.py`); `opentelemetry-sdk==1.45.2` pinned and installed so the tests can create real spans | TDD red step |
| 2026-11-24 | M5 | `tracing.py` (`JsonLinesSpanExporter`, `make_tracer`); `trace_id` and `span_id` in every log line | Traces per request, cross-referenced with logs (PLAN §5, §6) |
| 2026-11-24 | M5 | Failing tests for `LLMClient` (`test_llm.py`); shared `FakeMessages` fake in `tests/conftest.py`; `anthropic==2.13.0` pinned and installed so the fake can build real SDK types | TDD red step |
| 2026-11-24 | M5 | `agent/llm.py` (`LLMClient`, `CreateMessage`) and `agent/prompts.py` (`PROMPT_VERSION`, `SYSTEM_PROMPT_SINGLE_CALL`) | One wrapper for every paid model call, so cost tracking and tracing can't be bypassed |
| 2026-11-24 | M5 | Failing tests for the MCP bridge, the cost report and the agent's single tool call (`test_mcp_bridge.py`, `test_cost_report.py`, `test_agent_single_call.py`); `anyio_backend` fixture moved to `tests/conftest.py` | TDD red step |
| 2026-11-24 | M5 | `agent/mcp_bridge.py`, `agent/single_call.py`, `cost_report.py`, `read_ledger` and `usage_numbers` refactors, `scripts/agent_once.py`, `.env.example`, `python-dotenv` pin, `make agent-once` and `make cost-report` | Brief step 5a: the agent makes one tool call through MCP; the live run is ready for Julian |
| 2026-11-24 | M6 | Failing tests for the constraint guards (`test_guards.py`, 43 test functions): C1, C2, C3, C6, C7 and the decision table's required calls, with observations built from the real tool functions | TDD red step |
| 2026-11-24 | M6 | `agent/guards.py`: `Observation`, `FinalAnswer`, `check_tool_call`, `check_final_answer`, `violation_message` | C1, C2, C3, C6 and C7 enforced in code before the ReAct loop uses them |
| 2026-11-24 | M6 | Failing tests for the ReAct tools (`test_tools.py`), the ReAct system prompt (`test_prompts.py`) and `system_blocks` (`test_llm.py`); the `prompt_version` assertion moves to `m6-v1` | TDD red step |
| 2026-11-24 | M6 | `agent/tools.py`, `SYSTEM_PROMPT_REACT` and `PROMPT_VERSION` `m6-v1` in `agent/prompts.py`, `system_blocks` and block-list `system` in `agent/llm.py` | Every Action carries a Thought, the decision comes back as data, and the fixed prompt prefix is cached |
| 2026-11-24 | M6 | Failing tests for the ReAct loop (`tests/integration/test_agent_loop.py`) and the printed trace (`test_trace_format.py`); `build_message` in `tests/conftest.py` takes `tool_uses` (several tool_use blocks, ids `toolu_01`, `toolu_02`, ...) and `thinking` | TDD red step |
| 2026-11-24 | M6 | `agent/loop.py` (`run_agent`, `AgentRequest`, `AgentResult`, `Step`, `GuardEvent`), `agent/trace_format.py` (`format_trace`); `traceparent` and `span_safe_arguments` moved to `mcp_bridge.py` | The ReAct loop with guards, the step cap and the E11 fallback enforced in code, plus the printed trace |
| 2026-11-24 | M6 | `evals/cases.json` (T1 to T12, `cases-v1`); failing tests for the eval helpers (`test_evals.py`) and for the server logging `request_id` from `_meta` (`test_server_meta.py`) | TDD red step |
| 2026-11-24 | M6 | `evals.py`; `server.py` reads `request_id` from `_meta`; `guards.cites` made public; `scripts/run_agent.py`, `scripts/run_eval.py`; `make agent` and `make eval` | Live runs with a printed trace, a graded eval, and server logs joined to the agent's `request_id` |
| 2026-11-25 | M7 | Failing tests for the deterministic reflection checks, the `reflection_verdict` tool and `parse_verdict` (`test_reflection.py`), and for `SYSTEM_PROMPT_REFLECTION` and `PROMPT_VERSION` `m7-v1` (`test_prompts.py`) | TDD red step |
| 2026-11-25 | M7 | `agent/reflection.py`; `SYSTEM_PROMPT_REFLECTION` and `PROMPT_VERSION` `m7-v1` in `agent/prompts.py`; `guards.latest_verdict` made public | The deterministic half of reflection, ready for the model's reflection call |
| 2026-11-25 | M7 | Failing tests for `Reflector.reflect` and `ReflectionResult` (`test_reflection.py`): verdict handling, the copy rule, discarded revisions, code-made date removal, unparsed output, the ledger, span and logs | TDD red step |

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

### 7.5 M3 verification (2026-11-23)

Checked by the main Claude session on a copy of branch `m3-check-eligibility` at `c1c6a02`, Python 3.14.6:
- `ruff format --check`, `ruff check` and `mypy --strict` all pass.
- 152 unit tests and 24 integration tests pass. Coverage is 100%.
- Each step has a red commit before its green commit.
- `eligibility.py` follows the REQUIREMENTS §4.3 step order exactly. All 13 rows of the §8 table, the boundary dates and the month-end clamp are tested.
- One note: `eligibility.py` uses an `assert` to narrow types before the refresh step. The `ItemLimit` validator already guarantees that condition, so it is safe here. Python's `-O` flag strips asserts, so never rely on one for a check the data could actually fail.

### 7.6 M4 verification (2026-11-24)

Checked by the main Claude session on a copy of branch `m4-flag-for-review` at `a2cb9f1`, Python 3.14.6:
- `ruff format --check`, `ruff check`, `mypy --strict` and `pip-audit` all pass.
- 193 unit tests and 32 integration tests pass. Coverage is 98%.
- Each step has a red commit before its green commit.
- All four tool descriptions match REQUIREMENTS §7 exactly.
- **Untested lines (optional follow-up):**
  - `reviews.py` lines 75 to 77 (temp-file cleanup when a write fails) and 146 to 147 (an `OSError` on write becoming `QUEUE_UNAVAILABLE`). A test that monkeypatches `os.replace` to raise `OSError` would cover both.
  - `server.py` line 86 (`_utc_now`), which only runs in the real server.
- **`AGENTS.md`:** added as a copy of `CLAUDE.md` for Codex agents. A blanket find-and-replace had turned the Anthropic model IDs into `Codex-haiku-5-5` and `Codex-sonnet-5-5`. These are corrected back to `claude-haiku-5-5` and `claude-sonnet-5-5`, so an agent writing M5 code can't call a model that doesn't exist.

### 7.7 M5 verification (2026-11-24)

Checked by the main Claude session on a copy of branch `m5-agent-single-call` at `041d156`, Python 3.14.6:
- `ruff format --check`, `ruff check`, `mypy --strict` and `pip-audit` all pass.
- 263 unit tests and 39 integration tests pass. Coverage is 99%, and every new agent module is at 100%.
- Each of the four steps has a red commit before its green commit.

**Live run (Julian, 2026-11-24, `make agent-once`, Haiku, env `dev`):**
- Claude chose `get_employee_info` with `{"employee_id": "E1002"}`, and the server returned status `ok` (`employees-v1`).
- 1,159 input tokens and 55 output tokens, so $0.000143. That matches the price table: 1,159 × $0.10/1M + 55 × $0.50/1M = $0.0001434. Latency was 678 ms and `stop_reason` was `tool_use`.
- One ledger line and `traces/REQ-0501.jsonl` were written. `make cost-report` showed a remaining budget of $3.9999 of $4.00.
- **Correlation works across processes:** the server's `tool.call` log line carries the agent's `trace_id` (`4d2a925f…`), so agent and server logs can be joined on it.

**Known gaps (not blocking):**
- ~~The server logs `request_id: null`.~~ Fixed in M6: each tool reads `request_id` from `_meta` (`test_server_meta.py`).
- `cost_report` rounds to 4 decimal places, so a Haiku run shows as $0.0001. Showing 6 decimals would make dev spend readable.

### 7.8 M6 verification (2026-11-25)

Checked by the main Claude session on a copy of branch `m6-react-loop` at `8920c66` (merged as PR #7), Python 3.14.6:
- `ruff format --check`, `ruff check`, `mypy --strict` and `pip-audit` all pass.
- 426 unit tests and 70 integration tests pass. Coverage is 99%. Every agent module is at 100% except `loop.py` (99%, one partial branch).
- Each of the four steps has a red commit before its green commit.

**Live runs:**
- *Haiku dry runs (dev):* T1 to T4 each gave the expected decision. In T4, the first `final_answer` was blocked by C3 and the corrected answer passed. That shows the guard working on a real model.
- *Sonnet eval (`evals/results/20261124T213305Z-claude-sonnet-5-5.jsonl`):* **12 of 12 passed**, so T1 to T4 were each 3 out of 3. T1 approved under R-NEW, T2 denied under R-REFRESH, T3 escalated as E6 and T4 as E9. There were no guard events and no fallbacks, and exactly one review ticket per escalation. The eval cost $0.150 (about $0.0125 per run, against the $0.085 estimate in PLAN §4), because prompt caching made every step after the first cheap.
- *Sonnet demo runs (env `demo`):* `traces/T1.txt` to `traces/T4.txt`, the source for deliverables #6 and #7. The review queue wasn't reset first, so the demo tickets are REV-0007 and REV-0008. That's harmless: each reply quotes the ID it was given.
- **Total project spend after M6: $0.209 of the $4.00 budget.**

**Deliverables:** #5, #6 and #7 are LaTeX listings and verbatim blocks taken from the code and the four trace files (Julian's choice, recorded in project memory).

