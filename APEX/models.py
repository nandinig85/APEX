"""Input, configuration and output data structures for APEX.

All monetary values are in Indian Rupees (INR). Ratios are stored as
fractions (0.45 == 45 %) and only converted to percentages for display.

The models here carry no business logic beyond input validation, so the
calculation, eligibility and scoring modules can be tested independently.
"""
from __future__ import annotations

import re
from datetime import date
from enum import Enum
from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

PAN_PATTERN = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")

# Underwriting stages, in the order a lender typically runs them.
STAGES = ("KYC", "Internal checks", "Bureau", "Eligibility", "Affordability", "Banking behaviour", "Documentation")


# --------------------------------------------------------------------------
# Enumerations
# --------------------------------------------------------------------------
class EmploymentCategory(str, Enum):
    SALARIED = "salaried"
    SELF_EMPLOYED = "self_employed"
    INFORMAL = "informal"


EMPLOYMENT_LABELS = {
    EmploymentCategory.SALARIED: "Salaried",
    EmploymentCategory.SELF_EMPLOYED: "Self-employed",
    EmploymentCategory.INFORMAL: "Informal",
}


class IncomeVariability(str, Enum):
    """Self-reported variability; used only when no monthly observations exist."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    UNKNOWN = "unknown"


class DocStatus(str, Enum):
    AVAILABLE = "available"
    NOT_AVAILABLE = "not_available"
    UNKNOWN = "unknown"            # not yet asked / not recorded
    NOT_APPLICABLE = "not_applicable"  # e.g. GST for a non-GST-registered business


DOCUMENT_TYPES = {
    "identity": "Identity documentation",
    "income_proof": "Income proof",
    "salary_slips": "Salary slips",
    "bank_statements": "Bank statements",
    "itr": "Income tax returns (ITR)",
    "gst": "GST records",
    "upi_history": "UPI / digital payment history",
    "other": "Other bank-specific documents",
}


class CheckStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    DEVIATION = "DEVIATION"  # outside policy but within a configured deviation band
    REVIEW = "REVIEW"
    NOT_APPLICABLE = "NOT APPLICABLE"


class PanStatus(str, Enum):
    NOT_VERIFIED = "not_verified"
    VERIFIED = "verified"          # verified by the manager in the bank's KYC system
    INOPERATIVE = "inoperative"    # e.g. not linked to Aadhaar
    MISMATCH = "mismatch"          # details do not match KYC records


class EmployerCategory(str, Enum):
    """Bank-maintained employer list category (only used if a bank's rules reference it)."""
    A = "A"
    B = "B"
    C = "C"
    UNLISTED = "unlisted"


class Indicator(str, Enum):
    """Traffic-light indicator used by the UI and the report."""
    GREEN = "green"   # meets configured criterion
    AMBER = "amber"   # requires review
    RED = "red"       # does not meet configured criterion
    GREY = "grey"     # missing or not applicable
    INFO = "info"     # informational value, not compared against a criterion


class Evidence(str, Enum):
    """Evidence label shown on every output (NIKA_Model_Parameters A4)."""
    INPUT = "Input as entered (unverified unless stated)"
    CALCULATED = "Deterministic calculation"
    ESTIMATE = "Rule-based estimate"
    POLICY = "Policy check against selected (fictional) configuration"
    MODEL = "Statistical model prediction"  # reserved for Part B; never emitted in v1


class FactorStatus(str, Enum):
    SCORED = "scored"
    MISSING = "missing"
    NOT_APPLICABLE = "not_applicable"


class Classification(str, Enum):
    LIKELY = "Likely to meet preliminary criteria"
    BORDERLINE = "Borderline / Manual review recommended"
    UNLIKELY = "Unlikely to meet preliminary criteria"
    INSUFFICIENT = "Insufficient information"


# --------------------------------------------------------------------------
# Applicant input
# --------------------------------------------------------------------------
class ApplicantInput(BaseModel):
    """Everything the bank manager enters. ``None`` always means *not provided*.

    Only the loan request itself (amount, tenure, rate) is strictly required to
    run an assessment; everything else may be missing, and missing values are
    reported as REVIEW / Insufficient information rather than as failures.
    """

    model_config = ConfigDict(extra="forbid", use_enum_values=False)

    # A. Personal and loan information
    applicant_id: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_\-]+$")
    age: Optional[int] = Field(default=None, ge=18, le=100)
    employment_category: Optional[EmploymentCategory] = None
    requested_amount: float = Field(gt=0, le=1e9)
    tenure_months: int = Field(gt=0, le=480)
    annual_interest_rate: float = Field(ge=0, le=60, description="Percent per annum")
    loan_purpose: Optional[str] = Field(default=None, max_length=200,
                                        description="Recordkeeping only; never scored")

    # B. Income
    net_monthly_income: Optional[float] = Field(default=None, ge=0)
    years_in_employment: Optional[float] = Field(default=None, ge=0, le=60)
    income_variability: IncomeVariability = IncomeVariability.UNKNOWN
    monthly_income_observations: Optional[list[float]] = None
    verified_monthly_income: Optional[float] = Field(default=None, ge=0)
    continuous_employment_12m: Optional[bool] = Field(
        default=None, description="Employment stability indicator: no gap in last 12 months")
    other_recurring_obligations: Optional[float] = Field(
        default=None, ge=0, description="Verified rent/insurance etc. Optional (Part B1)")

    # C. Existing debt
    existing_monthly_emis: Optional[float] = Field(default=None, ge=0)
    active_loan_count: Optional[int] = Field(default=None, ge=0, le=100)
    has_credit_card: Optional[bool] = None
    credit_card_limit: Optional[float] = Field(default=None, ge=0)
    credit_card_balance: Optional[float] = Field(default=None, ge=0)
    credit_score: Optional[int] = Field(default=None, ge=300, le=900)
    credit_score_range_low: Optional[int] = Field(default=None, ge=300, le=900)
    credit_score_range_high: Optional[int] = Field(default=None, ge=300, le=900)
    no_credit_history: bool = Field(default=False, description="New-to-credit: no bureau score exists")

    # D. Documentation
    documents: dict[str, DocStatus] = Field(default_factory=dict)

    # E. KYC - PAN. Held as a secret: masked in every dump, never scored.
    pan: Optional[SecretStr] = None
    pan_consent: bool = Field(default=False, description="Customer consent to collect/verify PAN (DPDP Act)")
    pan_status: PanStatus = PanStatus.NOT_VERIFIED

    # F. Credit bureau (from a consented bureau report, entered manually in v2)
    max_dpd_12m: Optional[int] = Field(default=None, ge=0, le=999, description="Max days past due, last 12 months")
    max_dpd_24m: Optional[int] = Field(default=None, ge=0, le=999)
    written_off_or_settled_36m: Optional[bool] = None
    enquiries_3m: Optional[int] = Field(default=None, ge=0, le=100)
    enquiries_6m: Optional[int] = Field(default=None, ge=0, le=100)
    credit_history_months: Optional[int] = Field(default=None, ge=0, le=720)
    unsecured_loans_active: Optional[int] = Field(default=None, ge=0, le=50)

    # G. Bank-statement behaviour
    emi_bounces_6m: Optional[int] = Field(default=None, ge=0, le=100)
    avg_monthly_balance: Optional[float] = Field(default=None, ge=0)
    salary_credit_regular: Optional[bool] = None

    # H. Relationship and internal lists
    employer_category: Optional[EmployerCategory] = None
    existing_customer: Optional[bool] = None
    internal_negative_list_hit: Optional[bool] = None

    # I. Pricing inputs for APR (Key Fact Statement)
    processing_fee_pct: Optional[float] = Field(default=None, ge=0, le=10)
    other_upfront_charges: Optional[float] = Field(default=None, ge=0)
    rate_type: Literal["fixed", "floating"] = "fixed"

    @field_validator("pan", mode="before")
    @classmethod
    def _pan_format(cls, v):
        if v is None:
            return None
        raw = v.get_secret_value() if isinstance(v, SecretStr) else str(v)
        raw = raw.strip().upper()
        if raw == "":
            return None
        if not PAN_PATTERN.match(raw):
            raise ValueError("PAN must have the format AAAAA9999A")
        return SecretStr(raw)

    @field_validator("monthly_income_observations")
    @classmethod
    def _observations_non_negative(cls, v):
        if v is None:
            return v
        if len(v) == 0:
            return None
        if any(x is None or x < 0 for x in v):
            raise ValueError("monthly income observations must be non-negative numbers")
        return [float(x) for x in v]

    @field_validator("documents")
    @classmethod
    def _known_documents(cls, v):
        unknown = set(v) - set(DOCUMENT_TYPES)
        if unknown:
            raise ValueError(f"unknown document types: {sorted(unknown)}")
        return v

    @model_validator(mode="after")
    def _consistency(self):
        if self.credit_score is not None and self.no_credit_history:
            raise ValueError("credit_score given but no_credit_history is set")
        lo, hi = self.credit_score_range_low, self.credit_score_range_high
        if (lo is None) != (hi is None):
            raise ValueError("credit score range needs both a low and a high value")
        if lo is not None and lo > hi:
            raise ValueError("credit score range low must be <= high")
        if lo is not None and self.no_credit_history:
            raise ValueError("credit score range given but no_credit_history is set")
        if self.has_credit_card is False and (self.credit_card_limit or self.credit_card_balance):
            raise ValueError("credit card values given but has_credit_card is False")
        if self.pan is not None and not self.pan_consent:
            raise ValueError("PAN can only be recorded with the customer's consent")
        if None not in (self.enquiries_3m, self.enquiries_6m) and self.enquiries_3m > self.enquiries_6m:
            raise ValueError("enquiries in 3 months cannot exceed enquiries in 6 months")
        if None not in (self.max_dpd_12m, self.max_dpd_24m) and self.max_dpd_12m > self.max_dpd_24m:
            raise ValueError("max DPD in 12 months cannot exceed max DPD in 24 months")
        return self

    def doc(self, key: str) -> DocStatus:
        return self.documents.get(key, DocStatus.UNKNOWN)

    def to_state(self) -> dict:
        """JSON-safe dump that keeps the raw PAN (for in-session storage only, never reports)."""
        data = self.model_dump(mode="json")
        data["pan"] = self.pan.get_secret_value() if self.pan else None
        return data


# --------------------------------------------------------------------------
# Bank configuration
# --------------------------------------------------------------------------
Curve = list[tuple[float, float]]  # piecewise-linear breakpoints (x, score)


def _check_curve(points: Curve) -> Curve:
    if len(points) < 2:
        raise ValueError("a scoring curve needs at least two breakpoints")
    xs = [p[0] for p in points]
    if xs != sorted(xs) or len(set(xs)) != len(xs):
        raise ValueError("curve x-values must be strictly increasing")
    if any(not 0 <= p[1] <= 100 for p in points):
        raise ValueError("curve scores must lie in [0, 100]")
    return points


class PerCategory(BaseModel):
    model_config = ConfigDict(extra="forbid")
    salaried: float
    self_employed: float
    informal: float

    def get(self, cat: EmploymentCategory) -> float:
        return getattr(self, cat.value)


class BankMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bank_id: str
    display_name: str
    fictional: bool
    policy_version: str
    last_verified: date
    product: str
    source_note: str

    @field_validator("fictional")
    @classmethod
    def _must_be_fictional(cls, v):
        # Version 1 ships demonstration configurations only. A real lender
        # entry needs verified criteria (Part B2) and a deliberate code change.
        if not v:
            raise ValueError("v1 prototype accepts fictional demonstration configurations only")
        return v


class CreditScorePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    min_score: int = Field(ge=300, le=900)
    new_to_credit: Literal["accept", "review"]


class RangeLimit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    min: float = Field(ge=0)
    max: float = Field(gt=0)

    @model_validator(mode="after")
    def _ordered(self):
        if self.min > self.max:
            raise ValueError("min must be <= max")
        return self


class EligibilityPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    min_age: int
    max_age_at_maturity: int
    min_net_monthly_income: float = Field(ge=0)
    accepted_employment_categories: list[EmploymentCategory]
    min_employment_years: PerCategory
    credit_score: CreditScorePolicy
    loan_amount: RangeLimit
    tenure_months: RangeLimit
    foir_cap: PerCategory
    mandatory_documents: dict[EmploymentCategory, list[str]]

    @field_validator("foir_cap")
    @classmethod
    def _foir_range(cls, v: PerCategory):
        for cat in EmploymentCategory:
            if not 0 < v.get(cat) <= 1:
                raise ValueError("FOIR caps must be fractions in (0, 1]")
        return v

    @field_validator("mandatory_documents")
    @classmethod
    def _docs_known(cls, v):
        for docs in v.values():
            bad = set(docs) - set(DOCUMENT_TYPES)
            if bad:
                raise ValueError(f"unknown document types in config: {sorted(bad)}")
        return v


class CvHaircut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tolerance: float = Field(ge=0)
    slope: float = Field(ge=0)
    max_haircut: float = Field(ge=0, lt=1)


class IncomeAdjustmentPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category_factor: PerCategory
    unverified_factor: float = Field(gt=0, le=1)
    variability_applies_to: list[EmploymentCategory]
    self_reported_variability_factor: dict[IncomeVariability, float]
    cv_haircut: CvHaircut
    min_observation_months: int = Field(ge=2)


class AmountPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    income_multiple: float = Field(gt=0, description="x annual adjusted income")
    safety_buffer_min: float = Field(ge=0, lt=1)
    safety_buffer_max: float = Field(ge=0, lt=1)
    interest_rate_low: float = Field(ge=0, le=60)
    interest_rate_base: float = Field(ge=0, le=60)
    interest_rate_high: float = Field(ge=0, le=60)

    @model_validator(mode="after")
    def _ordered(self):
        if self.safety_buffer_min > self.safety_buffer_max:
            raise ValueError("safety_buffer_min must be <= safety_buffer_max")
        if not self.interest_rate_low <= self.interest_rate_base <= self.interest_rate_high:
            raise ValueError("interest rates must satisfy low <= base <= high")
        return self


class CreditScorePriceBand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    min_score: int = Field(ge=300, le=900)
    adjustment_pp: float = Field(ge=-10, le=10, description="Percentage points added to the base rate")


class PricingPolicy(BaseModel):
    """Transparent, fictional rate adjustments; no statistical model is used."""
    model_config = ConfigDict(extra="forbid")
    credit_score_bands: list[CreditScorePriceBand] = Field(min_length=2)
    low_existing_emi_share_max: float = Field(ge=0, le=1)
    low_existing_emi_adjustment_pp: float = Field(ge=-10, le=0)
    high_existing_emi_share_min: float = Field(ge=0, le=1)
    high_existing_emi_adjustment_pp: float = Field(ge=0, le=10)

    @model_validator(mode="after")
    def _ordered(self):
        bands = self.credit_score_bands
        if bands[-1].min_score != 300 or any(a.min_score <= b.min_score for a, b in zip(bands, bands[1:])):
            raise ValueError("pricing score bands must descend from the highest score and end at 300")
        if any(a.adjustment_pp > b.adjustment_pp for a, b in zip(bands, bands[1:])):
            raise ValueError("a lower credit-score band cannot receive a smaller rate adjustment")
        if self.low_existing_emi_share_max >= self.high_existing_emi_share_min:
            raise ValueError("low existing-EMI threshold must be below the high threshold")
        return self


class IndicatorPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    foir_amber_margin: float = Field(ge=0, description="FOIR within this of the cap is amber")
    utilisation_warn: float = Field(gt=0)
    utilisation_red: float = Field(gt=0)
    lti_amber_fraction: float = Field(gt=0, le=1, description="LTI above this share of the ceiling is amber")
    min_residual_income: float = Field(ge=0)
    stability_green: float
    stability_amber: float


class IncomeStabilityPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    weights: dict[str, float]
    tenure_curve: Curve
    cv_curve: Curve
    self_reported_score: dict[IncomeVariability, float]
    # Only used when a lender policy explicitly supplies it; null by default.
    employment_category_score: Optional[PerCategory] = None

    @field_validator("tenure_curve", "cv_curve")
    @classmethod
    def _curves(cls, v):
        return _check_curve(v)

    @field_validator("weights")
    @classmethod
    def _weights(cls, v):
        allowed = {"tenure", "variability", "documented_history", "employment_category"}
        if set(v) - allowed:
            raise ValueError(f"unknown income-stability components: {set(v) - allowed}")
        if abs(sum(v.values()) - 1) > 1e-9 or any(w < 0 for w in v.values()):
            raise ValueError("income-stability weights must be non-negative and sum to 1")
        return v


FACTORS = ("affordability", "credit_score", "credit_utilisation", "loan_to_income",
           "income_stability", "residual_income", "documentation")


class ScoringPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    weights: dict[str, float]
    curves: dict[str, Curve]
    missing_factor_score: float = Field(ge=0, le=100)
    min_score_coverage: float = Field(ge=0, le=1)
    likely_min: float
    borderline_min: float
    income_stability: IncomeStabilityPolicy

    @field_validator("weights")
    @classmethod
    def _weights(cls, v):
        if set(v) != set(FACTORS):
            raise ValueError(f"weights must cover exactly {FACTORS}")
        if any(w < 0 for w in v.values()) or abs(sum(v.values()) - 1) > 1e-9:
            raise ValueError("scoring weights must be non-negative and sum to 1")
        return v

    @field_validator("curves")
    @classmethod
    def _curves(cls, v):
        needed = {"affordability", "credit_score", "credit_utilisation", "loan_to_income", "residual_income"}
        if needed - set(v):
            raise ValueError(f"missing scoring curves: {needed - set(v)}")
        return {k: _check_curve(c) for k, c in v.items()}

    @model_validator(mode="after")
    def _bands(self):
        if not 0 <= self.borderline_min <= self.likely_min <= 100:
            raise ValueError("band thresholds must satisfy 0 <= borderline_min <= likely_min <= 100")
        return self


class CompletenessPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    required_weight: float = Field(gt=0)
    optional_weight: float = Field(ge=0)


RuleOperator = Literal["<", "<=", ">", ">=", "==", "!=", "in", "not_in", "is_true", "is_false"]
RuleAction = Literal["hard", "deviation", "review", "score"]


class PolicyRule(BaseModel):
    """A lender-defined filter or preference, evaluated by policy_rules.py.

    For hard / deviation / review rules, ``field operator value`` states the
    REQUIREMENT: the rule passes when it is true. For score rules, ``points``
    are added to the readiness score when the condition is true (negative
    points penalise). Field names are checked against a whitelist and a
    protected-attribute blocklist when the configuration is loaded.
    """
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[A-Za-z0-9_\-]+$", max_length=40)
    label: str = Field(min_length=3, max_length=120)
    stage: Literal[STAGES] = "Eligibility"  # type: ignore[valid-type]
    field: str
    operator: RuleOperator
    value: Optional[Union[float, bool, str, list[str]]] = None
    applies_to: Optional[list[EmploymentCategory]] = None
    action: RuleAction
    points: float = Field(default=0, ge=-50, le=50)
    authority: Optional[str] = None
    on_missing: Literal["review", "ignore"] = "review"
    rationale: str = ""
    author: str = "config"
    created: Optional[date] = None
    enabled: bool = True

    @model_validator(mode="after")
    def _consistent(self):
        if self.operator in ("is_true", "is_false"):
            if self.value is not None:
                raise ValueError(f"rule {self.id}: '{self.operator}' takes no value")
        elif self.operator in ("in", "not_in"):
            if not isinstance(self.value, list) or not self.value:
                raise ValueError(f"rule {self.id}: '{self.operator}' needs a non-empty list")
        elif self.value is None or isinstance(self.value, list):
            raise ValueError(f"rule {self.id}: '{self.operator}' needs a single value")
        if self.action == "deviation" and not self.authority:
            raise ValueError(f"rule {self.id}: deviation rules must name an approving authority")
        if self.action == "score" and self.points == 0:
            raise ValueError(f"rule {self.id}: score rules need non-zero points")
        return self


class DeviationTier(BaseModel):
    """A breach of up to ``within`` (in the check's own units) may be approved by ``authority``."""
    model_config = ConfigDict(extra="forbid")
    within: float = Field(gt=0)
    authority: str


class KycPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    require_pan: bool = True
    allowed_holder_types: list[str] = ["P"]   # 4th PAN character; P = individual
    require_verified: bool = True


class ChargesPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    default_processing_fee_pct: float = Field(ge=0, le=10)
    gst_on_fees_pct: float = Field(ge=0, le=50)
    max_apr_pct: Optional[float] = None  # review if APR exceeds this


class StressPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rate_shock_bps: float = Field(ge=0, le=2000)       # floating-rate loans only
    income_shock_pct: float = Field(ge=0, lt=1)
    max_stressed_foir_excess: float = Field(ge=0)       # stressed FOIR above cap + this -> review
    default_cv_by_category: PerCategory                 # used when no observations or self-report exist
    self_reported_cv: dict[IncomeVariability, float]    # low / medium / high -> assumed CV
    max_shortfall_probability: float = Field(gt=0, lt=1)


class ModelGate(BaseModel):
    """Minimum evidence before an approval probability may be shown."""
    model_config = ConfigDict(extra="forbid")
    min_samples: int = Field(ge=100)
    min_events_per_class: int = Field(ge=20)
    min_test_auc: float = Field(gt=0.5, lt=1)
    max_test_ece: float = Field(gt=0, lt=0.5)


class BankConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    meta: BankMeta
    eligibility: EligibilityPolicy
    income_adjustment: IncomeAdjustmentPolicy
    amount: AmountPolicy
    pricing: PricingPolicy
    indicators: IndicatorPolicy
    scoring: ScoringPolicy
    completeness: CompletenessPolicy
    kyc: KycPolicy
    charges: ChargesPolicy
    stress: StressPolicy
    model_gate: ModelGate
    deviation_matrix: dict[str, list[DeviationTier]] = Field(default_factory=dict)
    policy_rules: list[PolicyRule] = Field(default_factory=list)
    custom_rules: list[PolicyRule] = Field(default_factory=list)  # added by managers via Policy Studio
    score_adjustment_cap: float = Field(default=15, ge=0, le=50)

    @field_validator("deviation_matrix")
    @classmethod
    def _tiers_sorted(cls, v):
        return {k: sorted(t, key=lambda x: x.within) for k, t in v.items()}

    @model_validator(mode="after")
    def _unique_rule_ids(self):
        ids = [r.id for r in self.policy_rules + self.custom_rules]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            raise ValueError(f"duplicate rule ids: {sorted(dupes)}")
        return self

    def all_rules(self) -> list[PolicyRule]:
        return [r for r in self.policy_rules + self.custom_rules if r.enabled]


# --------------------------------------------------------------------------
# Outputs
# --------------------------------------------------------------------------
class EmiBreakdown(BaseModel):
    principal: float
    annual_rate: float
    tenure_months: int
    emi: float
    total_repayment: float
    total_interest: float


class IncomeAdjustment(BaseModel):
    declared_income: Optional[float]
    base_income: Optional[float]
    base_source: str
    category_factor: float
    verification_factor: float
    variability_factor: float
    coefficient_of_variation: Optional[float]
    adjusted_income: Optional[float]
    steps: list[str]


class MetricResult(BaseModel):
    key: str
    label: str
    value: Optional[float]
    display: str
    indicator: Indicator
    evidence: Evidence
    note: str = ""


class CheckResult(BaseModel):
    key: str
    label: str
    status: CheckStatus
    observed: str
    threshold: str
    detail: str
    missing_info: bool = False  # True when REVIEW is caused by missing data
    evidence: Evidence = Evidence.POLICY
    stage: str = "Eligibility"
    source: Literal["built_in", "policy_rule", "custom_rule"] = "built_in"
    # Numeric form of the comparison, used by the deviation matrix.
    observed_value: Optional[float] = None
    threshold_value: Optional[float] = None
    direction: Optional[Literal["min", "max"]] = None
    deviation_authority: Optional[str] = None


class AprResult(BaseModel):
    """Annual percentage rate including upfront charges (Key Fact Statement style)."""
    upfront_charges: float
    net_disbursal: float
    monthly_irr: float
    apr_nominal: float     # 12 x monthly IRR, percent
    apr_effective: float   # (1 + IRR)^12 - 1, percent


class StressResult(BaseModel):
    stressed_income: Optional[float]
    stressed_emi: float
    stressed_foir: Optional[float]
    rate_shock_applied_bps: float
    cv_used: Optional[float]
    cv_source: str
    shortfall_threshold: Optional[float]        # income needed to keep FOIR <= cap
    shortfall_probability: Optional[float]      # P(monthly income < threshold)
    negative_residual_probability: Optional[float]
    expected_shortfall_months_per_year: Optional[float]
    emi_rate_sensitivity: float                 # dEMI / d(annual rate in %-points)


class ScoreAdjustment(BaseModel):
    rule_id: str
    label: str
    points: float
    rationale: str


class ProbabilityEstimate(BaseModel):
    """Output of a lender-specific statistical model (Part B). Never overrides rule outcomes."""
    probability: float
    lower_95: float
    upper_95: float
    scorecard_points: float
    model_id: str
    trained_on: str            # e.g. "SYNTHETIC history (demo)" or "uploaded history"
    synthetic: bool
    n_train: int
    test_auc: float
    test_ece: float
    policy_version_at_training: str
    policy_changed_since_training: bool
    reason_codes: list[str]
    evidence: Evidence = Evidence.MODEL


class StageSummary(BaseModel):
    stage: str
    status: CheckStatus
    counts: dict[str, int]


class FactorScore(BaseModel):
    factor: str
    label: str
    status: FactorStatus
    score: Optional[float]          # score used in the aggregate (placeholder if missing)
    raw_input: str
    weight: float                   # configured weight
    effective_weight: float         # after removing not-applicable factors
    contribution: float             # effective_weight * score
    points_lost: float              # effective_weight * (100 - score)
    explanation: str


class CompletenessItem(BaseModel):
    field: str
    label: str
    required: bool
    status: Literal["provided", "partial", "missing", "not_applicable"]
    fraction: float


class DataCompleteness(BaseModel):
    weighted_score: float           # 0..100 over applicable fields
    required_score: float           # 0..100 over required fields only
    items: list[CompletenessItem]


class CapacityEstimate(BaseModel):
    """Indicative (rule-based) loan capacity. Not a lender-approved limit."""
    rate_used: float
    tenure_used: int
    foir_cap: float
    max_new_emi: float
    existing_emis_exceed_capacity: bool
    emi_supported_principal: float
    income_multiple_ceiling: float
    product_max: float
    lower: float                    # after the larger safety buffer
    upper: float                    # after the smaller safety buffer
    binding_constraint: str
    rate_sensitivity: list[dict]


class Finding(BaseModel):
    kind: Literal["rule_failure", "deviation", "review", "risk_indicator", "missing_info", "met", "preference"]
    text: str
    evidence: Evidence


class Assessment(BaseModel):
    applicant: ApplicantInput
    bank_id: str
    bank_name: str
    policy_version: str
    policy_last_verified: date
    timestamp: str
    income: IncomeAdjustment
    emi: EmiBreakdown
    effective_tenure_cap: Optional[int]
    metrics: dict[str, MetricResult]
    checks: list[CheckResult]
    factors: list[FactorScore]
    readiness_score: Optional[float]          # after bank preference adjustments
    base_readiness_score: Optional[float]     # before adjustments
    score_adjustments: list[ScoreAdjustment]
    score_coverage: float
    completeness: DataCompleteness
    capacity: Optional[CapacityEstimate]
    apr: AprResult
    stress: StressResult
    kyc: dict[str, Any]
    pipeline: list[StageSummary]
    classification: Classification
    classification_reasons: list[str]
    findings: list[Finding]
    next_steps: list[str]
    assumptions: list[str]
    probability: Optional[ProbabilityEstimate] = None
    disclaimer: str


# --------------------------------------------------------------------------
# Decision advice (advisor.py): how much, why, why not, how to improve
# --------------------------------------------------------------------------
class AffordabilityLimit(BaseModel):
    """One ceiling on the loan amount at a given tenure."""
    key: str
    label: str
    customer_text: str              # plain-language explanation for the customer
    formula: str
    max_amount: Optional[float]     # None when not computable
    binding: bool = False


class LoanOption(BaseModel):
    """A concrete loan structure, re-assessed by the engine."""
    key: str
    title: str
    description: str
    amount: float
    tenure_months: int
    emi: float
    total_interest: float
    foir: Optional[float]
    classification: Classification
    readiness_score: Optional[float]
    fails: int
    deviations: int
    recommended: bool = False


class Reason(BaseModel):
    kind: Literal["strength", "concern", "blocker", "missing"]
    title: str
    text: str
    weight: float = 0.0             # ordering only (points lost, etc.)


class ImprovementAction(BaseModel):
    key: str
    area: str
    current: str
    target: str
    action: str                     # what the customer can do
    horizon: str
    quantified: bool
    score_before: Optional[float] = None
    score_after: Optional[float] = None
    amount_before: Optional[float] = None
    amount_after: Optional[float] = None
    classification_before: Optional[Classification] = None
    classification_after: Optional[Classification] = None
    note: str = ""


class Sensitivity(BaseModel):
    label: str
    effect: float                   # change in the maximum eligible amount, INR


class RateFactor(BaseModel):
    key: str
    label: str
    adjustment_pp: float
    explanation: str


class RateRecommendation(BaseModel):
    """Illustrative policy rate, never a lender quote or an approval."""
    status: Literal["available", "needs_information", "blocked"]
    base_rate_pct: float
    range_low_pct: float
    range_high_pct: float
    recommended_rate_pct: Optional[float] = None
    unclamped_rate_pct: Optional[float] = None
    existing_emi_share: Optional[float] = None
    factors: list[RateFactor] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)


class DecisionAdvice(BaseModel):
    verdict: str
    detail: str
    tone: Literal["green", "amber", "red", "grey"]
    requested_amount: float
    requested_tenure: int
    tenure_used: Optional[int]
    max_tenure: Optional[int]
    max_eligible_amount: Optional[float]      # conditional affordability ceiling at tenure_used
    recommended_amount: Optional[float]       # indicative amount after blockers, not a bank offer
    recommended_emi: Optional[float]
    max_amount_any_tenure: Optional[float]
    binding_constraint: Optional[str]
    limits: list[AffordabilityLimit]
    blockers: list[str]
    why: list[Reason]
    why_not: list[Reason]
    options: list[LoanOption]
    improvements: list[ImprovementAction]
    sensitivities: list[Sensitivity]
    required_income: Optional[float]          # usable income at which the request fits
    max_existing_emis: Optional[float]        # existing EMIs at which the request fits
    frontier: list[dict]                      # [{tenure, max_amount, binding}]
