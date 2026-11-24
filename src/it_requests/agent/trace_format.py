"""The printed Thought, Action, Observation trace (REQUIREMENTS §12.2, deliverable 6).

Pure: AgentResult in, text out. Unlike logs, the full observation is shown here, because
this text is printed for the graded trace, not written to a log file.
"""

import json
from collections import defaultdict

from it_requests.agent.guards import FinalAnswer
from it_requests.agent.loop import AgentResult, GuardEvent


def _decision(final: FinalAnswer) -> str:
    if final.decision == "escalate":
        return f"ESCALATE ({final.reason_code}, review {final.review_id})"
    return f"{final.decision.upper()} (rule {final.rule_id})"


def format_trace(result: AgentResult) -> str:
    """One Thought/Action/Observation block per step, GUARD lines in place, then the answer."""
    guards: dict[int, list[GuardEvent]] = defaultdict(list)
    for event in result.guard_events:
        guards[event.after_step].append(event)

    lines: list[str] = []

    def add_guards(after_step: int) -> None:
        for event in guards[after_step]:
            lines.append(f"GUARD: blocked {event.tool} ({', '.join(event.codes)})")

    add_guards(0)
    for number, step in enumerate(result.steps, start=1):
        lines.append(f"Thought: {step.thought or '(none)'}")
        lines.append(f"Action: {step.tool}({json.dumps(step.arguments)})")
        lines.append(f"Observation: {json.dumps(step.observation)}")
        add_guards(number)

    if result.final_thought:
        lines.append(f"Thought: {result.final_thought}")
    label = "Final Answer (fallback)" if result.fallback else "Final Answer"
    lines.append(f"{label}: {_decision(result.final)}")
    lines.append(f"Reply: {result.final.draft_reply or '(none)'}")
    return "\n".join(lines)
