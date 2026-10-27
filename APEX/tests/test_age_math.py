"""Exact maturity limits when a date of birth is available."""
from datetime import date

import pytest

from age_math import add_months, completed_years, maximum_tenure
from conftest import variant
from engine import assess
from models import CheckStatus


def test_month_end_and_leap_day_are_deterministic():
    assert add_months(date(2024, 1, 31), 1) == date(2024, 2, 29)
    assert add_months(date(2000, 2, 29), 60 * 12) == date(2060, 2, 29)
    assert completed_years(date(2000, 2, 29), date(2026, 2, 28)) == 25


def test_date_of_birth_controls_exact_boundary_and_tenure(bank_b, worked):
    born = date(1966, 10, 28)
    as_of = date(2026, 10, 27)
    assert maximum_tenure(born, as_of, 60, 84) == 0
    a = variant(worked, age=59, date_of_birth=born.isoformat(), tenure_months=1)
    r = assess(a, bank_b, timestamp=as_of.isoformat() + "T00:00:00+00:00", use_model=False)
    check = next(c for c in r.checks if c.key == "age_at_maturity")
    assert r.applicant.age == 59
    assert check.status == CheckStatus.FAIL
    assert r.effective_tenure_cap == 0
    assert "2026-11-27" in check.detail
    assert "2026-10-28" in check.detail


def test_date_of_birth_overrides_stale_entered_age(bank_b, worked):
    a = variant(worked, age=59, date_of_birth="1990-10-28")
    r = assess(a, bank_b, timestamp="2026-10-27T00:00:00+00:00", use_model=False)
    assert r.applicant.age == 35
    assert r.effective_tenure_cap > 0


def test_future_birth_date_and_invalid_timestamp_rejected(bank_b, worked):
    a = variant(worked, date_of_birth="2030-01-01")
    with pytest.raises(ValueError, match="date of birth"):
        assess(a, bank_b, timestamp="2026-10-27T00:00:00+00:00", use_model=False)
    with pytest.raises(ValueError, match="ISO assessment timestamp"):
        assess(a, bank_b, timestamp="bad", use_model=False)
