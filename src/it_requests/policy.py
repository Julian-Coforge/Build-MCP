"""get_policy_limits as a plain, pure function (REQUIREMENTS §7.2). MCP wrapping comes later.

Pure: no I/O and no clock. The policy table is passed in, so the same inputs always give
the same output.
"""

from typing import Any

from it_requests.data import CATALOG_ITEMS, ROLES, PolicyTable, Role


def normalize_role(raw: str) -> Role | None:
    """Trim and lower-case (lookup is case-insensitive, §7.2); None if not a known role."""
    candidate = raw.strip().lower()
    # Return the matching ROLES element, not `candidate`, so the result is typed as Role.
    return next((role for role in ROLES if role == candidate), None)


def get_policy_limits(role: str, *, policy: PolicyTable) -> dict[str, Any]:
    """Return one role's limits for every catalog item. Errors are returned, never raised.

    Why errors as data (REQUIREMENTS §7): the agent reads tool output as text, so a returned
    {"error": "UNKNOWN_ROLE", ...} is something the model can reason about and act on.
    """
    normalised = normalize_role(role)
    if normalised is None:
        # Echo the raw input: there is no valid normalised form to report back.
        return {"error": "UNKNOWN_ROLE", "role": role}

    limits = policy.limits_for(normalised)
    return {
        "role": normalised,
        # Built in CATALOG_ITEMS order, not the file's order, so output is deterministic.
        # refresh_months is passed through unchanged: an int, "none" (no refresh cycle,
        # R-LIMIT) or None, which JSON sends as null (not entitled, R-ROLE). Keeping them
        # distinct means the model never has to infer one from max (§7.2, v1.1).
        "limits": {
            item: {"max": limits[item].max, "refresh_months": limits[item].refresh_months}
            for item in CATALOG_ITEMS
        },
        # Lineage (PLAN §3): every response names the data it was built from.
        "data_version": policy.data_version,
    }
