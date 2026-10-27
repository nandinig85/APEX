"""Bank configuration loading and Layer A: hard eligibility checks.

Each check returns PASS / FAIL / REVIEW / NOT APPLICABLE. Missing information
always yields REVIEW (flagged ``missing_info=True``), never FAIL.
"""
from __future__ import annotations

import copy
from datetime import date
from pathlib import Path
from typing import Optional

import yaml

from kyc import HOLDER_TYPES, holder_type, mask_pan
from age_math import add_months, maturity_age_years, maturity_date
from models import (DOCUMENT_TYPES, EMPLOYMENT_LABELS, ApplicantInput, BankConfig, CheckResult,
                    CheckStatus, DocStatus, IncomeAdjustment, PanStatus)
from policy_rules import custom_version_suffix, load_overlay, validate_config_rules

CONFIG_DIR = Path(__file__).resolve().parent / "config"
DEFAULTS_FILE = "_defaults.yaml"

# Documents a manager may mark "not applicable" (e.g. GST for a business below
# the registration threshold). For any other mandatory document, "not
# applicable" is treated as "not available".
CONDITIONAL_DOCUMENTS = {"gst", "other"}


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------
def deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _finalise(data: dict, overlay_dir: Optional[Path]) -> BankConfig:
    """Validate a merged config, add Policy Studio custom rules, and check every rule."""
    cfg = BankConfig.model_validate(data)
    if overlay_dir is not None:
        custom = load_overlay(overlay_dir, cfg.meta.bank_id)
        if custom:
            cfg = BankConfig.model_validate({**data, "custom_rules": custom})
            meta = cfg.meta.model_copy(update={
                "policy_version": cfg.meta.policy_version + custom_version_suffix(cfg.custom_rules)})
            cfg = cfg.model_copy(update={"meta": meta})
    validate_config_rules(cfg)
    return cfg


def load_bank_config(path: Path | str, defaults_path: Optional[Path | str] = None,
                     with_overlay: bool = True) -> BankConfig:
    path = Path(path)
    defaults_path = Path(defaults_path) if defaults_path else path.parent / DEFAULTS_FILE
    defaults = yaml.safe_load(defaults_path.read_text(encoding="utf-8")) if defaults_path.exists() else {}
    bank = yaml.safe_load(path.read_text(encoding="utf-8"))
    return _finalise(deep_merge(defaults, bank), path.parent if with_overlay else None)


def config_from_dict(data: dict, defaults_path: Path | str = CONFIG_DIR / DEFAULTS_FILE) -> BankConfig:
    defaults = yaml.safe_load(Path(defaults_path).read_text(encoding="utf-8"))
    return _finalise(deep_merge(defaults, data), None)


def available_banks(config_dir: Path | str = CONFIG_DIR, with_overlay: bool = True) -> dict[str, BankConfig]:
    """All bank configurations in ``config_dir`` (files starting with '_' are skipped)."""
    banks = {}
    for p in sorted(Path(config_dir).glob("*.yaml")):
        if p.name.startswith("_"):
            continue
        cfg = load_bank_config(p, with_overlay=with_overlay)
        banks[cfg.meta.bank_id] = cfg
    return banks


def bank_file(bank_id: str, config_dir: Path | str = CONFIG_DIR) -> Path:
    for p in sorted(Path(config_dir).glob("*.yaml")):
        if not p.name.startswith("_") and (yaml.safe_load(p.read_text(encoding="utf-8")) or {}).get(
                "meta", {}).get("bank_id") == bank_id:
            return p
    raise KeyError(bank_id)


# --------------------------------------------------------------------------
# Hard checks
# --------------------------------------------------------------------------
def _missing(key: str, label: str, threshold: str, what: str) -> CheckResult:
    return CheckResult(key=key, label=label, status=CheckStatus.REVIEW, observed="Not provided",
                       threshold=threshold, missing_info=True,
                       detail=f"{what} not provided; cannot be checked. This is missing information, not a failure.")


def _fmt_inr(x: float) -> str:
    return f"INR {x:,.0f}"


def check_min_age(a: ApplicantInput, cfg: BankConfig) -> CheckResult:
    e = cfg.eligibility
    thr = f">= {e.min_age} years"
    if a.age is None:
        return _missing("min_age", "Minimum age", thr, "Age")
    ok = a.age >= e.min_age
    return CheckResult(key="min_age", label="Minimum age", status=CheckStatus.PASS if ok else CheckStatus.FAIL,
                       observed=f"{a.age} years", threshold=thr,
                       detail="Meets the minimum age." if ok else "Below the configured minimum age.")


def check_age_at_maturity(a: ApplicantInput, cfg: BankConfig, as_of: Optional[date] = None) -> CheckResult:
    e = cfg.eligibility
    thr = f"<= {e.max_age_at_maturity} years at maturity"
    label = "Maximum age at loan maturity"
    if a.age is None:
        return _missing("age_at_maturity", label, thr, "Age")
    if a.date_of_birth is not None:
        if as_of is None:
            raise ValueError("assessment date required when date of birth is supplied")
        maturity_age = maturity_age_years(a.date_of_birth, as_of, a.tenure_months)
        ok = maturity_date(as_of, a.tenure_months) <= add_months(a.date_of_birth, e.max_age_at_maturity * 12)
    else:
        maturity_age = a.age + a.tenure_months / 12
        ok = maturity_age <= e.max_age_at_maturity
    if a.date_of_birth is not None:
        detail = (f"Loan maturity date: {maturity_date(as_of, a.tenure_months).isoformat()}. "
                  f"Age limit date: {add_months(a.date_of_birth, e.max_age_at_maturity * 12).isoformat()}.")
    else:
        detail = ("Loan ends within the age limit (age in completed years; verify date of birth if close)."
                  if ok else f"Loan would end at about {maturity_age:.1f} years. "
                             f"Longest tenure within the age limit: {max(0, (e.max_age_at_maturity - a.age) * 12)} months.")
    return CheckResult(key="age_at_maturity", label=label, status=CheckStatus.PASS if ok else CheckStatus.FAIL,
                       observed=f"{maturity_age:.1f} years", threshold=thr, detail=detail)


def check_min_income(a: ApplicantInput, cfg: BankConfig, income: IncomeAdjustment) -> CheckResult:
    thr = f">= {_fmt_inr(cfg.eligibility.min_net_monthly_income)} / month"
    label = "Minimum net monthly income"
    if income.base_income is None:
        return _missing("min_income", label, thr, "Net monthly income")
    ok = income.base_income >= cfg.eligibility.min_net_monthly_income
    note = "" if a.verified_monthly_income is not None else " Income is not verified."
    return CheckResult(key="min_income", label=label, status=CheckStatus.PASS if ok else CheckStatus.FAIL,
                       observed=f"{_fmt_inr(income.base_income)} ({income.base_source})", threshold=thr,
                       detail=("Meets the minimum income." if ok else "Below the configured minimum income.") + note)


def check_employment_category(a: ApplicantInput, cfg: BankConfig) -> CheckResult:
    accepted = cfg.eligibility.accepted_employment_categories
    thr = ", ".join(EMPLOYMENT_LABELS[c] for c in accepted)
    label = "Accepted employment category"
    if a.employment_category is None:
        return _missing("employment_category", label, thr, "Employment category")
    ok = a.employment_category in accepted
    return CheckResult(key="employment_category", label=label,
                       status=CheckStatus.PASS if ok else CheckStatus.FAIL,
                       observed=EMPLOYMENT_LABELS[a.employment_category], threshold=thr,
                       detail="Category accepted under this policy." if ok
                       else "This product is not offered to this employment category under the selected policy.")


def check_employment_tenure(a: ApplicantInput, cfg: BankConfig) -> CheckResult:
    label = "Minimum employment / business tenure"
    if a.employment_category is None:
        return _missing("employment_tenure", label, "Depends on category", "Employment category")
    need = cfg.eligibility.min_employment_years.get(a.employment_category)
    thr = f">= {need:g} years"
    if a.years_in_employment is None:
        return _missing("employment_tenure", label, thr, "Years in current employment/business")
    ok = a.years_in_employment >= need
    return CheckResult(key="employment_tenure", label=label, status=CheckStatus.PASS if ok else CheckStatus.FAIL,
                       observed=f"{a.years_in_employment:g} years", threshold=thr,
                       detail="Meets the minimum tenure." if ok else "Below the configured minimum tenure.")


def check_credit_score(a: ApplicantInput, cfg: BankConfig) -> CheckResult:
    pol = cfg.eligibility.credit_score
    thr = f">= {pol.min_score}"
    label = "Minimum credit score"
    if a.no_credit_history:
        if pol.new_to_credit == "accept":
            return CheckResult(key="credit_score", label=label, status=CheckStatus.NOT_APPLICABLE,
                               observed="No credit history (new to credit)", threshold=thr,
                               detail="Policy accepts new-to-credit applicants without a score.")
        return CheckResult(key="credit_score", label=label, status=CheckStatus.REVIEW,
                           observed="No credit history (new to credit)", threshold=thr,
                           detail="Policy requires manual review of new-to-credit applicants.")
    if a.credit_score is not None:
        ok = a.credit_score >= pol.min_score
        return CheckResult(key="credit_score", label=label, status=CheckStatus.PASS if ok else CheckStatus.FAIL,
                           observed=str(a.credit_score), threshold=thr,
                           detail="Meets the minimum score (self-reported unless a bureau report is on file)."
                           if ok else "Below the configured minimum score.")
    if a.credit_score_range_low is not None:
        lo, hi = a.credit_score_range_low, a.credit_score_range_high
        observed = f"Range {lo}-{hi}"
        if lo >= pol.min_score:
            return CheckResult(key="credit_score", label=label, status=CheckStatus.PASS, observed=observed,
                               threshold=thr, detail="Whole range meets the minimum score.")
        if hi < pol.min_score:
            return CheckResult(key="credit_score", label=label, status=CheckStatus.FAIL, observed=observed,
                               threshold=thr, detail="Whole range is below the minimum score.")
        return CheckResult(key="credit_score", label=label, status=CheckStatus.REVIEW, observed=observed,
                           threshold=thr, missing_info=True,
                           detail="Range straddles the minimum; an exact bureau score is needed.")
    return _missing("credit_score", label, thr, "Credit score")


def check_loan_amount(a: ApplicantInput, cfg: BankConfig) -> CheckResult:
    lim = cfg.eligibility.loan_amount
    thr = f"{_fmt_inr(lim.min)} - {_fmt_inr(lim.max)}"
    ok = lim.min <= a.requested_amount <= lim.max
    if ok:
        detail = "Within product limits."
    elif a.requested_amount > lim.max:
        detail = "Above the product maximum."
    else:
        detail = "Below the product minimum."
    return CheckResult(key="loan_amount", label="Loan amount within product limits",
                       status=CheckStatus.PASS if ok else CheckStatus.FAIL,
                       observed=_fmt_inr(a.requested_amount), threshold=thr, detail=detail)


def check_tenure(a: ApplicantInput, cfg: BankConfig) -> CheckResult:
    lim = cfg.eligibility.tenure_months
    thr = f"{lim.min:g} - {lim.max:g} months"
    ok = lim.min <= a.tenure_months <= lim.max
    return CheckResult(key="tenure", label="Tenure within product limits",
                       status=CheckStatus.PASS if ok else CheckStatus.FAIL,
                       observed=f"{a.tenure_months} months", threshold=thr,
                       detail="Within product limits." if ok else "Outside the product tenure limits.")


def document_gaps(a: ApplicantInput, cfg: BankConfig) -> tuple[list[str], list[str], list[str]]:
    """(mandatory, not_available, unknown) document keys for the applicant's category."""
    if a.employment_category is None:
        return [], [], []
    mandatory = cfg.eligibility.mandatory_documents.get(a.employment_category, [])
    not_available, unknown = [], []
    for d in mandatory:
        s = a.doc(d)
        if s == DocStatus.UNKNOWN:
            unknown.append(d)
        elif s == DocStatus.NOT_AVAILABLE or (s == DocStatus.NOT_APPLICABLE and d not in CONDITIONAL_DOCUMENTS):
            not_available.append(d)
    return mandatory, not_available, unknown


def check_documents(a: ApplicantInput, cfg: BankConfig) -> CheckResult:
    label = "Mandatory documentation"
    if a.employment_category is None:
        return _missing("documents", label, "Depends on category", "Employment category")
    mandatory, not_avail, unknown = document_gaps(a, cfg)
    thr = ", ".join(DOCUMENT_TYPES[d] for d in mandatory) or "None"
    available = [d for d in mandatory if d not in not_avail and d not in unknown]
    observed = f"{len(available)} of {len(mandatory)} available"
    if not not_avail and not unknown:
        return CheckResult(key="documents", label=label, status=CheckStatus.PASS, observed=observed,
                           threshold=thr, detail="All mandatory documents recorded as available.")
    parts = []
    if not_avail:
        parts.append("Not available: " + ", ".join(DOCUMENT_TYPES[d] for d in not_avail) + ".")
    if unknown:
        parts.append("Not yet recorded: " + ", ".join(DOCUMENT_TYPES[d] for d in unknown) + ".")
    parts.append("Missing documents are a remediable gap; they do not indicate fraud or ineligibility.")
    return CheckResult(key="documents", label=label, status=CheckStatus.REVIEW, observed=observed,
                       threshold=thr, detail=" ".join(parts), missing_info=True)


def check_foir(a: ApplicantInput, cfg: BankConfig, foir_value: Optional[float],
               income: IncomeAdjustment) -> CheckResult:
    label = "FOIR ceiling"
    if a.employment_category is None:
        return _missing("foir", label, "Depends on category", "Employment category")
    cap = cfg.eligibility.foir_cap.get(a.employment_category)
    thr = f"<= {cap:.0%}"
    if income.adjusted_income is None:
        return _missing("foir", label, thr, "Income")
    if a.existing_monthly_emis is None:
        return _missing("foir", label, thr, "Existing monthly EMIs")
    if foir_value is None:
        return CheckResult(key="foir", label=label, status=CheckStatus.REVIEW, observed="Undefined (zero income)",
                           threshold=thr, detail="FOIR is undefined because adjusted income is zero.")
    ok = foir_value <= cap
    return CheckResult(key="foir", label=label, status=CheckStatus.PASS if ok else CheckStatus.FAIL,
                       observed=f"{foir_value:.1%}", threshold=thr,
                       detail="Within the FOIR ceiling (on adjusted income)." if ok
                       else "Total EMIs would exceed the FOIR ceiling on adjusted income.")


def check_kyc(a: ApplicantInput, cfg: BankConfig) -> CheckResult:
    """PAN-based KYC gate. Format is validated on input; verification is recorded by the manager."""
    pol = cfg.kyc
    label = "PAN available and verified (KYC)"
    allowed = ", ".join(f"{h} ({HOLDER_TYPES.get(h, h)})" for h in pol.allowed_holder_types)
    thr = f"Valid PAN, holder type {allowed}" + (", verified" if pol.require_verified else "")
    if a.pan is None:
        if not pol.require_pan:
            return CheckResult(key="kyc_pan", label=label, status=CheckStatus.NOT_APPLICABLE,
                               observed="Not provided", threshold=thr, detail="Policy does not require PAN.")
        return CheckResult(key="kyc_pan", label=label, status=CheckStatus.REVIEW, observed="Not provided",
                           threshold=thr, missing_info=True,
                           detail="PAN not recorded (requires the customer's consent). KYC cannot be completed yet.")
    observed = mask_pan(a.pan)
    h = holder_type(a.pan)
    if h not in pol.allowed_holder_types:
        return CheckResult(key="kyc_pan", label=label, status=CheckStatus.FAIL, observed=observed, threshold=thr,
                           detail=f"PAN holder type '{h}' ({HOLDER_TYPES.get(h, 'unknown')}) is not eligible "
                                  "for this product.")
    if a.pan_status == PanStatus.INOPERATIVE:
        return CheckResult(key="kyc_pan", label=label, status=CheckStatus.REVIEW, observed=observed + " (inoperative)",
                           threshold=thr, detail="PAN reported inoperative (e.g. not linked to Aadhaar). KYC cannot "
                                                 "complete until the customer makes it operative.")
    if a.pan_status == PanStatus.MISMATCH:
        return CheckResult(key="kyc_pan", label=label, status=CheckStatus.REVIEW, observed=observed + " (mismatch)",
                           threshold=thr, detail="PAN details do not match KYC records; resolve before proceeding. "
                                                 "A mismatch is not by itself evidence of fraud.")
    if a.pan_status == PanStatus.NOT_VERIFIED and pol.require_verified:
        return CheckResult(key="kyc_pan", label=label, status=CheckStatus.REVIEW, observed=observed + " (not verified)",
                           threshold=thr, missing_info=True, detail="PAN format valid; verification is pending.")
    return CheckResult(key="kyc_pan", label=label, status=CheckStatus.PASS, observed=observed, threshold=thr,
                       detail="PAN valid for this product and verified.")


BUILT_IN_STAGE = {
    "kyc_pan": "KYC", "min_age": "Eligibility", "age_at_maturity": "Eligibility", "min_income": "Eligibility",
    "employment_category": "Eligibility", "employment_tenure": "Eligibility", "credit_score": "Bureau",
    "loan_amount": "Eligibility", "tenure": "Eligibility", "documents": "Documentation", "foir": "Affordability",
}


def run_hard_checks(a: ApplicantInput, cfg: BankConfig, income: IncomeAdjustment,
                    foir_value: Optional[float], as_of: Optional[date] = None) -> list[CheckResult]:
    """Built-in checks, tagged with their underwriting stage and (where numeric)
    the observed/threshold values that the deviation matrix needs."""
    e, cat = cfg.eligibility, a.employment_category
    numeric = {
        "age_at_maturity": (None if a.age is None else
                            maturity_age_years(a.date_of_birth, as_of, a.tenure_months)
                            if a.date_of_birth is not None and as_of is not None else
                            a.age + a.tenure_months / 12, e.max_age_at_maturity, "max"),
        "min_income": (income.base_income, e.min_net_monthly_income, "min"),
        "employment_tenure": (a.years_in_employment, e.min_employment_years.get(cat) if cat else None, "min"),
        "credit_score": (a.credit_score, e.credit_score.min_score, "min"),
        "foir": (foir_value, e.foir_cap.get(cat) if cat else None, "max"),
        "loan_amount": (a.requested_amount, e.loan_amount.max, "max") if a.requested_amount >= e.loan_amount.min
        else (None, None, None),
    }
    out = []
    for c in _built_in(a, cfg, income, foir_value, as_of):
        upd: dict = {"stage": BUILT_IN_STAGE[c.key]}
        ov, tv, d = numeric.get(c.key, (None, None, None))
        if ov is not None and tv is not None:
            upd.update(observed_value=float(ov), threshold_value=float(tv), direction=d)
        out.append(c.model_copy(update=upd))
    return out


def _built_in(a: ApplicantInput, cfg: BankConfig, income: IncomeAdjustment,
              foir_value: Optional[float], as_of: Optional[date] = None) -> list[CheckResult]:
    return [
        check_kyc(a, cfg),
        check_min_age(a, cfg),
        check_age_at_maturity(a, cfg, as_of),
        check_min_income(a, cfg, income),
        check_employment_category(a, cfg),
        check_employment_tenure(a, cfg),
        check_credit_score(a, cfg),
        check_loan_amount(a, cfg),
        check_tenure(a, cfg),
        check_documents(a, cfg),
        check_foir(a, cfg, foir_value, income),
    ]
