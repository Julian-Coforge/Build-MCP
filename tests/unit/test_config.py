"""Tests for project-wide configuration constants."""

from datetime import date

from it_requests import config


def test_reference_date_matches_requirements() -> None:
    # REQUIREMENTS.md fixes the reference date for all examples and tests.
    # Policy checks take `as_of` as input and never read the system clock.
    assert config.REFERENCE_DATE == date(2026, 11, 22)
