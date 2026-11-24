# Milestone Checklist

> Where the project is right now. Updated as each item is finished.
> Details for each milestone are in `PLAN.md` §13; what the code does is in `codebase.md`.

**Current stage:** M5 verified. Merge M5, then **M6**, prompt pack in `markdowns/prompts/M6.md`.
**Due:** 2026-11-26 · **LLM spend so far:** $0.00 of the $5 budget (code gate at $4.00)

Legend: `[x]` done · `[ ]` not done · 📸 = screenshot for the submission PDF (number = deliverable order)

---

## M0: Repo hygiene, tooling and CI ✅
- [x] `.gitignore` (keeps `.venv/`, `.env`, logs and traces out of git)
- [x] `pyproject.toml`, `src/it_requests/` layout, pinned dev tools
- [x] First test written before the code (red, then green)
- [x] GitHub Actions CI (format, lint, types, tests, audit), first run green
- [x] REQUIREMENTS v1.1 fixes
- [x] `codebase.md` started
- [x] PR #1 merged *(yours)*

## M1: `get_employee_info` end to end, plus JSON logging (brief steps 1 to 3) ✅
- [x] Step-1 demo moved to `examples/step1/`
- [x] Mock data file `data/employees.json`, with validation tests (duplicates, bad dates, unknown roles fail loudly)
- [x] Unit tests for `get_employee_info`: normal case, unknown ID, bad format, lower-case and whitespace input
- [x] Plain function passes the tests
- [x] JSON logging module, with tests
- [x] Wrapped as an MCP tool on the real server
- [x] `scripts/client_demo.py` calls it over HTTP and gets a real response
- [x] 📸 **#2** Terminal output: server running, plus the client getting a real `get_employee_info` response
- [x] M1 PR pushed, CI green and merged *(yours)*

## M2: `get_policy_limits` (brief step 4) ✅
- [x] `data/policy.json`, with validation tests
- [x] Unit tests: all four roles, unknown role, mixed case, `"none"` vs `null`
- [x] `tool.call` log for `get_employee_info` gains `resolved_employee_id`
- [x] Plain function passes, wrapped as a tool, callable from the client

## M3: `check_request_eligibility` (brief step 4) ✅
- [x] Every row of REQUIREMENTS §8 written as a test
- [x] Edge cases: refresh date equal to `as_of`, one day after, month-end clamp, unknown item, unknown employee
- [x] Pure function implements the §4.3 algorithm and passes
- [x] Wrapped as a tool, callable from the client

## M4: `flag_for_human_review` (brief step 4)
- [x] Unit tests: creates REV-0001, bad reason code rejected with nothing written, empty request rejected, unknown employee accepted, duplicate returns the same ID
- [x] Function passes, wrapped as a tool
- [x] Integration test: all four tools called through MCP
- [ ] 📸 **#3** MCP server code (all four tools)
- [ ] 📸 **#4** Unit tests and terminal output showing them passing

## M5: Agent: one hardcoded tool call, plus cost tracking and tracing (brief step 5a)
- [x] API key in `.env`; $5 spend limit set on the Anthropic Console *(yours)*
- [x] LLM wrapper records tokens and cost to `logs/cost_ledger.jsonl` (tested with a fake client)
- [x] Budget gate refuses to run at $4.00 or more (tested)
- [x] OpenTelemetry trace file per request (tested)
- [x] Agent connects to the server as an MCP client
- [x] One live call on a hardcoded request (Haiku); cost line and trace file appear

## M6: Full ReAct loop and guards (brief step 5b, Part 4)
- [ ] Each Action carries a `thought` field; `final_answer` tool with a strict schema
- [ ] Step cap (C6) and guards C1, C2, C3, C7, tested with a scripted fake LLM
- [ ] Trace prints Thought, Action, Observation, then Final Answer
- [ ] Live eval: T1 to T4 correct in 3 out of 3 runs each (Sonnet)
- [ ] 📸 **#5** Agent code, including the MCP client connection
- [ ] 📸 **#6** Full ReAct traces for all 4 test requests
- [ ] 📸 **#7** Final decision and reply for each of the 4 requests, including escalation reasons

## M7: Reflection (brief step 6, Part 5)
- [ ] §11 checklist implemented, with tests using a fake LLM
- [ ] Normal run logs `REFLECTION: CONFIRMED`
- [ ] `--inject-fault` run logs `REFLECTION: REVISED (...)`
- [ ] T1 to T4 re-run, with no regressions
- [ ] 📸 **#8** Reflection evidence (the confirmed run and the fault-injected run, side by side)

## M8: Eval report and submission
- [ ] T5 to T12 run once each: at least 7 of 8 correct
- [ ] Cost report printed; total spend under $5
- [ ] Final CI run green on `main`
- [ ] 📸 **#1** REQUIREMENTS.md and policy rules
- [ ] 📸 **#9** Passing pipeline run
- [ ] PDF put together in deliverable order (#1 to #9) and submitted *(yours)*
