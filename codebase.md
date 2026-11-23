# codebase.md: What the Code Does and Why

> This file is kept up to date with every code change, in the same commit.
> It describes the code **as it is now**, not as planned. For the plan, see `PLAN.md`. For the rules the system must follow, see `REQUIREMENTS.md`.

**Last updated:** 2026-11-23, planning stage (before M0)

---

## 1. What this project is

An internal IT equipment request handler for the Week 7 lab:
- **A Python MCP server** exposes four tools over mock employee and policy data.
- **A ReAct agent** on the Claude API uses those tools to approve, deny or escalate free-text equipment requests.

Policy decisions are made by plain, tested Python functions. The LLM only interprets the request text, chooses which tools to call, and drafts the reply.

## 2. Current state

| File | What it does | Status |
|---|---|---|
| `server.py` | Minimal MCP server named "Demo" with one trivial tool, `get_employee_id(employee_id: int) -> int`, which returns its input. It also has a sample `greeting://{name}` resource from the SDK quickstart. | Working (brief step 1). It will be replaced by the real server in M1. |
| `client.py` | Connects to `http://localhost:8000/mcp`, calls `get_employee_id` with `1234`, and prints the structured result `{'result': 1234}`. | Working (brief step 1) |
| `REQUIREMENTS.md` | Requirements and policy rules (Part 1 deliverable) | v1.0; the v1.1 fixes are planned in M0 |
| `PLAN.md` | Project plan | Draft, awaiting approval |

**Environment:**
- Python 3.14 in `.venv/`.
- `mcp` 2.3.0 is installed. Note that its API is `from mcp.server import MCPServer` and `from mcp import Client`, which is newer than many tutorials.
- `opentelemetry-api` is installed.
- Not installed yet: pytest, an LLM SDK, any CI configuration, a `.gitignore`.

## 3. Layout

To be filled in at M0, when the `src/` layout is created.

## 4. How to run

**Server (current trivial version):**
```bash
source .venv/bin/activate
python server.py          # serves on http://localhost:8000/mcp
```

**Client, in a second terminal:**
```bash
source .venv/bin/activate
python client.py          # expected output: {'result': 1234}
```

## 5. Design decisions

Each entry: the decision, and why. New entries are added as code lands.

1. **TDD throughout.** Every change starts with a failing test.
   *Why:* the policy rules are exact, so tests written from REQUIREMENTS §8 pin the behaviour down before any code exists.

2. **Policy logic lives in pure functions, and the MCP layer is a thin wrapper.**
   *Why:* the brief asks for unit tests on the plain functions. Pure functions are also deterministic and need no fakes.

## 6. Change log

| Date | Milestone | Change | Why |
|---|---|---|---|
| 2026-11-22 | — | `server.py` and `client.py` created with a trivial tool | Brief step 1: prove the SDK works before writing business logic |
| 2026-11-23 | Planning | `PLAN.md` and `codebase.md` added | Plan agreed before building; this file tracks the code from here on |

## 7. Known issues and fixes

None yet. Each entry will record the symptom, cause, evidence and fix.
