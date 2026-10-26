"""Preliminary screening report generation (Markdown and JSON)."""
from __future__ import annotations

import json
from typing import Optional

from engine import inr
from models import (DOCUMENT_TYPES, EMPLOYMENT_LABELS, Assessment, CheckStatus, DecisionAdvice, DocStatus,
                    ImprovementAction, RateRecommendation)

INDICATOR_TEXT = {"green": "Meets criterion", "amber": "Review", "red": "Not met",
                  "grey": "Missing / N.A.", "info": "Info"}


def _v(x, fmt="{}"):
    return "Not provided" if x is None else fmt.format(x)


def _yn(x):
    return "Not provided" if x is None else ("Yes" if x else "No")


def _table(headers: list[str], rows: list[list[str]]) -> str:
    esc = lambda s: str(s).replace("|", "\\|").replace("\n", " ")  # noqa: E731
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    out += ["| " + " | ".join(esc(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def _amt(x):
    return "-" if x is None else inr(x)


def _impact(i: ImprovementAction) -> str:
    if not i.quantified:
        return i.note or "Not quantified"
    parts = []
    if i.score_before is not None and i.score_after is not None and abs(i.score_after - i.score_before) >= 0.5:
        parts.append(f"score {i.score_before:.0f} -> {i.score_after:.0f}")
    if i.amount_after is not None and (i.amount_before or 0) != i.amount_after:
        parts.append(f"max amount {_amt(i.amount_before)} -> {_amt(i.amount_after)}")
    if i.classification_after is not None and i.classification_after != i.classification_before:
        parts.append(f"{i.classification_before.value} -> {i.classification_after.value}")
    return "; ".join(parts) or "Small effect on this assessment"


def decision_section(d: DecisionAdvice) -> list[str]:
    """Manager-report section: how much, why, why not, options and improvement plan."""
    L = ["## 6a. Decision support: how much, why and why not", "",
         f"**{d.verdict}.** {d.detail}", ""]
    L += [f"- Requested: {inr(d.requested_amount)} over {d.requested_tenure} months",
          f"- Conditional affordability ceiling at {d.tenure_used or '-'} months: {_amt(d.max_eligible_amount)}"
          + (f" (limited by: {d.binding_constraint})" if d.binding_constraint else ""),
          f"- Indicative amount after rule checks: {_amt(d.recommended_amount)}"
          + (f", EMI {inr(d.recommended_emi)}" if d.recommended_emi else ""),
          f"- Maximum at the longest allowed tenure ({d.max_tenure or '-'} months): {_amt(d.max_amount_any_tenure)}",
          f"- Usable income at which the full request fits: {_amt(d.required_income)}; existing EMIs at which it "
          f"fits: {_amt(d.max_existing_emis)}", ""]
    if d.limits:
        L += ["**Affordability limits (the lowest one sets the amount)**", "",
              _table(["Limit", "Maximum amount", "Binding", "Explanation"],
                     [[x.label, _amt(x.max_amount), "Yes" if x.binding else "", x.customer_text] for x in d.limits]), ""]
    if d.blockers:
        L += ["**Must be resolved first**", ""] + [f"- {b}" for b in d.blockers] + [""]
    L += ["The affordability ceiling is conditional on the entered rate and verified inputs; it is not a loan offer.", ""]
    L += ["**Why (strengths)**", ""] + ([f"- {x.title}: {x.text}" for x in d.why] or ["- None identified"]) + [""]
    L += ["**Why not (concerns)**", ""] + ([f"- [{x.kind}] {x.title}: {x.text}" for x in d.why_not] or ["- None"]) + [""]
    if d.options:
        L += ["**Loan options (each re-assessed)**", "",
              _table(["Option", "Amount", "Tenure", "EMI", "Total interest", "FOIR", "Outcome", "Recommended"],
                     [[o.title, inr(o.amount), f"{o.tenure_months} m", inr(o.emi), inr(o.total_interest),
                       "-" if o.foir is None else f"{o.foir:.1%}", o.classification.value,
                       "Yes" if o.recommended else ""] for o in d.options]), ""]
    if d.improvements:
        L += ["**Where to improve (each effect measured by re-running the assessment)**", "",
              _table(["Area", "Now", "Target", "What to do", "When", "Effect"],
                     [[i.area, i.current, i.target, i.action, i.horizon, _impact(i)] for i in d.improvements]), ""]
    if d.sensitivities:
        L += ["**Sensitivity of the maximum amount**", ""]
        L += [f"- {s.label}: {'+' if s.effect >= 0 else '-'}{inr(abs(s.effect))}" for s in d.sensitivities] + [""]
    return L


def pricing_section(p: RateRecommendation) -> list[str]:
    L = ["## 6b. Illustrative interest-rate recommendation", ""]
    if p.status != "available":
        L += ["No rate suggested: " + " ".join(p.reasons), ""]
        return L
    L += [f"- Fictional bank example base rate: {p.base_rate_pct:g}% p.a.",
          f"- Example permitted range: {p.range_low_pct:g}%–{p.range_high_pct:g}% p.a."]
    L += [f"- {f.label}: {f.adjustment_pp:+g} percentage points. {f.explanation}" for f in p.factors]
    L += [f"- Unbounded calculation: {p.unclamped_rate_pct:g}% p.a.",
          f"- **Illustrative suggested rate: {p.recommended_rate_pct:g}% p.a.**", "",
          "This is a fictional policy example, not a personalised AI prediction, bank quote, approval, or "
          "Key Fact Statement. Reassess the loan amount and EMI at this rate before discussing a scenario.", ""]
    return L


def customer_letter(r: Assessment, d: DecisionAdvice) -> str:
    """Plain-language summary the manager can share with the customer. No PAN, scores or model outputs."""
    a = r.applicant
    L = [f"# Your loan pre-assessment - {r.bank_name}", "",
         f"Reference: {a.applicant_id}  ",
         f"Date: {r.timestamp[:10]}", "",
         "Dear Applicant,", "",
         f"Thank you for your interest in a personal loan of **{inr(a.requested_amount)}** over "
         f"**{a.tenure_months} months**. We have done a preliminary check of your application using the "
         "information you shared. This letter explains what we can consider, why, and what could strengthen "
         "your application.", "",
         "## What we can consider", "", f"**{d.verdict}.**", "", d.detail, ""]
    if d.recommended_amount is not None:
        L += [f"- Amount: **{inr(d.recommended_amount)}**",
              f"- Repayment period: {d.tenure_used} months",
              f"- Estimated EMI: about {inr(d.recommended_emi)} a month at the assumed "
              f"{a.annual_interest_rate:g}% p.a. rate (not a rate quote)", ""]
    if d.limits:
        binding = next((x for x in d.limits if x.binding), None)
        L += ["## How this amount was worked out", "",
              "We check that the loan stays affordable under several limits and use the lowest one:", ""]
        L += [f"- {x.customer_text}" for x in d.limits if x.key not in ("product_max",)]
        if binding is not None:
            L += ["", f"In your case the deciding limit is: *{binding.label}*."]
        L += [""]
    if d.why:
        L += ["## What works in your favour", ""] + [f"- **{x.title}.** {x.text}" for x in d.why[:6]] + [""]
    if d.why_not:
        L += ["## What is holding the application back", ""]
        L += [f"- **{x.title}.** {x.text}" for x in d.why_not
              if x.kind == "blocker" or (x.kind == "concern" and not x.title.startswith("Needs review"))][:6]
        if any(x.title.startswith("Needs review") for x in d.why_not):
            L += ["- Some items need a manual check by our credit team before a decision can be made."]
        missing = [x for x in d.why_not if x.kind == "missing"]
        if missing:
            L += ["", "We also need the following information:", ""] + [f"- {x.title.replace('Information needed: ', '')}"
                                                                        for x in missing]
        L += [""]
    opts = [o for o in d.options if o.key != "requested" and o.fails == 0]
    if opts:
        L += ["## Options you can discuss with us", ""]
        L += [f"- **{o.title}** - {o.description}; EMI about {inr(o.emi)} a month, total interest {inr(o.total_interest)}."
              for o in opts] + [""]
    steps = [i for i in d.improvements if i.key != "combined"]
    if steps:
        L += ["## How you can strengthen your application", ""]
        for i in steps[:6]:
            gain = ""
            if i.quantified and i.amount_after is not None and i.amount_before is not None and i.amount_after > i.amount_before:
                gain = f" This could raise the amount we can consider to about {inr(i.amount_after)}."
            L += [f"- **{i.area}** ({i.horizon}): {i.action}{gain}"]
        L += [""]
    L += ["## Please note", "",
          "- This is a preliminary assessment, not a loan sanction or a guarantee of approval. The final decision is "
          "made by the bank after full verification of your documents and credit report.",
          "- Amounts and EMIs are estimates at an assumed interest rate; your Key Fact Statement will show the exact "
          "annual percentage rate and charges.",
          "- You may ask us for the reasons behind any decision, and you can raise a concern through the bank's "
          "grievance redressal process.", "",
          "Warm regards,  ", f"Loan Desk, {r.bank_name}", "",
          "*Prepared with APEX decision support. Fictional demonstration bank; synthetic data only.*", ""]
    return "\n".join(L)


def report_markdown(r: Assessment, advice: Optional[DecisionAdvice] = None,
                    pricing: Optional[RateRecommendation] = None) -> str:
    a = r.applicant
    L: list[str] = []
    L += ["# APEX Preliminary Screening Report", "",
          f"> **{r.disclaimer}**", "",
          f"**Assessment timestamp (UTC):** {r.timestamp}  ",
          f"**Policy version:** {r.policy_version} (last verified {r.policy_last_verified})", ""]

    L += ["## 1. Applicant reference", "", f"Applicant ID: `{a.applicant_id}` (synthetic test identifier)",
          f"Loan purpose (record only, not scored): {a.loan_purpose or 'Not recorded'}", ""]

    L += ["## 2. Selected bank configuration", "", f"{r.bank_name} - **fictional demonstration bank**.",
          f"Policy version {r.policy_version}; last verified {r.policy_last_verified}.", ""]

    cat = EMPLOYMENT_LABELS.get(a.employment_category, "Not provided") if a.employment_category else "Not provided"
    if a.no_credit_history:
        score = "No credit history (new to credit)"
    elif a.credit_score is not None:
        score = str(a.credit_score)
    elif a.credit_score_range_low is not None:
        score = f"Range {a.credit_score_range_low}-{a.credit_score_range_high}"
    else:
        score = "Not provided"
    card = ("No credit card" if a.has_credit_card is False else
            f"Limit {_v(a.credit_card_limit, 'INR {:,.0f}')}, balance {_v(a.credit_card_balance, 'INR {:,.0f}')}")
    L += ["## 3. Input summary", "", _table(["Input", "Value"], [
        ["Age", _v(a.age, "{} years")],
        ["Employment category", cat],
        ["Years in employment / business", _v(a.years_in_employment, "{:g}")],
        ["Requested amount", inr(a.requested_amount)],
        ["Tenure", f"{a.tenure_months} months"],
        ["Assumed interest rate", f"{a.annual_interest_rate:g}% p.a."],
        ["Declared net monthly income", _v(a.net_monthly_income, "INR {:,.0f}")],
        ["Verified monthly income", _v(a.verified_monthly_income, "INR {:,.0f}")],
        ["Monthly income observations", f"{len(a.monthly_income_observations or [])} months"],
        ["Self-reported income variability", a.income_variability.value],
        ["Existing monthly EMIs", _v(a.existing_monthly_emis, "INR {:,.0f}")],
        ["Active loans", _v(a.active_loan_count)],
        ["Credit card", card],
        ["Credit score", score],
        ["Other recurring obligations", _v(a.other_recurring_obligations, "INR {:,.0f}")],
        ["PAN (masked)", f"{r.kyc['pan_masked']} - status {r.kyc['status'].replace('_', ' ')}, "
                         f"consent {'yes' if r.kyc['consent'] else 'no'}"],
        ["Max DPD 12 / 24 months", f"{_v(a.max_dpd_12m)} / {_v(a.max_dpd_24m)}"],
        ["Write-off / settlement (36 m)", _yn(a.written_off_or_settled_36m)],
        ["Enquiries 3 / 6 months", f"{_v(a.enquiries_3m)} / {_v(a.enquiries_6m)}"],
        ["Credit history length", _v(a.credit_history_months, "{} months")],
        ["EMI bounces (6 m)", _v(a.emi_bounces_6m)],
        ["Average monthly balance", _v(a.avg_monthly_balance, "INR {:,.0f}")],
        ["Existing customer / negative-list hit", f"{_yn(a.existing_customer)} / {_yn(a.internal_negative_list_hit)}"],
        ["Rate type / processing fee", f"{a.rate_type} / {_v(a.processing_fee_pct, '{:g}%')}"],
    ]), "", "Inputs are as entered by the bank manager and are unverified unless stated. "
            "PAN is shown masked and is never used in scoring.", ""]

    L += ["## 4. Calculated financial metrics", "", _table(
        ["Metric", "Value", "Status", "Evidence", "Note"],
        [[m.label, m.display, INDICATOR_TEXT[m.indicator.value], m.evidence.value, m.note] for m in r.metrics.values()]),
        "", f"Proposed EMI {inr(r.emi.emi)}; total repayment {inr(r.emi.total_repayment)}; "
            f"total interest {inr(r.emi.total_interest)}.", "", "**Income adjustment**", ""]
    L += [f"- {s}" for s in r.income.steps] + [""]
    if r.capacity:
        c = r.capacity
        L += ["**Indicative loan capacity (rule-based estimate - not a lender-approved limit)**", "",
              f"- FOIR cap {c.foir_cap:.0%}; maximum new EMI {inr(c.max_new_emi)}; tenure used {c.tenure_used} months.",
              f"- EMI-supported principal {inr(c.emi_supported_principal)}; income-multiple ceiling "
              f"{inr(c.income_multiple_ceiling)}; product maximum {inr(c.product_max)}.",
              f"- Indicative range {inr(c.lower)} - {inr(c.upper)} (binding: {c.binding_constraint}).", "",
              _table(["Rate scenario", "Rate", "Lower", "Upper"],
                     [[s["scenario"], f"{s['rate']:g}%", inr(s["lower"]), inr(s["upper"])] for s in c.rate_sensitivity]),
              ""]
    ap, st = r.apr, r.stress
    L += ["**Pricing (APR, Key Fact Statement basis)**", "",
          f"- Upfront charges {inr(ap.upfront_charges)}; net disbursal {inr(ap.net_disbursal)}.",
          f"- Monthly IRR {ap.monthly_irr:.4%}; APR nominal {ap.apr_nominal:.2f}%, effective {ap.apr_effective:.2f}%.",
          "", "**Stress test**", "",
          f"- Stressed EMI {inr(st.stressed_emi)} (rate shock {st.rate_shock_applied_bps:g} bps); stressed income "
          f"{inr(st.stressed_income)}; stressed FOIR {_v(st.stressed_foir, '{:.1%}')}.",
          f"- Income CV {_v(st.cv_used, '{:.2f}')} ({st.cv_source}); FOIR-safe income {inr(st.shortfall_threshold)}; "
          f"monthly P(FOIR > cap) {_v(st.shortfall_probability, '{:.1%}')}; expected months per year above cap "
          f"{_v(st.expected_shortfall_months_per_year, '{:.1f}')}.",
          f"- EMI sensitivity: +1 percentage point in rate adds about {inr(st.emi_rate_sensitivity)} per month.", ""]

    L += ["## 5. Hard eligibility checks", "", "**Underwriting pipeline**", "",
          _table(["Stage", "Outcome", "PASS / DEVIATION / REVIEW / FAIL"],
                 [[s.stage, s.status.value, f"{s.counts['PASS']} / {s.counts['DEVIATION']} / {s.counts['REVIEW']} / "
                                            f"{s.counts['FAIL']}"] for s in r.pipeline]), "",
          _table(["Stage", "Check", "Source", "Status", "Observed", "Required", "Detail"],
                 [[c.stage, c.label, c.source.replace("_", " "), c.status.value + (
                     f" ({c.deviation_authority})" if c.deviation_authority else ""), c.observed, c.threshold, c.detail]
                  for c in r.checks]), ""]

    L += ["## 6. Readiness score and classification", "",
          f"**Classification: {r.classification.value}**", ""]
    L += [f"- {x}" for x in r.classification_reasons]
    score = "Not available" if r.readiness_score is None else f"{r.readiness_score:.1f} / 100"
    L += ["", f"Preliminary readiness score: **{score}** (score coverage {r.score_coverage:.0%}). "
              "This score is a rule-based readiness indicator, **not** a probability of approval.", "",
          _table(["Factor", "Status", "Sub-score", "Weight (effective)", "Contribution", "Input"],
                 [[f.label, f.status.value, "-" if f.score is None else f"{f.score:.1f}",
                   f"{f.weight:.2f} ({f.effective_weight:.3f})", f"{f.contribution:.1f}", f.raw_input]
                  for f in r.factors]),
          "", f"Data completeness: {r.completeness.weighted_score:.0f}% overall, "
              f"{r.completeness.required_score:.0f}% of required inputs (reported separately from readiness).", ""]
    if r.score_adjustments:
        L += [f"Base score {r.base_readiness_score:.1f}; bank preference adjustments:", ""]
        L += [f"- {s.label}: {s.points:+g} points" for s in r.score_adjustments] + [""]
    p = r.probability
    L += ["**Approval probability (bank's statistical model)**", ""]
    if p is None:
        L += ["Not available: no validated model for this bank. APEX does not display an approval probability "
              "without a model trained on the bank's own decisions that passed its validation gate.", ""]
    else:
        L += [f"- Estimated probability of approval: **{p.probability:.0%}** (95% interval {p.lower_95:.0%} - "
              f"{p.upper_95:.0%}); scorecard {p.scorecard_points:.0f} points.",
              f"- Model {p.model_id}, trained on {p.trained_on}; {p.n_train} training cases; out-of-time AUC "
              f"{p.test_auc:.3f}; calibration error {p.test_ece:.3f}.",
              "- Reflects how this bank decided similar past applications; it does not override the rule outcome."]
        if p.synthetic:
            L += ["- **WARNING: trained on synthetic data. The number is a demonstration, not an estimate.**"]
        if p.policy_changed_since_training:
            L += [f"- Policy changed since training ({p.policy_version_at_training} -> {r.policy_version}); "
                  "retraining recommended."]
        L += [f"- Factor: {c}" for c in p.reason_codes] + [""]

    if advice is not None:
        L += decision_section(advice)
    if pricing is not None:
        L += pricing_section(pricing)

    L += ["## 7. Potential concerns", "", "**Confirmed rule failures (against the fictional configuration)**", ""]
    fails = [f.text for f in r.findings if f.kind == "rule_failure"]
    L += [f"- {t}" for t in fails] or ["- None"]
    L += ["", "**Deviations requiring approval**", ""]
    devs = [f.text for f in r.findings if f.kind == "deviation"]
    L += [f"- {t}" for t in devs] or ["- None"]
    L += ["", "**Items requiring manual review**", ""]
    revs = [f.text for f in r.findings if f.kind == "review"]
    L += [f"- {t}" for t in revs] or ["- None"]
    L += ["", "**Estimated risk indicators (potential factors, not confirmed by a lender)**", ""]
    risks = [f.text for f in r.findings if f.kind == "risk_indicator"]
    L += [f"- {t}" for t in risks] or ["- None"]
    L += [""]

    L += ["## 8. Missing documentation and information", ""]
    docs = [f"- {label}: {a.doc(k).value.replace('_', ' ')}" for k, label in DOCUMENT_TYPES.items()
            if a.doc(k) != DocStatus.AVAILABLE]
    L += ["**Documents not recorded as available**", ""] + (docs or ["- None"]) + [""]
    missing = [f.text for f in r.findings if f.kind == "missing_info"]
    L += ["**Missing information**", ""] + ([f"- {t}" for t in missing] or ["- None"])
    L += ["", "Missing documents or information are remediable gaps; they are not evidence of fraud or ineligibility.", ""]

    L += ["## 9. Suggested manual review actions", ""] + [f"{i}. {s}" for i, s in enumerate(r.next_steps, 1)] + [""]
    L += ["## 10. Assumptions and limitations", ""] + [f"- {s}" for s in r.assumptions]
    L += ["- This is a prototype, not production-ready banking software, and has been exercised with synthetic data only.",
          "", "---", f"*Generated by APEX prototype. {r.disclaimer}*", ""]
    return "\n".join(L)


def report_json(r: Assessment, pricing: Optional[RateRecommendation] = None) -> str:
    data = r.model_dump(mode="json")
    if pricing is not None:
        data["pricing_recommendation"] = pricing.model_dump(mode="json")
    return json.dumps(data, indent=2, ensure_ascii=False)


def summary_counts(r: Assessment) -> dict[str, int]:
    out = {s.value: 0 for s in CheckStatus}
    for c in r.checks:
        out[c.status.value] += 1
    return out
