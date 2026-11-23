"""Mock employee data: Pydantic models and a loader that fails loudly on bad data.

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

# extra="forbid": a typo'd or unexpected field (e.g. "hire_dte") is an error, not silently
# ignored. frozen=True: loaded records are shared read-only state; no tool may mutate them.
_STRICT = ConfigDict(extra="forbid", frozen=True)


class DataValidationError(Exception):
    """Raised when a data file is missing, unreadable or fails validation."""


class EquipmentItem(BaseModel):
    model_config = _STRICT

    item: CatalogItem
    asset_id: NonEmptyStr
    issued_date: date


class Employee(BaseModel):
    model_config = _STRICT

    employee_id: Annotated[str, Field(pattern=r"^E\d{4}$")]
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
    message tells the operator which file to fix. `from err` keeps the original cause.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as err:
        raise DataValidationError(f"cannot read employee data file {path}: {err}") from err
    try:
        return EmployeeDirectory.model_validate_json(raw)
    except ValidationError as err:
        # Pydantic reports malformed JSON as a ValidationError too, so this one branch
        # covers both "not JSON" and "JSON with bad content".
        raise DataValidationError(f"invalid employee data in {path}: {err}") from err


__all__ = [
    "CATALOG_ITEMS",
    "ROLES",
    "CatalogItem",
    "DataValidationError",
    "Employee",
    "EmployeeDirectory",
    "EquipmentItem",
    "Role",
    "Status",
    "load_employees",
]
