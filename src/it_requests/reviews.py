"""flag_for_human_review: append a review ticket to the queue file (REQUIREMENTS §2.3, §7.4).

The only side-effecting tool. Its dependencies (the queue path and the current time) are
injected, so it is fully unit-testable with tmp_path and a fixed clock.
"""

import json
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

# One source of truth for the escalation codes (REQUIREMENTS §5).
ESCALATION_CODES: tuple[str, ...] = tuple(f"E{n}" for n in range(1, 12))

# fullmatch with an explicit 1-11 alternation: "E11: x" must parse as E11, never as E1
# followed by "1: x", and "E12" / "E0" must fail. [0-9]-style literals, not \d, so
# non-ASCII digits can't sneak in.
_REASON = re.compile(r"\s*(E(?:1[01]|[1-9])):(.*)", re.IGNORECASE | re.DOTALL)

_TICKET_STR_FIELDS = ("review_id", "employee_id", "request", "reason_code")


def parse_reason(reason: str) -> tuple[str, str] | None:
    """Split "E6: explanation" into ("E6", "explanation"). None if the form is invalid."""
    match = _REASON.fullmatch(reason)
    if match is None:
        return None
    explanation = match.group(2).strip()
    if not explanation:
        return None
    return match.group(1).upper(), explanation


def _error(code: str, detail: str) -> dict[str, Any]:
    return {"error": code, "detail": detail}


def _read_queue(queue_path: Path) -> list[dict[str, Any]] | str:
    """Return the tickets, or a short reason the queue can't be used."""
    if not queue_path.exists():
        return []  # no file yet = empty queue; it is created on the first write
    try:
        data = json.loads(queue_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as err:  # ValueError covers JSONDecodeError and bad UTF-8
        return f"cannot read queue file: {type(err).__name__}"
    tickets = data.get("tickets") if isinstance(data, dict) else None
    # Check every ticket's shape now, so the duplicate check below can't crash on a
    # hand-edited file and raise to the agent instead of returning an error.
    if not isinstance(tickets, list) or not all(
        isinstance(t, dict) and all(isinstance(t.get(k), str) for k in _TICKET_STR_FIELDS)
        for t in tickets
    ):
        return 'queue file is not in the expected {"tickets": [...]} format'
    return tickets


def _write_atomically(queue_path: Path, tickets: list[dict[str, Any]]) -> None:
    """Write via a temp file in the same folder, then os.replace() it over the queue.

    os.replace is atomic, so a crash mid-write leaves either the old file or the new one,
    never half a JSON file. The temp file must be in the same folder (same filesystem),
    or the replace is not guaranteed to be atomic.
    """
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=queue_path.parent, prefix=".review_queue.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp:
            json.dump({"tickets": tickets}, tmp, indent=2)
            tmp.flush()
            os.fsync(tmp.fileno())  # data on disk before the rename makes it visible
        os.replace(tmp_name, queue_path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)  # never leave temp files behind
        raise


def flag_for_human_review(
    employee_id: str,
    request: str,
    reason: str,
    *,
    queue_path: Path,
    now: datetime,
) -> dict[str, Any]:
    """Create a pending review ticket, or return the existing one for the same request.

    `now` is passed in instead of reading the clock: tests then get a fixed created_at and
    stay deterministic, and the server passes in the real time.
    Errors are returned as data, never raised (REQUIREMENTS §7).
    """
    # Validate everything BEFORE reading or writing: a rejected call must leave no trace
    # (§7.4), so a bad argument can never create a half-valid ticket.
    clean_request = request.strip()
    if not clean_request:
        return _error("INVALID_REQUEST", "request must not be empty")
    parsed = parse_reason(reason)
    if parsed is None:
        return _error(
            "INVALID_REASON",
            "reason must start with a code E1-E11 followed by ': ' and an explanation",
        )
    reason_code, explanation = parsed

    # Not validated and not required to exist: E1 (employee not found or missing) must
    # still be recordable (§2.3). Stripped only, so stray spaces don't break duplicates.
    clean_employee_id = employee_id.strip()

    tickets = _read_queue(queue_path)
    if isinstance(tickets, str):
        # Report, never overwrite: "fixing" a corrupt file by rewriting it would destroy
        # every existing ticket. A human must look at it.
        return _error("QUEUE_UNAVAILABLE", tickets)

    # Idempotency key = employee + request (§7.4), compared case- and space-insensitively
    # on the ID. A retried call (e.g. after a timeout) returns the same ticket, which also
    # protects C7: at most one flag per request.
    for ticket in tickets:
        if (
            ticket["employee_id"].strip().upper() == clean_employee_id.upper()
            and ticket["request"].strip() == clean_request
        ):
            return {
                "review_id": ticket["review_id"],
                "status": "pending",
                "reason_code": ticket["reason_code"],
                "duplicate": True,
            }

    review_id = f"REV-{len(tickets) + 1:04d}"  # sequential (§2.3); tickets are never deleted
    tickets.append(
        {
            "review_id": review_id,
            "employee_id": clean_employee_id,
            "request": clean_request,
            "reason_code": reason_code,
            "reason": explanation,
            "status": "pending",
            "created_at": now.isoformat(),
        }
    )
    try:
        _write_atomically(queue_path, tickets)
    except OSError as err:
        return _error("QUEUE_UNAVAILABLE", f"cannot write queue file: {type(err).__name__}")
    return {
        "review_id": review_id,
        "status": "pending",
        "reason_code": reason_code,
        "duplicate": False,
    }
