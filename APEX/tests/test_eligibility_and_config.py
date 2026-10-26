"""Layer A hard checks and bank configuration validation."""
import copy

import pytest
import yaml
from pydantic import ValidationError

from conftest import variant
from eligibility import CONFIG_DIR, available_banks, config_from_dict
from engine import assess
from models import CheckStatus, Classification


def checks_of(applicant, cfg):
    return {c.key: c for c in assess(applicant, cfg, timestamp="t").checks}


# ------------------------------------------------------------ configurations
def test_three_fictional_banks_with_version_and_date(banks):
    assert set(banks) == {"bank_a", "bank_b", "bank_c"}
    for cfg in banks.values():
        assert cfg.meta.fictional is True
        assert "fictional" in cfg.meta.display_name.lower()
        assert cfg.meta.policy_version and cfg.meta.last_verified
        assert sum(cfg.scoring.weights.values()) == pytest.approx(1.0)


def _raw(name="bank_b_moderate.yaml"):
    return yaml.safe_load((CONFIG_DIR / name).read_text(encoding="utf-8"))


def test_config_rejects_weights_not_summing_to_one():
    raw = _raw()
    raw.setdefault("scoring", {})["weights"] = {"affordability": 0.5, "credit_score": 0.5, "credit_utilisation": 0.1,
                                                "loan_to_income": 0, "income_stability": 0, "residual_income": 0,
                                                "documentation": 0}
    with pytest.raises(ValidationError):
        config_from_dict(raw)


def test_config_rejects_non_fictional_bank():
    raw = _raw()
    raw["meta"]["fictional"] = False
    with pytest.raises(ValidationError):
        config_from_dict(raw)


@pytest.mark.parametrize("path, value", [
    (("scoring", "likely_min"), 40),                    # below borderline_min
    (("eligibility", "foir_cap", "salaried"), 1.5),
    (("scoring", "curves", "credit_score"), [[700, 50], [600, 60]]),
    (("amount", "safety_buffer_min"), 0.2),             # > max
])
def test_config_rejects_inconsistent_values(path, value):
    raw = _raw()
    node = raw
    for k in path[:-1]:
        node = node.setdefault(k, {})
    node[path[-1]] = value
    with pytest.raises(ValidationError):
        config_from_dict(raw)


def test_bank_override_merges_with_defaults(bank_a, bank_b):
    # Bank A overrides only the band thresholds; weights come from defaults.
    assert bank_a.scoring.likely_min == 75
    assert bank_a.scoring.weights == bank_b.scoring.weights


# ------------------------------------------------------------ hard checks
def test_missing_information_is_review_not_fail(bank_b, worked):
    a = variant(worked, age=None, net_monthly_income=None, verified_monthly_income=None,
                existing_monthly_emis=None, years_in_employment=None, credit_score=None)
    c = checks_of(a, bank_b)
    for key in ("min_age", "age_at_maturity", "min_income", "employment_tenure", "credit_score", "foir"):
        assert c[key].status == CheckStatus.REVIEW, key
        assert c[key].missing_info, key
    assert all(x.status != CheckStatus.FAIL for x in c.values())


def test_credit_score_range_logic(bank_b, worked):
    def status(lo, hi):
        return checks_of(variant(worked, credit_score=None, credit_score_range_low=lo,
                                 credit_score_range_high=hi), bank_b)["credit_score"].status
    assert status(720, 760) == CheckStatus.PASS
    assert status(600, 650) == CheckStatus.FAIL
    assert status(680, 720) == CheckStatus.REVIEW  # straddles 700


def test_new_to_credit_policy(bank_b, bank_c, worked):
    ntc = variant(worked, credit_score=None, no_credit_history=True)
    assert checks_of(ntc, bank_b)["credit_score"].status == CheckStatus.REVIEW
    assert checks_of(ntc, bank_c)["credit_score"].status == CheckStatus.NOT_APPLICABLE


def test_missing_documents_are_review_never_fail(bank_b, worked):
    a = variant(worked, documents={"identity": "available", "salary_slips": "not_available"})
    c = checks_of(a, bank_b)["documents"]
    assert c.status == CheckStatus.REVIEW
    assert "do not indicate fraud" in c.detail


def test_not_applicable_only_allowed_for_conditional_documents(bank_a, worked):
    se = variant(worked, employment_category="self_employed", years_in_employment=5,
                 documents={"identity": "available", "bank_statements": "available", "itr": "available",
                            "gst": "not_applicable"})
    assert checks_of(se, bank_a)["documents"].status == CheckStatus.PASS
    no_id = variant(se, documents={**se.documents, "identity": "not_applicable"})
    assert checks_of(no_id, bank_a)["documents"].status == CheckStatus.REVIEW


def test_age_at_maturity_boundary(bank_b, worked):
    ok = variant(worked, age=55, tenure_months=60)     # ends at exactly 60
    bad = variant(worked, age=55, tenure_months=61)
    assert checks_of(ok, bank_b)["age_at_maturity"].status == CheckStatus.PASS
    assert checks_of(bad, bank_b)["age_at_maturity"].status == CheckStatus.FAIL


def test_employment_category_exclusion(bank_a, worked):
    inf = variant(worked, employment_category="informal", years_in_employment=5)
    assert checks_of(inf, bank_a)["employment_category"].status == CheckStatus.FAIL


def test_product_limits(bank_b, worked):
    c = checks_of(variant(worked, requested_amount=3_000_000, tenure_months=72), bank_b)
    assert c["loan_amount"].status == CheckStatus.FAIL
    assert c["tenure"].status == CheckStatus.FAIL


def test_foir_ceiling_and_deviation_matrix(bank_a, bank_b, bank_c, worked):
    a = checks_of(worked, bank_a)["foir"]            # 55.1 % vs 45 %: breach 10.1 pp > 3 pp band
    assert a.status == CheckStatus.FAIL
    b = checks_of(worked, bank_b)["foir"]            # 55.1 % vs 50 %: breach 5.1 pp, within 10 pp tier
    assert b.status == CheckStatus.DEVIATION and b.deviation_authority == "Regional Credit Head"
    smaller = checks_of(variant(worked, requested_amount=720_000), bank_b)["foir"]   # breach < 5 pp
    assert smaller.status == CheckStatus.DEVIATION and smaller.deviation_authority == "Branch Credit Manager"
    assert checks_of(worked, bank_c)["foir"].status == CheckStatus.PASS   # 55.1 % <= 60 %
