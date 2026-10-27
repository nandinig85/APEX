"""Deterministic age and loan-maturity dates for dated assessments."""
from __future__ import annotations

import calendar
from datetime import date


def add_months(day: date, months: int) -> date:
    month_index = day.year * 12 + day.month - 1 + months
    year, month_index = divmod(month_index, 12)
    month = month_index + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def completed_years(born: date, as_of: date) -> int:
    if born > as_of:
        raise ValueError("date of birth cannot be after the assessment date")
    return as_of.year - born.year - ((as_of.month, as_of.day) < (born.month, born.day))


def maturity_date(as_of: date, tenure_months: int) -> date:
    return add_months(as_of, tenure_months)


def maximum_tenure(born: date, as_of: date, max_age: int, product_max: int) -> int:
    limit = add_months(born, max_age * 12)
    if as_of > limit:
        return 0
    months = min(product_max, (limit.year - as_of.year) * 12 + limit.month - as_of.month)
    if add_months(as_of, months) > limit:
        months -= 1
    return max(0, months)


def maturity_age_years(born: date, as_of: date, tenure_months: int) -> float:
    return (maturity_date(as_of, tenure_months) - born).days / 365.2425
