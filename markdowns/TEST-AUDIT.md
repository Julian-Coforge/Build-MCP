# Test Audit: every test against the AAA standard

**Date:** 2026-11-25 · **Code audited:** `main` at `8fbf30f` (M6 merged) · **Standard:** `anthropic-skills:aaa-unit-testing` (summarised in `CLAUDE.md`, "Test standard")
**How:** every production module in `src/it_requests/` was read first, so each behaviour could be matched to the test that checks it. Then every test function was checked against the ten rules: by an `ast` scan (asserts, logic, `ids`, names) and by reading the files (layout, fakes, determinism, edge cases).

## Summary

- **118 of 292 test functions (40%) fit the standard as they are. 174 need changes.** Almost all of those are splits and renames. Only a handful need new code.
- **What already fits:**
  - *Rule 4, fakes over mocks:* there is no `patch` or `monkeypatch` anywhere. The LLM `create`, the MCP client, the clock, the tracer and every file path are injected.
  - *Rule 7, determinism:* tests use `tmp_path`, a fixed clock, a fresh tracer per test, and `pytest.approx` for money.
  - *Rule 8, edge cases:* the edge-case checklist is mostly covered already (boundaries, idempotency, no mutation, invalid input).
  - *Rule 6, strict xfail:* there are no known bugs, so it isn't needed yet.
- **What doesn't fit:**
  - *Rule 1, one assert per test:* 140 tests have more than one assert. The integration tests are the worst: all 30 agent-loop and single-call tests break it, and `test_t1_approve` has 11 asserts.
  - *Rule 9, no logic in test bodies:* 19 tests contain `if` or `for`.
  - *Rule 8, readable ids:* 40 parametrized tests have no `ids=`.
  - *Rule 2, names:* 13 names are too short or name no condition or outcome, for example `test_t1_approve` and `test_engineer_limits`.
  - *Rule 5, markers:* `slow` and `eval` aren't registered, and there is no `test_real_` twin.
  - *Rule 3, layout:* several multi-step tests have no blank lines between Arrange, Act and Assert. That can only be seen by reading the file, so it isn't counted in the table.
- **Behaviours with no test:** 8, listed below. Five are worth adding during the refactor.

## Per-file result

"Fit" means the test passes every rule. A test can break more than one rule.

| File | Tests | Fit | Multi-assert | Logic in body | Parametrize w/o ids | Weak name |
|---|---|---|---|---|---|---|
| `integration/test_agent_loop.py` | 20 | 0 | 20 | 3 | 0 | 2 |
| `integration/test_agent_single_call.py` | 7 | 0 | 7 | 1 | 0 | 0 |
| `integration/test_server_all_tools.py` | 2 | 0 | 2 | 0 | 0 | 0 |
| `integration/test_server_eligibility_tool.py` | 7 | 1 | 6 | 0 | 1 | 0 |
| `integration/test_server_employee_tool.py` | 6 | 0 | 6 | 0 | 1 | 0 |
| `integration/test_server_meta.py` | 5 | 3 | 2 | 1 | 0 | 0 |
| `integration/test_server_policy_tool.py` | 6 | 1 | 5 | 0 | 0 | 2 |
| `integration/test_server_review_tool.py` | 6 | 1 | 5 | 0 | 0 | 0 |
| `unit/test_config.py` | 1 | 1 | 0 | 0 | 0 | 0 |
| `unit/test_cost_report.py` | 7 | 1 | 6 | 1 | 0 | 0 |
| `unit/test_costs.py` | 17 | 11 | 0 | 0 | 6 | 0 |
| `unit/test_data.py` | 9 | 5 | 4 | 0 | 0 | 0 |
| `unit/test_eligibility.py` | 18 | 7 | 9 | 2 | 2 | 1 |
| `unit/test_employees.py` | 10 | 4 | 3 | 0 | 4 | 1 |
| `unit/test_evals.py` | 30 | 13 | 8 | 1 | 5 | 5 |
| `unit/test_guards.py` | 43 | 33 | 8 | 0 | 3 | 0 |
| `unit/test_llm.py` | 11 | 5 | 6 | 0 | 0 | 0 |
| `unit/test_mcp_bridge.py` | 6 | 4 | 1 | 0 | 1 | 0 |
| `unit/test_observability.py` | 13 | 4 | 9 | 1 | 1 | 0 |
| `unit/test_policy.py` | 7 | 0 | 4 | 1 | 4 | 1 |
| `unit/test_policy_data.py` | 6 | 3 | 2 | 0 | 2 | 0 |
| `unit/test_prompts.py` | 9 | 2 | 5 | 3 | 2 | 0 |
| `unit/test_reviews.py` | 15 | 5 | 8 | 0 | 7 | 0 |
| `unit/test_tools.py` | 16 | 8 | 7 | 3 | 1 | 0 |
| `unit/test_trace_format.py` | 7 | 3 | 3 | 0 | 0 | 1 |
| `unit/test_tracing.py` | 8 | 3 | 5 | 2 | 0 | 0 |
| **Total** | **292** | **118** | | | | |

## Notes per file

**Integration**
- `test_agent_loop.py`: 0 of 20 fit. Each test runs a whole scenario and then checks 4 to 11 facts. The fix is one fixture per scenario (T1, T2, T3, C1 correction, two rejections, step cap, C7, max calls, parallel tools, nudge) that returns the result, the fake's calls, the ledger, the spans and the logs. Then write one test per fact. `test_logs_have_step_events_and_run_end`, `test_first_request_uses_react_prompt_tools_and_request_id` and `test_server_never_sees_thought_and_gets_request_id` loop over records: move the loops into `_events()` and `_span()` helpers. Rename `test_t1_approve` and `test_t2_deny` to `test_run_agent_t1_...`.
- `test_agent_single_call.py`: 0 of 7 fit. Same pattern. `test_trace_links_run_llm_call_and_tool` has 11 asserts. The name-leak test loops over files: parametrize it over (trace, ledger, logs) with ids.
- `test_server_*_tool.py`: these mostly check a structured result and then a log line in the same test. Split them into "returns ..." and "logs ..." tests, one field each, or compare the whole log record with the timestamp dropped. Rename `test_engineer_limits` and `test_t2_deny_path`.
- `test_server_meta.py`: `test_tool_signatures_and_descriptions_are_unchanged` loops over tools. Parametrize it over the four tools with `ids=` and the tool name.

**Unit**
- `test_guards.py` (33 of 43 fit) and `test_costs.py` (11 of 17 fit) are closest to the standard. They mostly need `ids=` on parametrize and a few splits.
- `test_eligibility.py`: the §8 table test loops over rows. Make it parametrized with `ids=` like `E1002-monitor`, and compare the whole expected row, or one field per test. The per-employee tests have 4 to 6 asserts on the same verdict: split them per field, sharing a module-level `_verdict(...)` helper.
- `test_observability.py`: 9 of 13 have multiple asserts. For example, `test_request_id_taken_from_context_var` also asserts the reset. Make that its own test: `test_request_id_var_is_reset_after_use`.
- `test_reviews.py`: 7 parametrize decorators without ids. Duplicate and corrupt-queue tests check the result and the file in one test: split them.
- `test_policy.py`: 0 of 7 fit. `test_standard_returns_all_items_with_exact_values` has 7 asserts. Compare the whole `limits` dict instead. The contractor test loops over items: parametrize it.
- `test_prompts.py`: three tests loop over required words. Parametrize them over the words, with the word as the id.
- `test_tools.py`, `test_tracing.py`: loops over properties and spans. Parametrize, or project and compare, e.g. `assert [p for p in props][0] == "thought"`.
- `test_evals.py`: five weak names (e.g. `test_format_summary_table`) and five parametrize decorators without ids.
- `test_llm.py`: `test_one_call_writes_one_ledger_line` has 12 asserts. Use one fixture for the single call, then one test per ledger field.

## Behaviours in the code with no test

Found by reading each module against its tests. The first five should be added during the refactor, in the file named.

| # | Behaviour | Where | Add to |
|---|---|---|---|
| 1 | A failed queue write returns `QUEUE_UNAVAILABLE`, and the temp file is removed. These are the only uncovered lines in `reviews.py` (75-77, 146-147). | `reviews._write_atomically`, `flag_for_human_review` | `unit/test_reviews.py`: pass a `queue_path` whose parent is a *file*, so the write fails without patching anything. Two tests: the error code, and no `.review_queue.*.tmp` left behind. |
| 2 | A queue file with valid JSON but a malformed ticket (`{"tickets": [{}]}`) is rejected. Only `"not json"`, a missing key and a non-list are tested now. | `reviews._read_queue` | `unit/test_reviews.py`: add the case to the corrupt-queue parametrize. |
| 3 | Redaction is case-insensitive and covers `authorization`, `password` and `token`. Only lower-case `name` and `api_key` are tested. | `observability._redact` | `unit/test_observability.py`: parametrize over `API_KEY`, `Authorization`, `password`, `token`, with ids. |
| 4 | `with_thought` replaces a tool's existing `thought` property instead of adding a second one. | `agent/tools.with_thought` | `unit/test_tools.py`: two tests, one for properties and one for required. |
| 5 | A fallback with no employee ID in any step or in the request text files the ticket as `UNKNOWN`. | `agent/loop._Run.employee_id` | `integration/test_agent_loop.py`: a request with no `E####`, and a script that never calls a tool, then falls back. |
| 6 | `traceparent()` produces the W3C format, and `span_safe_arguments()` redacts `request` and `reason`. Both are only checked indirectly through the loop. | `agent/mcp_bridge` | `unit/test_mcp_bridge.py`: one test each. Low cost. |
| 7 | `JsonLinesSpanExporter.force_flush()` returns `True` (`tracing.py` line 60, uncovered). | `tracing` | Optional; trivial. |
| 8 | `server._utc_now()` and `server.main()` only run in the real server. | `server.py` | Leave as is (main has `# pragma: no cover`). It's covered by the live demo runs. |

Also missing per rule 5: a `test_real_` twin against the real model. That's in Prompt 3 of `AAA-tests.md`.

## Appendix: every test that does not fit (the refactor checklist)

#### `integration/test_agent_loop.py`

- `test_t1_approve`: 11 asserts, weak name
- `test_server_never_sees_thought_and_gets_request_id`: 4 asserts, logic
- `test_first_request_uses_react_prompt_tools_and_request_id`: 6 asserts, logic
- `test_t2_deny`: 5 asserts, weak name
- `test_t3_escalate_e6_writes_one_ticket`: 4 asserts
- `test_tool_error_is_returned_as_data_not_is_error`: 4 asserts
- `test_c1_rejects_approve_without_eligibility_then_model_corrects`: 8 asserts
- `test_malformed_final_answer_is_rejected_then_corrected`: 3 asserts
- `test_two_rejected_final_answers_fall_back_to_e11`: 9 asserts
- `test_fallback_reuses_an_existing_review_ticket`: 5 asserts
- `test_step_cap_blocks_ninth_call_then_falls_back`: 7 asserts
- `test_step_cap_then_escalation_is_not_a_fallback`: 3 asserts
- `test_second_flag_is_blocked_by_c7`: 4 asserts
- `test_max_model_calls_falls_back`: 4 asserts
- `test_two_tool_uses_in_one_turn_both_run_one_reply`: 4 asserts
- `test_assistant_turn_is_sent_back_unchanged`: 3 asserts
- `test_turn_without_tool_use_asks_for_final_answer`: 3 asserts
- `test_trace_has_run_llm_and_tool_spans`: 10 asserts
- `test_logs_have_step_events_and_run_end`: 7 asserts, logic
- `test_private_text_stays_out_of_trace_and_info_logs`: 4 asserts

#### `integration/test_agent_single_call.py`

- `test_one_tool_call_returns_server_verdict`: 7 asserts
- `test_model_gets_server_tools_system_prompt_and_request`: 4 asserts
- `test_ledger_has_one_line_matching_the_run`: 4 asserts
- `test_trace_links_run_llm_call_and_tool`: 11 asserts
- `test_run_start_and_end_logged_with_cost`: 6 asserts
- `test_employee_name_is_never_traced_logged_or_ledgered`: 4 asserts, logic
- `test_reply_without_tool_use_makes_no_tool_call`: 5 asserts

#### `integration/test_server_all_tools.py`

- `test_t2_deny_path`: 4 asserts
- `test_t3_escalate_path`: 3 asserts

#### `integration/test_server_eligibility_tool.py`

- `test_t1_new_monitor_is_eligible`: 3 asserts
- `test_t2_laptop_refresh_not_due`: 3 asserts
- `test_on_leave_needs_review`: 2 asserts
- `test_unknown_item_is_data_not_protocol_error`: 2 asserts
- `test_tool_call_log_line`: 8 asserts, no ids
- `test_log_never_contains_employee_name`: 2 asserts

#### `integration/test_server_employee_tool.py`

- `test_lists_exactly_the_registered_tools`: 2 asserts
- `test_known_employee_returns_structured_record`: 5 asserts
- `test_unknown_employee_is_data_not_protocol_error`: 2 asserts
- `test_messy_id_normalises_through_protocol`: 2 asserts
- `test_tool_call_logs_one_event_without_name`: 8 asserts
- `test_log_shows_raw_and_resolved_employee_id`: 3 asserts, no ids

#### `integration/test_server_meta.py`

- `test_request_id_does_not_leak_into_the_next_call`: 2 asserts
- `test_tool_signatures_and_descriptions_are_unchanged`: 2 asserts, logic

#### `integration/test_server_policy_tool.py`

- `test_engineer_limits`: 4 asserts, weak name
- `test_mixed_case_role_works_through_protocol`: 2 asserts, weak name
- `test_unknown_role_is_data_not_protocol_error`: 2 asserts
- `test_none_and_null_survive_the_protocol`: 3 asserts
- `test_tool_call_logs_one_event`: 5 asserts

#### `integration/test_server_review_tool.py`

- `test_t3_flag_creates_ticket`: 6 asserts
- `test_repeat_flag_is_idempotent`: 3 asserts
- `test_invalid_reason_is_data_and_writes_nothing`: 3 asserts
- `test_log_line_has_ticket_fields_but_no_request_text`: 8 asserts
- `test_error_log_line_has_error_status`: 3 asserts

#### `unit/test_cost_report.py`

- `test_summarise_totals_and_groups`: 7 asserts
- `test_empty_ledger_has_full_budget_remaining`: 4 asserts
- `test_missing_group_keys_go_to_unknown`: 3 asserts
- `test_format_report_shows_every_section`: 4 asserts, logic
- `test_main_prints_report_for_a_ledger`: 2 asserts
- `test_main_with_no_ledger_reports_zero`: 2 asserts

#### `unit/test_costs.py`

- `test_each_token_kind_has_its_own_price`: no ids
- `test_corrupt_line_raises_with_line_number`: no ids
- `test_check_budget_status`: no ids
- `test_check_budget_refuses_at_or_over_budget`: no ids
- `test_budget_from_env`: no ids
- `test_bad_budget_value_raises`: no ids

#### `unit/test_data.py`

- `test_real_employee_file_loads`: 2 asserts
- `test_get_returns_employee_with_all_equipment`: 2 asserts
- `test_catalog_items_match_requirements`: 2 asserts
- `test_roles_match_requirements`: 2 asserts

#### `unit/test_eligibility.py`

- `test_requirements_section_8`: 3 asserts, logic
- `test_e1001_laptop_refresh_not_due`: 6 asserts
- `test_e1002_monitor_new_unit`: 4 asserts
- `test_e1003_monitor_replacement_breaks_tie_by_asset_id`: 4 asserts
- `test_eligible_on_the_refresh_date`: 2 asserts
- `test_not_eligible_one_day_before`: 3 asserts
- `test_add_months`: no ids, weak name
- `test_status_checked_before_role`: 2 asserts
- `test_every_employee_and_item_gives_a_valid_verdict`: 3 asserts, logic
- `test_every_verdict_has_the_same_keys`: no ids
- `test_status_verdict_stops_before_policy`: 4 asserts

#### `unit/test_employees.py`

- `test_normalize_employee_id`: no ids
- `test_tenure_months`: no ids, weak name
- `test_e1001_returns_full_record`: 10 asserts
- `test_on_leave_employee_is_returned_not_judged`: 2 asserts
- `test_messy_input_normalises_to_e1001`: 2 asserts, no ids
- `test_bad_format_returns_invalid_id_with_raw_input`: no ids

#### `unit/test_evals.py`

- `test_real_cases_load_t1_to_t12`: 2 asserts
- `test_every_real_case_has_every_field`: logic
- `test_primary_four_text_is_word_for_word`: 4 asserts
- `test_expected_outcomes_match_requirements_section_9`: no ids
- `test_required_tools_follow_the_decision_table`: 4 asserts
- `test_missing_field_fails_loudly`: no ids
- `test_invalid_case_fails_loudly`: no ids
- `test_approve_passes`: 2 asserts, weak name
- `test_deny_passes`: weak name
- `test_escalate_passes`: weak name
- `test_fallback_never_passes`: 3 asserts
- `test_summarise_counts_passes_per_case_in_order`: 3 asserts
- `test_summarise_empty`: weak name
- `test_format_summary_table`: 4 asserts
- `test_resolve_model`: no ids, weak name
- `test_resolve_model_rejects_others`: no ids
- `test_estimate_uses_plan_per_run_figures`: 2 asserts

#### `unit/test_guards.py`

- `test_investigation_tools_and_limit_match_c6`: 2 asserts
- `test_ninth_investigation_call_is_blocked`: no ids
- `test_failed_investigation_calls_still_count`: 2 asserts
- `test_second_flag_is_blocked`: 2 asserts
- `test_flag_retry_is_allowed_after_a_rejected_flag`: 2 asserts
- `test_approve_with_not_eligible_observation_fails_c1`: 2 asserts
- `test_escalate_with_invalid_reason_code_fails_c3`: no ids
- `test_two_successful_flags_fail_c7`: 2 asserts
- `test_each_code_has_one_sentence_naming_it`: 3 asserts, no ids
- `test_c1_message_names_the_required_tool_and_verdict`: 2 asserts

#### `unit/test_llm.py`

- `test_one_call_writes_one_ledger_line`: 12 asserts
- `test_missing_cache_counts_are_zero`: 2 asserts
- `test_request_uses_auto_tool_choice_adaptive_thinking_and_token_cap`: 7 asserts
- `test_span_records_model_tokens_and_cost`: 11 asserts
- `test_failed_call_writes_no_ledger_line_and_marks_span_error`: 3 asserts
- `test_log_line_has_numbers_but_no_prompt_content`: 8 asserts

#### `unit/test_mcp_bridge.py`

- `test_call_tool_returns_structured_content_and_sends_meta`: 2 asserts
- `test_failed_tool_call_is_returned_as_data`: no ids

#### `unit/test_observability.py`

- `test_log_event_writes_one_json_line`: 6 asserts
- `test_ts_is_utc_iso_with_z`: 3 asserts
- `test_request_id_taken_from_context_var`: 2 asserts
- `test_sensitive_fields_are_redacted`: 2 asserts, no ids
- `test_exception_adds_type_and_message`: 4 asserts
- `test_log_line_inside_span_carries_its_ids`: 2 asserts
- `test_log_line_outside_span_has_null_ids`: 2 asserts
- `test_configure_logging_returns_named_logger`: 4 asserts
- `test_configure_logging_writes_json_lines_to_file`: 2 asserts, logic

#### `unit/test_policy.py`

- `test_normalize_role`: no ids, weak name
- `test_standard_returns_all_items_with_exact_values`: 7 asserts
- `test_every_role_matches_requirements_table`: 2 asserts, no ids
- `test_contractor_not_entitled_to_desk_hardware`: logic
- `test_role_lookup_is_case_insensitive`: 2 asserts, no ids
- `test_result_is_json_serialisable_with_null`: 2 asserts
- `test_unknown_role_returns_error_with_raw_input`: no ids

#### `unit/test_policy_data.py`

- `test_real_policy_file_loads_complete`: 3 asserts
- `test_limits_for_spot_checks`: 2 asserts, no ids
- `test_completeness_error_names_what_is_missing`: no ids

#### `unit/test_prompts.py`

- `test_every_escalation_code_and_trigger_is_present`: 2 asserts, no ids
- `test_every_constraint_is_present`: no ids
- `test_names_the_tools_the_method_depends_on`: 2 asserts, logic
- `test_includes_decision_table_order_and_reason_format`: 2 asserts
- `test_includes_the_two_non_triggers`: 2 asserts
- `test_includes_reply_rules`: logic
- `test_has_no_per_request_content`: 5 asserts, logic

#### `unit/test_reviews.py`

- `test_parse_reason_valid`: no ids
- `test_parse_reason_invalid`: no ids
- `test_first_flag_creates_rev_0001_with_full_ticket`: 2 asserts
- `test_second_different_flag_gets_next_id`: 2 asserts
- `test_duplicate_returns_existing_ticket`: 2 asserts, no ids
- `test_same_employee_different_request_is_new_ticket`: 2 asserts
- `test_unknown_or_missing_employee_is_accepted`: 2 asserts, no ids
- `test_invalid_reason_rejected_and_nothing_written`: 3 asserts, no ids
- `test_empty_request_rejected_and_nothing_written`: 2 asserts, no ids
- `test_corrupt_queue_is_reported_and_left_unchanged`: 3 asserts, no ids

#### `unit/test_tools.py`

- `test_thought_is_the_first_property_and_required`: 3 asserts, logic
- `test_original_properties_and_required_are_kept_in_order`: 3 asserts
- `test_every_tool_is_strict_with_no_additional_properties`: 2 asserts, logic
- `test_input_list_is_not_mutated`: 2 asserts
- `test_final_answer_tool_is_strict_and_named`: 5 asserts
- `test_final_answer_lists_every_property_as_required`: 2 asserts
- `test_final_answer_property_schemas`: 6 asserts, logic
- `test_parse_final_answer_rejects_malformed_input`: no ids

#### `unit/test_trace_format.py`

- `test_approve_trace`: weak name
- `test_guard_line_appears_after_the_step_it_followed`: 2 asserts
- `test_fallback_is_labelled`: 2 asserts
- `test_missing_thought_and_reply_are_shown_as_none`: 3 asserts

#### `unit/test_tracing.py`

- `test_child_links_to_parent`: 3 asserts
- `test_ids_are_hex_of_the_right_length`: 2 asserts, logic
- `test_times_are_utc_iso_with_duration`: 4 asserts, logic
- `test_attributes_round_trip`: 2 asserts
- `test_second_run_appends_not_overwrites`: 2 asserts
