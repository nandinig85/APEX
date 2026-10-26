"""Layer B: explainable readiness score, data completeness and classification.

Readiness = sum_i w'_i * s_i over applicable factors, where w'_i are the
configured weights renormalised over factors that apply to this applicant
(not-applicable factors, e.g. utilisation for someone with no credit card, are
removed rather than given an artificial value). A factor that applies but whose
input is missing is scored at a conservative placeholder and counted against
"score coverage".

The score is a rule-based readiness indicator. It is NOT a probability of
approval or rejection.
"""
from __future__ import annotations

from typing import Optional

from calculations import piecewise_linear
from eligibility import document_gaps
from models import (ApplicantInput, BankConfig, CheckResult, CheckStatus, Classification,
                    CompletenessItem, DataCompleteness, DocStatus, FactorScore, FactorStatus,
                    IncomeAdjustment, IncomeVariability)

FACTOR_LABELS = {
    "affordability": "Affordability (FOIR vs cap)",
    "credit_score": "Credit score",
    "credit_utilisation": "Credit utilisation",
    "loan_to_income": "Loan-to-income multiple",
    "income_stability": "Income stability",
    "residual_income": "Residual income",
    "documentation": "Documentation readiness",
}

INCOME_PROOF_DOCS = ("income_proof", "salary_slips", "itr")


# --------------------------------------------------------------------------
# Weighted aggregation shared by the readiness score and income stability
# --------------------------------------------------------------------------
def aggregate(parts: list[tuple[str, FactorStatus, Optional[float]]], weights: dict[str, float],
              missing_score: float) -> tuple[Optional[float], float, dict[str, float]]:
    """Return (score, coverage, effective_weights).

    parts: (name, status, raw score or None). Not-applicable parts are dropped
    and the remaining weights renormalised; missing parts use ``missing_score``.
    coverage = share of applicable weight backed by an actual score.
    """
    applicable = [(n, s, v) for n, s, v in parts if s != FactorStatus.NOT_APPLICABLE]
    total_w = sum(weights[n] for n, _, _ in applicable)
    if total_w == 0:
        return None, 0.0, {}
    eff = {n: weights[n] / total_w for n, _, _ in applicable}
    score = sum(eff[n] * (v if s == FactorStatus.SCORED else missing_score) for n, s, v in applicable)
    coverage = sum(eff[n] for n, s, _ in applicable if s == FactorStatus.SCORED)
    return score, coverage, eff


# --------------------------------------------------------------------------
# Income stability (4.8)
# --------------------------------------------------------------------------
def income_stability(a: ApplicantInput, cfg: BankConfig, income: IncomeAdjustment
                     ) -> tuple[Optional[float], float, list[dict]]:
    """Return (score 0-100 or None if nothing known, coverage, component details)."""
    pol = cfg.scoring.income_stability
    adj = cfg.income_adjustment
    cat = a.employment_category
    comps: list[tuple[str, FactorStatus, Optional[float], str]] = []

    if a.years_in_employment is None:
        comps.append(("tenure", FactorStatus.MISSING, None, "Years in employment not provided"))
    else:
        comps.append(("tenure", FactorStatus.SCORED, piecewise_linear(a.years_in_employment, pol.tenure_curve),
                      f"{a.years_in_employment:g} years"))

    if income.coefficient_of_variation is not None:
        cv = income.coefficient_of_variation
        comps.append(("variability", FactorStatus.SCORED, piecewise_linear(cv, pol.cv_curve), f"CV = {cv:.3f}"))
    elif a.income_variability != IncomeVariability.UNKNOWN:
        comps.append(("variability", FactorStatus.SCORED, pol.self_reported_score[a.income_variability],
                      f"self-reported {a.income_variability.value}"))
    elif cat is None or cat in adj.variability_applies_to:
        comps.append(("variability", FactorStatus.MISSING, None, "Income variability not provided"))
    else:
        comps.append(("variability", FactorStatus.NOT_APPLICABLE, None, "Not assessed for this category"))

    proof = [d for d in INCOME_PROOF_DOCS if a.doc(d) == DocStatus.AVAILABLE]
    enough_obs = len(a.monthly_income_observations or []) >= adj.min_observation_months
    if a.verified_monthly_income is not None or proof:
        comps.append(("documented_history", FactorStatus.SCORED, 100.0,
                      "verified income" if a.verified_monthly_income is not None else "income proof available"))
    elif enough_obs:
        comps.append(("documented_history", FactorStatus.SCORED, 50.0, "monthly observations only (unverified)"))
    elif any(a.doc(d) == DocStatus.NOT_AVAILABLE for d in INCOME_PROOF_DOCS):
        comps.append(("documented_history", FactorStatus.SCORED, 0.0, "no income documentation available"))
    else:
        comps.append(("documented_history", FactorStatus.MISSING, None, "Income documentation not recorded"))

    if "employment_category" in pol.weights:
        if pol.employment_category_score is not None and cat is not None:
            comps.append(("employment_category", FactorStatus.SCORED, pol.employment_category_score.get(cat),
                          f"explicit policy score for {cat.value}"))
        else:
            comps.append(("employment_category", FactorStatus.NOT_APPLICABLE, None, "No explicit policy"))

    weights = {n: pol.weights.get(n, 0.0) for n, *_ in comps}
    parts = [(n, s, v) for n, s, v, _ in comps]
    if all(s != FactorStatus.SCORED for _, s, _ in parts):
        return None, 0.0, [dict(component=n, status=s.value, score=v, note=t) for n, s, v, t in comps]
    score, coverage, eff = aggregate(parts, weights, cfg.scoring.missing_factor_score)
    details = [dict(component=n, status=s.value, score=v, weight=eff.get(n, 0.0), note=t) for n, s, v, t in comps]
    return score, coverage, details


# --------------------------------------------------------------------------
# Factor sub-scores (Layer B)
# --------------------------------------------------------------------------
def factor_inputs(a: ApplicantInput, cfg: BankConfig, income: IncomeAdjustment, foir_value: Optional[float],
                  utilisation: Optional[float], lti: Optional[float], residual: Optional[float],
                  stability: Optional[float]) -> dict[str, tuple[FactorStatus, Optional[float], str]]:
    """Map each factor to (status, sub-score 0-100, description of the input)."""
    c = cfg.scoring.curves
    out: dict[str, tuple[FactorStatus, Optional[float], str]] = {}
    cat = a.employment_category

    # Affordability: x = FOIR / cap, so the curve is relative to each bank's ceiling.
    if foir_value is None or cat is None:
        out["affordability"] = (FactorStatus.MISSING, None, "FOIR not computable (missing income, EMIs or category)")
    else:
        cap = cfg.eligibility.foir_cap.get(cat)
        out["affordability"] = (FactorStatus.SCORED, piecewise_linear(foir_value / cap, c["affordability"]),
                                f"FOIR {foir_value:.1%} vs cap {cap:.0%} (ratio {foir_value / cap:.2f})")

    # Credit score: an exact score, or the LOWER end of a range (conservative).
    if a.no_credit_history:
        out["credit_score"] = (FactorStatus.NOT_APPLICABLE, None, "New to credit: no score exists")
    elif a.credit_score is not None:
        out["credit_score"] = (FactorStatus.SCORED, piecewise_linear(a.credit_score, c["credit_score"]),
                               f"Score {a.credit_score}")
    elif a.credit_score_range_low is not None:
        out["credit_score"] = (FactorStatus.SCORED, piecewise_linear(a.credit_score_range_low, c["credit_score"]),
                               f"Range {a.credit_score_range_low}-{a.credit_score_range_high} (lower bound used)")
    else:
        out["credit_score"] = (FactorStatus.MISSING, None, "Credit score not provided")

    # Credit utilisation: no revolving credit -> not applicable, not an artificial 0 %.
    if utilisation is not None:
        out["credit_utilisation"] = (FactorStatus.SCORED, piecewise_linear(utilisation, c["credit_utilisation"]),
                                     f"Utilisation {utilisation:.1%}")
    elif a.has_credit_card is False or (a.credit_card_limit == 0 and a.has_credit_card is not True):
        out["credit_utilisation"] = (FactorStatus.NOT_APPLICABLE, None, "No revolving credit")
    else:
        out["credit_utilisation"] = (FactorStatus.MISSING, None, "Card limit/balance not provided")

    # Loan-to-income: x = LTI / income-multiple ceiling.
    mult = cfg.amount.income_multiple
    if income.adjusted_income is None:
        out["loan_to_income"] = (FactorStatus.MISSING, None, "Income not provided")
    elif lti is None:  # zero income
        out["loan_to_income"] = (FactorStatus.SCORED, 0.0, "Adjusted income is zero")
    else:
        out["loan_to_income"] = (FactorStatus.SCORED, piecewise_linear(lti / mult, c["loan_to_income"]),
                                 f"LTI {lti:.2f}x vs ceiling {mult:g}x")

    if stability is None:
        out["income_stability"] = (FactorStatus.MISSING, None, "No stability information")
    else:
        out["income_stability"] = (FactorStatus.SCORED, stability, f"Stability sub-score {stability:.0f}")

    # Residual income: x = residual / adjusted income (share of income left).
    if residual is None:
        out["residual_income"] = (FactorStatus.MISSING, None, "Income or existing EMIs not provided")
    elif not income.adjusted_income:
        out["residual_income"] = (FactorStatus.SCORED, 0.0, "Adjusted income is zero")
    else:
        share = residual / income.adjusted_income
        out["residual_income"] = (FactorStatus.SCORED, piecewise_linear(share, c["residual_income"]),
                                  f"Residual INR {residual:,.0f} ({share:.0%} of adjusted income)")

    # Documentation: share of mandatory documents available ("unknown" counts as not available).
    mandatory, not_avail, unknown = document_gaps(a, cfg)
    if cat is None:
        out["documentation"] = (FactorStatus.MISSING, None, "Employment category unknown")
    elif not mandatory:
        out["documentation"] = (FactorStatus.NOT_APPLICABLE, None, "No mandatory documents configured")
    elif len(unknown) == len(mandatory):
        out["documentation"] = (FactorStatus.MISSING, None, "Document status not recorded")
    else:
        have = len(mandatory) - len(not_avail) - len(unknown)
        out["documentation"] = (FactorStatus.SCORED, 100.0 * have / len(mandatory),
                                f"{have} of {len(mandatory)} mandatory documents available")
    return out


def readiness(inputs: dict[str, tuple[FactorStatus, Optional[float], str]], cfg: BankConfig
              ) -> tuple[Optional[float], float, list[FactorScore]]:
    sc = cfg.scoring
    parts = [(f, s, v) for f, (s, v, _) in inputs.items()]
    score, coverage, eff = aggregate(parts, sc.weights, sc.missing_factor_score)
    factors = []
    for f, (status, v, desc) in inputs.items():
        used = v if status == FactorStatus.SCORED else (sc.missing_factor_score if status == FactorStatus.MISSING else None)
        ew = eff.get(f, 0.0)
        if status == FactorStatus.MISSING:
            expl = f"{desc}. Conservative placeholder score {sc.missing_factor_score:g} used."
        elif status == FactorStatus.NOT_APPLICABLE:
            expl = f"{desc}. Excluded; weight redistributed."
        else:
            expl = desc
        factors.append(FactorScore(
            factor=f, label=FACTOR_LABELS[f], status=status, score=used, raw_input=desc,
            weight=sc.weights[f], effective_weight=ew,
            contribution=ew * used if used is not None else 0.0,
            points_lost=ew * (100 - used) if used is not None else 0.0,
            explanation=expl))
    return score, coverage, factors


# --------------------------------------------------------------------------
# Data completeness (4.9) - reported separately from credit readiness
# --------------------------------------------------------------------------
def data_completeness(a: ApplicantInput, cfg: BankConfig) -> DataCompleteness:
    items: list[CompletenessItem] = []

    def add(field, label, required, status, fraction=None):
        frac = fraction if fraction is not None else {"provided": 1.0, "missing": 0.0, "not_applicable": 0.0}[status]
        items.append(CompletenessItem(field=field, label=label, required=required, status=status, fraction=frac))

    p = lambda cond: "provided" if cond else "missing"  # noqa: E731
    add("loan_request", "Loan amount, tenure and rate", True, "provided")
    add("age", "Age", True, p(a.age is not None))
    add("employment_category", "Employment category", True, p(a.employment_category is not None))
    add("income", "Net monthly income", True,
        p(a.net_monthly_income is not None or a.verified_monthly_income is not None))
    add("years_in_employment", "Years in employment/business", True, p(a.years_in_employment is not None))
    add("existing_emis", "Existing monthly EMIs", True, p(a.existing_monthly_emis is not None))

    mandatory, _, unknown = document_gaps(a, cfg)
    if a.employment_category is None:
        add("documents", "Mandatory document status", True, "missing")
    elif not mandatory:
        add("documents", "Mandatory document status", True, "not_applicable")
    else:
        frac = 1 - len(unknown) / len(mandatory)
        status = "provided" if frac == 1 else ("missing" if frac == 0 else "partial")
        add("documents", "Mandatory document status", True, status, frac)

    add("credit_score", "Credit score / range / no-history flag", False,
        p(a.credit_score is not None or a.credit_score_range_low is not None or a.no_credit_history))
    if a.has_credit_card is False:
        add("credit_card", "Credit card limit and balance", False, "provided")
    else:
        add("credit_card", "Credit card limit and balance", False,
            p(a.credit_card_limit is not None and a.credit_card_balance is not None))
    enough_obs = len(a.monthly_income_observations or []) >= cfg.income_adjustment.min_observation_months
    variability_known = enough_obs or a.income_variability != IncomeVariability.UNKNOWN
    if a.employment_category is not None and a.employment_category not in cfg.income_adjustment.variability_applies_to \
            and not variability_known:
        add("income_variability", "Income variability / monthly history", False, "not_applicable")
    else:
        add("income_variability", "Income variability / monthly history", False, p(variability_known))
    add("verified_income", "Verified / documented income", False, p(a.verified_monthly_income is not None))
    add("active_loans", "Number of active loans", False, p(a.active_loan_count is not None))
    add("employment_continuity", "Employment continuity (12 months)", False, p(a.continuous_employment_12m is not None))
    add("other_obligations", "Other recurring obligations", False, p(a.other_recurring_obligations is not None))

    w = lambda it: cfg.completeness.required_weight if it.required else cfg.completeness.optional_weight  # noqa: E731
    applicable = [it for it in items if it.status != "not_applicable"]
    total = sum(w(it) for it in applicable)
    weighted = 100 * sum(w(it) * it.fraction for it in applicable) / total if total else 100.0
    req = [it for it in applicable if it.required]
    req_score = 100 * sum(it.fraction for it in req) / len(req) if req else 100.0
    return DataCompleteness(weighted_score=weighted, required_score=req_score, items=items)


# --------------------------------------------------------------------------
# Classification
# --------------------------------------------------------------------------
CRITICAL_FIELDS = ("age", "employment_category", "income", "years_in_employment", "existing_emis")


def classify(checks: list[CheckResult], score: Optional[float], coverage: float,
             completeness: DataCompleteness, cfg: BankConfig) -> tuple[Classification, list[str]]:
    """Combine Layer A and Layer B. Order matters:

    1. Any hard FAIL -> Unlikely (a high score can never override it).
    2. Critical inputs missing or score coverage too low -> Insufficient information.
    3. Score >= likely_min and no REVIEW or DEVIATION checks -> Likely.
    4. Score >= borderline_min (or a Likely-level score with open REVIEW/DEVIATION items) -> Borderline.
    5. Otherwise -> Unlikely.

    ``score`` is the readiness score after bank preference adjustments.
    """
    sc = cfg.scoring
    fails = [c for c in checks if c.status == CheckStatus.FAIL]
    reviews = [c for c in checks if c.status in (CheckStatus.REVIEW, CheckStatus.DEVIATION)]
    if fails:
        return Classification.UNLIKELY, [f"Hard criterion not met: {c.label} ({c.observed}; required {c.threshold})."
                                         for c in fails]
    missing_critical = [it.label for it in completeness.items if it.field in CRITICAL_FIELDS and it.status == "missing"]
    if missing_critical or score is None or coverage < sc.min_score_coverage:
        reasons = []
        if missing_critical:
            reasons.append("Critical inputs missing: " + ", ".join(missing_critical) + ".")
        if score is None or coverage < sc.min_score_coverage:
            reasons.append(f"Only {coverage:.0%} of the score weight is backed by data "
                           f"(minimum {sc.min_score_coverage:.0%}).")
        return Classification.INSUFFICIENT, reasons
    if score >= sc.likely_min and not reviews:
        return Classification.LIKELY, [f"No hard criterion failed and readiness score {score:.0f} >= {sc.likely_min:g}."]
    if score >= sc.borderline_min:
        reasons = [f"Readiness score {score:.0f} is in the borderline band ({sc.borderline_min:g}-{sc.likely_min:g})."
                   if score < sc.likely_min else f"Readiness score {score:.0f} is high but open review items remain."]
        reasons += [f"Deviation: {c.label} - approval required from {c.deviation_authority}."
                    if c.status == CheckStatus.DEVIATION else f"Review: {c.label} - {c.detail}" for c in reviews]
        return Classification.BORDERLINE, reasons
    return Classification.UNLIKELY, [f"No hard criterion failed, but readiness score {score:.0f} is below "
                                     f"{sc.borderline_min:g}. This is a score-based indication for manual "
                                     f"review, not a rejection."]
