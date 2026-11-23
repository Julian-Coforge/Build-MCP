# IT Equipment Request Handler: Project Plan

**Status:** v1.0, draft for approval
**Date:** 2026-11-23
**Due:** 2026-11-26 (2.5 days from now)
**Rigor tier:** Prototype, with full cost tracking, structured logging and tracing
**Companion docs:** `REQUIREMENTS.md` (what the system must do), `codebase.md` (what the code does now)

---

## Summary

We are building two things. The first is a Python MCP server that exposes four tools over mock HR and policy data: `get_employee_info`, `get_policy_limits`, `check_request_eligibility` and `flag_for_human_review`. The second is a ReAct agent on the Claude API. It connects to that server as an MCP client and decides whether to approve, deny or escalate each equipment request. It then checks its own draft reply before finalising it.

**Tier: Prototype.** This is a single-user course project on synthetic data with no external users. Production process (staging environments, rollout plans, on-call) would cost time and protect nothing. As requested, cost tracking, structured logs and tracing are still built in full. They are cheap to design in now and are also the evidence the graders need: the traces and the cost ledger.

**Main risks:**
1. The 2.5-day deadline.
2. Non-deterministic agent behaviour in the demo runs.
3. The MCP SDK 2.x API differing from older tutorials.
4. Reflection never revising anything, which would leave deliverable 8 thin.

Each risk has a mitigation in Section 12.

**Budget: under $5 in total (hard constraint from Julian).** Estimated spend is about $2.70:
- Claude Haiku 5.5 for development runs, at about $0.004 per run.
- Claude Sonnet 5.5 for the evaluation and demo runs, at about $0.085 per run.

CI costs nothing, because CI never calls the live model. A ledger gate in code stops all runs at $4.00 spent. See Section 4.

**First milestone (M0):** repo hygiene, tooling and a green CI pipeline running one trivial test. Everything after M0 then builds on a pipeline already known to work.

**Working rules for the whole project:**
- **TDD.** Every change starts with a failing test, then the code that makes it pass, then a refactor.
- **A reason for every decision.** Each decision in this plan and in the code carries a "Why".
- **`codebase.md` is updated in the same commit as every code change.**
- **One milestone at a time.** Each milestone is confirmed with Julian before the next starts.

---

## 1. Goal and success criteria

**Goal:** an agent that handles free-text IT equipment requests correctly and explains itself. Deterministic code makes every policy decision. When the data does not clearly support approve or deny, the agent escalates instead of guessing.

| # | Criterion | Target | How it is measured |
|---|---|---|---|
| S1 | Unit tests pass | 100%, in CI on every push | GitHub Actions run status |
| S2 | Policy coverage | Every row of REQUIREMENTS §8 and every edge case in §12.1 has a test | Test names map to rule IDs; reviewed at M3 |
| S3 | Code coverage of the server package | 90% or more of lines | `pytest --cov`, reported in CI |
| S4 | Primary demos correct | T1 to T4 reach the expected decision in 3 out of 3 runs each | `scripts/run_eval.py` results file |
| S5 | Extra coverage | T5 to T12 correct in at least 7 of 8 | Same eval script, one run each |
| S6 | Hard constraints | 0 violations of C1 to C7 across all eval runs | A code guard checks each run, and the eval script counts violations |
| S7 | Reflection evidence | At least 1 run logs `REFLECTION: REVISED` and at least 1 logs `REFLECTION: CONFIRMED` | Grep of the run logs |
| S8 | Cost visibility and budget | Every live run writes one cost line; total project spend under $5 (gate at $4.00) | `logs/cost_ledger.jsonl` and `cost_report` |
| S9 | Traceability | Every run produces one trace with a span for each LLM call, tool call and reflection | `traces/<request_id>.jsonl` |

**Non-goals:**
- Real procurement, authentication, a user interface, a database, or deployment to a server.
- Running the live agent in CI.
- Multi-tenant or concurrent use.
- Tuning prompts beyond what is needed to pass S4 to S7.

*Why the numbers:* "3 out of 3" on the primary demos guards against a lucky single run when the PDF screenshots are taken. 7 of 8 on the extras allows for one non-deterministic miss while still showing the escalation logic works widely.

---

## 2. Architecture and data flow

```
 request text ──▶ Agent (agent/loop.py) ──── Claude API  [$ paid, trust boundary]
                    │   ▲
     MCP client     │   │  tool results (JSON)
                    ▼   │
                 MCP server (server.py)  ── thin wrappers only
                    │
                    ▼
            Pure functions (policy.py, employees.py, reviews.py)
                    │
         ┌──────────┴───────────┐
   data/*.json (read-only)   data/review_queue.json (append-only, side effect)
```

**Components:**

| Component | Responsibility | Why it is separate |
|---|---|---|
| `employees.py`, `policy.py`, `reviews.py` | Pure business logic: lookups, the eligibility algorithm (§4.3), review tickets | The brief requires plain functions with unit tests before any MCP wrapping. Pure functions are also fast and deterministic to test. |
| `data.py` | Load and validate the mock data | Bad data should fail loudly at start-up, not as a wrong decision later |
| `server.py` | Register the four tools with the MCP SDK and translate errors into structured JSON | Keeping protocol code thin means almost nothing is untested |
| `agent/llm.py` | One wrapper around the Anthropic client that records tokens, cost, latency and a span | It is the single place every paid call goes through, so cost tracking can't be bypassed |
| `agent/loop.py` | The ReAct loop, the step cap and the constraint guards | Owns control flow, so C1, C6 and C7 are enforced in code, not only in the prompt |
| `agent/reflection.py` | The checklist from §11, run against the draft | Added last (brief step 6), behind its own interface |
| `observability.py` | JSON logging, OpenTelemetry setup and the correlation ID | Shared by server and agent so both write the same log shape |

**Key design decisions:**

1. **The agent is a manual loop on the Messages API, not the SDK's Tool Runner or Managed Agents.**
   - *Why:* the brief grades an explicit Thought, Action, Observation trace, a step cap and guards on approve and deny. A hand-written loop makes each of those visible and testable.
   - Managed Agents would host the loop on Anthropic's side. That hides exactly the part the assignment asks us to build.

2. **Every Action carries an explicit Thought.** The agent shows Claude each MCP tool with one extra required `thought` argument. The agent strips it off before calling the server, and prints it as the Thought line.
   - *Why:* on current Claude models (Sonnet 5.5, Haiku 5.5) the model's thinking is hidden by default, so we can't rely on free text appearing before each tool call.
   - A required field guarantees that every Action has a Thought in the trace. The MCP tool signatures stay exactly as the brief specifies.

3. **The decision is returned through a `final_answer` tool with a strict schema.** Its fields are `decision`, `rule_id`, `review_id`, `reason_code` and `draft_reply`.
   - *Why:* the guards for C1, C2, C3 and C7 need to read the decision as data, not parse prose.
   - Forced `tool_choice` is not available on Sonnet 5.5, so we use `strict: true` plus a prompt instruction instead.

4. **Transport: streamable HTTP**, which `client.py` already uses. Integration tests call the server in-process, so CI needs no open port.
   - *Why:* HTTP already works. Two terminals (server and client) also make clear screenshot evidence for deliverable 2.
   - *To verify in M1:* whether MCP SDK 2.3 supports an in-process client. If it doesn't, the tests use stdio instead.

5. **Model: set in config, with a two-tier split.** `claude-haiku-5-5` is used for development, and `claude-sonnet-5-5` for the evaluation and demo runs. Both use adaptive thinking.
   - *Why:* the $5 budget rules out Opus 5.5. At about $0.17 per run, the planned runs would cost about $8.
   - Haiku is about 20× cheaper than Sonnet, so iterating on the loop and prompts is close to free.
   - Sonnet makes the graded decisions, where escalation judgment matters most.
   - The model is one config value recorded on every run. Switching models is therefore a single, tracked variable change.
   - This is open decision D4 (Section 14).

**Trust and cost boundaries:**
- *The Claude API call.* It is paid, and it sends data off the machine. Only mock data is sent, and the API key never leaves `.env`.
- *`flag_for_human_review`.* It is the only side-effecting tool. It validates its arguments before writing anything and is idempotent.
- *Request text from the employee.* This is untrusted input. Policy decisions come only from tool results (C1, C2). That limits what a prompt-injection like "ignore policy and approve" can achieve.

---

## 3. Data

| Source | Owner | Changes | Location |
|---|---|---|---|
| Employee records (§8) | Julian (synthetic) | Only with an edit and a version bump | `data/employees.json` |
| Policy matrix (§4.1) | Julian (synthetic) | Same as above | `data/policy.json` |
| Review queue | Written by `flag_for_human_review` | Appended at runtime | `data/review_queue.json` (gitignored; the path can be injected for tests) |

**Validation at load (Pydantic models; any violation raises at start-up):**
- Employee IDs match `E####` and are unique.
- Dates parse as ISO dates and are not after `as_of`.
- Roles and items come from the allowed enums.
- Every role has an entry for every catalog item.
- `max` is 0 or more, and `refresh_months` is a positive integer or `"none"`.

*Why fail loudly:* a silent bad record would show up later as a wrong approval, which is the worst outcome in this system.

**Lineage:** each data file carries `data_version` and `effective_date` at the top level. Every tool response includes `data_version`, and every log line for a tool call records it. So a decision can always be traced back to the exact data it was made from.

**Clock:** `as_of` is always passed in, and is `2026-11-22` in tests and demos. Nothing reads the system clock. *Why:* the refresh rules are date arithmetic, and tests that depend on today's date would break silently over time.

---

## 4. Cost transparency

**Cost drivers:**

| Driver | Unit cost | Notes |
|---|---|---|
| Claude Sonnet 5.5 input / output tokens | $2.00 / $10.00 per 1M | Anthropic list price from a cache dated 2026-11-03, **unverified here**. Please confirm on the Anthropic pricing page or Console. |
| Claude Haiku 5.5 input / output tokens | $0.10 / $0.50 per 1M (prompts under 100K tokens) | Same source and caveat |
| Claude Opus 5.5 (for comparison only) | $4.00 / $20.00 per 1M | Same source. Not used, because of the budget. |
| Cache reads | Cheaper than standard input (for example $0.10 per 1M on Sonnet 5.5) | Prompt caching on the system prompt and tool definitions; only counts when the cached prefix is long enough |
| GitHub Actions | $0 | Free tier on a public repo, or within the private-repo minute allowance. Under 2 minutes per run. |
| Compute and storage | $0 | Runs on your laptop |

**Estimate for one agent run** (labelled as an estimate):
- *Assumptions:* 5 model calls per run (4 tool steps plus a final answer), and 1 reflection call.
- *Input per call:* the system prompt plus tool definitions are about 2,500 tokens, and the history grows by about 500 tokens per step. Across the 5 calls that is 2,500 + 3,000 + 3,500 + 4,000 + 4,500 = about 17,500 input tokens.
- *Output:* about 400 tokens per call (thought, tool arguments, thinking), so 5 × 400 = about 2,000 output tokens.
- *Reflection call:* about 4,000 input tokens and 800 output tokens.

| Model | Loop | Reflection | Per run | With 20% margin |
|---|---|---|---|---|
| Sonnet 5.5 | 17,500 × $2 / 1M = $0.035, plus 2,000 × $10 / 1M = $0.020, giving $0.055 | $0.008 + $0.008 = $0.016 | $0.071 | **$0.085** |
| Haiku 5.5 | 17,500 × $0.10 / 1M = $0.0018, plus 2,000 × $0.50 / 1M = $0.0010, giving $0.0028 | $0.0004 + $0.0004 = $0.0008 | $0.0036 | **$0.004** |
| Opus 5.5 (not used) | $0.110 | $0.032 | $0.142 | $0.17 |

**Estimate for the whole project:**

| Activity | Model | Runs | Cost (estimate) |
|---|---|---|---|
| Development and prompt iteration | Haiku 5.5 | about 40 | $0.16 |
| Eval: T1 to T4 × 3, plus T5 to T12 × 1 | Sonnet 5.5 | 20 | $1.70 |
| One eval re-run after the reflection step (M7), T1 to T4 only | Sonnet 5.5 | 4 | $0.34 |
| Final demo runs for screenshots | Sonnet 5.5 | about 6 | $0.51 |
| **Total** | | about 70 | **about $2.70**, under the $5 limit with about $2.30 to spare |

Each live eval is approved before it runs, so spend can't drift.

**Measurement:**
- `agent/llm.py` reads `response.usage` on every call: input, output, cache-read and cache-write tokens.
- It computes the cost from a price table kept in `config.py`, with the date the prices were checked written next to it.
- It writes one JSON line to `logs/cost_ledger.jsonl` with these fields: `request_id`, `run_id`, `stage` (`react_step_n` or `reflection`), `model`, `prompt_version`, the token counts, `cost_usd`, `latency_ms` and `env` (`dev` or `eval` or `demo`).
- Each run ends with a summary line, and the trace's root span carries the run total.

**Controls:**
- **Per-run hard limit:** 8 tool calls (C6) plus at most 12 model calls. `max_tokens` is set per call. A run that would go over aborts and escalates as E11.
- **Project budget:** `BUDGET_USD=4.00` is read from `.env`. Before each run, the agent sums the ledger and refuses to start if the total is at or over budget.
  - *Why $4.00 and not $5:* the ledger relies on our own price table, which could be slightly wrong. The $1 gap absorbs that error.
- **Warning:** a log line at WARN level when spend passes $3.00 (75%).
- **Per-run ceiling:** `max_tokens` is capped at 4,000 per model call. A single run can't cost more than about $0.50 even if it misbehaves.
- **Recommended (your action):** set a monthly spend limit on the Anthropic Console, because that is the only limit the platform itself enforces.
- **CI:** never calls the model, so CI costs $0. *Why:* unit tests must be deterministic and free to run on every push.

**Reporting:** `python -m it_requests.cost_report` prints spend by `env`, by `stage` and by request ID from the ledger. Julian reviews it at the end of each milestone that calls the model (M5 onwards).

---

## 5. Logging

- **Format:** one JSON object per line. The standard library `logging` module with a small JSON formatter writes to stderr and to `logs/app.jsonl`.
  - *Why:* no new dependency, and the output can be searched with `grep` or `jq`.
- **Fields on every line:** `ts`, `level`, `component` (`agent` or `server`), `event`, `request_id`, `trace_id`, `span_id`.
- **Levels:**
  - DEBUG: full tool arguments.
  - INFO: step events.
  - WARN: budget, retries, guard triggers.
  - ERROR: failures.

**What is logged at each stage:**

| Stage | Event | Key fields |
|---|---|---|
| Run start | `run.start` | `request_id`, `prompt_version`, `model`, `as_of` |
| Each step | `react.thought`, `react.action`, `react.observation` | step number, tool name, arguments (DEBUG), result status or error code |
| Server tool call | `tool.call` | tool name, `employee_id`, result status, `data_version`, `duration_ms` |
| Guard | `guard.blocked` | the constraint ID (C1 to C7) and what was blocked |
| Reflection | `reflection.result` | `CONFIRMED`, or `REVISED` with a description of the change |
| Run end | `run.end` | decision, rule or reason code, `review_id`, total cost, total tokens |

**Never logged:**
- The API key or any environment variable.
- Full request headers.
- Employee names (log `employee_id` only).

The raw request text is logged only at DEBUG level. It is synthetic here, but the habit is the point.

**Retention:** `logs/` is gitignored and kept locally. Delete it as needed. **Searching:** `jq 'select(.request_id=="REQ-0001")' logs/app.jsonl`.

**Correlation ID across agent and server:**
- The agent puts `request_id` and the W3C `traceparent` in each MCP call's request metadata (`_meta`), and the server reads them into its log context.
- *To verify in M5:* that SDK 2.3 exposes `_meta` on both sides. If not, the fallback is that the server logs carry `tool` and argument values, and the agent's span links them.

---

## 6. Tracing

- **OpenTelemetry**, using the `opentelemetry-sdk` package (`opentelemetry-api` is already installed).
- **Exporter:** a JSON-lines file exporter writing to `traces/<request_id>.jsonl`, with a console exporter optional.
  - *Why:* no tracing backend is needed for a prototype, and the files can go straight into screenshots.
  - Pointing it at Jaeger or a hosted backend later is a one-line exporter change.

**Span tree for one request:**
```
agent.run  (request_id, decision, total_cost_usd, total_tokens)
├── llm.call            (model, prompt_version, input/output/cache tokens, cost_usd, latency_ms, stop_reason)
├── tool.get_employee_info   (thought, args, status)  ──▶ server: mcp.tool.get_employee_info (data_version)
├── llm.call
├── tool.check_request_eligibility (…) ──▶ server span
├── llm.call
├── tool.final_answer   (decision, rule_id, review_id)
├── guard.check         (constraints evaluated, violations)
└── reflection
    └── llm.call        (…, result=CONFIRMED|REVISED)
```

For the AI-specific fields, each `llm.call` span records the model, `prompt_version`, token counts, latency and `stop_reason`. A wrong answer can then be reconstructed after the fact. There is no retrieval in this system, so there are no document IDs to record.

The trace ID goes into every log line (Section 5), so logs and traces cross-reference each other.

---

## 7. Metrics and alerting

At prototype scale, metrics are computed from the logs and ledger by the eval and cost-report scripts. No metrics backend.

| Measure | Type | Threshold | Who responds |
|---|---|---|---|
| Unit test pass rate | Quality | Below 100% fails CI | Julian (GitHub email notification) |
| Eval decision accuracy (S4, S5) | Quality | T1 to T4 below 3/3, or T5 to T12 below 7/8 | Julian, before taking screenshots |
| Constraint violations (S6) | Quality | Any above 0 | Julian; the guard already blocks the action |
| Run latency (p50, max) | Service | Max over 120 seconds gets investigated | Julian |
| Cost per run, cumulative spend | Cost | Over $0.30 per run, or over 80% of budget, gives a WARN log; over 100% blocks runs | Julian |

---

## 8. Quality and testing

**Static checks:**
- `ruff format` and `ruff check` (formatter and linter), and `mypy --strict` on `src/`.
- Run locally through `make check` and in CI.
- *Why ruff:* one fast tool replaces black, isort and flake8.

**TDD loop for every change:**
1. Write the failing test and run it to see it fail for the right reason.
2. Write the smallest code that passes.
3. Refactor with the tests green.
4. Update `codebase.md`, then commit.

Commit messages say `test:` or `feat:` so the red-then-green order is visible in the git history.

**Test layers:**

| Layer | Location | Covers | Fakes | In CI |
|---|---|---|---|---|
| Unit | `tests/unit/` | Pure functions: every row of §8, every edge case in §12.1, data validation, date maths, cost maths, log formatting | None needed, because the functions are pure | Yes |
| Integration | `tests/integration/` | The tools called through the MCP protocol in-process; the agent loop with a **scripted fake LLM** (canned tool-use responses) that proves loop control, the step cap, the guards and the trace format | Fake Anthropic client | Yes |
| Evaluation | `evals/` + `scripts/run_eval.py` | T1 to T12 against the live model, each checked against the expected decision, code, required tool calls and the forbidden-content rules | None (live) | **No**: run by hand, because it is paid and non-deterministic |

**Regression rule:** every bug found gets a test that fails before the fix is written.

**Evaluation design:**
- *Fixed set.* T1 to T12 from REQUIREMENTS §9 are written into `evals/cases.yaml` before any prompt tuning.
- *Baseline.* The first full eval run with prompt v1 is recorded in `evals/results/`.
- *One variable at a time.* Each run records the model, `prompt_version`, effort and data version, and a comparison between two runs changes only one of them.
- *Assertions check the whole claim:*
  - The decision is correct.
  - The rule or reason code is correct.
  - The required tool calls happened.
  - The draft contains the `review_id` or rule ID when required.
  - The draft contains **no** delivery date, no brand or model promise, and no date that did not appear in an observation (C4, C5).
- *Not covered:* multi-turn conversations, non-English requests, adversarial prompt injection beyond one smoke case, concurrency, and real HR data shapes.

---

## 9. Security

- `ANTHROPIC_API_KEY` lives in `.env`, which is gitignored. `.env.example` is committed with placeholder values.
- The key never appears in code, logs or screenshots. If it is ever exposed (in a screenshot or a commit), rotate it on the Console straight away.
- CI needs no secrets, because no live calls run in CI.
- **Before the first commit:** add a `.gitignore` covering `.venv/`, `__pycache__/`, `.env`, `logs/`, `traces/` and `data/review_queue.json`. These files are currently untracked, so one `git add .` would commit them.
- **Dependency scanning:** a `pip-audit` step in CI. Dependabot alerts on the repo are optional.
- **Sensitive data:** all data is synthetic. Employee names stay out of the logs anyway, to keep the right habit.
- **Prompt injection:** handled by the design. The model cannot approve without an `ELIGIBLE` observation (the C1 guard in code), so injected text can at most cause an unnecessary escalation.

---

## 10. Delivery process

- **Branches:** one short-lived branch per milestone (`m1-get-employee-info` and so on), merged into `main` by pull request. `main` is protected and requires CI to pass.
  - *Why:* each pull request then maps to one brief step, which makes the "incremental build" story easy to show.
- **CI (GitHub Actions, `.github/workflows/ci.yml`)**, ordered fast to slow on Python 3.14:
  1. `ruff format --check`, `ruff check`
  2. `mypy`
  3. `pytest tests/unit --cov`
  4. `pytest tests/integration`
  5. `pip-audit`
- **Environments:** only `dev` (your laptop). The `env` tag in the ledger separates dev, eval and demo spend.
- **Release and rollback:** a release is a git tag (`v0.1-m1` and so on). Rolling back means checking out the previous tag. `prompt_version` is a constant in `agent/prompts.py` and changes with every prompt edit.

---

## 11. Operations

**Owner:** Julian.

**Runbook:**

| Symptom | Likely cause | Check | Fix |
|---|---|---|---|
| Client can't connect | Server not running, or wrong port | `curl localhost:8000/mcp` | Start the server; check the port setting |
| `authentication_error` from the Claude API | Key missing or wrong | `.env` is loaded? | Fix `.env`; rotate the key if it was exposed |
| `429` or `529` | Rate limited or overloaded | Log `llm.call` errors | The SDK retries twice; wait and rerun |
| Agent escalates everything as E11 | Step cap hit, or a tool error | Trace: which span failed | Fix the tool, or raise the cap with a reason |
| Budget block | Ledger over `BUDGET_USD` | `cost_report` | Raise the budget deliberately, or stop |
| Wrong decision in eval | Prompt or rule gap | Trace for that `request_id` | Add an eval or regression test, then fix |

**Incident write-ups:** a short entry in `codebase.md` under "Known issues and fixes" with four parts: symptom, cause, evidence (log or trace excerpt), fix.

---

## 12. Risks and assumptions

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Deadline slips (2.5 days) | Medium | High | Strict milestone order; the screenshot for each deliverable is taken at the end of the milestone that produces it, not all on the last day |
| Agent decisions vary between runs | Medium | High | Deterministic policy in code; strict `final_answer` schema; guards; 3/3 eval on T1 to T4 before taking screenshots |
| MCP SDK 2.3 API differs from tutorials | Medium | Medium | Verify against the installed package source in M1; record findings in `codebase.md` |
| Reflection never revises a draft, so deliverable 8 is thin | Medium | Medium | Open decision D3: a documented, clearly labelled fault-injection demo run |
| `_meta` propagation unsupported | Low | Low | Fallback in Section 5 |
| API cost overrun past the $5 limit | Low | High | Ledger gate at $4.00, `max_tokens` cap per call, every live eval approved first, $5 Console spend limit |
| Python 3.14 wheel gaps in CI | Low | Medium | Pin dependency versions; fall back to 3.13 in CI if needed |
| Accidental commit of `.venv` or `.env` | Medium (no `.gitignore` today) | Medium | `.gitignore` is the first commit in M0 |

**Assumptions:**
- Requests arrive as single-turn English text that contains an `E####` ID when one exists.
- Employee and policy data match the shapes in REQUIREMENTS §2 and §4. `as_of` is `2026-11-22`.
- Julian has, or will create, an Anthropic API key with billing enabled (open decision D1).
- The GitHub repo `Julian-Coforge/Build-MCP` allows Actions.
- The PDF of screenshots is put together by hand from terminal captures.

---

## 13. Milestones

Every milestone is "done" only when:
- Its tests were written first and pass locally and in CI.
- `codebase.md` is updated.
- Logs are structured.
- From M5 onwards, costs and traces are visible.
- The deliverable screenshot (where one applies) is captured.

| # | Milestone | Brief step | Definition of done | Screenshot | Target |
|---|---|---|---|---|---|
| M0 | Repo hygiene and CI | Part 7 base | `.gitignore`, `pyproject.toml` (pytest, ruff, mypy, pytest-cov, pip-audit), `src/` layout, `Makefile`, CI workflow green on one trivial test, `codebase.md` committed, REQUIREMENTS v1.1 fixes (D5) | — | Mon 23 |
| M1 | `get_employee_info` end to end, plus JSON logging | Steps 1 to 3 | Data loader and validation (TDD), the plain function with unit tests (normal, unknown, bad format, normalisation), wrapped as an MCP tool, `scripts/client_demo.py` calls it over HTTP, logging module tested | **#2** server and client output | Mon 23 |
| M2 | `get_policy_limits` | Step 4 | Unit tests (all roles, unknown role, mixed case), tool wrapped, callable from the client | — | Mon 23 / Tue 24 |
| M3 | `check_request_eligibility` | Step 4 | Every row of §8, plus boundary dates and the month-end clamp, all as tests; the pure function implements the §4.3 algorithm exactly | — | Tue 24 |
| M4 | `flag_for_human_review` | Step 4 | Validation, idempotency, unknown employee accepted, injected queue path; integration test of all four tools through MCP | **#3** server code, **#4** tests passing | Tue 24 |
| M5 | Agent: one hardcoded tool call, plus cost and tracing | Step 5a | `agent/llm.py` with ledger and spans (tested with a fake client), MCP client connection, one live call on a hardcoded request, cost line and trace file produced | — | Tue 24 |
| M6 | Full ReAct loop and guards | Step 5b, Part 4 | `thought`-augmented tools, the `final_answer` schema, the step cap, guards for C1 to C7, integration tests with a scripted fake LLM, T1 to T4 passing 3/3 live | **#5** agent code, **#6** traces, **#7** decisions | Wed 25 |
| M7 | Reflection | Step 6, Part 5 | The §11 checklist, revise-or-confirm logic, `REFLECTION:` log lines, tests with a fake LLM, eval re-run with no regression | **#8** reflection evidence | Wed 25 |
| M8 | Eval report and submission pack | Parts 6 and 7 | `run_eval.py` results meet S4 to S9, cost report, final passing CI run, screenshots assembled in order into a PDF | **#1**, **#9**, and the PDF | Wed 25 / Thu 26 |

*Why this order:* it follows the brief's suggested approach exactly, which the "confirmed working before full build" rubric line rewards. Cost tracking and tracing land in M5, the first milestone that spends money, and not later.

---

## 14. Open decisions

| # | Decision | Options | Recommendation |
|---|---|---|---|
| D1 | API key | Confirmed: Julian has one | Put it in `.env` before M5. Set a $5 spend limit on the Console. |
| D2 | Budget | Fixed by Julian: under $5 | Code gate at $4.00, WARN at $3.00, estimated spend about $2.70 |
| D3 | How to show reflection revising a draft | (a) Rely on a natural revision; (b) **a documented fault-injection run** (a `--inject-fault` flag that adds a delivery-date promise to the draft) shown next to a natural `CONFIRMED` run | **(b)** It is guaranteed evidence for the 10-point rubric line. It is labelled honestly in the PDF, and the natural runs still show `CONFIRMED`. |
| D4 | Model | (a) **Haiku 5.5 for development, Sonnet 5.5 for eval and demos** (about $2.70); (b) Sonnet 5.5 for everything (about $5.50: over budget unless development runs are cut hard); (c) Haiku 5.5 for everything (about $0.30) | **(a)** Development is close to free, and the graded runs use the stronger model. Option (c) is the fallback if spend runs high. |
| D5 | REQUIREMENTS v1.1 fixes | Apply the four fixes from the review: explicit `"refresh": "none"` marker; add `get_employee_info` to the required calls for T2 and row 3; cap of "8 calls plus the escalation call"; reflection evidence method | **Apply in M0.** Tests are written against the requirements, so they need to be consistent first. |
| D6 | Repo visibility | Public or private | **Either.** A public repo makes CI minutes unlimited. Private is fine within the free allowance. |
