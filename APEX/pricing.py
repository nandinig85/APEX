"""Explainable fictional interest-rate suggestion for a loan pre-screen.

This is a configurable policy example, not an AI prediction or a bank quote.
It uses only an exact bureau score and existing EMIs as a share of adjusted
monthly income. The latter does not depend on the entered interest rate, so
applying the suggestion cannot change the suggestion itself.
"""
from __future__ import annotations

from models import Assessment, BankConfig, CheckStatus, RateFactor, RateRecommendation

# These request-level failures can be addressed by changing amount or tenure.
# Other hard failures block any illustrative price recommendation.
REQUEST_STRUCTURE_CHECKS = {"loan_amount", "tenure", "age_at_maturity", "foir"}


def recommend_rate(r: Assessment, cfg: BankConfig) -> RateRecommendation:
    amount = cfg.amount
    common = dict(base_rate_pct=amount.interest_rate_base, range_low_pct=amount.interest_rate_low,
                  range_high_pct=amount.interest_rate_high)
    if r.bank_id != cfg.meta.bank_id or r.policy_version != cfg.meta.policy_version:
        return RateRecommendation(status="blocked", reasons=["Assessment and bank policy versions do not match."],
                                  **common)

    failures = [c.label for c in r.checks if c.status == CheckStatus.FAIL and c.key not in REQUEST_STRUCTURE_CHECKS]
    if failures:
        return RateRecommendation(status="blocked", reasons=["Hard criteria must be resolved first: "
                                                              + ", ".join(failures) + "."], **common)

    a = r.applicant
    missing = []
    if a.credit_score is None:
        missing.append("An exact bureau score is needed for the pricing band; a range or no credit history "
                       "requires manual pricing.")
    income = r.income.adjusted_income
    if income is None or income <= 0:
        missing.append("Positive verified or documented usable monthly income is needed.")
    if a.existing_monthly_emis is None:
        missing.append("Existing monthly EMIs must be recorded, including zero when there are none.")
    if a.employment_category is None:
        missing.append("Employment category is needed to identify the bank's affordability limit.")
    if missing:
        return RateRecommendation(status="needs_information", reasons=missing, **common)

    share = a.existing_monthly_emis / income
    foir_cap = cfg.eligibility.foir_cap.get(a.employment_category)
    if share >= foir_cap:
        return RateRecommendation(status="blocked", existing_emi_share=share,
                                  reasons=["Existing EMIs already use the bank's full EMI-to-income allowance; "
                                           "reduce obligations before considering a loan rate."], **common)

    bands = cfg.pricing.credit_score_bands
    index = next(i for i, band in enumerate(bands) if a.credit_score >= band.min_score)
    band = bands[index]
    upper = 900 if index == 0 else bands[index - 1].min_score - 1
    factors = [RateFactor(
        key="credit_score", label="Exact bureau score", adjustment_pp=band.adjustment_pp,
        explanation=f"Score {a.credit_score} falls in the configured {band.min_score}–{upper} band.")]

    policy = cfg.pricing
    if share <= policy.low_existing_emi_share_max:
        debt_adjustment = policy.low_existing_emi_adjustment_pp
        burden_reason = f"Existing EMIs use {share:.1%} of usable income, at or below {policy.low_existing_emi_share_max:.0%}."
    elif share >= policy.high_existing_emi_share_min:
        debt_adjustment = policy.high_existing_emi_adjustment_pp
        burden_reason = f"Existing EMIs use {share:.1%} of usable income, at or above {policy.high_existing_emi_share_min:.0%}."
    else:
        debt_adjustment = 0.0
        burden_reason = f"Existing EMIs use {share:.1%} of usable income, between the two adjustment thresholds."
    factors.append(RateFactor(key="existing_emi_share", label="Existing repayment burden",
                              adjustment_pp=debt_adjustment, explanation=burden_reason))

    raw = amount.interest_rate_base + sum(f.adjustment_pp for f in factors)
    rate = min(amount.interest_rate_high, max(amount.interest_rate_low, raw))
    if rate != raw:
        factors.append(RateFactor(
            key="bank_range", label="Bank's fictional rate range", adjustment_pp=rate - raw,
            explanation=f"The example policy bounds the rate to {amount.interest_rate_low:g}%–"
                        f"{amount.interest_rate_high:g}%."))
    return RateRecommendation(status="available", recommended_rate_pct=round(rate, 2),
                              unclamped_rate_pct=round(raw, 2), existing_emi_share=share,
                              factors=factors, **common)
