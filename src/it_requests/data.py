"""Mock employee and policy data: Pydantic models and loaders that fail loudly on bad data.

Why fail loudly (PLAN §3): a silently bad record would surface later as a wrong
approval, which is the worst outcome in this system. So any problem stops start-up.
"""

from datetime import date
from pathlib import Path
from typing import Annotated, Literal, Self, get_args

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

# --- Enums (REQUIREMENTS §2.2, §3) -------------------------------------------
# Literal types give both static checking (mypy) and runtime validation (Pydantic).
Role = Literal["standard", "engineer", "manager", "contractor"]
Status = Literal["active", "on_leave", "terminated"]
CatalogItem = Literal[
    "laptop", "monitor", "docking_station", "headset", "keyboard_mouse", "mobile_phone"
]

# Derived from the Literals, not retyped, so the allowed values have one source of truth.
ROLES: tuple[str, ...] = get_args(Role)
CATALOG_ITEMS: tuple[str, ...] = get_args(CatalogItem)

NonEmptyStr = Annotated[str, Field(min_length=1)]

# The one definition of a valid employee ID (REQUIREMENTS §2.2: E####). employees.py
# imports it, so the data check and the input check cannot drift apart.
# [0-9], not \d: \d also matches non-ASCII digits (e.g. Arabic-Indic "E١٠٠١"), which would
# load fine but never match the ASCII ID a lookup uses. Works as-is in both Pydantic's
# regex engine and Python's re.fullmatch.
EMPLOYEE_ID_PATTERN = r"^E[0-9]{4}$"

# extra="forbid": a typo'd or unexpected field (e.g. "hire_dte") is an error, not silently
# ignored. frozen=True: loaded records are shared read-only state; no tool may mutate them.
_STRICT = ConfigDict(extra="forbid", frozen=True)


class DataValidationError(Exception):
    """Raised when a data file is missing, unreadable or fails validation."""


def _load_model[M: BaseModel](path: Path, model: type[M], what: str) -> M:
    """Read a JSON file and validate it as `model`. Every failure becomes DataValidationError.

    One shared helper, so the error handling exists once: every data file fails the same
    way (one exception type, the path in the message, the cause chained with `from err`),
    and a fix here applies to all loaders.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as err:
        raise DataValidationError(f"cannot read {what} data file {path}: {err}") from err
    try:
        return model.model_validate_json(raw)
    except ValidationError as err:
        # Pydantic reports malformed JSON as a ValidationError too, so this one branch
        # covers both "not JSON" and "JSON with bad content".
        raise DataValidationError(f"invalid {what} data in {path}: {err}") from err


class EquipmentItem(BaseModel):
    model_config = _STRICT

    item: CatalogItem
    asset_id: NonEmptyStr
    issued_date: date


class Employee(BaseModel):
    model_config = _STRICT

    employee_id: Annotated[str, Field(pattern=EMPLOYEE_ID_PATTERN)]
    name: NonEmptyStr
    role: Role
    hire_date: date
    status: Status
    # Tuple, not list: keeps the frozen model genuinely immutable.
    equipment: tuple[EquipmentItem, ...]


class EmployeeDirectory(BaseModel):
    model_config = _STRICT

    data_version: NonEmptyStr
    effective_date: date
    employees: tuple[Employee, ...]

    # Cross-record checks live on the directory because they need more than one record
    # (uniqueness across employees) or a top-level field (effective_date). A field
    # validator on Employee only sees one employee at a time.
    @model_validator(mode="after")
    def _check_cross_record_rules(self) -> Self:
        seen_ids: set[str] = set()
        seen_assets: set[str] = set()
        for emp in self.employees:
            if emp.employee_id in seen_ids:
                raise ValueError(f"duplicate employee_id: {emp.employee_id}")
            seen_ids.add(emp.employee_id)

            if emp.hire_date > self.effective_date:
                raise ValueError(
                    f"{emp.employee_id}: hire_date {emp.hire_date} is after "
                    f"effective_date {self.effective_date}"
                )

            for eq in emp.equipment:
                if eq.asset_id in seen_assets:
                    raise ValueError(f"duplicate asset_id: {eq.asset_id}")
                seen_assets.add(eq.asset_id)

                if eq.issued_date < emp.hire_date:
                    raise ValueError(
                        f"{emp.employee_id}: {eq.asset_id} issued_date {eq.issued_date} "
                        f"is before hire_date {emp.hire_date}"
                    )
                if eq.issued_date > self.effective_date:
                    raise ValueError(
                        f"{emp.employee_id}: {eq.asset_id} issued_date {eq.issued_date} "
                        f"is after effective_date {self.effective_date}"
                    )
        return self

    def get(self, employee_id: str) -> Employee | None:
        """Exact-match lookup. Normalising user input is the caller's job, not the data layer's."""
        # Linear scan is fine for six records; avoids a cached index on a frozen model.
        return next((e for e in self.employees if e.employee_id == employee_id), None)


def load_employees(path: Path) -> EmployeeDirectory:
    """Read and validate an employee file. Every failure becomes DataValidationError.

    One exception type means start-up code catches a single error, and the path in the
    message tells the operator which file to fix.
    """
    return _load_model(path, EmployeeDirectory, "employee")


# --- Policy (REQUIREMENTS §4.1, §4.2, §7.2) -------------------------------------


class ItemLimit(BaseModel):
    model_config = _STRICT

    max: Annotated[int, Field(ge=0)]
    # Positive int = refresh interval; "none" = entitled, no refresh cycle (R-LIMIT);
    # None (JSON null) = not entitled (R-ROLE). Distinct values, so the model never has to
    # infer one meaning from max (REQUIREMENTS §7.2, v1.1).
    refresh_months: Annotated[int, Field(gt=0)] | Literal["none"] | None

    # The max/refresh pairing is enforced here, in the model, not left to convention: the
    # eligibility logic relies on it, and a contradictory pair (max 0 with refresh 24, or
    # max 1 with null) would otherwise load fine and give a wrong decision later.
    @model_validator(mode="after")
    def _check_max_matches_refresh(self) -> Self:
        if self.max == 0 and self.refresh_months is not None:
            raise ValueError(
                f"max is 0 (not entitled), so refresh_months must be null, "
                f"got {self.refresh_months!r}"
            )
        if self.max > 0 and self.refresh_months is None:
            raise ValueError(
                f"max is {self.max} (entitled), so refresh_months must be a positive "
                f'integer or "none", got null'
            )
        return self


class PolicyTable(BaseModel):
    model_config = _STRICT

    data_version: NonEmptyStr
    effective_date: date
    # Literal keys reject unknown roles and items; the validator below catches missing ones.
    roles: dict[Role, dict[CatalogItem, ItemLimit]]

    @model_validator(mode="after")
    def _check_complete(self) -> Self:
        # Every role must define every item: a gap would leave the eligibility check with
        # no rule to apply, so it fails at load instead of at request time.
        missing_roles = [r for r in ROLES if r not in self.roles]
        if missing_roles:
            raise ValueError(f"policy is missing role(s): {', '.join(missing_roles)}")
        for role, items in self.roles.items():
            missing_items = [i for i in CATALOG_ITEMS if i not in items]
            if missing_items:
                raise ValueError(f"role {role} is missing item(s): {', '.join(missing_items)}")
        return self

    def limits_for(self, role: Role) -> dict[CatalogItem, ItemLimit]:
        """Limits for one role. Returns a copy, so callers can't alter the shared table."""
        return dict(self.roles[role])


def load_policy(path: Path) -> PolicyTable:
    """Read and validate the policy file. Every failure becomes DataValidationError."""
    return _load_model(path, PolicyTable, "policy")


__all__ = [
    "CATALOG_ITEMS",
    "EMPLOYEE_ID_PATTERN",
    "ROLES",
    "CatalogItem",
    "DataValidationError",
    "Employee",
    "EmployeeDirectory",
    "EquipmentItem",
    "ItemLimit",
    "PolicyTable",
    "Role",
    "Status",
    "load_employees",
    "load_policy",
]
