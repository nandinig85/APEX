"""Pure financial calculations for APEX.

Every function here is deterministic, has no knowledge of bank configurations
or the UI, and raises ``ValueError`` for invalid inputs. Functions return
``None`` where a metric is mathematically undefined for valid inputs (for
example a credit-utilisation ratio for an applicant with no revolving credit).

See docs/MATHEMATICAL_MODEL.md for the derivation and a worked example of each
formula.
"""
from __future__ import annotations

import math
from typing import Optional, Sequence

import numpy as np

from models import (ApplicantInput, EmiBreakdown, EmploymentCategory, IncomeAdjustment,
                    IncomeAdjustmentPolicy)


def _require_finite(name: str, value: float) -> None:
    if value is None or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")


# --------------------------------------------------------------------------
# 4.1 EMI
# --------------------------------------------------------------------------
def monthly_rate(annual_rate_pct: float) -> float:
    """r = annual rate (%) / 1200."""
    _require_finite("annual_rate_pct", annual_rate_pct)
    if annual_rate_pct < 0:
        raise ValueError("interest rate cannot be negative")
    return annual_rate_pct / 1200.0


def _validate_loan(principal: float, annual_rate_pct: float, tenure_months: int) -> None:
    _require_finite("principal", principal)
    if principal < 0:
        raise ValueError("loan principal cannot be negative")
    if not isinstance(tenure_months, (int, np.integer)) or isinstance(tenure_months, bool):
        raise ValueError("tenure must be a whole number of months")
    if tenure_months <= 0:
        raise ValueError("tenure must be at least one month")
    monthly_rate(annual_rate_pct)


def emi(principal: float, annual_rate_pct: float, tenure_months: int) -> float:
    """EMI = P r (1+r)^n / ((1+r)^n - 1); EMI = P / n when r = 0."""
    _validate_loan(principal, annual_rate_pct, tenure_months)
    r = monthly_rate(annual_rate_pct)
    n = tenure_months
    if r == 0:
        return principal / n
    growth = (1 + r) ** n
    return principal * r * growth / (growth - 1)


def emi_breakdown(principal: float, annual_rate_pct: float, tenure_months: int) -> EmiBreakdown:
    """EMI plus total repayment (EMI x n) and total interest (repayment - P)."""
    e = emi(principal, annual_rate_pct, tenure_months)
    total = e * tenure_months
    return EmiBreakdown(principal=principal, annual_rate=annual_rate_pct,
                        tenure_months=tenure_months, emi=e,
                        total_repayment=total, total_interest=total - principal)


# --------------------------------------------------------------------------
# 4.4 Inverse EMI: principal supported by a given EMI
# --------------------------------------------------------------------------
def principal_from_emi(emi_amount: float, annual_rate_pct: float, tenure_months: int) -> float:
    """P = EMI ((1+r)^n - 1) / (r (1+r)^n); P = EMI x n when r = 0."""
    _validate_loan(emi_amount, annual_rate_pct, tenure_months)
    r = monthly_rate(annual_rate_pct)
    n = tenure_months
    if r == 0:
        return emi_amount * n
    growth = (1 + r) ** n
    return emi_amount * (growth - 1) / (r * growth)


# --------------------------------------------------------------------------
# 4.2 / 4.3 FOIR and maximum permissible EMI
# --------------------------------------------------------------------------
def foir(existing_emis: float, proposed_emi: float, adjusted_income: float) -> Optional[float]:
    """(existing EMIs + proposed EMI) / adjusted monthly income.

    Returns None when income is zero (ratio undefined)."""
    for name, v in (("existing_emis", existing_emis), ("proposed_emi", proposed_emi),
                    ("adjusted_income", adjusted_income)):
        _require_finite(name, v)
        if v < 0:
            raise ValueError(f"{name} cannot be negative")
    if adjusted_income == 0:
        return None
    return (existing_emis + proposed_emi) / adjusted_income


def max_new_emi(foir_cap: float, adjusted_income: float, existing_emis: float) -> tuple[float, bool]:
    """max(0, cap x income - existing EMIs).

    Returns (capacity, exceeded) where ``exceeded`` is True when existing EMIs
    already use up (or exceed) the FOIR capacity."""
    _require_finite("foir_cap", foir_cap)
    if not 0 < foir_cap <= 1:
        raise ValueError("FOIR cap must be a fraction in (0, 1]")
    for name, v in (("adjusted_income", adjusted_income), ("existing_emis", existing_emis)):
        _require_finite(name, v)
        if v < 0:
            raise ValueError(f"{name} cannot be negative")
    raw = foir_cap * adjusted_income - existing_emis
    return max(0.0, raw), raw <= 0


# --------------------------------------------------------------------------
# 4.5 Credit utilisation
# --------------------------------------------------------------------------
def credit_utilisation(balance: float, limit: float) -> Optional[float]:
    """Revolving balance / revolving limit. None when there is no limit."""
    for name, v in (("balance", balance), ("limit", limit)):
        _require_finite(name, v)
        if v < 0:
            raise ValueError(f"{name} cannot be negative")
    if limit == 0:
        return None
    return balance / limit


# --------------------------------------------------------------------------
# 4.6 Loan-to-income and income-multiple ceiling
# --------------------------------------------------------------------------
def loan_to_income(requested: float, annual_income: float) -> Optional[float]:
    _require_finite("requested", requested)
    _require_finite("annual_income", annual_income)
    if requested < 0 or annual_income < 0:
        raise ValueError("amounts cannot be negative")
    if annual_income == 0:
        return None
    return requested / annual_income


def income_multiple_ceiling(annual_income: float, multiple: float) -> float:
    _require_finite("annual_income", annual_income)
    _require_finite("multiple", multiple)
    if annual_income < 0 or multiple <= 0:
        raise ValueError("income must be >= 0 and multiple > 0")
    return annual_income * multiple


# --------------------------------------------------------------------------
# 4.7 Residual income
# --------------------------------------------------------------------------
def residual_income(adjusted_income: float, existing_emis: float, proposed_emi: float,
                    other_obligations: float = 0.0) -> float:
    for name, v in (("adjusted_income", adjusted_income), ("existing_emis", existing_emis),
                    ("proposed_emi", proposed_emi), ("other_obligations", other_obligations)):
        _require_finite(name, v)
        if v < 0:
            raise ValueError(f"{name} cannot be negative")
    return adjusted_income - existing_emis - proposed_emi - other_obligations


# --------------------------------------------------------------------------
# 4.8 Income variability
# --------------------------------------------------------------------------
def coefficient_of_variation(observations: Sequence[float]) -> Optional[float]:
    """Sample standard deviation (ddof=1) / mean. None if < 2 points or mean is 0."""
    arr = np.asarray(list(observations), dtype=float)
    if arr.size and (not np.all(np.isfinite(arr)) or np.any(arr < 0)):
        raise ValueError("income observations must be finite and non-negative")
    if arr.size < 2:
        return None
    mean = float(arr.mean())
    if mean == 0:
        return None
    return float(arr.std(ddof=1)) / mean


def cv_income_factor(cv: float, tolerance: float, slope: float, max_haircut: float) -> float:
    """1 - min(max_haircut, slope x max(0, CV - tolerance)).

    Variability up to ``tolerance`` is free; each unit of CV above it removes
    ``slope`` of usable income, capped at ``max_haircut``."""
    _require_finite("cv", cv)
    if cv < 0:
        raise ValueError("CV cannot be negative")
    return 1.0 - min(max_haircut, slope * max(0.0, cv - tolerance))


def adjust_income(applicant: ApplicantInput, policy: IncomeAdjustmentPolicy) -> IncomeAdjustment:
    """Adjusted monthly income = base x category factor x verification factor x variability factor.

    Base income: verified income if supplied; otherwise declared income, lowered
    to the mean of the monthly observations when that mean is lower (evidence
    caps declarations); otherwise the observation mean alone.
    """
    steps: list[str] = []
    declared = applicant.net_monthly_income
    cat = applicant.employment_category
    obs = applicant.monthly_income_observations or []
    enough_obs = len(obs) >= policy.min_observation_months
    obs_mean = float(np.mean(obs)) if enough_obs else None
    cv = coefficient_of_variation(obs) if enough_obs else None
    if obs and not enough_obs:
        steps.append(f"{len(obs)} monthly observations supplied; at least "
                     f"{policy.min_observation_months} are needed, so they were not used.")

    verified = applicant.verified_monthly_income is not None
    if verified:
        base, source = applicant.verified_monthly_income, "verified / documented income"
    elif declared is not None:
        base, source = declared, "declared net income (unverified)"
        if obs_mean is not None and obs_mean < declared:
            base, source = obs_mean, "mean of monthly observations (lower than declared)"
    elif obs_mean is not None:
        base, source = obs_mean, "mean of monthly observations (no declared income)"
    else:
        steps.append("No income information: adjusted income cannot be calculated.")
        return IncomeAdjustment(declared_income=declared, base_income=None, base_source="missing",
                                category_factor=1.0, verification_factor=1.0, variability_factor=1.0,
                                coefficient_of_variation=cv, adjusted_income=None, steps=steps)
    steps.append(f"Base income = {base:,.0f} ({source}).")

    if cat is None:
        cat_factor = min(policy.category_factor.get(c) for c in EmploymentCategory)
        steps.append(f"Employment category missing: lowest category factor {cat_factor:.2f} applied.")
    else:
        cat_factor = policy.category_factor.get(cat)
        steps.append(f"Category factor ({cat.value}) = {cat_factor:.2f}.")

    ver_factor = 1.0 if verified else policy.unverified_factor
    steps.append(f"Verification factor = {ver_factor:.2f}"
                 + (" (income verified)." if verified else " (income not verified)."))

    if cv is not None:
        h = policy.cv_haircut
        var_factor = cv_income_factor(cv, h.tolerance, h.slope, h.max_haircut)
        steps.append(f"Variability factor from CV {cv:.3f} = {var_factor:.3f}.")
    elif cat is None or cat in policy.variability_applies_to:
        var_factor = policy.self_reported_variability_factor[applicant.income_variability]
        steps.append(f"Variability factor from self-reported '{applicant.income_variability.value}' = {var_factor:.2f}.")
    else:
        var_factor = 1.0
        steps.append("Variability factor = 1.00 (not applied to this category without observations).")

    adjusted = base * cat_factor * ver_factor * var_factor
    steps.append(f"Adjusted income = {base:,.0f} x {cat_factor:.2f} x {ver_factor:.2f} x "
                 f"{var_factor:.3f} = {adjusted:,.0f}.")
    return IncomeAdjustment(declared_income=declared, base_income=base, base_source=source,
                            category_factor=cat_factor, verification_factor=ver_factor,
                            variability_factor=var_factor, coefficient_of_variation=cv,
                            adjusted_income=adjusted, steps=steps)


# --------------------------------------------------------------------------
# Tenure cap
# --------------------------------------------------------------------------
def age_based_tenure_cap(age: int, max_age_at_maturity: int) -> int:
    """Whole months until the applicant reaches ``max_age_at_maturity``.

    Age is entered in completed years, so the true remaining period can be up
    to 11 months shorter than this; date of birth must be verified manually
    when the requested tenure is close to the cap."""
    return max(0, (max_age_at_maturity - age) * 12)


def effective_tenure_cap(age: Optional[int], max_age_at_maturity: int, lender_max_tenure: int) -> Optional[int]:
    """min(lender maximum tenure, age-based limit). None if age is unknown."""
    if age is None:
        return None
    return min(int(lender_max_tenure), age_based_tenure_cap(age, max_age_at_maturity))


# --------------------------------------------------------------------------
# Amortisation and rate sensitivity
# --------------------------------------------------------------------------
def outstanding_balance(principal: float, annual_rate_pct: float, tenure_months: int, k: int) -> float:
    """Balance after k payments: B_k = P(1+r)^k - EMI((1+r)^k - 1)/r  (= P(1 - k/n) when r = 0)."""
    if not 0 <= k <= tenure_months:
        raise ValueError("k must lie between 0 and the tenure")
    e = emi(principal, annual_rate_pct, tenure_months)
    r = monthly_rate(annual_rate_pct)
    if r == 0:
        return max(0.0, principal - e * k)
    g = (1 + r) ** k
    return max(0.0, principal * g - e * (g - 1) / r)


def amortisation_schedule(principal: float, annual_rate_pct: float, tenure_months: int) -> list[dict]:
    """Month-by-month split of each EMI into interest and principal."""
    e = emi(principal, annual_rate_pct, tenure_months)
    r = monthly_rate(annual_rate_pct)
    bal, rows = principal, []
    for t in range(1, tenure_months + 1):
        interest = bal * r
        princ = e - interest if t < tenure_months else bal
        bal = max(0.0, bal - princ)
        rows.append({"month": t, "emi": interest + princ, "interest": interest, "principal": princ, "balance": bal})
    return rows


def emi_rate_sensitivity(principal: float, annual_rate_pct: float, tenure_months: int) -> float:
    """dEMI / dR, R in annual percentage points.

    dEMI/dr = P [ g/(g-1) - r n (1+r)^(n-1) / (g-1)^2 ],  g = (1+r)^n;
    at r = 0 the limit is P (n+1) / (2n). Divided by 1200 because r = R/1200."""
    _validate_loan(principal, annual_rate_pct, tenure_months)
    r, n = monthly_rate(annual_rate_pct), tenure_months
    if r == 0:
        return principal * (n + 1) / (2 * n) / 1200
    g = (1 + r) ** n
    return principal * (g / (g - 1) - r * n * (1 + r) ** (n - 1) / (g - 1) ** 2) / 1200


# --------------------------------------------------------------------------
# APR including upfront charges (internal rate of return)
# --------------------------------------------------------------------------
def annuity_pv(payment: float, i: float, n: int) -> float:
    return payment * n if i == 0 else payment * (1 - (1 + i) ** -n) / i


def monthly_irr(net_disbursal: float, payment: float, n: int, tol: float = 1e-12) -> float:
    """Solve net_disbursal = sum_{t=1..n} payment / (1+i)^t for i >= 0.

    The present value is strictly decreasing in i, so the root is unique.
    Newton's method is used with a bisection safeguard on [0, 1]."""
    if net_disbursal <= 0 or payment <= 0:
        raise ValueError("disbursal and payment must be positive")
    if net_disbursal >= payment * n:
        return 0.0
    lo, hi = 0.0, 1.0
    i = payment / net_disbursal - 1 / n  # starting guess
    i = min(max(i, 1e-9), 0.999)
    for _ in range(200):
        f = annuity_pv(payment, i, n) - net_disbursal
        if abs(f) < tol * net_disbursal:
            return i
        if f > 0:
            lo = i
        else:
            hi = i
        # derivative of PV: sum -t * payment / (1+i)^(t+1)
        d = -payment * sum(t * (1 + i) ** -(t + 1) for t in range(1, n + 1))
        step = i - f / d if d != 0 else (lo + hi) / 2
        i = step if lo < step < hi else (lo + hi) / 2
    return i


def apr(principal: float, annual_rate_pct: float, tenure_months: int, upfront_charges: float) -> tuple[float, float, float]:
    """(monthly IRR, nominal APR %, effective APR %) for a loan whose borrower pays charges upfront."""
    if upfront_charges < 0:
        raise ValueError("charges cannot be negative")
    if upfront_charges >= principal:
        raise ValueError("charges cannot exceed the principal")
    e = emi(principal, annual_rate_pct, tenure_months)
    i = monthly_irr(principal - upfront_charges, e, tenure_months)
    return i, 1200 * i, 100 * ((1 + i) ** 12 - 1)


# --------------------------------------------------------------------------
# Income-shortfall probability under a lognormal income model
# --------------------------------------------------------------------------
def normal_cdf(z: float) -> float:
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def normal_ppf(p: float) -> float:
    """Inverse standard normal CDF, Phi^-1(p), for 0 < p < 1.

    Acklam's rational approximation (relative error < 1.2e-9) followed by one
    Halley refinement step against normal_cdf, giving close to machine precision."""
    _require_finite("p", p)
    if not 0 < p < 1:
        raise ValueError("p must be strictly between 0 and 1")
    a = (-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00)
    b = (-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01)
    c = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00)
    d = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00, 3.754408661907416e+00)
    lo = 0.02425
    if p < lo:
        q = math.sqrt(-2 * math.log(p))
        x = (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
            ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    elif p > 1 - lo:
        q = math.sqrt(-2 * math.log(1 - p))
        x = -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
            ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    else:
        q = p - 0.5
        r = q * q
        x = (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
            (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)
    e = normal_cdf(x) - p
    u = e * math.sqrt(2 * math.pi) * math.exp(x * x / 2)
    return x - u / (1 + x * u / 2)


def lognormal_quantile_factor(cv: float, p: float) -> float:
    """k such that the p-quantile of a lognormal with mean m and CV cv equals k * m.

    q_p = exp(mu + sigma z_p) with mu = ln m - sigma^2/2, so k = exp(sigma z_p - sigma^2/2).
    The quantile is linear in the mean, which keeps the affordability limits linear in income."""
    if cv < 0:
        raise ValueError("CV cannot be negative")
    if cv == 0:
        return 1.0
    s = math.sqrt(math.log(1 + cv * cv))
    return math.exp(s * normal_ppf(p) - s * s / 2)


def annuity_factor(annual_rate_pct: float, tenure_months: int) -> float:
    """Principal supported by an EMI of 1: A(r, n) = (1 - (1+r)^-n) / r, or n when r = 0."""
    return principal_from_emi(1.0, annual_rate_pct, tenure_months)


def lognormal_shortfall_probability(mean_income: float, cv: float, threshold: float) -> float:
    """P(Y < threshold) where Y is lognormal with E[Y] = mean_income and CV = cv.

    sigma^2 = ln(1 + CV^2), mu = ln(mean) - sigma^2/2,  P = Phi((ln T - mu) / sigma).
    With CV = 0 the income is deterministic."""
    for name, v in (("mean_income", mean_income), ("cv", cv), ("threshold", threshold)):
        _require_finite(name, v)
    if mean_income <= 0:
        return 1.0
    if cv < 0:
        raise ValueError("CV cannot be negative")
    if threshold <= 0:
        return 0.0
    if cv == 0:
        return 1.0 if mean_income < threshold else 0.0
    s2 = math.log(1 + cv * cv)
    mu = math.log(mean_income) - s2 / 2
    return normal_cdf((math.log(threshold) - mu) / math.sqrt(s2))


# --------------------------------------------------------------------------
# Scoring transformation
# --------------------------------------------------------------------------
def piecewise_linear(x: float, points: Sequence[Sequence[float]]) -> float:
    """Linear interpolation through breakpoints, flat beyond the ends."""
    _require_finite("x", x)
    xs = [float(p[0]) for p in points]
    ys = [float(p[1]) for p in points]
    return float(np.interp(x, xs, ys))
