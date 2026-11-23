"""get_employee_info as a plain, pure function (REQUIREMENTS §7.1). MCP wrapping comes later.

Pure means: no I/O and no system clock. The directory and `as_of` are passed in, so the
same inputs always give the same output and tests never depend on today's date.
"""

import re
from datetime import date
from typing import Any

from it_requests import config
from it_requests.data import EmployeeDirectory

# [0-9], not \d: \d also matches non-ASCII digits (e.g. Arabic-Indic), which are not valid
# IDs. fullmatch, not match with $: $ would accept a trailing newline.
_EMPLOYEE_ID = re.compile(r"E[0-9]{4}")


def normalize_employee_id(raw: str) -> str | None:
    """Trim and upper-case (REQUIREMENTS §2.1); return None if the result is not E####."""
    candidate = raw.strip().upper()
    return candidate if _EMPLOYEE_ID.fullmatch(candidate) else None


def tenure_months(hire_date: date, as_of: date) -> int:
    """Whole calendar months completed. A month counts once as_of reaches the same day-of-month."""
    months = (as_of.year - hire_date.year) * 12 + (as_of.month - hire_date.month)
    if as_of.day < hire_date.day:
        months -= 1
    return months


def get_employee_info(
    employee_id: str,
    *,
    directory: EmployeeDirectory,
    as_of: date = config.REFERENCE_DATE,
) -> dict[str, Any]:
    """Look up an employee. Errors are returned as dicts, never raised.

    Why errors as data (REQUIREMENTS §7): the agent receives tool output as text. A returned
    {"error": ...} lets the model reason about it ("ask for a valid ID", "escalate E1"),
    whereas a raised exception would surface as an opaque tool failure.
    """
    normalised = normalize_employee_id(employee_id)
    if normalised is None:
        # Echo the raw input, so the model sees exactly what it sent.
        return {"error": "INVALID_EMPLOYEE_ID", "employee_id": employee_id}

    employee = directory.get(normalised)
    if employee is None:
        return {"error": "EMPLOYEE_NOT_FOUND", "employee_id": normalised}

    # Sorted by (issued_date, asset_id) so the output is identical on every call, whatever
    # the order in the data file. Deterministic output keeps tests, logs and traces comparable.
    equipment = sorted(employee.equipment, key=lambda e: (e.issued_date, e.asset_id))

    return {
        "employee_id": employee.employee_id,
        "name": employee.name,
        "role": employee.role,
        "hire_date": employee.hire_date.isoformat(),
        "tenure_months": tenure_months(employee.hire_date, as_of),
        "status": employee.status,
        "equipment": [
            {"item": e.item, "asset_id": e.asset_id, "issued_date": e.issued_date.isoformat()}
            for e in equipment
        ],
        # Lineage (PLAN §3): every response names the data it was built from.
        "data_version": directory.data_version,
    }
