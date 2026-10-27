"""Lender-defined policy rules: filters, deviations and score preferences.

Banks express their own credit policy (and preferences) as data, not code:

    - id: BUR-DPD90
      label: No 90+ DPD in the last 24 months
      stage: Bureau
      field: max_dpd_24m
      operator: "<"
      value: 90
      action: hard           # hard | deviation | review | score
      on_missing: review     # review | ignore

Safety properties enforced here:

* Rules may only reference fields in ``FIELD_REGISTRY`` (a whitelist). A
  rule cannot read anything the system does not deliberately expose.
* Field names matching protected characteristics or common proxies for them
  (religion, caste, gender, pincode, ...) are rejected with a fairness error,
  even if someone later adds such a field to the registry.
* Fields that are lawful but can act as proxies carry a fairness note that the
  Policy Studio shows when a rule uses them.
* No rule can turn missing data into a FAIL: missing data is REVIEW or ignored.
* Score preferences are capped in total (``score_adjustment_cap``).
* Every change made through the Policy Studio is versioned and logged.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import yaml

from models import (BankConfig, CheckResult, CheckStatus, EmployerCategory, EmploymentCategory,
                    PolicyRule, ScoreAdjustment)


class RuleError(ValueError):
    pass


class FairnessError(RuleError):
    pass


@dataclass(frozen=True)
class FieldSpec:
    label: str
    kind: str                 # number | bool | category
    source: str               # input | derived
    unit: str = ""
    options: tuple = ()
    fairness_note: str = ""


_EMP = tuple(c.value for c in EmploymentCategory)
_EMPR = tuple(c.value for c in EmployerCategory)

FIELD_REGISTRY: dict[str, FieldSpec] = {
    # Applicant inputs
    "age": FieldSpec("Age", "number", "input", "years",
                     fairness_note="Age is legally sensitive; use it only for tenure and maturity limits."),
    "employment_category": FieldSpec("Employment category", "category", "input", options=_EMP),
    "years_in_employment": FieldSpec("Years in employment / business", "number", "input", "years"),
    "continuous_employment_12m": FieldSpec("Continuous employment (12 months)", "bool", "input"),
    "requested_amount": FieldSpec("Requested amount", "number", "input", "INR"),
    "tenure_months": FieldSpec("Tenure", "number", "input", "months"),
    "annual_interest_rate": FieldSpec("Interest rate", "number", "input", "% p.a."),
    "net_monthly_income": FieldSpec("Declared net monthly income", "number", "input", "INR"),
    "verified_monthly_income": FieldSpec("Verified monthly income", "number", "input", "INR"),
    "existing_monthly_emis": FieldSpec("Existing EMIs", "number", "input", "INR"),
    "other_recurring_obligations": FieldSpec("Other recurring obligations", "number", "input", "INR"),
    "active_loan_count": FieldSpec("Active loans", "number", "input"),
    "credit_score": FieldSpec("Credit score", "number", "input"),
    "no_credit_history": FieldSpec("New to credit", "bool", "input"),
    "max_dpd_12m": FieldSpec("Max DPD, 12 months", "number", "input", "days"),
    "max_dpd_24m": FieldSpec("Max DPD, 24 months", "number", "input", "days"),
    "written_off_or_settled_36m": FieldSpec("Write-off / settlement in 36 months", "bool", "input"),
    "enquiries_3m": FieldSpec("Credit enquiries, 3 months", "number", "input"),
    "enquiries_6m": FieldSpec("Credit enquiries, 6 months", "number", "input"),
    "credit_history_months": FieldSpec("Credit history length", "number", "input", "months"),
    "unsecured_loans_active": FieldSpec("Active unsecured loans", "number", "input"),
    "emi_bounces_6m": FieldSpec("EMI / NACH bounces, 6 months", "number", "input"),
    "avg_monthly_balance": FieldSpec("Average monthly balance", "number", "input", "INR"),
    "salary_credit_regular": FieldSpec("Regular salary credits", "bool", "input"),
    "employer_category": FieldSpec("Employer category (bank list)", "category", "input", options=_EMPR,
                                   fairness_note="Review the employer list periodically: it must not act as a "
                                                 "proxy for protected groups or geography."),
    "existing_customer": FieldSpec("Existing customer", "bool", "input",
                                   fairness_note="Relationship preferences disadvantage new-to-bank customers; "
                                                 "prefer score points over hard filters."),
    "internal_negative_list_hit": FieldSpec("Internal negative-list hit", "bool", "input",
                                            fairness_note="Negative lists need documented, reviewable "
                                                          "grounds and a removal process."),
    # Derived metrics
    "adjusted_monthly_income": FieldSpec("Adjusted monthly income", "number", "derived", "INR"),
    "emi": FieldSpec("Proposed EMI", "number", "derived", "INR"),
    "foir": FieldSpec("FOIR", "number", "derived", "fraction"),
    "stressed_foir": FieldSpec("Stressed FOIR", "number", "derived", "fraction"),
    "loan_to_income": FieldSpec("Loan-to-income multiple", "number", "derived", "x"),
    "credit_utilisation": FieldSpec("Credit utilisation", "number", "derived", "fraction"),
    "residual_income": FieldSpec("Residual income", "number", "derived", "INR"),
    "residual_share": FieldSpec("Residual income share", "number", "derived", "fraction"),
    "income_stability": FieldSpec("Income stability score", "number", "derived", "0-100"),
    "income_cv": FieldSpec("Income coefficient of variation", "number", "derived"),
    "shortfall_probability": FieldSpec("Monthly P(FOIR > cap)", "number", "derived", "fraction"),
    "documentation_share": FieldSpec("Share of mandatory documents available", "number", "derived", "fraction"),
    "data_completeness": FieldSpec("Data completeness", "number", "derived", "0-100"),
    "age_at_maturity": FieldSpec("Age at maturity", "number", "derived", "years",
                                 fairness_note="Age is legally sensitive; use it only for maturity limits."),
    "apr_effective": FieldSpec("Effective APR", "number", "derived", "%"),
    "abb_to_emi": FieldSpec("Average balance / EMI", "number", "derived", "x"),
}

# Match whole field-name tokens. Substrings such as "pan" in "company" and
# "name" in "rename" must not block otherwise permissible fields.
PROHIBITED_TOKENS = {"gender", "sex", "religion", "caste", "community", "tribe", "marital", "spouse",
                     "pincode", "postal", "zip", "district", "locality", "region", "address",
                     "nationality", "ethnic", "race", "disability", "disabled", "pregnancy", "pregnant",
                     "orientation", "political", "language", "name", "surname", "father", "mother",
                     "photo", "pan", "aadhaar"}
PROHIBITED_FIELDS = {"pin_code", "postal_code", "zip_code", "full_name", "first_name", "last_name"}
# PAN is an identifier for KYC only - filtering on it would amount to a hidden blacklist.

OPERATORS_BY_KIND = {
    "number": {"<", "<=", ">", ">=", "==", "!="},
    "bool": {"is_true", "is_false"},
    "category": {"==", "!=", "in", "not_in"},
}


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------
def check_field_allowed(field: str) -> FieldSpec:
    norm = field.lower().replace("-", "_").replace(" ", "_")
    blocked = norm if norm in PROHIBITED_FIELDS else next(
        (term for term in norm.split("_") if term in PROHIBITED_TOKENS), None)
    if blocked:
        raise FairnessError(
            f"Field '{field}' refers to a protected characteristic or a known proxy ('{blocked}'). "
            "Rules on such attributes are not permitted by APEX guardrails.")
    if field not in FIELD_REGISTRY:
        raise RuleError(f"Unknown field '{field}'. Allowed fields: {', '.join(sorted(FIELD_REGISTRY))}")
    return FIELD_REGISTRY[field]


def validate_rule(rule: PolicyRule) -> list[str]:
    """Raise RuleError/FairnessError if the rule is not allowed; return fairness warnings."""
    spec = check_field_allowed(rule.field)
    if rule.operator not in OPERATORS_BY_KIND[spec.kind]:
        raise RuleError(f"Rule {rule.id}: operator '{rule.operator}' does not apply to a {spec.kind} field "
                        f"(allowed: {', '.join(sorted(OPERATORS_BY_KIND[spec.kind]))}).")
    if spec.kind == "number" and (isinstance(rule.value, bool) or not isinstance(rule.value, (int, float))):
        raise RuleError(f"Rule {rule.id}: '{rule.field}' needs a numeric value.")
    if spec.kind == "category":
        values = rule.value if isinstance(rule.value, list) else [rule.value]
        bad = [v for v in values if str(v) not in spec.options]
        if bad:
            raise RuleError(f"Rule {rule.id}: invalid value(s) {bad} for '{rule.field}' "
                            f"(options: {', '.join(spec.options)}).")
    warnings = []
    if spec.fairness_note:
        warnings.append(f"{rule.id}: {spec.fairness_note}")
    if rule.action == "hard" and rule.field in ("existing_customer", "employer_category"):
        warnings.append(f"{rule.id}: a hard filter on '{rule.field}' excludes whole groups of applicants; "
                        "consider a deviation or score rule instead.")
    return warnings


def validate_config_rules(cfg: BankConfig) -> list[str]:
    warnings = []
    for r in cfg.policy_rules + cfg.custom_rules:
        warnings += validate_rule(r)
    return warnings


# --------------------------------------------------------------------------
# Evaluation
# --------------------------------------------------------------------------
def _compare(op: str, x: Any, v: Any) -> bool:
    if op == "<":
        return x < v
    if op == "<=":
        return x <= v
    if op == ">":
        return x > v
    if op == ">=":
        return x >= v
    if op == "==":
        return x == v
    if op == "!=":
        return x != v
    if op == "in":
        return x in v
    if op == "not_in":
        return x not in v
    if op == "is_true":
        return x is True
    if op == "is_false":
        return x is False
    raise RuleError(f"unknown operator {op}")


def _fmt(x: Any, spec: FieldSpec) -> str:
    if x is None:
        return "Not provided"
    if isinstance(x, bool):
        return "Yes" if x else "No"
    if isinstance(x, list):
        return ", ".join(map(str, x))
    if spec.unit == "fraction":
        return f"{x:.1%}"
    if spec.unit == "INR":
        return f"INR {x:,.0f}"
    if isinstance(x, float):
        return f"{x:,.2f}".rstrip("0").rstrip(".") + (f" {spec.unit}" if spec.unit else "")
    return f"{x}" + (f" {spec.unit}" if spec.unit else "")


def requirement_text(rule: PolicyRule) -> str:
    spec = FIELD_REGISTRY[rule.field]
    if rule.operator in ("is_true", "is_false"):
        return f"{spec.label} = {'Yes' if rule.operator == 'is_true' else 'No'}"
    sym = {"<": "<", "<=": "<=", ">": ">", ">=": ">=", "==": "=", "!=": "!=", "in": "in", "not_in": "not in"}
    return f"{spec.label} {sym[rule.operator]} {_fmt(rule.value, spec)}"


def evaluate(rule: PolicyRule, ctx: dict[str, Any], category: Optional[EmploymentCategory]
             ) -> tuple[str, Any]:
    """Return (state, observed) with state in pass | fail | missing | not_applicable."""
    if rule.applies_to is not None and category not in rule.applies_to:
        return "not_applicable", None
    x = ctx.get(rule.field)
    if x is None:
        return "missing", None
    if hasattr(x, "value") and not isinstance(x, (int, float, bool)):
        x = x.value  # enums
    return ("pass" if _compare(rule.operator, x, rule.value) else "fail"), x


def run_policy_rules(cfg: BankConfig, ctx: dict[str, Any], category: Optional[EmploymentCategory]
                     ) -> tuple[list[CheckResult], list[ScoreAdjustment], list[str]]:
    """Evaluate every enabled rule. Returns (checks, score adjustments, notes)."""
    checks, adjustments, notes = [], [], []
    custom_ids = {r.id for r in cfg.custom_rules}
    for rule in cfg.all_rules():
        spec = FIELD_REGISTRY[rule.field]
        state, observed = evaluate(rule, ctx, category)
        source = "custom_rule" if rule.id in custom_ids else "policy_rule"
        if rule.action == "score":
            if state == "pass":
                adjustments.append(ScoreAdjustment(rule_id=rule.id, label=rule.label, points=rule.points,
                                                   rationale=rule.rationale))
            elif state == "missing":
                notes.append(f"Preference '{rule.label}' not applied: {spec.label} not provided.")
            continue
        numeric = spec.kind == "number" and rule.operator in ("<", "<=", ">", ">=")
        base = dict(key=f"rule:{rule.id}", label=rule.label, observed=_fmt(observed, spec),
                    threshold=requirement_text(rule), stage=rule.stage, source=source,
                    observed_value=float(observed) if numeric and observed is not None else None,
                    threshold_value=float(rule.value) if numeric else None,
                    direction=("max" if rule.operator in ("<", "<=") else "min") if numeric else None)
        if state == "not_applicable":
            checks.append(CheckResult(status=CheckStatus.NOT_APPLICABLE, detail="Rule does not apply to this "
                                      "employment category.", **base))
        elif state == "missing":
            if rule.on_missing == "ignore":
                checks.append(CheckResult(status=CheckStatus.NOT_APPLICABLE,
                                          detail=f"{spec.label} not provided; rule configured to be skipped.", **base))
            else:
                checks.append(CheckResult(status=CheckStatus.REVIEW, missing_info=True,
                                          detail=f"{spec.label} not provided; cannot be checked. "
                                                 "This is missing information, not a failure.", **base))
        elif state == "pass":
            checks.append(CheckResult(status=CheckStatus.PASS, detail="Requirement met.", **base))
        else:
            status = {"hard": CheckStatus.FAIL, "deviation": CheckStatus.DEVIATION,
                      "review": CheckStatus.REVIEW}[rule.action]
            detail = rule.rationale or "Requirement not met."
            checks.append(CheckResult(status=status, detail=detail,
                                      deviation_authority=rule.authority if rule.action == "deviation" else None,
                                      **base))
    return checks, adjustments, notes


def apply_deviation_matrix(checks: list[CheckResult], cfg: BankConfig) -> list[CheckResult]:
    """Convert a FAIL into DEVIATION when the breach lies within a configured tier.

    gap = observed - threshold for maximum limits, threshold - observed for
    minimum limits, in the check's own units (e.g. FOIR fraction, score points).
    The smallest tier that covers the gap names the approving authority."""
    out = []
    for c in checks:
        tiers = cfg.deviation_matrix.get(c.key)
        if (c.status == CheckStatus.FAIL and tiers and c.direction and c.observed_value is not None
                and c.threshold_value is not None):
            gap = (c.observed_value - c.threshold_value if c.direction == "max"
                   else c.threshold_value - c.observed_value)
            tier = next((t for t in tiers if gap <= t.within + 1e-12), None)
            if tier is not None:
                c = c.model_copy(update=dict(
                    status=CheckStatus.DEVIATION, deviation_authority=tier.authority,
                    detail=f"{c.detail} Breach of {gap:.4g} is within the deviation band "
                           f"({tier.within:g}); requires approval by {tier.authority}."))
        out.append(c)
    return out


def total_adjustment(adjustments: list[ScoreAdjustment], cap: float) -> float:
    raw = sum(a.points for a in adjustments)
    return max(-cap, min(cap, raw))


# --------------------------------------------------------------------------
# Overlays: custom rules saved from the Policy Studio
# --------------------------------------------------------------------------
def overlay_path(config_dir: Path, bank_id: str) -> Path:
    return Path(config_dir) / "overlays" / f"{bank_id}.yaml"


def load_overlay(config_dir: Path, bank_id: str) -> list[dict]:
    p = overlay_path(config_dir, bank_id)
    if not p.exists():
        return []
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return data.get("custom_rules", [])


def custom_version_suffix(custom_rules: list[PolicyRule]) -> str:
    if not custom_rules:
        return ""
    blob = json.dumps([r.model_dump(mode="json") for r in custom_rules], sort_keys=True)
    return "+custom." + hashlib.sha256(blob.encode()).hexdigest()[:8]


def save_overlay(config_dir: Path, bank_id: str, rules: list[PolicyRule], action: str, rule_id: str,
                 author: str) -> None:
    """Persist custom rules for a bank and append an audit record."""
    for r in rules:
        validate_rule(r)
    p = overlay_path(config_dir, bank_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    header = ("# Custom rules added through the APEX Policy Studio. Merged on top of the bank file.\n"
              "# Every change is recorded in audit_log.jsonl.\n")
    body = yaml.safe_dump({"custom_rules": [r.model_dump(mode="json", exclude_none=True) for r in rules]},
                          sort_keys=False, allow_unicode=True)
    p.write_text(header + body, encoding="utf-8")
    entry = {"timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"), "bank_id": bank_id,
             "action": action, "rule_id": rule_id, "author": author,
             "version_suffix": custom_version_suffix(rules)}
    with (p.parent / "audit_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def read_audit_log(config_dir: Path, bank_id: Optional[str] = None) -> list[dict]:
    p = Path(config_dir) / "overlays" / "audit_log.jsonl"
    if not p.exists():
        return []
    rows = [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [r for r in rows if bank_id is None or r["bank_id"] == bank_id]
