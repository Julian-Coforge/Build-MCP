"""Project-wide configuration constants.

Kept in one module so every component (tools, agent, tests) reads the same values.
"""

from datetime import date
from pathlib import Path

# Reference date for every policy check in tests and demos (REQUIREMENTS.md, header).
# Eligibility depends on date arithmetic, so it is passed in as `as_of` and never
# read from the system clock. Tests that use today's date would break over time.
REFERENCE_DATE: date = date(2026, 11, 22)

# Repo-root data/ folder. This file is src/it_requests/config.py, so parents[2] is the repo
# root. Resolving from __file__ (not the working directory) means tests, the server and
# scripts find the same files wherever they are launched from.
DATA_DIR: Path = Path(__file__).resolve().parents[2] / "data"
EMPLOYEES_FILE: Path = DATA_DIR / "employees.json"
POLICY_FILE: Path = DATA_DIR / "policy.json"
# Written at runtime by flag_for_human_review; gitignored, never committed.
REVIEW_QUEUE_FILE: Path = DATA_DIR / "review_queue.json"
