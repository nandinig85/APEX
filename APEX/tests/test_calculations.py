"""Unit tests for the pure financial calculations (section 4)."""
import math

import pytest

import calculations as calc
from models import ApplicantInput


# ---------------------------------------------------------------- EMI (4.1)
@pytest.mark.parametrize("p, rate, n, expected", [
    (800_000, 12, 48, 21_067.07),     # worked example
    (100_000, 10, 12, 8_791.59),      # textbook value
    (500_000, 13, 60, 11_376.54),
    (1_000_000, 8.5, 240, 8_678.23),
])
def test_emi_known_values(p, rate, n, expected):
    assert calc.emi(p, rate, n) == pytest.approx(expected, abs=0.01)


def test_emi_zero_interest_is_principal_over_tenure():
    assert calc.emi(120_000, 0, 12) == 10_000


def test_emi_breakdown_totals_are_consistent():
    b = calc.emi_breakdown(800_000, 12, 48)
    assert b.total_repayment == pytest.approx(b.emi * 48)
    assert b.total_interest == pytest.approx(b.total_repayment - 800_000)
    assert b.total_interest > 0
    assert calc.emi_breakdown(120_000, 0, 12).total_interest == pytest.approx(0)


@pytest.mark.parametrize("p, rate, n", [
    (-1, 12, 12), (100_000, 12, 0), (100_000, 12, -6), (100_000, -1, 12),
    (100_000, 12, 12.5), (math.nan, 12, 12), (100_000, math.inf, 12), (100_000, 12, True),
])
def test_emi_rejects_invalid_inputs(p, rate, n):
    with pytest.raises(ValueError):
        calc.emi(p, rate, n)


def test_monthly_rate():
    assert calc.monthly_rate(12) == pytest.approx(0.01)


# ------------------------------------------------- Inverse EMI (4.4)
@pytest.mark.parametrize("p, rate, n", [(800_000, 12, 48), (250_000, 0, 24), (3_000_000, 18.5, 84), (1, 0.01, 1)])
def test_principal_from_emi_inverts_emi(p, rate, n):
    assert calc.principal_from_emi(calc.emi(p, rate, n), rate, n) == pytest.approx(p, rel=1e-9)


def test_principal_from_zero_emi_is_zero():
    assert calc.principal_from_emi(0, 12, 48) == 0


# ------------------------------------------------- FOIR / max EMI (4.2-4.3)
def test_foir_worked_example():
    assert calc.foir(12_000, 21_067.07, 60_000) == pytest.approx(0.551118, abs=1e-6)


def test_foir_undefined_for_zero_income():
    assert calc.foir(0, 1_000, 0) is None


def test_foir_rejects_negative():
    with pytest.raises(ValueError):
        calc.foir(-1, 1_000, 50_000)


def test_max_new_emi_positive_and_floored_at_zero():
    assert calc.max_new_emi(0.5, 60_000, 12_000) == (18_000, False)
    cap, exceeded = calc.max_new_emi(0.4, 50_000, 30_000)
    assert cap == 0 and exceeded


def test_max_new_emi_rejects_bad_cap():
    with pytest.raises(ValueError):
        calc.max_new_emi(1.5, 50_000, 0)


# ------------------------------------------------- Utilisation (4.5)
def test_credit_utilisation():
    assert calc.credit_utilisation(50_000, 200_000) == 0.25
    assert calc.credit_utilisation(0, 0) is None  # no revolving credit -> undefined, not 0 %
    assert calc.credit_utilisation(110, 100) == pytest.approx(1.1)  # over-limit shown as is


# ------------------------------------------------- LTI and income multiple (4.4, 4.6)
def test_loan_to_income_and_ceiling():
    assert calc.loan_to_income(800_000, 720_000) == pytest.approx(1.1111, abs=1e-4)
    assert calc.loan_to_income(800_000, 0) is None
    assert calc.income_multiple_ceiling(720_000, 2.0) == 1_440_000


# ------------------------------------------------- Residual income (4.7)
def test_residual_income():
    assert calc.residual_income(60_000, 12_000, 21_067.07) == pytest.approx(26_932.93)
    assert calc.residual_income(60_000, 12_000, 21_067.07, 10_000) == pytest.approx(16_932.93)
    assert calc.residual_income(20_000, 15_000, 10_000) < 0


# ------------------------------------------------- Variability (4.8)
def test_coefficient_of_variation():
    assert calc.coefficient_of_variation([50_000] * 6) == 0
    assert calc.coefficient_of_variation([10, 20, 30]) == pytest.approx(0.5)  # sd(ddof=1)=10, mean=20
    assert calc.coefficient_of_variation([50_000]) is None
    assert calc.coefficient_of_variation([0, 0, 0]) is None
    with pytest.raises(ValueError):
        calc.coefficient_of_variation([1, -1, 2])


def test_cv_income_factor():
    assert calc.cv_income_factor(0.05, 0.10, 1.0, 0.4) == 1.0
    assert calc.cv_income_factor(0.30, 0.10, 1.0, 0.4) == pytest.approx(0.8)
    assert calc.cv_income_factor(2.0, 0.10, 1.0, 0.4) == pytest.approx(0.6)


# ------------------------------------------------- Tenure cap, curves
def test_tenure_caps():
    assert calc.age_based_tenure_cap(55, 60) == 60
    assert calc.age_based_tenure_cap(62, 60) == 0
    assert calc.effective_tenure_cap(55, 58, 60) == 36
    assert calc.effective_tenure_cap(30, 60, 60) == 60
    assert calc.effective_tenure_cap(None, 60, 60) is None


def test_piecewise_linear_interpolates_and_clamps():
    pts = [[0, 100], [0.5, 50], [1.0, 0]]
    assert calc.piecewise_linear(0.25, pts) == pytest.approx(75)
    assert calc.piecewise_linear(-1, pts) == 100
    assert calc.piecewise_linear(5, pts) == 0


# ------------------------------------------------- Income adjustment
def _app(**kw):
    base = dict(applicant_id="T", requested_amount=100_000, tenure_months=12, annual_interest_rate=12)
    base.update(kw)
    return ApplicantInput(**base)


def test_adjust_income_verified_salaried_is_unchanged(bank_b):
    inc = calc.adjust_income(_app(employment_category="salaried", net_monthly_income=60_000,
                                  verified_monthly_income=60_000), bank_b.income_adjustment)
    assert inc.adjusted_income == 60_000


def test_adjust_income_unverified_and_informal_haircuts(bank_b):
    pol = bank_b.income_adjustment
    inc = calc.adjust_income(_app(employment_category="informal", net_monthly_income=30_000,
                                  income_variability="high"), pol)
    assert inc.adjusted_income == pytest.approx(30_000 * 0.75 * 0.9 * 0.75)


def test_adjust_income_observations_cap_declared(bank_b):
    obs = [40_000] * 6
    inc = calc.adjust_income(_app(employment_category="self_employed", net_monthly_income=60_000,
                                  monthly_income_observations=obs), bank_b.income_adjustment)
    assert inc.base_income == 40_000
    assert inc.coefficient_of_variation == 0
    assert inc.variability_factor == 1.0


def test_adjust_income_too_few_observations_ignored(bank_b):
    inc = calc.adjust_income(_app(employment_category="self_employed", net_monthly_income=60_000,
                                  monthly_income_observations=[10_000, 90_000]), bank_b.income_adjustment)
    assert inc.base_income == 60_000 and inc.coefficient_of_variation is None


def test_adjust_income_missing(bank_b):
    inc = calc.adjust_income(_app(employment_category="salaried"), bank_b.income_adjustment)
    assert inc.adjusted_income is None
