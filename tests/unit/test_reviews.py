"""Tests for flag_for_human_review and parse_reason (REQUIREMENTS §2.3, §5, §7.4, §12.1)."""

import json
import os
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


def _write_queue(queue_path: Path, content: str) -> None:
    queue_path.parent.mkdir(parents=True)
    queue_path.write_text(content, encoding="utf-8")


# ---------- escalation codes ----------


def test_escalation_codes_are_e1_to_e11() -> None:
    assert ESCALATION_CODES == tuple(f"E{n}" for n in range(1, 12))


# ---------- parse_reason ----------


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
    ids=["e6", "lower-no-space", "padded", "e11-not-e1", "e1", "e10"],
)
def test_parse_reason_valid_form_returns_code_and_explanation(
    reason: str, expected: tuple[str, str]
) -> None:
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
INVALID_REASON_IDS = [
    "e12",
    "e0",
    "no-colon",
    "no-explanation",
    "blank-explanation",
    "no-code",
    "empty",
    "two-codes",
]


@pytest.mark.parametrize("reason", INVALID_REASONS, ids=INVALID_REASON_IDS)
def test_parse_reason_invalid_form_returns_none(reason: str) -> None:
    assert parse_reason(reason) is None


# ---------- flag_for_human_review: success ----------


def test_flag_first_call_returns_rev_0001_pending(queue_path: Path) -> None:
    result = _flag(queue_path, "E1003", REQUEST, REASON)

    assert result == {
        "review_id": "REV-0001",
        "status": "pending",
        "reason_code": "E6",
        "duplicate": False,
    }


def test_flag_first_call_writes_the_whole_ticket(queue_path: Path) -> None:
    _flag(queue_path, "E1003", REQUEST, REASON)

    tickets = _tickets(queue_path)

    assert tickets == [
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


def test_flag_second_different_request_gets_next_id(queue_path: Path) -> None:
    _flag(queue_path, "E1003", REQUEST, REASON)

    result = _flag(queue_path, "E1001", "I need a standing desk.", "E3: Not a catalog item.")

    assert result["review_id"] == "REV-0002"


def test_flag_second_different_request_appends_a_ticket(queue_path: Path) -> None:
    _flag(queue_path, "E1003", REQUEST, REASON)

    _flag(queue_path, "E1001", "I need a standing desk.", "E3: Not a catalog item.")

    assert len(_tickets(queue_path)) == 2


SAME_REQUEST_VARIANTS = [
    ("E1003", REQUEST),
    (" e1003 ", REQUEST),  # case and whitespace in the ID don't make a new ticket
    ("E1003", f"  {REQUEST}\n"),  # nor does whitespace around the request
]
SAME_REQUEST_IDS = ["exact", "messy-id", "padded-request"]


@pytest.mark.parametrize(
    ("employee_id", "request_text"), SAME_REQUEST_VARIANTS, ids=SAME_REQUEST_IDS
)
def test_flag_duplicate_returns_existing_ticket(
    queue_path: Path, employee_id: str, request_text: str
) -> None:
    _flag(queue_path, "E1003", REQUEST, REASON)

    again = _flag(queue_path, employee_id, request_text, "E11: different reason, same request")

    assert again == {
        "review_id": "REV-0001",
        "status": "pending",
        "reason_code": "E6",  # the existing ticket's code, not the new one
        "duplicate": True,
    }


@pytest.mark.parametrize(
    ("employee_id", "request_text"), SAME_REQUEST_VARIANTS, ids=SAME_REQUEST_IDS
)
def test_flag_duplicate_writes_no_new_ticket(
    queue_path: Path, employee_id: str, request_text: str
) -> None:
    _flag(queue_path, "E1003", REQUEST, REASON)

    _flag(queue_path, employee_id, request_text, "E11: different reason, same request")

    assert len(_tickets(queue_path)) == 1


def test_flag_same_employee_different_request_is_a_new_ticket(queue_path: Path) -> None:
    _flag(queue_path, "E1003", REQUEST, REASON)

    result = _flag(queue_path, "E1003", "My laptop screen is cracked.", "E7: Damage claim.")

    assert (result["review_id"], result["duplicate"]) == ("REV-0002", False)


@pytest.mark.parametrize("employee_id", ["E9999", ""], ids=["unknown", "missing"])
def test_flag_unknown_or_missing_employee_creates_ticket(
    queue_path: Path, employee_id: str
) -> None:
    # E1 escalations (employee not found or missing) must still be recorded (§2.3).
    result = _flag(queue_path, employee_id, REQUEST, "E1: Employee ID not found.")

    assert result["review_id"] == "REV-0001"


@pytest.mark.parametrize("employee_id", ["E9999", ""], ids=["unknown", "missing"])
def test_flag_unknown_or_missing_employee_stores_id_as_given(
    queue_path: Path, employee_id: str
) -> None:
    _flag(queue_path, employee_id, REQUEST, "E1: Employee ID not found.")

    assert _tickets(queue_path)[0]["employee_id"] == employee_id


def test_flag_stores_employee_id_stripped(queue_path: Path) -> None:
    _flag(queue_path, "  E1003 ", REQUEST, REASON)

    assert _tickets(queue_path)[0]["employee_id"] == "E1003"


def test_flag_created_at_is_the_injected_now(queue_path: Path) -> None:
    _flag(queue_path, "E1003", REQUEST, REASON)

    assert _tickets(queue_path)[0]["created_at"] == NOW.isoformat()


def test_flag_atomic_write_leaves_no_temp_files(queue_path: Path) -> None:
    _flag(queue_path, "E1003", REQUEST, REASON)
    _flag(queue_path, "E1001", "Another request.", "E4: Item unclear.")

    assert [p.name for p in queue_path.parent.iterdir()] == ["review_queue.json"]


# ---------- flag_for_human_review: validation before any write ----------


@pytest.mark.parametrize("reason", INVALID_REASONS, ids=INVALID_REASON_IDS)
def test_flag_invalid_reason_returns_invalid_reason(queue_path: Path, reason: str) -> None:
    result = _flag(queue_path, "E1003", REQUEST, reason)

    assert result == {
        "error": "INVALID_REASON",
        "detail": "reason must start with a code E1-E11 followed by ': ' and an explanation",
    }


@pytest.mark.parametrize("reason", INVALID_REASONS, ids=INVALID_REASON_IDS)
def test_flag_invalid_reason_writes_nothing(queue_path: Path, reason: str) -> None:
    _flag(queue_path, "E1003", REQUEST, reason)

    assert not queue_path.exists()


# "request_text", not "request": pytest reserves the name "request" for its own fixture.
EMPTY_REQUESTS = ["", "   ", "\n\t"]
EMPTY_REQUEST_IDS = ["empty", "spaces", "tab-newline"]


@pytest.mark.parametrize("request_text", EMPTY_REQUESTS, ids=EMPTY_REQUEST_IDS)
def test_flag_empty_request_returns_invalid_request(queue_path: Path, request_text: str) -> None:
    result = _flag(queue_path, "E1003", request_text, REASON)

    assert result == {"error": "INVALID_REQUEST", "detail": "request must not be empty"}


@pytest.mark.parametrize("request_text", EMPTY_REQUESTS, ids=EMPTY_REQUEST_IDS)
def test_flag_empty_request_writes_nothing(queue_path: Path, request_text: str) -> None:
    _flag(queue_path, "E1003", request_text, REASON)

    assert not queue_path.exists()


def test_flag_request_is_validated_before_reason(queue_path: Path) -> None:
    assert _flag(queue_path, "E1003", "", "not a reason")["error"] == "INVALID_REQUEST"


# ---------- flag_for_human_review: corrupt queue is never overwritten ----------


CORRUPT_QUEUES = [
    "not json",
    '{"no_tickets_key": []}',
    '{"tickets": "x"}',
    '{"tickets": [{}]}',  # a ticket missing every field
]
CORRUPT_QUEUE_IDS = ["not-json", "no-tickets-key", "tickets-not-list", "ticket-missing-fields"]


@pytest.mark.parametrize("content", CORRUPT_QUEUES, ids=CORRUPT_QUEUE_IDS)
def test_flag_corrupt_queue_returns_queue_unavailable(queue_path: Path, content: str) -> None:
    _write_queue(queue_path, content)

    result = _flag(queue_path, "E1003", REQUEST, REASON)

    assert result["error"] == "QUEUE_UNAVAILABLE"


@pytest.mark.parametrize("content", CORRUPT_QUEUES, ids=CORRUPT_QUEUE_IDS)
def test_flag_corrupt_queue_is_left_unchanged(queue_path: Path, content: str) -> None:
    # Existing tickets are never destroyed to "fix" the file: a human must look at it.
    _write_queue(queue_path, content)

    _flag(queue_path, "E1003", REQUEST, REASON)

    assert queue_path.read_text(encoding="utf-8") == content


# ---------- flag_for_human_review: write failure ----------


def test_flag_unwritable_queue_returns_queue_unavailable(tmp_path: Path) -> None:
    # The queue's parent is a file, so the folder can't be created and the write fails.
    blocker = tmp_path / "data"
    blocker.write_text("", encoding="utf-8")

    result = _flag(blocker / "review_queue.json", "E1003", REQUEST, REASON)

    assert result["error"] == "QUEUE_UNAVAILABLE"


def test_flag_failed_replace_removes_the_temp_file(
    queue_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A failing os.replace is the only way to fail after the temp file exists.
    def _replace_fails(src: str, dst: Path) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", _replace_fails)

    _flag(queue_path, "E1003", REQUEST, REASON)

    assert list(queue_path.parent.iterdir()) == []
