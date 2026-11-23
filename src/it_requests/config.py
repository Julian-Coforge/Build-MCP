"""Project-wide configuration constants.

Kept in one module so every component (tools, agent, tests) reads the same values.
"""

from datetime import date

# Reference date for every policy check in tests and demos (REQUIREMENTS.md, header).
# Eligibility depends on date arithmetic, so it is passed in as `as_of` and never
# read from the system clock. Tests that use today's date would break over time.
REFERENCE_DATE: date = date(2026, 11, 22)
