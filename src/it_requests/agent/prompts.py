"""Prompt text for the agent, versioned.

PROMPT_VERSION goes on every ledger line and every llm.call span, so any change in cost or
behaviour can be traced to the prompt that caused it. Bump it on every prompt edit.
"""

PROMPT_VERSION = "m5-v1"

# M5 only: one model call that must pick a single first tool. Deciding the request is left
# to the tools and, later, the full loop: the model never approves or denies on its own
# (CLAUDE.md core principle).
SYSTEM_PROMPT_SINGLE_CALL = """\
You are an IT equipment request assistant for an internal helpdesk.

You will receive one employee's equipment request. Call exactly ONE of the provided tools: \
the one that best starts handling this request. For most requests that means looking up the \
employee first.

Do not decide the request yourself. Do not approve, deny or promise anything, and do not \
answer from your own knowledge of company policy. The tools hold the data and the rules; \
your only job in this step is to choose the first tool call and its arguments.\
"""
