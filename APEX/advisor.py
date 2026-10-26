"""How much can be lent, why, why not, and how the customer can improve.

Builds on ``engine.assess`` in three steps:

1. Affordability limits. Every affordability limit is linear in usable
   (adjusted) income Y and existing EMIs X:

        L_k(n) = A(R_k, n) (1 - b_k) max(0, a_k Y - X - d_k)

   where A(R, n) is the annuity factor. Together with the income-multiple and
   product ceilings, the maximum eligible amount is the lower envelope
   min_k L_k(n). Because every L_k is linear, the income or EMI level at which
   a requested amount fits has a closed form (``required_income``,
   ``max_existing_emis``).
2. Counter-offers: concrete loan structures (smaller amount, longer tenure,
   lower EMIs, more documented income), each re-assessed by the engine.
3. Improvement plan: counterfactuals that change one input at a time and
   re-run the engine, so every stated benefit is measured, not asserted.

Nothing here approves a loan. The output explains the rule-based assessment
in words a customer can act on.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Optional

import calculations as calc
from eligibility import document_gaps
from engine import assess, inr
from models import (DOCUMENT_TYPES, AffordabilityLimit, ApplicantInput, Assessment, BankConfig, CheckStatus,
                    Classification, DecisionAdvice, FactorStatus, ImprovementAction, LoanOption, Reason, Sensitivity)

CUSTOMER_CHANGE = {"reduce_emis", "more_income"}   # options that need the customer to change their finances
AMOUNT_CHECKS = {"loan_amount", "tenure", "foir", "age_at_maturity"}   # cured by a different amount or tenure
CLASS_RANK = {Classification.INSUFFICIENT: 0, Classification.UNLIKELY: 1, Classification.BORDERLINE: 2,
              Classification.LIKELY: 3}

# Plain-language names for built-in checks (bank rules use their own label).
CUSTOMER_LABEL = {
    "kyc_pan": "Identity verification (PAN)",
    "min_age": "Minimum age",
    "age_at_maturity": "Age when the loan would end",
    "min_income": "Minimum monthly income",
    "employment_category": "Employment type accepted for this product",
    "employment_tenure": "Time in current job or business",
    "credit_score": "Credit score",
    "loan_amount": "Loan amount limits",
    "tenure": "Repayment period limits",
    "documents": "Required documents",
    "foir": "EMI-to-income limit (FOIR)",
    "stress_test": "Repayment resilience under stress",
}


def round_down(x: float) -> float:
    """Round an amount down to a sanction-friendly step (INR 1,000 below 1 lakh, else INR 5,000)."""
    if x <= 0:
        return 0.0
    step = 1_000 if x < 100_000 else 5_000
    return math.floor(x / step) * step


# --------------------------------------------------------------------------
# Affordability limits
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class AffordCtx:
    income: float            # Y, adjusted monthly income
    existing: float          # X, existing EMIs
    other: float             # O, other recurring obligations (0 if not provided)
    rate: float              # R, assessment rate % p.a.
    floating: bool
    cv: Optional[float]      # income coefficient of variation used by the stress model
    cv_source: str
    var_factor: float       # variability factor applied in the income adjustment
    cat_factor: float
    foir_cap: float
    n_min: int
    n_max: int               # min(product max, age-based cap)


@dataclass(frozen=True)
class LinearLimit:
    """L(n) = A(rate, n) (1 - buffer) max(0, a Y - X - d)."""
    key: str
    label: str
    a: float
    d: float
    rate: float
    buffer: float
    customer_text: str
    formula: str

    def amount(self, ctx: AffordCtx, n: int) -> float:
        return calc.annuity_factor(self.rate, n) * (1 - self.buffer) * max(0.0, self.a * ctx.income - ctx.existing - self.d)


def build_ctx(r: Assessment, cfg: BankConfig) -> Optional[AffordCtx]:
    a = r.applicant
    if r.income.adjusted_income is None or a.existing_monthly_emis is None or a.employment_category is None:
        return None
    e = cfg.eligibility
    n_max = int(e.tenure_months.max) if r.effective_tenure_cap is None else min(int(e.tenure_months.max),
                                                                                r.effective_tenure_cap)
    return AffordCtx(income=r.income.adjusted_income, existing=a.existing_monthly_emis,
                     other=a.other_recurring_obligations or 0.0, rate=a.annual_interest_rate,
                     floating=a.rate_type == "floating", cv=r.stress.cv_used, cv_source=r.stress.cv_source,
                     var_factor=r.income.variability_factor or 1.0, cat_factor=r.income.category_factor,
                     foir_cap=e.foir_cap.get(a.employment_category), n_min=int(math.ceil(e.tenure_months.min)),
                     n_max=n_max)


def linear_limits(ctx: AffordCtx, cfg: BankConfig) -> list[LinearLimit]:
    cap, b = ctx.foir_cap, cfg.amount.safety_buffer_min
    st, ind = cfg.stress, cfg.indicators
    shock = st.rate_shock_bps / 100 if ctx.floating else 0.0
    out = [
        LinearLimit("foir", "EMI-to-income limit (FOIR)", cap, 0.0, ctx.rate, b,
                    f"All EMIs together may use at most {cap:.0%} of usable income ({inr(cap * ctx.income)} a "
                    f"month), and a {b:.0%} safety margin is kept.",
                    "A(R,n)(1-b) max(0, cap*Y - X)"),
        LinearLimit("stress", "Stress test", (cap + st.max_stressed_foir_excess) * (1 - st.income_shock_pct), 0.0,
                    ctx.rate + shock, 0.0,
                    f"EMIs must stay manageable if income falls by {st.income_shock_pct:.0%}"
                    + (f" and the floating rate rises by {shock:g} percentage points." if shock else "."),
                    "A(R+s,n) max(0, (cap+e)(1-d)Y - X)"),
        LinearLimit("residual", "Living-cost buffer", 1.0, ctx.other + ind.min_residual_income, ctx.rate, 0.0,
                    f"At least {inr(ind.min_residual_income)} a month should remain after all EMIs"
                    + (" and other regular obligations" if ctx.other else "") + " for living costs.",
                    "A(R,n) max(0, Y - X - O - Rmin)"),
    ]
    if ctx.cv:
        k = calc.lognormal_quantile_factor(ctx.cv, st.max_shortfall_probability)
        src = ("measured from monthly income" if ctx.cv_source.startswith("monthly") else
               "assumed for this employment type" if ctx.cv_source.startswith("category") else "from self-reported variability")
        out.append(LinearLimit(
            "volatility", "Income-volatility limit", cap * k / ctx.var_factor, 0.0, ctx.rate, 0.0,
            f"Allowing for month-to-month income variation (CV {ctx.cv:.2f}, {src}), EMIs should stay within "
            f"the FOIR limit in all but the weakest {st.max_shortfall_probability:.0%} of months.",
            "A(R,n) max(0, cap*k*Y/v - X),  k = exp(sigma*z_p - sigma^2/2)"))
    return out


def limits_at(ctx: AffordCtx, cfg: BankConfig, n: int) -> list[AffordabilityLimit]:
    rows = [AffordabilityLimit(key=l.key, label=l.label, customer_text=l.customer_text, formula=l.formula,
                               max_amount=l.amount(ctx, n)) for l in linear_limits(ctx, cfg)]
    mult = cfg.amount.income_multiple
    rows.append(AffordabilityLimit(
        key="income_multiple", label="Income-multiple ceiling", formula="m * 12 * Y",
        customer_text=f"Unsecured loans are limited to {mult:g} times annual usable income.",
        max_amount=mult * 12 * ctx.income))
    rows.append(AffordabilityLimit(
        key="product_max", label="Product maximum", formula="L_max",
        customer_text=f"This product lends at most {inr(cfg.eligibility.loan_amount.max)}.",
        max_amount=cfg.eligibility.loan_amount.max))
    low = min(rows, key=lambda x: x.max_amount)
    return [x.model_copy(update={"binding": x is low}) for x in rows]


def max_amount(ctx: AffordCtx, cfg: BankConfig, n: int) -> tuple[float, str]:
    rows = limits_at(ctx, cfg, n)
    low = next(x for x in rows if x.binding)
    return low.max_amount, low.label


def required_income(ctx: AffordCtx, cfg: BankConfig, amount: float, n: int) -> Optional[float]:
    """Smallest usable income Y at which ``amount`` fits every limit (closed form).

    For each linear limit: A(1-b)(aY - X - d) >= L  <=>  Y >= (L / (A(1-b)) + X + d) / a."""
    if amount > cfg.eligibility.loan_amount.max:
        return None
    need = [amount / (12 * cfg.amount.income_multiple)]
    for l in linear_limits(ctx, cfg):
        f = calc.annuity_factor(l.rate, n) * (1 - l.buffer)
        need.append((amount / f + ctx.existing + l.d) / l.a)
    return max(need)


def max_existing_emis(ctx: AffordCtx, cfg: BankConfig, amount: float, n: int) -> Optional[float]:
    """Largest existing-EMI total X at which ``amount`` fits (closed form); None if no X >= 0 works."""
    if amount > cfg.eligibility.loan_amount.max or amount > 12 * cfg.amount.income_multiple * ctx.income:
        return None
    xs = []
    for l in linear_limits(ctx, cfg):
        f = calc.annuity_factor(l.rate, n) * (1 - l.buffer)
        xs.append(l.a * ctx.income - l.d - amount / f)
    x = min(xs)
    return x if x >= 0 else None


def tenure_for(ctx: AffordCtx, cfg: BankConfig, amount: float, start: int) -> Optional[int]:
    """Shortest tenure in [start, n_max] at which ``amount`` fits, if any."""
    for n in range(max(start, ctx.n_min), ctx.n_max + 1):
        if max_amount(ctx, cfg, n)[0] >= amount:
            return n
    return None


# --------------------------------------------------------------------------
# Re-assessment helpers
# --------------------------------------------------------------------------
def rerun(r: Assessment, cfg: BankConfig, **updates) -> Assessment:
    data = r.applicant.to_state()
    data.update(updates)
    return assess(ApplicantInput.model_validate(data), cfg, timestamp=r.timestamp, use_model=False)


def eligible_amount(r: Assessment, cfg: BankConfig) -> Optional[float]:
    ctx = build_ctx(r, cfg)
    if ctx is None or ctx.n_max < ctx.n_min:
        return None
    n = min(max(r.applicant.tenure_months, ctx.n_min), ctx.n_max)
    return round_down(max_amount(ctx, cfg, n)[0])


def _option(r: Assessment, cfg: BankConfig, key: str, title: str, description: str, **updates) -> LoanOption:
    x = rerun(r, cfg, **updates) if updates else r
    a = x.applicant
    return LoanOption(key=key, title=title, description=description, amount=a.requested_amount,
                      tenure_months=a.tenure_months, emi=x.emi.emi, total_interest=x.emi.total_interest,
                      foir=x.metrics["foir"].value, classification=x.classification,
                      readiness_score=x.readiness_score,
                      fails=sum(c.status == CheckStatus.FAIL for c in x.checks),
                      deviations=sum(c.status == CheckStatus.DEVIATION for c in x.checks))


# --------------------------------------------------------------------------
# Why / why not
# --------------------------------------------------------------------------
def _factor_reason(r: Assessment, cfg: BankConfig, f) -> Optional[Reason]:
    a, m = r.applicant, r.metrics
    good = f.score >= 75
    bad = f.score <= 50
    if not (good or bad):
        return None
    kind = "strength" if good else "concern"
    w = f.points_lost
    if f.factor == "affordability":
        cap = cfg.eligibility.foir_cap.get(a.employment_category)
        t = (f"All EMIs, including this loan, would take {m['foir'].display} of usable income; the bank's limit is "
             f"{cap:.0%}.")
        return Reason(kind=kind, title="Comfortable EMI burden" if good else "High EMI burden", text=t, weight=w)
    if f.factor == "credit_score":
        s = a.credit_score if a.credit_score is not None else a.credit_score_range_low
        return Reason(kind=kind, title="Good credit score" if good else "Credit score below comfort level",
                      text=f"Credit score {s}" + (" shows a reliable repayment history." if good else
                                                  " is below the level the bank is most comfortable with."), weight=w)
    if f.factor == "credit_utilisation":
        return Reason(kind=kind, title="Low card usage" if good else "High card usage",
                      text=f"Credit cards are {m['credit_utilisation'].display} used"
                           + (" (below 30% is healthy)." if good else "; heavy usage can signal financial stress."),
                      weight=w)
    if f.factor == "loan_to_income":
        return Reason(kind=kind, title="Loan size suits income" if good else "Loan large relative to income",
                      text=f"The loan is {m['loan_to_income'].display} annual usable income (ceiling "
                           f"{cfg.amount.income_multiple:g}x).", weight=w)
    if f.factor == "income_stability":
        yrs = "" if a.years_in_employment is None else f" ({a.years_in_employment:g} years in current work)"
        return Reason(kind=kind, title="Stable income" if good else "Income stability limited",
                      text=("Income looks stable" if good else "Short tenure or varying income lowers stability")
                           + yrs + ".", weight=w)
    if f.factor == "residual_income":
        return Reason(kind=kind, title="Healthy money left each month" if good else "Little money left each month",
                      text=f"After all EMIs about {m['residual_income'].display} a month would remain.", weight=w)
    if f.factor == "documentation":
        return Reason(kind=kind, title="Documents in order" if good else "Documents incomplete",
                      text=f.raw_input + ".", weight=w)
    return None


def _reasons(r: Assessment, cfg: BankConfig, ctx: Optional[AffordCtx], eligible: Optional[float]
             ) -> tuple[list[Reason], list[Reason]]:
    why: list[Reason] = []
    why_not: list[Reason] = []
    a = r.applicant
    for c in r.checks:
        name = CUSTOMER_LABEL.get(c.key, c.label)
        if c.status == CheckStatus.FAIL:
            why_not.append(Reason(kind="blocker", title=name, weight=1000,
                                  text=f"{c.observed}; the bank requires {c.threshold}."))
        elif c.status == CheckStatus.DEVIATION:
            why_not.append(Reason(kind="concern", title=f"{name} (exception needed)", weight=500,
                                  text=f"{c.observed} is outside standard policy ({c.threshold}) but within a band "
                                       f"that the {c.deviation_authority} may approve."))
        elif c.status == CheckStatus.REVIEW and c.missing_info:
            why_not.append(Reason(kind="missing", title=f"Information needed: {name}", text=c.detail, weight=50))
        elif c.status == CheckStatus.REVIEW:
            why_not.append(Reason(kind="concern", title=f"Needs review: {name}", text=c.detail, weight=100))
    if eligible is not None and a.requested_amount > eligible and ctx is not None:
        n = min(max(a.tenure_months, ctx.n_min), ctx.n_max)
        binding = next(x for x in limits_at(ctx, cfg, n) if x.binding)
        why_not.append(Reason(kind="concern", title="Request above what income supports", weight=900,
                              text=f"The request is {inr(a.requested_amount - eligible)} above the maximum of "
                                   f"{inr(eligible)} at {n} months. Limiting factor: {binding.customer_text}"))
    elif eligible is not None and a.requested_amount <= eligible:
        why.append(Reason(kind="strength", title="Request fits every affordability limit", weight=100,
                          text=f"Up to {inr(eligible)} is supported at this tenure; {inr(a.requested_amount)} was "
                               "requested."))
    for f in r.factors:
        if f.status == FactorStatus.SCORED and f.score is not None:
            x = _factor_reason(r, cfg, f)
            if x is not None:
                (why if x.kind == "strength" else why_not).append(
                    x if x.kind == "concern" else x.model_copy(update={"weight": f.effective_weight * f.score}))
    if a.max_dpd_12m == 0 and a.written_off_or_settled_36m is False:
        why.append(Reason(kind="strength", title="Clean repayment record", weight=40,
                          text="No overdue payments in the last 12 months and no settled or written-off accounts."))
    c = {x.key: x for x in r.checks}
    if c.get("kyc_pan") and c["kyc_pan"].status == CheckStatus.PASS:
        why.append(Reason(kind="strength", title="Identity verified", text="PAN is verified.", weight=10))
    order = {"blocker": 0, "concern": 1, "missing": 2}
    why_not.sort(key=lambda x: (order[x.kind], -x.weight))
    why.sort(key=lambda x: -x.weight)
    # Keep one reason per title (a FAIL check and a low factor can describe the same thing).
    seen: set[str] = set()
    why_not = [x for x in why_not if not (x.title in seen or seen.add(x.title))]
    return why, why_not


# --------------------------------------------------------------------------
# Improvement plan (counterfactual re-assessment)
# --------------------------------------------------------------------------
def _next_breakpoint(score: int, curve) -> Optional[int]:
    for x, _ in curve:
        if x > score:
            return int(x)
    return None


def _improvements(r: Assessment, cfg: BankConfig, ctx: Optional[AffordCtx]) -> list[ImprovementAction]:
    a, e = r.applicant, cfg.eligibility
    cat = a.employment_category
    candidates: list[tuple[str, str, str, str, str, str, dict]] = []   # key, area, current, target, action, horizon, upd

    if r.metrics["credit_utilisation"].value is not None and r.metrics["credit_utilisation"].value > cfg.indicators.utilisation_warn:
        target = cfg.indicators.utilisation_warn * a.credit_card_limit
        candidates.append(("utilisation", "Credit card usage", f"{r.metrics['credit_utilisation'].display} used",
                           f"{cfg.indicators.utilisation_warn:.0%} or less",
                           f"Pay down card balances by about {inr(a.credit_card_balance - target)}.",
                           "1-2 billing cycles", {"credit_card_balance": math.floor(target)}))
    s = a.credit_score if a.credit_score is not None else a.credit_score_range_low
    if s is not None:
        t = max(e.credit_score.min_score, _next_breakpoint(s, cfg.scoring.curves["credit_score"]) or s)
        if t > s:
            candidates.append(("credit_score", "Credit score", str(s), f"{t} or above",
                               "Pay every EMI and card bill on time, keep card usage low, avoid new credit "
                               "applications and check the credit report for errors.",
                               "6-12 months", {"credit_score": t, "credit_score_range_low": None,
                                               "credit_score_range_high": None}))
    if a.age is not None and a.age < e.min_age:
        candidates.append(("age", "Age", f"{a.age} years", f"{e.min_age} years",
                           f"Re-apply after turning {e.min_age}.", f"{e.min_age - a.age} year(s)", {"age": e.min_age}))
    base_income = r.income.base_income
    if base_income is not None and base_income < e.min_net_monthly_income:
        v = float(e.min_net_monthly_income)
        candidates.append(("min_income", "Monthly income", inr(base_income), f"{inr(v)} or more (documented)",
                           f"This product needs a documented income of at least {inr(v)} a month (for example after "
                           "a salary revision); a product with a lower income threshold may suit better meanwhile.",
                           "When income rises", {"verified_monthly_income": v,
                                                 "net_monthly_income": max(v, a.net_monthly_income or 0.0)}))
    if a.verified_monthly_income is None and a.net_monthly_income is not None:
        candidates.append(("verify_income", "Income proof", "Income not verified", "Income verified",
                           "Submit salary slips, ITR or 6-12 months of bank statements so the full income counts.",
                           "Immediate", {"verified_monthly_income": a.net_monthly_income}))
    if cat is not None:
        mandatory, not_avail, unknown = document_gaps(a, cfg)
        gaps = not_avail + unknown
        if gaps:
            docs = dict(a.documents)
            docs.update({d: "available" for d in gaps})
            candidates.append(("documents", "Documents", f"{len(mandatory) - len(gaps)} of {len(mandatory)} available",
                               "All mandatory documents",
                               "Submit: " + ", ".join(DOCUMENT_TYPES[d] for d in gaps) + ".", "Immediate",
                               {"documents": {k: getattr(v, "value", v) for k, v in docs.items()}}))
    if ctx is not None and ctx.existing > 0:
        n = min(max(a.tenure_months, ctx.n_min), ctx.n_max)
        x_star = max_existing_emis(ctx, cfg, a.requested_amount, n)
        foir_ind = r.metrics["foir"].indicator.value
        if x_star is not None and x_star < ctx.existing - 1:
            target = math.floor(x_star / 100) * 100
        elif foir_ind in ("amber", "red"):
            target = math.floor(ctx.existing * 0.5 / 100) * 100
        else:
            target = None
        if target is not None:
            candidates.append(("existing_emis", "Existing EMIs", inr(ctx.existing), inr(target),
                               f"Prepay or close a loan to cut monthly EMIs by {inr(ctx.existing - target)}.",
                               "1-3 months", {"existing_monthly_emis": float(target)}))
    if a.enquiries_3m is not None and a.enquiries_3m >= 3:
        candidates.append(("enquiries", "Recent credit applications", f"{a.enquiries_3m} in 3 months", "0-1",
                           "Avoid new loan or card applications for the next 3-6 months.", "3-6 months",
                           {"enquiries_3m": 0}))
    if a.emi_bounces_6m:
        candidates.append(("bounces", "EMI bounces", f"{a.emi_bounces_6m} in 6 months", "None",
                           "Keep enough balance before each EMI date and use auto-debit.", "6 months",
                           {"emi_bounces_6m": 0}))
    if a.max_dpd_12m:
        candidates.append(("dpd", "Late payments", f"Up to {a.max_dpd_12m} days late (12 months)", "No late payments",
                           "Clear all overdue amounts and pay on time every month.", "12 months",
                           {"max_dpd_12m": 0}))
    if a.written_off_or_settled_36m:
        candidates.append(("write_off", "Settled / written-off account", "Present in 36 months", "None in 36 months",
                           "Repay any settled balance in full, obtain a no-dues certificate and ask the bureau to "
                           "update the status.", "Up to 36 months", {"written_off_or_settled_36m": False}))
    if cat is not None and a.years_in_employment is not None and a.years_in_employment < e.min_employment_years.get(cat):
        need = e.min_employment_years.get(cat)
        months = math.ceil((need - a.years_in_employment) * 12)
        candidates.append(("tenure_job", "Time in current work", f"{a.years_in_employment:g} years", f"{need:g} years",
                           f"Re-apply after about {months} more months in the current job or business.",
                           f"{months} months", {"years_in_employment": float(need)}))
    if a.pan is not None and a.pan_status.value != "verified":
        candidates.append(("kyc", "PAN verification", a.pan_status.value.replace("_", " "), "Verified",
                           "Complete PAN verification (link PAN with Aadhaar if it is inoperative).", "Immediate",
                           {"pan_status": "verified"}))
    if a.rate_type == "floating" and r.stress.stressed_foir is not None:
        candidates.append(("fixed_rate", "Rate type", "Floating", "Fixed",
                           "Ask for a fixed-rate option so rate rises cannot increase the EMI.", "At sanction",
                           {"rate_type": "fixed"}))

    base_amt = eligible_amount(r, cfg)
    out: list[ImprovementAction] = []
    combined: dict = {}
    for key, area, cur, tgt, act, hz, upd in candidates:
        try:
            x = rerun(r, cfg, **upd)
        except ValueError:
            continue
        combined.update(upd)
        out.append(ImprovementAction(
            key=key, area=area, current=cur, target=tgt, action=act, horizon=hz, quantified=True,
            score_before=r.readiness_score, score_after=x.readiness_score, amount_before=base_amt,
            amount_after=eligible_amount(x, cfg), classification_before=r.classification,
            classification_after=x.classification))

    def gain(i: ImprovementAction):
        return (CLASS_RANK[i.classification_after] - CLASS_RANK[i.classification_before],
                (i.score_after or 0) - (i.score_before or 0),
                (i.amount_after or 0) - (i.amount_before or 0))
    out.sort(key=gain, reverse=True)

    if len(out) > 1:
        try:
            x = rerun(r, cfg, **combined)
            out.append(ImprovementAction(
                key="combined", area="All of the above", current="-", target="-",
                action="Effect if every step above is completed.", horizon="Longest of the above", quantified=True,
                score_before=r.readiness_score, score_after=x.readiness_score, amount_before=base_amt,
                amount_after=eligible_amount(x, cfg), classification_before=r.classification,
                classification_after=x.classification))
        except ValueError:
            pass

    # Steps that matter but cannot be quantified without new data.
    if a.credit_score is None and a.credit_score_range_low is None and not a.no_credit_history:
        out.append(ImprovementAction(key="bureau", area="Credit report", current="Not available", target="Pulled",
                                     action="Give consent for a credit bureau check.", horizon="Immediate",
                                     quantified=False, note="Needed to complete the assessment."))
    if r.kyc.get("holder_type") and r.kyc["holder_type"] not in cfg.kyc.allowed_holder_types:
        out.append(ImprovementAction(key="pan_type", area="PAN type", current=r.kyc["holder_type_label"],
                                     target="Individual PAN", action="Apply with the applicant's own personal PAN; "
                                     "a business PAN needs a business-loan product.", horizon="Immediate",
                                     quantified=False, note="KYC requirement of this product."))
    if a.pan is None and cfg.kyc.require_pan:
        out.append(ImprovementAction(key="pan", area="PAN", current="Not provided", target="Provided and verified",
                                     action="Provide PAN with consent for KYC.", horizon="Immediate",
                                     quantified=False, note="Required for KYC; never used in scoring."))
    if (cat is not None and cat.value != "salaried" and r.income.coefficient_of_variation is None):
        out.append(ImprovementAction(key="statements", area="Income history", current="Not measured",
                                     target=f"{cfg.income_adjustment.min_observation_months}+ months of income",
                                     action="Share 6-12 months of bank statements so income stability is measured "
                                            "instead of assumed.", horizon="Immediate", quantified=False,
                                     note="Can raise or lower usable income depending on how stable it is."))
    return out


# --------------------------------------------------------------------------
# Main entry point
# --------------------------------------------------------------------------
def advise(r: Assessment, cfg: BankConfig) -> DecisionAdvice:
    a = r.applicant
    L, n_req = a.requested_amount, a.tenure_months
    ctx = build_ctx(r, cfg)
    e = cfg.eligibility
    limits: list[AffordabilityLimit] = []
    frontier: list[dict] = []
    sens: list[Sensitivity] = []
    options: list[LoanOption] = []
    n_used = eligible = recommended = rec_emi = max_any = binding = req_y = max_x = None
    blockers: list[str] = []

    if ctx is not None and ctx.n_max >= ctx.n_min:
        n_used = min(max(n_req, ctx.n_min), ctx.n_max)
        limits = limits_at(ctx, cfg, n_used)
        raw, binding = max_amount(ctx, cfg, n_used)
        eligible = round_down(raw)
        max_any = round_down(max_amount(ctx, cfg, ctx.n_max)[0])
        req_y = required_income(ctx, cfg, L, n_used)
        max_x = max_existing_emis(ctx, cfg, L, n_used)
        for n in sorted(set(list(range(max(6, ctx.n_min), ctx.n_max + 1, 6)) + [ctx.n_max, n_used])):
            amt, lab = max_amount(ctx, cfg, n)
            frontier.append({"tenure": n, "max_amount": amt, "binding": lab})
        for label, c2 in (("+INR 1,000 monthly usable income", replace(ctx, income=ctx.income + 1_000)),
                          ("-INR 1,000 existing EMIs", replace(ctx, existing=max(0.0, ctx.existing - 1_000))),
                          ("-1 percentage point interest rate", replace(ctx, rate=max(0.0, ctx.rate - 1)))):
            sens.append(Sensitivity(label=label, effect=max_amount(c2, cfg, n_used)[0] - raw))
        if n_used + 12 <= ctx.n_max:
            sens.append(Sensitivity(label="+12 months tenure", effect=max_amount(ctx, cfg, n_used + 12)[0] - raw))
        if min(L, eligible) >= e.loan_amount.min:
            recommended = min(L, eligible)
            rec_emi = calc.emi(recommended, a.annual_interest_rate, n_used)
    elif ctx is not None:
        blockers.append("No repayment period fits both the product's minimum tenure and the age limit at maturity.")

    # Hard criteria that a different amount or tenure would not fix.
    if recommended is not None:
        probe, skip = rerun(r, cfg, requested_amount=recommended, tenure_months=n_used), {"loan_amount", "tenure"}
    else:
        probe, skip = r, AMOUNT_CHECKS
    for c in probe.checks:
        if c.status == CheckStatus.FAIL and c.key not in skip:
            blockers.append(f"{CUSTOMER_LABEL.get(c.key, c.label)}: {c.observed} (required {c.threshold})")

    # Options, each re-assessed by the engine.
    options.append(_option(r, cfg, "requested", "As requested", f"{inr(L)} over {n_req} months"))
    if ctx is not None and n_used is not None:
        if recommended is not None and recommended < L:
            options.append(_option(r, cfg, "reduced", f"{inr(recommended)} over {n_used} months",
                                   "Largest amount that fits every limit at this tenure",
                                   requested_amount=recommended, tenure_months=n_used))
        n_full = tenure_for(ctx, cfg, L, n_used) if L <= e.loan_amount.max else None
        if n_full is not None and n_full > n_req:
            options.append(_option(r, cfg, "longer", f"Full amount over {n_full} months",
                                   "Same amount, lower EMI from a longer tenure (more total interest)",
                                   tenure_months=n_full))
        elif n_full is not None and n_full < n_req:
            options.append(_option(r, cfg, "within_tenure_limit", f"Full amount over {n_full} months",
                                   "Same amount within the maximum tenure allowed by product and age limits",
                                   tenure_months=n_full))
        if max_any and max_any > (eligible or 0) and ctx.n_max != n_used and max_any >= e.loan_amount.min \
                and (n_full is None or max_any < L):
            options.append(_option(r, cfg, "max_tenure", f"{inr(min(max_any, L))} over {ctx.n_max} months",
                                   "Largest amount at the longest allowed tenure",
                                   requested_amount=min(max_any, L), tenure_months=ctx.n_max))
        if max_x is not None and max_x < ctx.existing - 1:
            t = math.floor(max_x / 100) * 100
            options.append(_option(r, cfg, "reduce_emis", f"Full amount after cutting EMIs to {inr(t)}",
                                   f"Prepay or close loans worth {inr(ctx.existing - t)} a month of EMIs",
                                   existing_monthly_emis=float(t), tenure_months=n_used))
        if req_y is not None and req_y > ctx.income + 1 and ctx.cat_factor * ctx.var_factor > 0:
            v = math.ceil(req_y / (ctx.cat_factor * ctx.var_factor) / 100) * 100
            if v <= 10 * max(ctx.income, 1):
                options.append(_option(r, cfg, "more_income", f"Full amount with documented income of {inr(v)}",
                                       "Verified income at this level (for example with an earning co-applicant, "
                                       "if the bank's policy allows one)",
                                       verified_monthly_income=float(v),
                                       net_monthly_income=max(float(v), a.net_monthly_income or 0.0),
                                       tenure_months=n_used))
    feasible = [o for o in options if o.fails == 0 and o.classification != Classification.INSUFFICIENT]
    if feasible:
        # Prefer structures the bank can offer today over ones that need the customer to change finances.
        best = max(feasible, key=lambda o: (CLASS_RANK[o.classification], o.key not in CUSTOMER_CHANGE,
                                            o.key == "requested", o.amount, -o.total_interest))
        options = [o.model_copy(update={"recommended": o is best}) for o in options]

    why, why_not = _reasons(r, cfg, ctx, eligible)
    improvements = _improvements(r, cfg, ctx)

    # Verdict
    if ctx is None:
        miss = [x for x, v in (("usable income", r.income.adjusted_income), ("existing EMIs", a.existing_monthly_emis),
                               ("employment category", a.employment_category)) if v is None]
        verdict, tone = "More information is needed before an amount can be indicated", "grey"
        detail = "Missing: " + ", ".join(miss) + "."
    elif blockers:
        verdict, tone = "Not eligible at present", "red"
        detail = ("Affordability alone would support up to " + inr(eligible) + f" over {n_used} months once the "
                  "points below are resolved." if eligible and eligible >= e.loan_amount.min else
                  "The points below must be resolved first.")
    elif recommended is None:
        verdict, tone = "Current income and EMIs do not support the minimum loan amount", "red"
        detail = (f"The product minimum is {inr(e.loan_amount.min)}; the maximum supported now is "
                  f"{inr(eligible or 0)}. See how to improve below.")
    elif recommended >= L:
        tone = {Classification.LIKELY: "green", Classification.BORDERLINE: "amber"}.get(r.classification, "amber")
        verdict = f"The full {inr(L)} over {n_used} months fits within every affordability limit"
        detail = (f"Up to {inr(eligible)} is supported at this tenure. Overall assessment: "
                  f"{r.classification.value}.")
    else:
        verdict, tone = f"Up to {inr(recommended)} over {n_used} months can be considered", "amber"
        detail = f"That is {inr(L - recommended)} less than requested. Limiting factor: {binding}."
    if r.classification == Classification.INSUFFICIENT and tone != "red":
        tone = "grey"
        detail += " Some information is missing, so this is provisional."

    return DecisionAdvice(
        verdict=verdict, detail=detail, tone=tone, requested_amount=L, requested_tenure=n_req, tenure_used=n_used,
        max_tenure=None if ctx is None else ctx.n_max, max_eligible_amount=eligible,
        recommended_amount=None if blockers else recommended, recommended_emi=None if blockers else rec_emi,
        max_amount_any_tenure=max_any, binding_constraint=binding, limits=limits, blockers=list(dict.fromkeys(blockers)),
        why=why, why_not=why_not, options=options, improvements=improvements, sensitivities=sens,
        required_income=req_y, max_existing_emis=max_x, frontier=frontier)
