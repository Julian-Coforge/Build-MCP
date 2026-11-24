"""Tests for flag_for_human_review and parse_reason (REQUIREMENTS §2.3, §5, §7.4, §12.1)."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from it_requests.reviews import ESCALATION_CODES, flag_for_human_review, parse_reason

NOW = datetime(2026, 11, 22, 9, 0, tzinfo=UTC)
REASON = "E6: Employee reports company phone stolen."
REQUEST = "My work phone was stolen yesterday, I need a new one."


@pytest.fixture
def queue_path(tmp_path: Path) -> Path:
    # Inside a sub-folder that doesn't exist yet, so the first write must create it.
    return tmp_path / "data" / "review_queue.json"


def _flag(queue_path: Path, employee_id: str, request: str, reason: str) -> dict[str, Any]:
    return flag_for_human_review(employee_id, request, reason, queue_path=queue_path, now=NOW)


def _tickets(queue_path: Path) -> list[dict[str, Any]]:
    data: dict[str, Any] = json.loads(queue_path.read_text(encoding="utf-8"))
    tickets: list[dict[str, Any]] = data["tickets"]
    return tickets


# --- Escalation codes -------------------------------------------------------------------


def test_escalation_codes_are_e1_to_e11() -> None:
    assert ESCALATION_CODES == tuple(f"E{n}" for n in range(1, 12))


# --- parse_reason --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        ("E6: Phone stolen", ("E6", "Phone stolen")),
        ("e9:needs HR", ("E9", "needs HR")),
        ("  E1: Employee ID not found  ", ("E1", "Employee ID not found")),
        # Full match: E11 must not be read as E1 followed by "1: ...".
        ("E11: x", ("E11", "x")),
        ("E1: x", ("E1", "x")),
        ("E10: on behalf of a colleague", ("E10", "on behalf of a colleague")),
    ],
)
def test_parse_reason_valid(reason: str, expected: tuple[str, str]) -> None:
    assert parse_reason(reason) == expected


INVALID_REASONS = [
    "E12: x",
    "E0: x",
    "E6 phone stolen",  # no colon
    "E6:",  # no explanation
    "E6:   ",  # whitespace-only explanation
    "phone stolen",
    "",
    "E1E2: x",
]


@pytest.mark.parametrize("reason", INVALID_REASONS)
def test_parse_reason_invalid(reason: str) -> None:
    assert parse_reason(reason) is None


# --- flag_for_human_review: success ---------------------------------------------------------


def test_first_flag_creates_rev_0001_with_full_ticket(queue_path: Path) -> None:
    result = _flag(queue_path, "E1003", REQUEST, REASON)

    assert result == {
        "review_id": "REV-0001",
        "status": "pending",
        "reason_code": "E6",
        "duplicate": False,
    }
    assert _tickets(queue_path) == [
        {
            "review_id": "REV-0001",
            "employee_id": "E1003",
            "request": REQUEST,
            "reason_code": "E6",
            "reason": "Employee reports company phone stolen.",
            "status": "pending",
            "created_at": "2026-11-22T09:00:00+00:00",
        }
    ]


def test_second_different_flag_gets_next_id(queue_path: Path) -> None:
    _flag(queue_path, "E1003", REQUEST, REASON)

    result = _flag(queue_path, "E1001", "I need a standing desk.", "E3: Not a catalog item.")

    assert result["review_id"] == "REV-0002"
    assert len(_tickets(queue_path)) == 2


@pytest.mark.parametrize(
    ("employee_id", "request"),
    [
        ("E1003", REQUEST),
        (" e1003 ", REQUEST),  # case and whitespace in the ID don't make a new ticket
        ("E1003", f"  {REQUEST}\n"),  # nor does whitespace around the request
    ],
)
def test_duplicate_returns_existing_ticket(
    queue_path: Path, employee_id: str, request: str
) -> None:
    first = _flag(queue_path, "E1003", REQUEST, REASON)

    again = _flag(queue_path, employee_id, request, "E11: different reason, same request")

    assert again == {
        "review_id": first["review_id"],
        "status": "pending",
        "reason_code": "E6",  # the existing ticket's code, not the new one
        "duplicate": True,
    }
    assert len(_tickets(queue_path)) == 1


def test_same_employee_different_request_is_new_ticket(queue_path: Path) -> None:
    _flag(queue_path, "E1003", REQUEST, REASON)

    result = _flag(queue_path, "E1003", "My laptop screen is cracked.", "E7: Damage claim.")

    assert result["review_id"] == "REV-0002"
    assert result["duplicate"] is False


@pytest.mark.parametrize("employee_id", ["E9999", ""])
def test_unknown_or_missing_employee_is_accepted(queue_path: Path, employee_id: str) -> None:
    # E1 escalations (employee not found or missing) must still be recorded (§2.3).
    result = _flag(queue_path, employee_id, REQUEST, "E1: Employee ID not found.")

    assert result["review_id"] == "REV-0001"
    assert _tickets(queue_path)[0]["employee_id"] == employee_id


def test_employee_id_stored_stripped(queue_path: Path) -> None:
    _flag(queue_path, "  E1003 ", REQUEST, REASON)

    assert _tickets(queue_path)[0]["employee_id"] == "E1003"


def test_created_at_is_injected_now(queue_path: Path) -> None:
    _flag(queue_path, "E1003", REQUEST, REASON)

    assert _tickets(queue_path)[0]["created_at"] == NOW.isoformat()


def test_atomic_write_leaves_no_temp_files(queue_path: Path) -> None:
    _flag(queue_path, "E1003", REQUEST, REASON)
    _flag(queue_path, "E1001", "Another request.", "E4: Item unclear.")

    assert [p.name for p in queue_path.parent.iterdir()] == ["review_queue.json"]


# --- flag_for_human_review: validation before any write ----------------------------------------


@pytest.mark.parametrize("reason", INVALID_REASONS)
def test_invalid_reason_rejected_and_nothing_written(queue_path: Path, reason: str) -> None:
    result = _flag(queue_path, "E1003", REQUEST, reason)

    assert result["error"] == "INVALID_REASON"
    assert "detail" in result
    assert not queue_path.exists()


@pytest.mark.parametrize("request", ["", "   ", "\n\t"])
def test_empty_request_rejected_and_nothing_written(queue_path: Path, request: str) -> None:
    result = _flag(queue_path, "E1003", request, REASON)

    assert result == {"error": "INVALID_REQUEST", "detail": "request must not be empty"}
    assert not queue_path.exists()


def test_request_validated_before_reason(queue_path: Path) -> None:
    assert _flag(queue_path, "E1003", "", "not a reason")["error"] == "INVALID_REQUEST"


# --- flag_for_human_review: corrupt queue is never overwritten -----------------------------------


@pytest.mark.parametrize("content", ["not json", '{"no_tickets_key": []}', '{"tickets": "x"}'])
def test_corrupt_queue_is_reported_and_left_unchanged(queue_path: Path, content: str) -> None:
    queue_path.parent.mkdir(parents=True)
    queue_path.write_text(content, encoding="utf-8")

    result = _flag(queue_path, "E1003", REQUEST, REASON)

    assert result["error"] == "QUEUE_UNAVAILABLE"
    assert "detail" in result
    # Existing tickets are never destroyed to "fix" the file: a human must look at it.
    assert queue_path.read_text(encoding="utf-8") == content
