# IT Equipment Request Handler: Requirements and Policy Rules

**Project:** Week 7 Lab, Build an MCP Server
**Author:** Julian Marchington
**Status:** v1.0, Part 1 deliverable
**Reference date for all examples and tests:** `2026-11-22` (passed in as `as_of`, never read from the system clock)

---

## 1. Purpose and scope

Employees send free-text requests for IT equipment. The system must:

1. Identify the employee and the item they want.
2. Look up the employee's role, tenure, and equipment on file.
3. Check the request against that role's policy limits.
4. **Approve** requests that clearly fall within policy, **deny** requests that clearly fall outside it, and **escalate** everything else to a human reviewer.

The guiding principle: **the agent never guesses.** If the data does not clearly support approve or deny, the request is escalated.

### 1.1 Who decides what

Following the "When Agents Are the Wrong Answer" decision framework, each part of the job uses the simplest tool that works.

| Responsibility | Owner | Why |
|---|---|---|
| Employee lookup, policy lookup | Plain code (MCP tools) | Deterministic data retrieval |
| Eligibility verdict (`ELIGIBLE` / `NOT_ELIGIBLE` / `NEEDS_REVIEW`) | Plain code (`check_request_eligibility`) | Same input must always give the same answer. Fully unit-testable. |
| Mapping free text to an employee ID, catalog item, and reason category | LLM agent | Needs language understanding |
| Spotting escalation triggers in the text (loss, damage, medical, etc.) | LLM agent | Needs language understanding |
| Choosing which tools to call, and in what order | LLM agent (ReAct loop) | Requests vary, so the investigation path varies |
| Final action | LLM agent, **bound by the fixed decision table in Section 6** | The agent applies rules. It does not invent them. |
| Drafting the reply, then reflecting on it | LLM agent | Language generation and self-check |

### 1.2 Out of scope (v1)

- Real procurement, shipping, or asset-tag assignment. "Approved" means the request is approved, not that hardware has shipped.
- Authentication. The `employee_id` in the request is trusted as the requester's ID (but see trigger `E10`).
- Software licences, access requests, and repairs.
- Cost budgets per department.

---

## 2. Data model

### 2.1 Equipment request (agent input)

| Field | Type | Required | Source | Rules |
|---|---|---|---|---|
| `request_id` | string | yes | test harness | Format `REQ-####` |
| `employee_id` | string | yes | parsed from request | Format `E####`. Trimmed and upper-cased before lookup. |
| `request_text` | string | yes | employee | Raw free text, 1 to 2,000 characters |
| `item` | catalog item ID | derived | agent | Must be exactly one ID from Section 3, or the request is escalated |
| `reason_category` | enum | derived | agent | One of: `new_need`, `refresh`, `performance`, `damaged`, `lost_or_stolen`, `accommodation`, `other` |
| `submitted_at` | ISO date | yes | test harness | Used as `as_of` for eligibility |

### 2.2 Employee record (mock data)

| Field | Type | Notes |
|---|---|---|
| `employee_id` | string | `E####`, unique |
| `name` | string | Display only |
| `role` | enum | `standard`, `engineer`, `manager`, `contractor` |
| `hire_date` | ISO date | Tenure is computed from this |
| `status` | enum | `active`, `on_leave`, `terminated` |
| `equipment` | list | Each entry has `item`, `asset_id`, `issued_date` |

### 2.3 Review ticket (created by `flag_for_human_review`)

| Field | Type | Notes |
|---|---|---|
| `review_id` | string | `REV-####`, sequential |
| `employee_id` | string | Stored as given, **even if the employee does not exist** (trigger `E1` must still be flaggable) |
| `request` | string | The original request text |
| `reason_code` | string | One escalation code from Section 5 |
| `reason` | string | Plain-English explanation for the reviewer |
| `status` | enum | Always `pending` when created |
| `created_at` | ISO datetime | |

---

## 3. Equipment catalog

Only these items can be approved automatically. Anything else is escalated (`E3`).

| Item ID | Description | Example phrases the agent should map to it |
|---|---|---|
| `laptop` | Standard-spec company laptop | "new laptop", "replacement computer" |
| `monitor` | 27-inch external monitor | "second screen", "another monitor" |
| `docking_station` | USB-C dock | "dock", "docking station" |
| `headset` | Wired or wireless headset | "headphones for calls" |
| `keyboard_mouse` | Keyboard and mouse set | "new keyboard", "mouse" |
| `mobile_phone` | Company smartphone | "work phone" |

---

## 4. Policy rules

### 4.1 Role entitlements

`max` is the most units of that item an employee may hold at once. `refresh` is how many months must pass after the issue date before an existing unit can be replaced. `0` means the role is not entitled to the item. `none` means the item has no refresh cycle and is replaced only after human review.

| Item | standard | engineer | manager | contractor |
|---|---|---|---|---|
| `laptop` | max 1, refresh 36 mo | max 1, refresh 24 mo | max 1, refresh 24 mo | **0** (supplied by agency) |
| `monitor` | max 1, refresh 36 mo | max 2, refresh 36 mo | max 2, refresh 36 mo | **0** |
| `docking_station` | max 1, refresh none | max 1, refresh none | max 1, refresh none | **0** |
| `headset` | max 1, refresh 24 mo | max 1, refresh 24 mo | max 1, refresh 24 mo | max 1, refresh 24 mo |
| `keyboard_mouse` | max 1, refresh 24 mo | max 1, refresh 24 mo | max 1, refresh 24 mo | max 1, refresh 24 mo |
| `mobile_phone` | **0** | **0** | max 1, refresh 24 mo | **0** |

### 4.2 Rule definitions

Each rule has an ID so that denials and test assertions can cite it.

| Rule ID | Rule |
|---|---|
| `R-ROLE` | If the role's `max` for the item is 0, the request is `NOT_ELIGIBLE`. |
| `R-NEW` | If the employee holds fewer units than `max`, a request for one more unit is `ELIGIBLE`. |
| `R-REFRESH-OK` | If the employee holds `max` units, the item has a refresh interval, and the **oldest** unit's refresh date is on or before `as_of`, the request is `ELIGIBLE` (as a replacement). |
| `R-REFRESH` | If the employee holds `max` units and the oldest unit's refresh date is **after** `as_of`, the request is `NOT_ELIGIBLE`. The result includes `next_eligible_date`. |
| `R-LIMIT` | If the employee holds `max` units and the item has no refresh cycle (`none`), the request is `NOT_ELIGIBLE`. |
| `R-STATUS` | If the employee's status is not `active`, the result is `NEEDS_REVIEW`. |

**Date rule (for testability):** refresh date = `issued_date` + N calendar months. If that day does not exist in the target month, use the last day of that month (for example, `2024-01-31` + 1 month = `2024-02-29`). A request is eligible **on** the refresh date, not only after it.

**Quantity rule:** every request is for exactly one unit. A request for more than one unit (for example, "two monitors") is escalated (`E5`).

### 4.3 Eligibility algorithm (`check_request_eligibility`)

The function evaluates these steps in order and stops at the first one that applies:

1. Employee ID not found: return error `EMPLOYEE_NOT_FOUND`.
2. Item not in catalog: return error `UNKNOWN_ITEM`.
3. Status not `active`: return `NEEDS_REVIEW` with `R-STATUS`.
4. Role `max` is 0: return `NOT_ELIGIBLE` with `R-ROLE`.
5. Units held < `max`: return `ELIGIBLE` with `R-NEW`.
6. Item has no refresh cycle: return `NOT_ELIGIBLE` with `R-LIMIT`.
7. Oldest unit's refresh date ≤ `as_of`: return `ELIGIBLE` with `R-REFRESH-OK`.
8. Otherwise: return `NOT_ELIGIBLE` with `R-REFRESH` and `next_eligible_date`.

This function is pure: no LLM, no side effects, and no system clock.

---

## 5. What counts as ambiguous: escalation triggers

A request is **ambiguous** when the tools' data alone cannot justify approve or deny, or when deciding needs judgment, verification, or authority beyond the written policy. Each trigger has a code that goes into `flag_for_human_review(reason=...)`.

| Code | Trigger | Detected by | Why a human is needed |
|---|---|---|---|
| `E1` | `EMPLOYEE_NOT_FOUND`: the employee ID does not exist or is missing | Tool | Cannot verify who is asking |
| `E2` | `EMPLOYEE_NOT_ACTIVE`: status is `on_leave` or `terminated` | Tool (`R-STATUS`) | Possible HR or security issue |
| `E3` | `NON_CATALOG_ITEM`: the item is not in Section 3 (for example, a standing desk or tablet) | Tool or agent | No policy exists to apply |
| `E4` | `ITEM_UNCLEAR`: the text cannot be mapped to exactly one catalog item ("I need a better setup") | Agent | Guessing the item would be guessing the decision |
| `E5` | `MULTIPLE_ITEMS`: more than one item, or more than one unit, in a single request | Agent | Mixed outcomes need a person to coordinate |
| `E6` | `LOST_OR_STOLEN`: the employee says the item was lost or stolen | Agent | Needs a security report and asset write-off |
| `E7` | `DAMAGE_OUTSIDE_CYCLE`: the employee says the item is broken, **and** the tool returns `NOT_ELIGIBLE` | Agent and tool | Damage claims need IT to verify before an early replacement |
| `E8` | `NON_STANDARD_SPEC`: asks for a specific model, brand, or upgraded spec ("MacBook Pro with 64 GB") | Agent | Policy only covers standard-spec items |
| `E9` | `ACCOMMODATION`: mentions a medical need, disability, or ergonomic accommodation | Agent | Accommodations are an HR decision, not an IT policy lookup |
| `E10` | `IDENTITY_MISMATCH`: request is on behalf of someone else, or the named person does not match the ID's record | Agent | Requester and recipient must be confirmed |
| `E11` | `TOOL_FAILURE_OR_CONFLICT`: a tool returned an error not listed above, data conflicts, or the agent hit its step limit | Agent | Unreliable data must not drive a decision |

Not escalation triggers on their own:

- **Performance complaints** ("my laptop is slow") are treated as an ordinary refresh request. The tool's verdict decides.
- **Damage claims where the tool returns `ELIGIBLE`** are approved, because the employee is due a replacement anyway.

---

## 6. Decision table (the agent must follow this exactly)

The agent evaluates rows top to bottom and takes the **first** matching row.

| # | Condition | Action | Tool calls required before acting |
|---|---|---|---|
| 1 | Any of `E1`, `E2`, `E3`, `E4`, `E5`, `E6`, `E8`, `E9`, `E10`, `E11` applies | **Escalate** | `flag_for_human_review` |
| 2 | `check_request_eligibility` returned `ELIGIBLE` | **Approve** | `get_employee_info`, `check_request_eligibility` |
| 3 | Returned `NOT_ELIGIBLE` and `reason_category` is `damaged` | **Escalate** (`E7`) | `check_request_eligibility`, `flag_for_human_review` |
| 4 | Returned `NOT_ELIGIBLE` | **Deny** and cite the rule ID | `get_employee_info`, `check_request_eligibility`, `get_policy_limits` |
| 5 | Anything else | **Escalate** (`E11`) | `flag_for_human_review` |

### 6.1 Hard constraints

- **C1.** The agent may approve **only** if `check_request_eligibility` returned `ELIGIBLE` in the current run.
- **C2.** The agent may deny **only** if `check_request_eligibility` returned `NOT_ELIGIBLE`, and the reply must cite the rule ID.
- **C3.** Every escalation must call `flag_for_human_review` and quote the returned `review_id` in the reply.
- **C4.** The reply must not state any date, quantity, or entitlement that did not appear in a tool observation in this run.
- **C5.** The reply must not promise a delivery date, a specific model, or an outcome of human review.
- **C6.** Maximum of 8 tool calls per request. Reaching the limit triggers `E11`.
- **C7.** `flag_for_human_review` may be called at most once per request.

---

## 7. MCP tool contracts

All tools return JSON. Errors are returned as structured objects, never raised to the agent, so the model can reason about them (per the "Tool Use and Function Calling" lesson).

### 7.1 `get_employee_info(employee_id)`

- **Read-only.**
- **Description for the model:** "Look up an employee's role, hire date, tenure, employment status, and the equipment currently issued to them. Use this first for any request about a specific employee."
- **Success:**
  ```json
  {"employee_id": "E1001", "name": "Priya Shah", "role": "standard",
   "hire_date": "2021-06-14", "tenure_months": 65, "status": "active",
   "equipment": [{"item": "laptop", "asset_id": "LT-2291", "issued_date": "2025-03-10"}]}
  ```
- **Error:** `{"error": "EMPLOYEE_NOT_FOUND", "employee_id": "E9999"}`
- **Error:** `{"error": "INVALID_EMPLOYEE_ID", "employee_id": "abc"}` (does not match `E####`)

### 7.2 `get_policy_limits(role)`

- **Read-only.**
- **Description for the model:** "Return the equipment a role is entitled to, the maximum units per item, and the refresh interval in months. Use this to explain a decision or a denial."
- **Success:** `{"role": "standard", "limits": {"laptop": {"max": 1, "refresh_months": 36}, "mobile_phone": {"max": 0, "refresh_months": null}, ...}}`
- **Error:** `{"error": "UNKNOWN_ROLE", "role": "intern"}`
- Role lookup is case-insensitive.

### 7.3 `check_request_eligibility(employee_id, item)`

- **Read-only, deterministic.** Implements Section 4.3 exactly.
- **Description for the model:** "Decide whether one unit of a catalog item is within policy for this employee. Returns ELIGIBLE, NOT_ELIGIBLE, or NEEDS_REVIEW, with the rule applied. This is the only source of truth for eligibility."
- **Success:**
  ```json
  {"employee_id": "E1001", "item": "laptop", "status": "NOT_ELIGIBLE",
   "rule_id": "R-REFRESH", "units_held": 1, "max": 1,
   "oldest_issued_date": "2025-03-10", "next_eligible_date": "2028-03-10",
   "as_of": "2026-11-22"}
  ```
- **Errors:** `EMPLOYEE_NOT_FOUND`, `INVALID_EMPLOYEE_ID`, `UNKNOWN_ITEM`
- `as_of` defaults to the reference date in tests and is injectable.

### 7.4 `flag_for_human_review(employee_id, request, reason)`

- **Side-effecting.** Appends a review ticket (Section 2.3) to the review queue (a JSON file in v1).
- **Description for the model:** "Escalate a request to a human reviewer. Use this when any escalation trigger applies or the decision is not clear-cut. Do not use it for requests that are clearly approved or denied."
- **Argument validation (before writing anything):**
  - `request` must be non-empty.
  - `reason` must start with a valid code (`E1` to `E11`) followed by an explanation, for example `"E6: Employee reports company phone stolen."`
  - `employee_id` is **not** required to exist, because `E1` escalations must still be recorded.
- **Idempotent:** the same `employee_id` and `request` flagged twice returns the existing `review_id` and creates no duplicate.
- **Success:** `{"review_id": "REV-0001", "status": "pending", "reason_code": "E6"}`
- **Error:** `{"error": "INVALID_REASON", "detail": "reason must start with a code E1-E11"}`

---

## 8. Mock data (as of 2026-11-22)

| ID | Name | Role | Hire date | Status | Equipment on file (item, issued date) |
|---|---|---|---|---|---|
| E1001 | Priya Shah | standard | 2021-06-14 | active | laptop 2025-03-10; monitor 2021-06-14; keyboard_mouse 2021-06-14 |
| E1002 | Marcus Lee | engineer | 2022-01-10 | active | laptop 2024-02-05; monitor 2022-01-10; docking_station 2022-01-10 |
| E1003 | Dana Okafor | manager | 2019-09-03 | active | laptop 2025-08-18; monitor 2023-04-11; monitor 2023-04-11; mobile_phone 2025-01-20 |
| E1004 | Sam Rivera | contractor | 2026-07-01 | active | headset 2026-07-01 |
| E1005 | Alex Chen | standard | 2020-02-17 | active | laptop 2022-10-03; monitor 2020-02-17 |
| E1006 | Jordan Park | engineer | 2023-05-22 | on_leave | laptop 2025-05-22; monitor 2023-05-22 |

Pre-computed eligibility checks (these become unit test assertions):

| Employee | Item | Expected status | Rule | Reasoning |
|---|---|---|---|---|
| E1001 | laptop | NOT_ELIGIBLE | R-REFRESH | Issued 2025-03-10, plus 36 months = 2028-03-10, which is after as_of |
| E1001 | keyboard_mouse | ELIGIBLE | R-REFRESH-OK | 2021-06-14 plus 24 months = 2023-06-14, which has passed |
| E1001 | mobile_phone | NOT_ELIGIBLE | R-ROLE | Standard role max is 0 |
| E1002 | monitor | ELIGIBLE | R-NEW | Holds 1, engineer max is 2 |
| E1002 | docking_station | NOT_ELIGIBLE | R-LIMIT | Holds 1 of 1, no refresh cycle |
| E1002 | laptop | ELIGIBLE | R-REFRESH-OK | 2024-02-05 plus 24 months = 2026-02-05, which has passed |
| E1003 | mobile_phone | NOT_ELIGIBLE | R-REFRESH | 2025-01-20 plus 24 months = 2027-01-20 |
| E1003 | monitor | ELIGIBLE | R-REFRESH-OK | Holds 2 of 2; oldest 2023-04-11 plus 36 months = 2026-04-11, which has passed (replacement, not a third monitor) |
| E1004 | laptop | NOT_ELIGIBLE | R-ROLE | Contractor max is 0 |
| E1005 | laptop | ELIGIBLE | R-REFRESH-OK | 2022-10-03 plus 36 months = 2025-10-03, which has passed |
| E1006 | monitor | NEEDS_REVIEW | R-STATUS | Status is on_leave |
| E9999 | laptop | error EMPLOYEE_NOT_FOUND | — | Not in data |
| E1001 | standing_desk | error UNKNOWN_ITEM | — | Not in catalog |

---

## 9. Required demonstration requests

The brief requires at least one approval, one denial, and two escalations for different reasons. These four are the primary demo. Extra requests are listed for wider testing.

### 9.1 Primary four

| # | Request text | Expected path | Expected result |
|---|---|---|---|
| **T1, approve** | "Hi, I'm Marcus (E1002). I'd like a second monitor for my desk, I'm juggling a lot of windows." | `get_employee_info` → `check_request_eligibility(E1002, monitor)` returns ELIGIBLE, R-NEW | **Approved.** Reply says the engineer role allows 2 monitors and Marcus holds 1. |
| **T2, deny** | "This is Priya, E1001. My laptop feels slow lately, can I get a new one?" | `check_request_eligibility(E1001, laptop)` returns NOT_ELIGIBLE, R-REFRESH → `get_policy_limits(standard)` | **Denied** under `R-REFRESH`. Reply gives next eligible date 2028-03-10 and suggests the IT helpdesk for performance troubleshooting. No escalation, because "slow" is a performance complaint, not damage. |
| **T3, escalate (security)** | "Dana Okafor, E1003. My work phone was stolen from my car last night and I need a replacement ASAP." | Agent detects `E6`. May confirm the employee and phone on file first. → `flag_for_human_review(E1003, ..., "E6: ...")` | **Escalated, E6.** Reply quotes the review ID and says a security report may be needed. It does not promise a replacement. |
| **T4, escalate (HR)** | "I'm Alex, E1005. My doctor recommended a standing desk and an ergonomic keyboard for my back pain." | Agent detects `E9` (accommodation) and `E3` (standing desk not in catalog). → `flag_for_human_review(E1005, ..., "E9: ...")` | **Escalated, E9.** Reply quotes the review ID and says HR will follow up. It does not approve the keyboard on its own. |

### 9.2 Extra coverage

| # | Request | Expected |
|---|---|---|
| T5 | E1005: "My laptop is 4 years old and slow, can I get a replacement?" (the brief's own example) | **Approve**, R-REFRESH-OK |
| T6 | E1004 (contractor): "Can I get a company laptop?" | **Deny**, R-ROLE |
| T7 | E9999: "I need a new headset." | **Escalate**, E1 |
| T8 | E1006 (on leave): "Can I get a second monitor?" | **Escalate**, E2 |
| T9 | E1002: "My dock stopped working, can I get a new one?" | NOT_ELIGIBLE (R-LIMIT) plus damage claim, so **escalate**, E7 |
| T10 | E1002: "I want a MacBook Pro M4 Max with 64 GB RAM." | **Escalate**, E8 |
| T11 | E1001: "Can I get a new laptop and a headset?" | **Escalate**, E5 |
| T12 | E1001: "I need a better setup." | **Escalate**, E4 |

---

## 10. Draft response requirements

Every reply includes the decision, the reason in plain English, and one next step.

- **Approve:** name the item, cite the rule (for example, "your role allows up to 2 monitors and you have 1"), and say IT will arrange fulfilment. Do not give a delivery date.
- **Approve under `R-REFRESH-OK`:** say clearly that this is a **replacement**, and that the old unit (by `asset_id`) must be returned. It never raises the employee above `max`.
- **Deny:** name the item, cite the rule ID and the plain-English reason, and give `next_eligible_date` when the tool returned one. Offer an alternative where one exists (helpdesk troubleshooting, or ask a manager to raise an exception).
- **Escalate:** say the request was sent for human review, quote the `review_id`, state the reason in plain English, and do not predict the outcome.

---

## 11. Reflection checklist (Part 5)

Before finalizing, the agent reviews its own draft against these questions. If any answer is "no", it revises the draft and logs what changed.

1. Does the decision match the first matching row of the decision table (Section 6)?
2. Does every date, count, and entitlement in the draft appear in a tool observation from this run? (C4)
3. Does the draft avoid promises of delivery dates, specific models, or review outcomes? (C5)
4. For a denial, is the rule ID cited, and is `next_eligible_date` included when it exists?
5. For an escalation, was `flag_for_human_review` actually called, and is its `review_id` in the draft?
6. Does the draft answer the employee's actual request, including every item they mentioned?

The reflection result is logged as `REFLECTION: CONFIRMED` or `REFLECTION: REVISED (<what changed>)`. This log is the evidence for deliverable 8.

---

## 12. Acceptance criteria and test plan

### 12.1 Unit tests (on the plain functions, not through MCP)

| Tool | Normal cases | Edge cases |
|---|---|---|
| `get_employee_info` | Returns correct record for E1001 | Unknown ID (E9999); bad format (`abc`, empty string); lower-case and whitespace input (`" e1001 "`) normalizes to E1001 |
| `get_policy_limits` | Returns all six items for each of the four roles | Unknown role; mixed case (`"Manager"`) |
| `check_request_eligibility` | Every row of the table in Section 8 | Refresh date exactly equal to `as_of` is ELIGIBLE; refresh date one day after `as_of` is NOT_ELIGIBLE; month-end clamp (issued 2024-01-31 with 1-month interval gives 2024-02-29); unknown item; unknown employee |
| `flag_for_human_review` | Creates REV-0001 with status pending and correct reason code | Missing or invalid reason code is rejected and nothing is written; empty request is rejected; unknown employee ID is still accepted; duplicate flag returns the same review ID |

### 12.2 Agent acceptance criteria

- Each of T1 to T4 produces the expected decision in Section 9.1.
- Each run prints a full trace in the form `Thought → Action → Observation`, ending in `Final Answer`.
- No approval happens without an `ELIGIBLE` observation (C1). No denial happens without `NOT_ELIGIBLE` (C2).
- Every escalation produces exactly one review ticket (C3, C7).
- At least one run shows the reflection step revising a draft, or explicitly confirming it.

### 12.3 Pipeline

- CI runs all unit tests on every push. A failing test fails the build.
- Agent demo runs are not part of CI, because they call a live model and are not deterministic.
