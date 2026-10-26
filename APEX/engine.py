"""Orchestrates one assessment: inputs + bank config -> Assessment.

The flow mirrors how a lender underwrites an unsecured personal loan:

    KYC (PAN) -> internal checks (negative list) -> bureau -> eligibility
    -> income & affordability (incl. stress test) -> banking behaviour
    -> documentation -> deviations -> readiness score (+ bank preferences)
    -> optional calibrated approval probability (bank's own model)

``assess`` is deterministic for a given applicant, configuration and saved
model. The only external field is the timestamp, which callers may pass.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

import calculations as calc
from eligibility import document_gaps, run_hard_checks
from kyc import kyc_summary
from models import (DOCUMENT_TYPES, STAGES, ApplicantInput, AprResult, Assessment, BankConfig, CapacityEstimate,
                    CheckResult, CheckStatus, Classification, Evidence, Finding, IncomeAdjustment, Indicator, MetricResult,
                    StageSummary, StressResult)
from policy_rules import apply_deviation_matrix, run_policy_rules, total_adjustment
from predictors import NullPredictor, OutcomePredictor
from scoring import classify, data_completeness, factor_inputs, income_stability, readiness

DISCLAIMER = (
    "APEX is a decision-support tool for bank staff. It uses fictional lender configurations and placeholder "
    "thresholds, does not approve or reject loans and does not guarantee approval. The readiness score is not a "
    "probability. An approval probability is shown only from a bank's own validated model and estimates how that "
    "bank decided similar past applications. Every finding is a potential factor unless confirmed by the lender. "
    "Final credit decisions remain with the lender."
)

SEVERITY = {CheckStatus.FAIL: 4, CheckStatus.DEVIATION: 3, CheckStatus.REVIEW: 2, CheckStatus.PASS: 1,
            CheckStatus.NOT_APPLICABLE: 0}


def inr(x: Optional[float]) -> str:
    return "-" if x is None else f"INR {x:,.0f}"


# --------------------------------------------------------------------------
# Indicative loan capacity
# --------------------------------------------------------------------------
def _capacity_at(rate: float, n: int, max_emi: float, income_ceiling: float, product_max: float,
                 buf_min: float, buf_max: float) -> tuple[float, float, float, str]:
    emi_supported = calc.principal_from_emi(max_emi, rate, n) if n > 0 else 0.0
    candidates = {"EMI affordability (after safety buffer)": emi_supported * (1 - buf_min),
                  "Income-multiple ceiling": income_ceiling,
                  "Product maximum": product_max}
    binding = min(candidates, key=candidates.get)
    upper = candidates[binding]
    lower = min(emi_supported * (1 - buf_max), income_ceiling, product_max)
    return emi_supported, lower, upper, binding


def compute_capacity(a: ApplicantInput, cfg: BankConfig, income: IncomeAdjustment,
                     tenure_cap: Optional[int], rate: Optional[float] = None) -> Optional[CapacityEstimate]:
    """Indicative maximum loan: min(EMI-supported x (1 - buffer), income multiple, product max)."""
    if income.adjusted_income is None or a.existing_monthly_emis is None or a.employment_category is None:
        return None
    rate = a.annual_interest_rate if rate is None else rate
    am = cfg.amount
    cap = cfg.eligibility.foir_cap.get(a.employment_category)
    n = min(a.tenure_months, int(cfg.eligibility.tenure_months.max))
    if tenure_cap is not None:
        n = min(n, tenure_cap)
    max_emi, exceeded = calc.max_new_emi(cap, income.adjusted_income, a.existing_monthly_emis)
    income_ceiling = calc.income_multiple_ceiling(12 * income.adjusted_income, am.income_multiple)
    product_max = cfg.eligibility.loan_amount.max
    emi_supported, lower, upper, binding = _capacity_at(rate, n, max_emi, income_ceiling, product_max,
                                                        am.safety_buffer_min, am.safety_buffer_max)
    sensitivity = []
    for label, r in (("Low", am.interest_rate_low), ("Base", am.interest_rate_base),
                     ("High (stress)", am.interest_rate_high), ("Assessment rate", rate)):
        _, lo, up, _ = _capacity_at(r, n, max_emi, income_ceiling, product_max,
                                    am.safety_buffer_min, am.safety_buffer_max)
        sensitivity.append({"scenario": label, "rate": r, "lower": lo, "upper": up})
    return CapacityEstimate(rate_used=rate, tenure_used=n, foir_cap=cap, max_new_emi=max_emi,
                            existing_emis_exceed_capacity=exceeded, emi_supported_principal=emi_supported,
                            income_multiple_ceiling=income_ceiling, product_max=product_max,
                            lower=lower, upper=upper, binding_constraint=binding, rate_sensitivity=sensitivity)


# --------------------------------------------------------------------------
# APR and stress test
# --------------------------------------------------------------------------
def compute_apr(a: ApplicantInput, cfg: BankConfig) -> AprResult:
    fee_pct = a.processing_fee_pct if a.processing_fee_pct is not None else cfg.charges.default_processing_fee_pct
    charges = a.requested_amount * fee_pct / 100 * (1 + cfg.charges.gst_on_fees_pct / 100) + (a.other_upfront_charges or 0)
    charges = min(charges, a.requested_amount * 0.5)
    i, nominal, effective = calc.apr(a.requested_amount, a.annual_interest_rate, a.tenure_months, charges)
    return AprResult(upfront_charges=charges, net_disbursal=a.requested_amount - charges, monthly_irr=i,
                     apr_nominal=nominal, apr_effective=effective)


def compute_stress(a: ApplicantInput, cfg: BankConfig, income: IncomeAdjustment, emi: float) -> StressResult:
    sp = cfg.stress
    shock = sp.rate_shock_bps if a.rate_type == "floating" else 0.0
    stressed_emi = calc.emi(a.requested_amount, a.annual_interest_rate + shock / 100, a.tenure_months)
    adj, existing, cat = income.adjusted_income, a.existing_monthly_emis, a.employment_category
    stressed_income = None if adj is None else adj * (1 - sp.income_shock_pct)
    stressed_foir = (calc.foir(existing, stressed_emi, stressed_income)
                     if stressed_income is not None and existing is not None else None)
    if income.coefficient_of_variation is not None:
        cv, src = income.coefficient_of_variation, "monthly income observations"
    elif a.income_variability in sp.self_reported_cv:
        cv, src = sp.self_reported_cv[a.income_variability], "self-reported variability (assumed CV)"
    elif cat is not None:
        cv, src = sp.default_cv_by_category.get(cat), "category default (assumption)"
    else:
        cv, src = None, "not available"
    threshold = p_short = p_neg = None
    if adj is not None and existing is not None and cv is not None and cat is not None:
        # Expected income before the variability haircut is the distribution mean.
        mean = adj / income.variability_factor if income.variability_factor else adj
        threshold = (existing + emi) / cfg.eligibility.foir_cap.get(cat)
        p_short = calc.lognormal_shortfall_probability(mean, cv, threshold)
        p_neg = calc.lognormal_shortfall_probability(mean, cv, existing + emi + (a.other_recurring_obligations or 0))
    return StressResult(stressed_income=stressed_income, stressed_emi=stressed_emi, stressed_foir=stressed_foir,
                        rate_shock_applied_bps=shock, cv_used=cv, cv_source=src, shortfall_threshold=threshold,
                        shortfall_probability=p_short, negative_residual_probability=p_neg,
                        expected_shortfall_months_per_year=None if p_short is None else 12 * p_short,
                        emi_rate_sensitivity=calc.emi_rate_sensitivity(a.requested_amount, a.annual_interest_rate,
                                                                       a.tenure_months))


def check_stress(a: ApplicantInput, cfg: BankConfig, stress: StressResult) -> CheckResult:
    sp = cfg.stress
    label = "Affordability stress test"
    cat = a.employment_category
    thr = (f"Stressed FOIR <= cap + {sp.max_stressed_foir_excess:.0%}; "
           f"monthly P(FOIR > cap) <= {sp.max_shortfall_probability:.0%}")
    base = dict(key="stress_test", label=label, threshold=thr, stage="Affordability", evidence=Evidence.ESTIMATE)
    if stress.stressed_foir is None or cat is None:
        return CheckResult(status=CheckStatus.REVIEW, observed="Not computable", missing_info=True,
                           detail="Income, existing EMIs or category missing; stress test not possible.", **base)
    cap = cfg.eligibility.foir_cap.get(cat)
    p = stress.shortfall_probability
    observed = f"Stressed FOIR {stress.stressed_foir:.1%}" + (f"; P(FOIR > cap) {p:.1%}" if p is not None else "")
    problems = []
    if stress.stressed_foir > cap + sp.max_stressed_foir_excess:
        problems.append(f"stressed FOIR exceeds {cap + sp.max_stressed_foir_excess:.0%}")
    if p is not None and p > sp.max_shortfall_probability:
        problems.append(f"estimated probability that FOIR exceeds the cap in a given month is {p:.0%}")
    if problems:
        return CheckResult(status=CheckStatus.REVIEW, observed=observed,
                           detail="Under stress (" + "; ".join(problems) + "). Review repayment resilience.", **base)
    return CheckResult(status=CheckStatus.PASS, observed=observed, detail="Resilient to the configured shocks.", **base)


# --------------------------------------------------------------------------
# Rule context
# --------------------------------------------------------------------------
def rule_context(a: ApplicantInput, values: dict[str, Any]) -> dict[str, Any]:
    ctx: dict[str, Any] = {k: getattr(a, k) for k in ApplicantInput.model_fields if k not in ("pan", "documents")}
    ctx["credit_score"] = a.credit_score if a.credit_score is not None else a.credit_score_range_low
    if a.no_credit_history:
        # A new-to-credit bureau report has no delinquency record ("no history"),
        # which is a known fact rather than missing data.
        for k, v in (("max_dpd_12m", 0), ("max_dpd_24m", 0), ("written_off_or_settled_36m", False),
                     ("credit_history_months", 0)):
            if ctx.get(k) is None:
                ctx[k] = v
    for k in ("employment_category", "employer_category"):
        v = ctx.get(k)
        ctx[k] = v.value if v is not None else None
    ctx.update(values)
    return ctx


def pipeline_summary(checks: list[CheckResult]) -> list[StageSummary]:
    out = []
    for stage in STAGES:
        cs = [c for c in checks if c.stage == stage]
        if not cs:
            continue
        counts = {s.value: sum(c.status == s for c in cs) for s in CheckStatus}
        worst = max(cs, key=lambda c: SEVERITY[c.status]).status
        out.append(StageSummary(stage=stage, status=worst, counts=counts))
    return out


# --------------------------------------------------------------------------
# Main entry point
# --------------------------------------------------------------------------
def assess(a: ApplicantInput, cfg: BankConfig, timestamp: Optional[str] = None, use_model: bool = True,
           predictor: Optional[OutcomePredictor] = None) -> Assessment:
    ts = timestamp or datetime.now(timezone.utc).isoformat(timespec="seconds")
    ind = cfg.indicators
    cat = a.employment_category

    # --- Calculations --------------------------------------------------------
    income = calc.adjust_income(a, cfg.income_adjustment)
    adj = income.adjusted_income
    emi = calc.emi_breakdown(a.requested_amount, a.annual_interest_rate, a.tenure_months)
    tenure_cap = calc.effective_tenure_cap(a.age, cfg.eligibility.max_age_at_maturity,
                                           int(cfg.eligibility.tenure_months.max))
    existing = a.existing_monthly_emis
    foir_value = calc.foir(existing, emi.emi, adj) if adj is not None and existing is not None else None
    utilisation = None
    if a.has_credit_card is not False and a.credit_card_limit and a.credit_card_balance is not None:
        utilisation = calc.credit_utilisation(a.credit_card_balance, a.credit_card_limit)
    lti = calc.loan_to_income(a.requested_amount, 12 * adj) if adj is not None else None
    residual = calc.residual_income(adj, existing, emi.emi) if adj is not None and existing is not None else None
    residual_adj = (calc.residual_income(adj, existing, emi.emi, a.other_recurring_obligations)
                    if residual is not None and a.other_recurring_obligations is not None else None)
    residual_used = residual_adj if residual_adj is not None else residual
    stability, _, stability_details = income_stability(a, cfg, income)
    capacity = compute_capacity(a, cfg, income, tenure_cap)
    apr_res = compute_apr(a, cfg)
    stress = compute_stress(a, cfg, income, emi.emi)
    completeness = data_completeness(a, cfg)
    inputs = factor_inputs(a, cfg, income, foir_value, utilisation, lti, residual_used, stability)
    doc_factor = inputs["documentation"]

    # --- Layer A: built-in checks + bank policy rules + deviation matrix -----
    ctx = rule_context(a, {
        "adjusted_monthly_income": adj, "emi": emi.emi, "foir": foir_value, "stressed_foir": stress.stressed_foir,
        "loan_to_income": lti, "credit_utilisation": utilisation, "residual_income": residual_used,
        "residual_share": (residual_used / adj) if adj and residual_used is not None else None,
        "income_stability": stability, "income_cv": stress.cv_used,
        "shortfall_probability": stress.shortfall_probability,
        "documentation_share": doc_factor[1] / 100 if doc_factor[1] is not None else None,
        "data_completeness": completeness.weighted_score,
        "age_at_maturity": None if a.age is None else a.age + a.tenure_months / 12,
        "apr_effective": apr_res.apr_effective,
        "abb_to_emi": (a.avg_monthly_balance / emi.emi) if a.avg_monthly_balance is not None and emi.emi else None,
    })
    rule_checks, adjustments, rule_notes = run_policy_rules(cfg, ctx, cat)
    checks = run_hard_checks(a, cfg, income, foir_value) + [check_stress(a, cfg, stress)] + rule_checks
    checks = apply_deviation_matrix(checks, cfg)
    checks.sort(key=lambda c: STAGES.index(c.stage) if c.stage in STAGES else len(STAGES))

    # --- Layer B: readiness score with bank preferences ----------------------
    base_score, coverage, factors = readiness(inputs, cfg)
    adj_points = total_adjustment(adjustments, cfg.score_adjustment_cap)
    score = None if base_score is None else max(0.0, min(100.0, base_score + adj_points))
    classification, reasons = classify(checks, score, coverage, completeness, cfg)
    if adjustments and score is not None:
        reasons.append(f"Bank preference adjustments of {adj_points:+.0f} points applied "
                       f"(base score {base_score:.0f}, cap +/-{cfg.score_adjustment_cap:g}).")

    # --- Metrics with indicators ---------------------------------------------
    check = {c.key: c for c in checks}
    m: dict[str, MetricResult] = {}

    def put(key, label, value, display, indicator, evidence, note=""):
        m[key] = MetricResult(key=key, label=label, value=value, display=display, indicator=indicator,
                              evidence=evidence, note=note)

    put("requested_amount", "Requested loan amount", a.requested_amount, inr(a.requested_amount),
        Indicator.GREEN if check["loan_amount"].status == CheckStatus.PASS else Indicator.RED, Evidence.INPUT,
        f"Product limits {check['loan_amount'].threshold}")
    put("adjusted_income", "Adjusted monthly income", adj, inr(adj),
        Indicator.INFO if adj is not None else Indicator.GREY, Evidence.CALCULATED, income.base_source)
    put("proposed_emi", "Proposed EMI", emi.emi, inr(emi.emi), Indicator.INFO, Evidence.CALCULATED,
        f"{a.tenure_months} months at {a.annual_interest_rate:g}% p.a.; total interest {inr(emi.total_interest)}")
    max_apr = cfg.charges.max_apr_pct
    put("apr", "APR (incl. charges)", apr_res.apr_effective, f"{apr_res.apr_effective:.2f}%",
        Indicator.AMBER if max_apr is not None and apr_res.apr_effective > max_apr else Indicator.INFO,
        Evidence.CALCULATED, f"Nominal {apr_res.apr_nominal:.2f}%; upfront charges {inr(apr_res.upfront_charges)}")

    if foir_value is None or cat is None:
        put("foir", "FOIR", None, "Not available", Indicator.GREY, Evidence.CALCULATED,
            "Needs income, existing EMIs and employment category")
    else:
        cap = cfg.eligibility.foir_cap.get(cat)
        i = (Indicator.RED if foir_value > cap else
             Indicator.AMBER if foir_value > cap - ind.foir_amber_margin else Indicator.GREEN)
        put("foir", "FOIR", foir_value, f"{foir_value:.1%}", i, Evidence.CALCULATED, f"Cap {cap:.0%} (configured)")

    if stress.stressed_foir is None or cat is None:
        put("stressed_foir", "Stressed FOIR", None, "Not available", Indicator.GREY, Evidence.ESTIMATE)
    else:
        cap = cfg.eligibility.foir_cap.get(cat)
        sf = stress.stressed_foir
        i = (Indicator.GREEN if sf <= cap else
             Indicator.AMBER if sf <= cap + cfg.stress.max_stressed_foir_excess else Indicator.RED)
        put("stressed_foir", "Stressed FOIR", sf, f"{sf:.1%}", i, Evidence.ESTIMATE,
            f"Income -{cfg.stress.income_shock_pct:.0%}" + (f", rate +{stress.rate_shock_applied_bps:g} bps"
                                                             if stress.rate_shock_applied_bps else " (fixed rate)"))

    p_short = stress.shortfall_probability
    if p_short is None:
        put("shortfall_probability", "P(FOIR > cap) in a month", None, "Not available", Indicator.GREY,
            Evidence.ESTIMATE)
    else:
        mx = cfg.stress.max_shortfall_probability
        i = Indicator.GREEN if p_short <= mx / 2 else Indicator.AMBER if p_short <= mx else Indicator.RED
        put("shortfall_probability", "P(FOIR > cap) in a month", p_short, f"{p_short:.1%} / month", i,
            Evidence.ESTIMATE, f"Income below (EMIs / cap) under lognormal income; CV {stress.cv_used:.2f} from {stress.cv_source}")

    if utilisation is None:
        na = inputs["credit_utilisation"][0].value == "not_applicable"
        put("credit_utilisation", "Credit utilisation", None, "No revolving credit" if na else "Not provided",
            Indicator.GREY, Evidence.CALCULATED)
    else:
        i = (Indicator.GREEN if utilisation <= ind.utilisation_warn else
             Indicator.AMBER if utilisation <= ind.utilisation_red else Indicator.RED)
        put("credit_utilisation", "Credit utilisation", utilisation, f"{utilisation:.1%}", i, Evidence.CALCULATED,
            f"Review above {ind.utilisation_warn:.0%} (indicator, not a rejection rule)")

    mult = cfg.amount.income_multiple
    if lti is None:
        put("loan_to_income", "Loan-to-income multiple", None, "Not available", Indicator.GREY, Evidence.CALCULATED)
    else:
        i = (Indicator.RED if lti > mult else
             Indicator.AMBER if lti > ind.lti_amber_fraction * mult else Indicator.GREEN)
        put("loan_to_income", "Loan-to-income multiple", lti, f"{lti:.2f}x", i, Evidence.CALCULATED,
            f"Ceiling {mult:g}x annual adjusted income")

    if residual is None:
        put("residual_income", "Residual income", None, "Not available", Indicator.GREY, Evidence.CALCULATED)
    else:
        i = (Indicator.RED if residual_used < 0 else
             Indicator.AMBER if residual_used < ind.min_residual_income else Indicator.GREEN)
        note = f"Review below {inr(ind.min_residual_income)}"
        if residual_adj is not None:
            note += f"; after other obligations (before: {inr(residual)})"
        put("residual_income", "Residual income", residual_used, inr(residual_used), i, Evidence.CALCULATED, note)

    if stability is None:
        put("income_stability", "Income stability", None, "Not available", Indicator.GREY, Evidence.ESTIMATE)
    else:
        i = (Indicator.GREEN if stability >= ind.stability_green else
             Indicator.AMBER if stability >= ind.stability_amber else Indicator.RED)
        put("income_stability", "Income stability", stability, f"{stability:.0f} / 100", i, Evidence.ESTIMATE,
            "; ".join(f"{d['component']}: {d['note']}" for d in stability_details))

    put("data_completeness", "Data completeness", completeness.weighted_score,
        f"{completeness.weighted_score:.0f}% (required {completeness.required_score:.0f}%)",
        Indicator.GREEN if completeness.required_score == 100 else Indicator.AMBER, Evidence.CALCULATED,
        "Reliability of this assessment; not a credit criterion")

    if capacity is None:
        put("indicative_max_loan", "Indicative maximum loan", None, "Not available", Indicator.GREY, Evidence.ESTIMATE,
            "Needs income, existing EMIs and employment category")
    else:
        i = (Indicator.GREEN if a.requested_amount <= capacity.lower else
             Indicator.AMBER if a.requested_amount <= capacity.upper else Indicator.RED)
        put("indicative_max_loan", "Indicative maximum loan", capacity.upper,
            f"{inr(capacity.lower)} - {inr(capacity.upper)}", i, Evidence.ESTIMATE,
            f"Rule-based estimate, not a lender-approved limit. Binding: {capacity.binding_constraint}")

    if score is None:
        put("readiness_score", "Readiness score", None, "Not available", Indicator.GREY, Evidence.ESTIMATE)
    else:
        sc = cfg.scoring
        i = (Indicator.GREEN if score >= sc.likely_min else
             Indicator.AMBER if score >= sc.borderline_min else Indicator.RED)
        adj_note = f" Includes {adj_points:+.0f} bank preference points." if adjustments else ""
        put("readiness_score", "Readiness score", score, f"{score:.0f} / 100", i, Evidence.ESTIMATE,
            f"Score coverage {coverage:.0%}. Rule-based; not a probability.{adj_note}")

    result = Assessment(
        applicant=a, bank_id=cfg.meta.bank_id, bank_name=cfg.meta.display_name,
        policy_version=cfg.meta.policy_version, policy_last_verified=cfg.meta.last_verified, timestamp=ts,
        income=income, emi=emi, effective_tenure_cap=tenure_cap, metrics=m, checks=checks, factors=factors,
        readiness_score=score, base_readiness_score=base_score, score_adjustments=adjustments,
        score_coverage=coverage, completeness=completeness, capacity=capacity, apr=apr_res, stress=stress,
        kyc=kyc_summary(a.pan, a.pan_consent, a.pan_status.value), pipeline=pipeline_summary(checks),
        classification=classification, classification_reasons=reasons,
        findings=_findings(a, cfg, checks, m, capacity, income, completeness, adjustments, rule_notes),
        next_steps=_next_steps(a, cfg, checks, m, capacity, tenure_cap),
        assumptions=_assumptions(a, cfg, income), disclaimer=DISCLAIMER)

    # A model fed mostly missing inputs would return a meaningless number, so no
    # probability is produced while the case is "Insufficient information".
    if use_model and classification != Classification.INSUFFICIENT:
        if predictor is None:
            from approval_model import ActiveModelPredictor
            predictor = ActiveModelPredictor()
        prob = predictor.predict(result, cfg)
        if prob is not None:
            result = result.model_copy(update={"probability": prob})
    return result


# --------------------------------------------------------------------------
# Diagnosis helpers
# --------------------------------------------------------------------------
def _findings(a, cfg, checks, m, capacity, income, completeness, adjustments, rule_notes) -> list[Finding]:
    out: list[Finding] = []
    for c in checks:
        src = "" if c.source == "built_in" else (" [custom rule]" if c.source == "custom_rule" else " [bank policy rule]")
        if c.status == CheckStatus.PASS:
            out.append(Finding(kind="met", text=f"{c.label}{src}: {c.observed} (required {c.threshold}).",
                               evidence=c.evidence))
        elif c.status == CheckStatus.FAIL:
            out.append(Finding(kind="rule_failure", text=f"{c.label}{src}: {c.observed}; required {c.threshold}. "
                                                         f"{c.detail}", evidence=c.evidence))
        elif c.status == CheckStatus.DEVIATION:
            out.append(Finding(kind="deviation", text=f"{c.label}{src}: {c.observed}; policy {c.threshold}. "
                                                      f"Deviation approval needed from {c.deviation_authority}.",
                               evidence=c.evidence))
        elif c.status == CheckStatus.REVIEW:
            out.append(Finding(kind="missing_info" if c.missing_info else "review",
                               text=f"{c.label}{src}: {c.detail}", evidence=c.evidence))
    for adj in adjustments:
        out.append(Finding(kind="preference", text=f"Bank preference '{adj.label}': {adj.points:+g} points"
                                                   + (f" ({adj.rationale})" if adj.rationale else ""),
                           evidence=Evidence.POLICY))
    for note in rule_notes:
        out.append(Finding(kind="missing_info", text=note, evidence=Evidence.INPUT))

    def risk(text):
        out.append(Finding(kind="risk_indicator", text=f"Potential factor: {text}", evidence=Evidence.ESTIMATE))

    for key in ("foir", "credit_utilisation", "loan_to_income", "residual_income", "income_stability",
                "stressed_foir", "shortfall_probability"):
        mm = m[key]
        if mm.indicator == Indicator.AMBER:
            risk(f"{mm.label} {mm.display} is in the review range ({mm.note}).")
        elif mm.indicator == Indicator.RED and key != "foir":
            risk(f"{mm.label} {mm.display} is outside the configured comfort range ({mm.note}).")
    if capacity is not None:
        if capacity.existing_emis_exceed_capacity:
            risk("Existing EMIs already use all FOIR capacity; no room for a new EMI on adjusted income.")
        elif a.requested_amount > capacity.upper:
            risk(f"Requested {inr(a.requested_amount)} exceeds the indicative capacity "
                 f"{inr(capacity.lower)} - {inr(capacity.upper)}.")
        if capacity.upper < cfg.eligibility.loan_amount.min:
            risk("Indicative capacity is below the product minimum loan amount.")
    if a.continuous_employment_12m is False:
        risk("Employment gap reported in the last 12 months.")
    if income.coefficient_of_variation is not None and income.coefficient_of_variation > cfg.income_adjustment.cv_haircut.tolerance:
        risk(f"Monthly income varies (CV {income.coefficient_of_variation:.2f}); usable income reduced to "
             f"{income.variability_factor:.0%}.")
    if a.verified_monthly_income is None and income.base_income is not None:
        risk("Income is unverified; a conservative verification factor was applied.")
    if a.credit_score is None and a.credit_score_range_low is not None:
        risk("Only a credit score range is known; the lower bound was used.")
    if a.enquiries_3m is not None and a.enquiries_3m >= 3:
        risk(f"{a.enquiries_3m} credit enquiries in 3 months may indicate credit hunger.")
    for it in completeness.items:
        if not it.required and it.status == "missing":
            out.append(Finding(kind="missing_info", text=f"Optional information not provided: {it.label}.",
                               evidence=Evidence.INPUT))
        elif it.required and it.status in ("missing", "partial") and it.field != "documents":
            out.append(Finding(kind="missing_info", text=f"Required information not provided: {it.label}.",
                               evidence=Evidence.INPUT))
    return out


def _next_steps(a, cfg, checks, m, capacity, tenure_cap) -> list[str]:
    steps: list[str] = []
    c = {x.key: x for x in checks}
    if c["foir"].status in (CheckStatus.FAIL, CheckStatus.DEVIATION) or (capacity and a.requested_amount > capacity.upper):
        if capacity and capacity.upper > 0:
            steps.append(f"Discuss a lower amount (indicative range {inr(capacity.lower)} - {inr(capacity.upper)} "
                         f"at {capacity.tenure_used} months), a longer tenure within limits, or reducing existing EMIs.")
        else:
            steps.append("Existing obligations leave no indicative capacity; discuss debt reduction before re-assessment.")
    for x in checks:
        if x.status == CheckStatus.DEVIATION:
            steps.append(f"Raise a deviation request for '{x.label}' to {x.deviation_authority}, with justification.")
    if c["kyc_pan"].status == CheckStatus.REVIEW:
        steps.append("Complete PAN KYC: obtain consent, record PAN and verify it in the bank's KYC system.")
    if c["age_at_maturity"].status == CheckStatus.FAIL and tenure_cap is not None:
        steps.append(f"Consider a tenure of at most {tenure_cap} months to stay within the age-at-maturity limit.")
    if c["tenure"].status == CheckStatus.FAIL:
        steps.append("Adjust the tenure to the product limits.")
    if c["loan_amount"].status == CheckStatus.FAIL:
        steps.append("Adjust the requested amount to the product limits.")
    if c["employment_category"].status == CheckStatus.FAIL:
        steps.append("This product is not configured for the applicant's employment category; consider other products.")
    _, not_avail, unknown = document_gaps(a, cfg)
    if not_avail or unknown:
        steps.append("Request or record documents: " + ", ".join(DOCUMENT_TYPES[d] for d in not_avail + unknown) + ".")
    if c["credit_score"].status == CheckStatus.REVIEW or any(
            x.stage == "Bureau" and x.missing_info for x in checks):
        steps.append("Obtain a consented credit bureau report (score, DPD history, enquiries).")
    if a.verified_monthly_income is None and a.net_monthly_income is not None:
        steps.append("Verify income against salary slips, ITR or bank statements.")
    if m["credit_utilisation"].indicator in (Indicator.AMBER, Indicator.RED):
        steps.append("Review revolving credit balances and repayment behaviour on statements.")
    if m["income_stability"].indicator in (Indicator.AMBER, Indicator.RED) or c["stress_test"].status == CheckStatus.REVIEW:
        steps.append("Review 6-12 months of bank statements for income regularity and repayment resilience.")
    if any(x.missing_info for x in checks if x.key not in ("documents", "credit_score", "kyc_pan")
           and x.stage != "Bureau"):
        steps.append("Collect the missing required inputs and re-run the assessment.")
    steps.append("Proceed to the lender's full underwriting process; the final credit decision rests with the lender.")
    return list(dict.fromkeys(steps))


def _assumptions(a, cfg, income: IncomeAdjustment) -> list[str]:
    am, ia, st = cfg.amount, cfg.income_adjustment, cfg.stress
    return [
        f"Bank configuration '{cfg.meta.display_name}' is FICTIONAL (policy {cfg.meta.policy_version}, "
        f"last verified {cfg.meta.last_verified}). {cfg.meta.source_note}",
        f"Proposed EMI and indicative capacity use the assessment rate of {a.annual_interest_rate:g}% p.a.; "
        f"sensitivity shown at {am.interest_rate_low:g}% / {am.interest_rate_base:g}% / {am.interest_rate_high:g}%.",
        f"Safety buffer of {am.safety_buffer_min:.0%}-{am.safety_buffer_max:.0%} applied to the EMI-supported amount; "
        "capacity is shown as a range.",
        f"Income adjustment: category factors {ia.category_factor.salaried:g}/{ia.category_factor.self_employed:g}/"
        f"{ia.category_factor.informal:g} (salaried/self-employed/informal), unverified factor {ia.unverified_factor:g}.",
        f"Stress test: income -{st.income_shock_pct:.0%}; rate +{st.rate_shock_bps:g} bps for floating-rate loans. "
        "Monthly P(FOIR > cap) assumes lognormal monthly income with the stated CV.",
        f"APR includes processing fee plus {cfg.charges.gst_on_fees_pct:g}% GST on fees and other upfront charges, "
        "computed as the internal rate of return of the net disbursal.",
        "FOIR uses EMIs only; other recurring obligations affect residual income, not FOIR.",
        "Age is in completed years; the age-at-maturity check may be off by up to 11 months.",
        "Bureau and bank-statement data are as entered by the manager from consented reports.",
        "PAN is used for KYC only, shown masked, and never used in any score or model.",
        "Scoring weights, curves and band thresholds are illustrative prototype assumptions, not empirically validated.",
        "The readiness score is not a probability. An approval probability appears only from the bank's own model "
        "that passed its validation gate, and reflects that bank's past decisions.",
        "Loan purpose is recorded for reference only and is not used in any calculation.",
    ]
