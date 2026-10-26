"""The fictional policy rate must be explainable, bounded, and safe to withhold."""
import json

import pytest
from pydantic import ValidationError

from conftest import variant
from eligibility import CONFIG_DIR, config_from_dict
from engine import assess
from pricing import recommend_rate
from profiles import PROFILES_BY_KEY
from reports import report_json, report_markdown


def priced(key, cfg, **changes):
    applicant = PROFILES_BY_KEY[key].applicant
    if changes:
        applicant = variant(applicant, **changes)
    assessment = assess(applicant, cfg, timestamp="2026-10-26T00:00:00+00:00", use_model=False)
    return assessment, recommend_rate(assessment, cfg)


def test_fictional_rate_explains_the_full_calculation(banks):
    for cfg in banks.values():
        _, price = priced("SYN-01", cfg)
        assert price.status == "available"
        assert {f.key for f in price.factors} >= {"credit_score", "existing_emi_share"}
        assert price.base_rate_pct + sum(f.adjustment_pp for f in price.factors) == pytest.approx(
            price.recommended_rate_pct)
        assert price.range_low_pct <= price.recommended_rate_pct <= price.range_high_pct


def test_suggestion_does_not_depend_on_assumed_interest_rate(bank_b):
    _, at_base = priced("SYN-01", bank_b)
    _, at_high_rate = priced("SYN-01", bank_b, annual_interest_rate=18.0)
    assert at_base.status == at_high_rate.status == "available"
    assert at_base.recommended_rate_pct == at_high_rate.recommended_rate_pct


def test_rate_is_withheld_for_missing_score_or_hard_blocker(bank_b):
    _, missing = priced("SYN-15", bank_b)
    _, failed = priced("SYN-16", bank_b)
    _, no_capacity = priced("SYN-13", bank_b)
    assert missing.status == "needs_information" and missing.recommended_rate_pct is None
    assert failed.status == "blocked" and failed.recommended_rate_pct is None
    assert no_capacity.status == "blocked" and no_capacity.recommended_rate_pct is None


def test_bank_range_caps_the_fictional_rate(bank_a):
    amount = bank_a.amount.model_copy(update={"interest_rate_low": 13.5, "interest_rate_high": 14.0})
    cfg = bank_a.model_copy(update={"amount": amount})
    _, price = priced("SYN-01", cfg)
    assert price.status == "available"
    assert price.unclamped_rate_pct < 13.5
    assert price.recommended_rate_pct == 13.5
    assert price.factors[-1].key == "bank_range"


def test_report_contains_rate_and_factors(bank_b):
    assessment, price = priced("SYN-01", bank_b)
    manager_report = report_markdown(assessment, pricing=price)
    data = json.loads(report_json(assessment, price))
    assert "Illustrative interest-rate recommendation" in manager_report
    assert "Exact bureau score" in manager_report
    assert data["pricing_recommendation"]["recommended_rate_pct"] == price.recommended_rate_pct


def test_invalid_pricing_bands_are_rejected():
    import yaml

    raw = yaml.safe_load((CONFIG_DIR / "bank_b_moderate.yaml").read_text(encoding="utf-8"))
    defaults = yaml.safe_load((CONFIG_DIR / "_defaults.yaml").read_text(encoding="utf-8"))
    raw["pricing"] = defaults["pricing"]
    raw["pricing"]["credit_score_bands"][0], raw["pricing"]["credit_score_bands"][1] = (
        raw["pricing"]["credit_score_bands"][1], raw["pricing"]["credit_score_bands"][0])
    with pytest.raises(ValidationError):
        config_from_dict(raw)
