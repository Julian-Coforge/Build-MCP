"""check_request_eligibility as a plain, pure function (REQUIREMENTS §4.3, §7.3).

This is the only source of truth for eligibility. The LLM never decides policy; it reads
this function's verdict. Pure: no I/O, no clock. Data and as_of are passed in.
"""

import calendar
from datetime import date
from typing import Any

from it_requests import config
from it_requests.data import CATALOG_ITEMS, CatalogItem, EmployeeDirectory, PolicyTable
from it_requests.employees import normalize_employee_id


def add_months(d: date, months: int) -> date:
    """Add calendar months. If the target day doesn't exist, use the month's last day (§4.2).

    Example: 2024-01-31 + 1 month = 2024-02-29. Without the clamp, date() would raise.
    """
    month_index = d.month - 1 + months
    year, month = d.year + month_index // 12, month_index % 12 + 1
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(d.day, last_day))


def _normalize_item(raw: str) -> CatalogItem | None:
    candidate = raw.strip().lower()
    # Return the CATALOG_ITEMS element, not `candidate`, so the result is typed CatalogItem.
    return next((item for item in CATALOG_ITEMS if item == candidate), None)


def check_request_eligibility(
    employee_id: str,
    item: str,
    *,
    directory: EmployeeDirectory,
    policy: PolicyTable,
    as_of: date = config.REFERENCE_DATE,
) -> dict[str, Any]:
    """Decide whether one unit of a catalog item is within policy (REQUIREMENTS §4.3).

    The steps run in the §4.3 order and the first that applies wins. The order is part of
    the rules: e.g. an on-leave employee gets NEEDS_REVIEW (R-STATUS) even for an item their
    role can't have, because a human must look at any non-active employee first.
    Errors are returned as data, never raised (REQUIREMENTS §7).
    """
    # Step 0: bad format. Echo the raw input so the model sees exactly what it sent.
    normalised_id = normalize_employee_id(employee_id)
    if normalised_id is None:
        return {"error": "INVALID_EMPLOYEE_ID", "employee_id": employee_id}

    # Step 1: valid format but unknown employee.
    employee = directory.get(normalised_id)
    if employee is None:
        return {"error": "EMPLOYEE_NOT_FOUND", "employee_id": normalised_id}

    # Step 2: not in the catalog (the agent must escalate E3, not guess an item).
    catalog_item = _normalize_item(item)
    if catalog_item is None:
        return {"error": "UNKNOWN_ITEM", "item": item}

    # Oldest unit = earliest issued_date, ties broken by asset_id. Without the tie-break,
    # E1003's two monitors (same issue date) could swap between runs or data reorders,
    # changing which asset the employee is told to return.
    held = sorted(
        (e for e in employee.equipment if e.item == catalog_item),
        key=lambda e: (e.issued_date, e.asset_id),
    )
    oldest = held[0] if held else None

    # One fixed key set for every verdict, with null where a key doesn't apply. The agent
    # and the tests then never have to ask "is this key present?", and logs stay uniform.
    verdict: dict[str, Any] = {
        "employee_id": employee.employee_id,
        "item": catalog_item,
        "status": None,
        "rule_id": None,
        "role": employee.role,
        "employment_status": employee.status,
        "units_held": len(held),
        "max": None,
        "refresh_months": None,
        "oldest_issued_date": oldest.issued_date.isoformat() if oldest else None,
        "refresh_date": None,
        "next_eligible_date": None,
        "replaces_asset_id": None,
        "as_of": as_of.isoformat(),
        # The verdict depends on both files, so lineage names both (PLAN §3).
        "data_versions": {"employees": directory.data_version, "policy": policy.data_version},
    }

    # Step 3: any non-active status goes to a human. Stops before the policy is read, so
    # max and refresh_months stay null.
    if employee.status != "active":
        return verdict | {"status": "NEEDS_REVIEW", "rule_id": "R-STATUS"}

    limit = policy.limits_for(employee.role)[catalog_item]
    verdict |= {"max": limit.max, "refresh_months": limit.refresh_months}

    # Step 4: role not entitled.
    if limit.max == 0:
        return verdict | {"status": "NOT_ELIGIBLE", "rule_id": "R-ROLE"}

    # Step 5: room for one more unit.
    if len(held) < limit.max:
        return verdict | {"status": "ELIGIBLE", "rule_id": "R-NEW"}

    # Step 6: at max with no refresh cycle; only a human can approve a replacement.
    if limit.refresh_months == "none":
        return verdict | {"status": "NOT_ELIGIBLE", "rule_id": "R-LIMIT"}

    # At max with a refresh interval, so at least one unit is held: max > 0 and the
    # ItemLimit validator guarantees refresh_months is a positive int here.
    assert oldest is not None and isinstance(limit.refresh_months, int)
    refresh_date = add_months(oldest.issued_date, limit.refresh_months)
    verdict["refresh_date"] = refresh_date.isoformat()

    # Step 7: eligible ON the refresh date, not only after it (§4.2).
    if refresh_date <= as_of:
        # Name the unit being returned: approval is a replacement, never a unit above max
        # (REQUIREMENTS §10).
        return verdict | {
            "status": "ELIGIBLE",
            "rule_id": "R-REFRESH-OK",
            "replaces_asset_id": oldest.asset_id,
        }

    # Step 8: refresh not yet due; tell the employee when it will be.
    return verdict | {
        "status": "NOT_ELIGIBLE",
        "rule_id": "R-REFRESH",
        "next_eligible_date": refresh_date.isoformat(),
    }
