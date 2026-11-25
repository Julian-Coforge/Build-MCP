# Test Refactor Prompt Pack: Arrange-Act-Assert, one assert per test

**Why:** Julian asked for every test to follow the `aaa-unit-testing` standard. The rules: one behaviour and one `assert` per test; names in `test_<unit>_<condition>_<expected>` form; visible Arrange / Act / Assert; fakes over mocks; `slow` and `eval` markers; strict `xfail` for known bugs; deterministic tests; deliberate edge cases; CAPS data and `_helpers`; tests grouped by unit.

**Branch:** `tests-aaa` · **One PR** at the end (text in Part 5). Do this **before M7**, so the M7 reflection tests are written to the same standard from the start.

**Production code does not change.** This is a refactor of `tests/` only (plus pytest config, the Makefile and CI). Every behaviour checked today must still be checked afterwards, and coverage must not drop.

---

## Audit of the current suite (main at `8fbf30f`, checked 2026-11-25)

| Measure | Now | Target |
|---|---|---|
| Test functions / collected tests | 292 / 496 | more functions; collected count goes **up** |
| `assert` statements | 657 | at least 657 (none lost) |
| Test functions with more than one `assert` | **141** | 0 |
| Test bodies with `if` / `for` / `while` | 19 | 0 (move to `_helpers` or parametrize) |
| `@pytest.mark.parametrize` without `ids=` | 40 | 0 |
| Test names with fewer than 3 words after `test_` | 12 | 0 |
| `patch` / `monkeypatch` | 0 | 0 (fakes are already injected: good) |
| Registered markers (`slow`, `eval`) | none | both, with `--strict-markers` |
| Real-dependency (`test_real_`) twin | none | one paid Haiku smoke test, `slow` + `eval`, off by default |
| Coverage | 99.30% | ≥ 99.30% |

Worst files by multi-assert tests: `integration/test_agent_loop.py` (20, one with 11 asserts), `unit/test_eligibility.py` (9), `unit/test_observability.py` (9), then `test_evals`, `test_guards`, `test_reviews` (8 each), `integration/test_agent_single_call.py` (7) and `unit/test_tools.py` (7).

Already good: everything external is injected (fake Anthropic `create`, in-process MCP server, `tmp_path` queue and ledger, fixed clock, explicit tracer). No patching anywhere. So the work is mostly **splitting and renaming**, not redesign.

---

## Part 0: Branch first (you)

```bash
cd ~/"Desktop/FDE Course/Week 7/Assignment/Build-MCP"
git checkout main
git pull
git checkout -b tests-aaa
git branch --show-current                  # must print tests-aaa
git add markdowns/prompts/AAA-tests.md markdowns/TEST-AUDIT.md CLAUDE.md AGENTS.md
git commit -m "docs: add AAA test standard and test refactor pack"
git branch -D m6-react-loop                # optional cleanup
make check
```

(`CLAUDE.md` and `AGENTS.md` now have a "Test standard" section, so your agent follows the rules automatically from here on.)

---

## Part 1: Files this refactor creates or changes

```
pyproject.toml                       CHANGED  register `slow` and `eval` markers
Makefile                             CHANGED  `test` runs -m "not slow"; new `test-live`
.github/workflows/ci.yml             CHANGED  both pytest steps run -m "not slow" -rs
tests/unit/test_test_style.py        NEW      the guard: fails on any test that breaks the rules
tests/unit/*.py                      CHANGED  split, renamed, grouped (no behaviour lost)
tests/integration/*.py               CHANGED  split, renamed, scenario fixtures
tests/conftest.py                    CHANGED  only if shared fakes/helpers move here
tests/live/test_real_agent.py        NEW      one paid Haiku run of T1, slow + eval, skipped without a key
```

---

## Part 2: Prompts for your coding agent

Three prompts. Prompt 1 is the red step (a guard test that lists every offender). Prompts 2 and 3 make it green, one test layer at a time.

### Prompt 1 of 3: The style guard (red)

```text
Read CLAUDE.md (section "Test standard") and markdowns/prompts/AAA-tests.md (the audit
table). Do not change anything under src/.

GOAL: a fast test that enforces the AAA standard on the whole suite, so it can't drift
back. It must FAIL now and list the offenders.

1. pyproject.toml, [tool.pytest.ini_options]: add
     markers = [
       "slow: real network, real model or other slow resource (deselected by default)",
       "eval: calls the paid Claude API; skipped when ANTHROPIC_API_KEY is not set",
     ]
   Keep "-ra --strict-markers".
2. New tests/unit/test_test_style.py. It parses every tests/**/test_*.py file with `ast`
   (no imports of the test modules). Module-level helpers prefixed `_`. Checks, each as its
   own parametrized test over the test files, with ids= the path relative to tests/, and
   exactly ONE assert per test, comparing a list of offending test names to []:
   - test_<file>_has_at_most_one_assert_per_test: count ast.Assert nodes inside each
     `test_*` function (sync or async). More than one is an offence. A test with zero
     asserts is allowed only if its body contains `pytest.raises` or calls a fake whose
     name starts with `_exploding` (rule 1, exception and must-not-be-called tests).
   - test_<file>_has_no_logic_in_test_bodies: no ast.For, ast.While or ast.If statement
     inside a `test_*` function. Comprehensions are fine; helpers (`_name`) are not checked.
   - test_<file>_parametrize_always_has_ids: every @pytest.mark.parametrize decorator on a
     test has an `ids=` keyword.
   - test_<file>_test_names_describe_behaviour: at least 3 words after "test_", and no
     word in {"works", "correct", "ok", "stuff"}.
   Name the tests so `pytest --collect-only -q` reads as the standard itself, e.g.
   test_style_test_has_at_most_one_assert[unit/test_costs.py].
   test_test_style.py must itself pass all four checks.
3. Run `pytest tests/unit/test_test_style.py -q` and show the failures (expect roughly 141
   multi-assert tests, 19 with logic, 40 parametrize decorators without ids, 12 short names,
   spread over the files listed in the audit).

STOP after showing the failures and wait for me to reply `go`. Do not refactor anything yet.
```

### Prompt 2 of 3: Unit tests to green

```text
Read CLAUDE.md "Test standard" and the skill rules summarised in it. Do not change
anything under src/.

GOAL: every file in tests/unit/ passes tests/unit/test_test_style.py, with no behaviour
lost and coverage unchanged.

Before you start, record:
  pytest --collect-only -q | tail -1
  pytest tests/unit --cov --cov-fail-under=0 -q | grep TOTAL
  python -c "import ast,pathlib;print(sum(isinstance(n,ast.Assert) for f in pathlib.Path('tests').rglob('test_*.py') for n in ast.walk(ast.parse(f.read_text()))))"

For each file in tests/unit (worst first: test_eligibility, test_observability,
test_evals, test_guards, test_reviews, test_tools, test_cost_report, test_llm, then the
rest):
1. Split every multi-assert test into one test per behaviour. Each existing assert must
   survive, either as its own test or inside one whole-value comparison that clearly
   checks the same fact (e.g. a dict or list compared in full). Prefer one test per field
   when a failure should name the broken field (rule 1).
2. Name each test test_<unit>_<condition>_<expected>, using domain words
   (e.g. test_check_request_eligibility_refresh_not_due_returns_not_eligible). The name
   must stay true if the implementation is rewritten. No "and": split instead.
3. Lay out Arrange / Act / Assert with a blank line between phases when setup is more
   than one line. Inline one-liners are fine for pure functions.
4. Repeated setup moves to a fixture (in the file, or tests/conftest.py if two files use
   it) or a `_helper`. Shared data goes at module top in CAPS with a comment saying WHY it
   produces the expected outcome.
5. Loops and ifs that build expectations become @pytest.mark.parametrize with ids=
   (e.g. ids=lambda c: c["employee_id"] + "-" + c["item"]). The REQUIREMENTS §8 table
   test becomes one parametrized test per checked field, or one whole-row comparison.
6. Group tests by unit with section comments (# ---------- check_request_eligibility
   ----------), happy path first, then edges.
7. Do not weaken any assert (no `in` where `==` was used, no dropped fields). Floats use
   pytest.approx. Keep tmp_path and the fixed clock; never touch logs/ or data/.

After each file: run that file and test_test_style.py for it. When all unit files are
done, run make check and report: collected tests before/after, assert count before/after
(after must be >= before), unit coverage before/after, and test_test_style results for
tests/unit (all green; integration may still be red).
```

### Prompt 3 of 3: Integration tests to green, markers, live twin

```text
Same rules as Prompt 2. Do not change anything under src/.

GOAL: tests/integration passes the style guard, slow tests are separated, and there is one
real-dependency twin.

1. Integration files, worst first: test_agent_loop.py, test_agent_single_call.py,
   test_server_employee_tool.py, test_server_eligibility_tool.py,
   test_server_review_tool.py, test_server_policy_tool.py, then the rest.
   Scenario tests (for example the T1 run with 11 asserts) become one FIXTURE that runs
   the scenario and returns everything the asserts need (the AgentResult, the FakeMessages
   calls, the ledger lines, the trace spans, the captured log lines), plus one small test
   per fact:
     test_run_agent_t1_returns_approve
     test_run_agent_t1_cites_rule_r_new
     test_run_agent_t1_makes_three_model_calls
     test_run_agent_t1_writes_three_ledger_lines
     ...
   The fixture is function-scoped (each test gets fresh state; the in-process server and
   fake are fast). Async fixtures follow the existing anyio setup in the file.
   Log-line and span checks that loop over records become helpers like
   _events(log_lines) / _span(spans, name) and one assert per test.
2. Makefile: `test` becomes `pytest -m "not slow" --cov`. Add
     test-live:  ## paid: one real Haiku run of T1 (about $0.001); needs .env and make serve
         pytest -m "slow" -rs tests/live
3. .github/workflows/ci.yml: add -m "not slow" -rs to BOTH pytest steps (keep the coverage
   flags). CI must still never call the real API.
4. New tests/live/test_real_agent.py: module-level
     pytestmark = [pytest.mark.slow, pytest.mark.eval,
                   pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"),
                                      reason="needs ANTHROPIC_API_KEY in .env")]
   load .env with python-dotenv at import. One module-scoped async fixture runs T1 once
   with the real Anthropic client on claude-haiku-5-5 against the running server at
   http://127.0.0.1:8000/mcp (env "dev", budget gate first, ledger and traces as normal).
   Tests, one assert each, sharing that single run so it costs one call sequence:
     test_real_agent_t1_returns_approve
     test_real_agent_t1_cites_rule_r_new
     test_real_agent_t1_does_not_fall_back
   If the server is not reachable, skip with a clear reason (missing environment, not a
   bug, so skip is correct here).
5. Run make check (fast suite only), report: collected tests and assert count before/after
   for the whole suite, coverage (must be >= 99.30%), all test_test_style checks green,
   and `pytest -m slow --collect-only -q` showing the 3 live tests.
Do NOT run make test-live: it is a paid call and I run it myself.
```

---

## Part 3: Check it (you, after Prompt 3 is green)

```bash
make check                                  # fast suite, all green, coverage >= 99.30%
pytest --collect-only -q | tail -1          # more tests than 496
pytest tests/unit/test_test_style.py -q     # every file passes the guard
make serve                                  # terminal 1
make test-live                              # terminal 2: 3 passed, about $0.001
```

Send me the outputs and I'll verify the refactor (assert count, coverage, a sample of renamed tests) and update `codebase.md`.

---

## Part 4: Commit messages

| When | Message |
|---|---|
| Prompt 1 red | `test: add style guard enforcing one assert per test and AAA naming` |
| Prompt 2 green | `test: split unit tests to one assert per test with behaviour names` |
| Prompt 3 green | `test: split integration tests, add slow/eval markers and live T1 twin` |

---

## Part 5: PR description (paste into GitHub)

Title: `Tests: Arrange-Act-Assert, one assert per test, slow/eval markers`

```markdown
## What

The whole test suite now follows the Arrange-Act-Assert standard:

- One behaviour and one `assert` per test. Multi-assert tests (141 before) are split, so a
  failure names exactly what broke.
- Names follow `test_<unit>_<condition>_<expected>`; `pytest --collect-only -q` reads as
  the specification.
- Loops and ifs in test bodies are now parametrized cases with readable `ids`, or helpers.
- `tests/unit/test_test_style.py` parses the suite with `ast` and fails on any test with
  more than one assert, logic in its body, parametrize without ids, or a vague name, so the
  standard can't drift.
- `slow` and `eval` markers are registered. CI and `make check` run `-m "not slow"`.
- `tests/live/test_real_agent.py`: one real Haiku run of T1 (slow + eval, skipped without
  a key), run by hand with `make test-live`.

No production code changed.

## Why

- One assert per test means the first failure no longer hides the others, and the test
  name alone says what broke.
- A guard test keeps the standard enforced on every push instead of relying on review.
- Fakes were already injected everywhere (no patching), so only the tests needed to change.
- The live twin checks the same behaviour against the real model once, at about $0.001,
  without ever running in CI.

## Testing

- Collected tests: 496 before, ___ after. Assert statements: 657 before, ___ after
  (none removed).
- Coverage: 99.30% before, ___% after.
- Style guard: green for every test file.
- `make test-live` (Haiku): 3 passed, cost $_____.

## Next

M7: the reflection step, with its tests written to this standard from the start.
```
