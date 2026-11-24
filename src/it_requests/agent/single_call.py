"""Brief step 5a: the agent connects to the MCP server and makes ONE tool call.

Claude sees the server's tools and a hardcoded request, picks one tool call, and the agent
runs it on the server. No second model call: answering in prose and the ReAct loop are M6.
"""

import logging
import uuid
from typing import Any

from opentelemetry.trace import Tracer

from it_requests.agent.llm import LLMClient, usage_numbers
from it_requests.agent.mcp_bridge import (
    MCPToolClient,
    call_tool,
    span_safe_arguments,
    to_claude_tools,
    traceparent,
)
from it_requests.agent.prompts import PROMPT_VERSION, SYSTEM_PROMPT_SINGLE_CALL
from it_requests.observability import log_event, request_id_var


async def run_single_tool_call(
    request_text: str,
    *,
    request_id: str,
    llm: LLMClient,
    mcp_client: MCPToolClient,
    tracer: Tracer,
    logger: logging.Logger,
) -> dict[str, Any]:
    """Let Claude choose one tool call for `request_text`, run it, and return what happened.

    Returns request_id, run_id, tool, arguments, result_status, result and cost_usd; or
    {"error": "NO_TOOL_CALL", ...} when the model answered without calling a tool.
    """
    run_id = uuid.uuid4().hex  # each run is distinct, even for the same request_id
    # Every log line in this run carries request_id (ContextVar, safe across async tasks).
    token = request_id_var.set(request_id)
    try:
        with tracer.start_as_current_span("agent.run") as root:
            root.set_attributes(
                {
                    "request_id": request_id,
                    "run_id": run_id,
                    "prompt_version": PROMPT_VERSION,
                    "model": llm.model,
                }
            )
            log_event(
                logger, "run.start", run_id=run_id, model=llm.model, prompt_version=PROMPT_VERSION
            )
            # DEBUG only: the request is the employee's own words (PLAN §5).
            log_event(logger, "run.request", level=logging.DEBUG, request_text=request_text)

            tools = to_claude_tools(await mcp_client.list_tools())
            response = llm.call(
                request_id=request_id,
                run_id=run_id,
                stage="single_call",
                system=SYSTEM_PROMPT_SINGLE_CALL,
                messages=[{"role": "user", "content": request_text}],
                tools=tools,
            )
            numbers = usage_numbers(llm.model, response.usage)
            run_info = {"request_id": request_id, "run_id": run_id, "cost_usd": numbers["cost_usd"]}

            tool_use = next((b for b in response.content if b.type == "tool_use"), None)
            if tool_use is None:
                # The model answered in prose. Nothing is sent to the server.
                outcome: dict[str, Any] = {
                    "error": "NO_TOOL_CALL",
                    **run_info,
                    "stop_reason": response.stop_reason,
                }
                tool_name, result_status = None, "NO_TOOL_CALL"
            else:
                tool_name, arguments = tool_use.name, dict(tool_use.input)
                with tracer.start_as_current_span(f"tool.{tool_name}") as span:
                    context = span.get_span_context()
                    span.set_attributes(
                        {"tool": tool_name, "arguments": span_safe_arguments(arguments)}
                    )
                    result = await call_tool(
                        mcp_client,
                        tool_name,
                        arguments,
                        request_id=request_id,
                        # The tool span's own ID, so a server span joins under this call.
                        traceparent=traceparent(context.trace_id, context.span_id),
                    )
                    result_status = str(result.get("error", "ok"))
                    span.set_attribute("result_status", result_status)
                    if isinstance(result.get("data_version"), str):
                        span.set_attribute("data_version", result["data_version"])
                    # Never the full result: get_employee_info returns the employee's name.
                outcome = {
                    **run_info,
                    "tool": tool_name,
                    "arguments": arguments,
                    "result_status": result_status,
                    "result": result,
                }

            root.set_attributes(
                {
                    "total_cost_usd": numbers["cost_usd"],
                    "total_tokens": numbers["input_tokens"] + numbers["output_tokens"],
                }
            )
            log_event(
                logger,
                "run.end",
                run_id=run_id,
                tool=tool_name,
                result_status=result_status,
                cost_usd=numbers["cost_usd"],
                input_tokens=numbers["input_tokens"],
                output_tokens=numbers["output_tokens"],
            )
            return outcome
    finally:
        request_id_var.reset(token)
