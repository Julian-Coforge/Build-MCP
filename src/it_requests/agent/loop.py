"""The ReAct loop: the model picks its own tools until it calls final_answer (PLAN §2).

The model only interprets the request, picks tool calls and drafts the reply. Every policy
decision comes from the server's tested Python, and the guards (C1-C3, C6, C7) are checked
here in code before any tool call or final answer is accepted. When the run can't end
cleanly, the CODE escalates as E11 (the fallback): it never guesses a decision.
"""

import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from anthropic.types import Message, ToolUseBlock
from opentelemetry.trace import Tracer

from it_requests.agent.guards import (
    FLAG_TOOL,
    FinalAnswer,
    Observation,
    check_final_answer,
    check_tool_call,
    violation_message,
)
from it_requests.agent.llm import LLMClient, system_blocks, usage_numbers
from it_requests.agent.mcp_bridge import (
    MCPToolClient,
    call_tool,
    span_safe_arguments,
    to_claude_tools,
    traceparent,
)
from it_requests.agent.prompts import PROMPT_VERSION, SYSTEM_PROMPT_REACT
from it_requests.agent.tools import (
    FINAL_ANSWER_TOOL,
    parse_final_answer,
    strip_thought,
    with_thought,
)
from it_requests.observability import log_event, request_id_var

FINAL_ANSWER = FINAL_ANSWER_TOOL["name"]
NUDGE = "Call final_answer to finish."
_EMPLOYEE_ID_IN_TEXT = re.compile(r"\b[Ee][0-9]{4}\b")
# One correction is allowed; a second rejected final answer triggers the fallback.
_MAX_REJECTED_FINAL_ANSWERS = 2


@dataclass(frozen=True)
class AgentRequest:
    request_id: str
    request_text: str


@dataclass(frozen=True)
class Step:
    """One executed tool call: the model's thought, the call (without thought), the result."""

    thought: str
    tool: str
    arguments: dict[str, Any]
    observation: dict[str, Any]


@dataclass(frozen=True)
class GuardEvent:
    """A blocked tool call or rejected final answer. after_step places it in the trace."""

    after_step: int
    tool: str
    codes: tuple[str, ...]


@dataclass(frozen=True, kw_only=True)
class AgentResult:
    request_id: str
    run_id: str
    steps: tuple[Step, ...]
    final: FinalAnswer
    guard_events: tuple[GuardEvent, ...]
    fallback: bool
    cost_usd: float
    input_tokens: int
    output_tokens: int
    final_thought: str = ""


@dataclass
class _Ending:
    """How a turn ended the run: an accepted answer, or a reason to fall back."""

    answer: FinalAnswer | None = None
    thought: str = ""
    fallback_reason: str | None = None


def _tool_result(tool_use_id: str, content: str, *, is_error: bool = False) -> dict[str, Any]:
    block: dict[str, Any] = {"type": "tool_result", "tool_use_id": tool_use_id, "content": content}
    # is_error only for blocks the loop refused. A tool's own {"error": ...} is normal data
    # the model reasons about (CLAUDE.md rule 6), so it is sent without the flag.
    if is_error:
        block["is_error"] = True
    return block


@dataclass
class _Run:
    """The mutable state of one run. Kept out of run_agent so each step is a short method."""

    request: AgentRequest
    llm: LLMClient
    mcp_client: MCPToolClient
    tracer: Tracer
    logger: logging.Logger
    run_id: str
    steps: list[Step] = field(default_factory=list)
    guard_events: list[GuardEvent] = field(default_factory=list)
    rejected_final_answers: int = 0
    step_limit_hit: bool = False
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0

    # --- State helpers ---

    def observations(self) -> list[Observation]:
        # Blocked calls never ran, so they are not observations (and don't count for C6).
        return [Observation(s.tool, s.arguments, s.observation) for s in self.steps]

    def existing_flag(self) -> dict[str, Any] | None:
        return next(
            (
                s.observation
                for s in self.steps
                if s.tool == FLAG_TOOL and isinstance(s.observation.get("review_id"), str)
            ),
            None,
        )

    def awaiting_escalation(self) -> bool:
        # C6: once the step limit has blocked a call, the next turn must escalate.
        return self.step_limit_hit and self.existing_flag() is None

    def add_usage(self, response: Message) -> None:
        numbers = usage_numbers(self.llm.model, response.usage)
        self.cost_usd += numbers["cost_usd"]
        self.input_tokens += numbers["input_tokens"]
        self.output_tokens += numbers["output_tokens"]

    def guard(self, tool: str, codes: tuple[str, ...]) -> None:
        self.guard_events.append(GuardEvent(after_step=len(self.steps), tool=tool, codes=codes))
        log_event(self.logger, "guard.blocked", logging.WARNING, tool=tool, codes=list(codes))

    # --- Tool calls ---

    async def call_server(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Run one tool on the server inside a tool.<name> span."""
        with self.tracer.start_as_current_span(f"tool.{tool}") as span:
            # No thought on the span: it can repeat the employee's name, and trace files
            # are kept on disk like logs (CLAUDE.md). It is logged at DEBUG instead.
            span.set_attributes({"tool": tool, "arguments": span_safe_arguments(arguments)})
            context = span.get_span_context()
            result = await call_tool(
                self.mcp_client,
                tool,
                arguments,
                request_id=self.request.request_id,
                # The tool span's own ID, so a server span joins under this call.
                traceparent=traceparent(context.trace_id, context.span_id),
            )
            span.set_attribute("result_status", str(result.get("error", "ok")))
            if isinstance(result.get("data_version"), str):
                span.set_attribute("data_version", result["data_version"])
            return result

    async def run_tool(self, block: ToolUseBlock) -> dict[str, Any]:
        """Guard, run and record one MCP tool call; return its tool_result block."""
        blocked = check_tool_call(block.name, self.observations())
        if blocked is not None:
            self.guard(block.name, (blocked,))
            self.step_limit_hit = self.step_limit_hit or blocked == "C6_STEP_LIMIT"
            # The server is never called: the model reads why and must change course.
            return _tool_result(block.id, violation_message([blocked]), is_error=True)

        thought, arguments = strip_thought(dict(block.input))
        step = len(self.steps) + 1
        # Thought and arguments at DEBUG: both can hold the employee's own words (PLAN §5).
        log_event(self.logger, "react.thought", logging.DEBUG, step=step, thought=thought)
        log_event(self.logger, "react.action", step=step, tool=block.name)
        log_event(self.logger, "react.arguments", logging.DEBUG, step=step, arguments=arguments)
        result = await self.call_server(block.name, arguments)
        self.steps.append(Step(thought, block.name, arguments, result))
        # Status or error code only: the full result can hold the employee's name.
        log_event(
            self.logger,
            "react.observation",
            step=step,
            tool=block.name,
            result_status=str(result.get("error", "ok")),
        )
        return _tool_result(block.id, json.dumps(result))

    def judge_final(self, block: ToolUseBlock) -> tuple[_Ending | None, dict[str, Any]]:
        """Accept final_answer (an _Ending), or reject it with an is_error tool_result."""
        thought, fields = strip_thought(dict(block.input))
        try:
            answer = parse_final_answer(fields)
        except ValueError as err:
            codes: tuple[str, ...] = ("INVALID_FINAL_ANSWER",)
            message = f"INVALID_FINAL_ANSWER: {err}."
        else:
            codes = tuple(check_final_answer(answer, self.observations()))
            if not codes:
                return _Ending(answer=answer, thought=thought), {}
            message = violation_message(codes)
        self.guard(FINAL_ANSWER, codes)
        self.rejected_final_answers += 1
        if self.rejected_final_answers >= _MAX_REJECTED_FINAL_ANSWERS:
            return _Ending(fallback_reason="final answer rejected twice"), {}
        return None, _tool_result(block.id, message, is_error=True)

    async def handle_turn(self, response: Message) -> tuple[_Ending | None, Any]:
        """Handle every tool_use block in order. Returns (ending, next user content)."""
        tool_uses = [b for b in response.content if isinstance(b, ToolUseBlock)]
        if self.awaiting_escalation() and not any(b.name == FLAG_TOOL for b in tool_uses):
            return _Ending(fallback_reason="step limit reached without escalation"), None
        if not tool_uses:
            return None, NUDGE  # end_turn without a tool: ask for the answer
        results = []
        for block in tool_uses:
            if block.name == FINAL_ANSWER:
                ending, result = self.judge_final(block)
                if ending is not None:
                    # Later blocks in this turn are dropped: the run is over.
                    return ending, None
            else:
                result = await self.run_tool(block)
            results.append(result)
        # All results in ONE user message: the API expects every tool_use answered together.
        return None, results

    # --- Fallback ---

    def employee_id(self) -> str:
        """The best known employee ID for a fallback ticket (E1 must still be recordable)."""
        for step in reversed(self.steps):
            value = step.arguments.get("employee_id")
            if isinstance(value, str) and value.strip():
                return value
        match = _EMPLOYEE_ID_IN_TEXT.search(self.request.request_text)
        return match.group(0).upper() if match else "UNKNOWN"

    async def fallback(self, why: str) -> FinalAnswer:
        """Escalate in code (E11) with a fixed, safe reply. No further model call."""
        log_event(self.logger, "agent.fallback", logging.WARNING, reason=why)
        existing = self.existing_flag()
        if existing is not None:
            # C7: never a second ticket. Keep the ticket's own code so answer and ticket agree.
            review_id, reason_code = existing["review_id"], existing.get("reason_code", "E11")
        else:
            arguments = {
                "employee_id": self.employee_id(),
                "request": self.request.request_text,
                "reason": f"E11: {why}.",
            }
            result = await self.call_server(FLAG_TOOL, arguments)
            self.steps.append(Step(f"Fallback: {why}.", FLAG_TOOL, arguments, result))
            review_id, reason_code = result.get("review_id"), "E11"
        # Fixed text: states only the review_id, promises no outcome (C3, C5).
        reply = (
            f"Your request has been sent for human review (reference {review_id}). "
            "A reviewer will follow up; we can't say in advance what the outcome will be."
            if review_id
            else "Your request could not be processed automatically. "
            "Please contact the IT helpdesk."
        )
        return FinalAnswer(
            decision="escalate",
            rule_id=None,
            review_id=review_id,
            reason_code=reason_code,
            draft_reply=reply,
        )


async def run_agent(
    request: AgentRequest,
    *,
    llm: LLMClient,
    mcp_client: MCPToolClient,
    tracer: Tracer,
    logger: logging.Logger,
    max_model_calls: int = 12,  # PLAN §4 per-run limit
) -> AgentResult:
    """Run the ReAct loop for one request and return the decision and the full trace."""
    run = _Run(request, llm, mcp_client, tracer, logger, run_id=uuid.uuid4().hex)
    token = request_id_var.set(request.request_id)  # every log line carries request_id
    try:
        with tracer.start_as_current_span("agent.run") as root:
            root.set_attributes(
                {
                    "request_id": request.request_id,
                    "run_id": run.run_id,
                    "prompt_version": PROMPT_VERSION,
                    "model": llm.model,
                }
            )
            log_event(
                logger,
                "run.start",
                run_id=run.run_id,
                model=llm.model,
                prompt_version=PROMPT_VERSION,
            )
            log_event(logger, "run.request", logging.DEBUG, request_text=request.request_text)

            tools = [
                *with_thought(to_claude_tools(await mcp_client.list_tools())),
                FINAL_ANSWER_TOOL,
            ]
            system = system_blocks(SYSTEM_PROMPT_REACT)
            # The request ID goes in the message, not the system prompt, so the system prompt
            # stays identical across runs and can be cached.
            messages: list[dict[str, Any]] = [
                {
                    "role": "user",
                    "content": f"Request ID: {request.request_id}\n\n{request.request_text}",
                }
            ]
            ending = _Ending(fallback_reason="model call limit reached")
            calls = 0
            for calls in range(1, max_model_calls + 1):  # calls is logged in run.end
                response = llm.call(
                    request_id=request.request_id,
                    run_id=run.run_id,
                    stage=f"react_step_{calls}",
                    system=system,
                    # A snapshot: the list grows after this call, and what was sent must not.
                    messages=list(messages),
                    tools=tools,
                )
                run.add_usage(response)
                turn_ending, user_content = await run.handle_turn(response)
                if turn_ending is not None:
                    ending = turn_ending
                    break
                # Unchanged: thinking blocks must go back exactly as received.
                messages.append({"role": "assistant", "content": response.content})
                messages.append({"role": "user", "content": user_content})

            fallback = ending.answer is None
            final = (
                ending.answer
                if ending.answer is not None
                else await run.fallback(ending.fallback_reason or "unknown")
            )
            _finish(root, run, final, fallback, calls)
            return AgentResult(
                request_id=request.request_id,
                run_id=run.run_id,
                steps=tuple(run.steps),
                final=final,
                guard_events=tuple(run.guard_events),
                fallback=fallback,
                cost_usd=run.cost_usd,
                input_tokens=run.input_tokens,
                output_tokens=run.output_tokens,
                final_thought=ending.thought,
            )
    finally:
        request_id_var.reset(token)


def _finish(root: Any, run: _Run, final: FinalAnswer, fallback: bool, calls: int) -> None:
    """Root span totals and the run.end line. The reply is logged at DEBUG only."""
    outcome = {"rule_id": final.rule_id, "reason_code": final.reason_code}
    # Span attributes can't be None: set only the fields that apply to this decision.
    root.set_attributes(
        {
            "decision": final.decision,
            **{k: v for k, v in outcome.items() if v is not None},
            **({"review_id": final.review_id} if final.review_id else {}),
            "fallback": fallback,
            "total_cost_usd": run.cost_usd,
            "total_tokens": run.input_tokens + run.output_tokens,
        }
    )
    log_event(run.logger, "run.reply", logging.DEBUG, draft_reply=final.draft_reply)
    log_event(
        run.logger,
        "run.end",
        run_id=run.run_id,
        decision=final.decision,
        rule_id=final.rule_id,
        reason_code=final.reason_code,
        review_id=final.review_id,
        fallback=fallback,
        model_calls=calls,
        cost_usd=run.cost_usd,
        input_tokens=run.input_tokens,
        output_tokens=run.output_tokens,
    )
