"""Layer B scoring, classification and the logical-consistency properties in section 8."""
import numpy as np
import pytest

from conftest import variant
from engine import assess, compute_capacity
from eligibility import CONFIG_DIR, config_from_dict
from calculations import adjust_income, effective_tenure_cap, emi
from models import CheckStatus, Classification, FactorStatus
from profiles import PROFILES, PROFILES_BY_KEY

import yaml


def run(a, cfg):
    return assess(a, cfg, timestamp="2026-10-24T00:00:00+00:00")


def capacity_upper(a, cfg):
    inc = adjust_income(a, cfg.income_adjustment)
    cap = effective_tenure_cap(a.age, cfg.eligibility.max_age_at_maturity, int(cfg.eligibility.tenure_months.max))
    return compute_capacity(a, cfg, inc, cap)


# ------------------------------------------------------------ worked example
def test_worked_example_numbers(bank_b):
    r = run(PROFILES_BY_KEY["WORKED-EX"].applicant, bank_b)
    assert r.emi.emi == pytest.approx(21_067.07, abs=0.01)
    assert r.emi.total_interest == pytest.approx(211_219.28, abs=0.01)
    assert r.metrics["foir"].value == pytest.approx(0.5511, abs=1e-4)
    assert r.metrics["loan_to_income"].value == pytest.approx(1.1111, abs=1e-4)
    assert r.metrics["credit_utilisation"].value == 0.25
    assert r.metrics["residual_income"].value == pytest.approx(26_932.93, abs=0.01)
    assert r.capacity.max_new_emi == 18_000
    assert r.capacity.emi_supported_principal == pytest.approx(683_531.27, abs=0.5)


def test_worked_example_differs_by_bank(banks):
    a = PROFILES_BY_KEY["WORKED-EX"].applicant
    got = {b: run(a, cfg).classification for b, cfg in banks.items()}
    assert got == {"bank_a": Classification.UNLIKELY, "bank_b": Classification.BORDERLINE,
                   "bank_c": Classification.LIKELY}


# ------------------------------------------------------------ score mechanics
def test_score_bounds_and_effective_weights(banks):
    for p in PROFILES:
        for cfg in banks.values():
            r = run(p.applicant, cfg)
            assert r.readiness_score is None or 0 <= r.readiness_score <= 100
            applicable = [f for f in r.factors if f.status != FactorStatus.NOT_APPLICABLE]
            assert sum(f.effective_weight for f in applicable) == pytest.approx(1.0)
            assert r.base_readiness_score == pytest.approx(sum(f.contribution for f in r.factors))
            adj = sum(s.points for s in r.score_adjustments)
            assert abs(r.readiness_score - r.base_readiness_score) <= min(abs(adj), cfg.score_adjustment_cap) + 1e-9
            assert 0 <= r.score_coverage <= 1


def test_no_credit_card_is_not_applicable_not_zero(bank_b, worked):
    a = variant(worked, has_credit_card=False, credit_card_limit=None, credit_card_balance=None)
    f = {x.factor: x for x in run(a, bank_b).factors}["credit_utilisation"]
    assert f.status == FactorStatus.NOT_APPLICABLE and f.effective_weight == 0


def test_missing_data_gets_conservative_placeholder_not_favourable(bank_b, worked):
    a = variant(worked, credit_score=None)
    r = run(a, bank_b)
    f = {x.factor: x for x in r.factors}["credit_score"]
    assert f.status == FactorStatus.MISSING
    assert f.score == bank_b.scoring.missing_factor_score
    assert r.score_coverage < 1
    # The missing value never scores better than an actual weak score would.
    weak = run(variant(worked, credit_score=650), bank_b)
    assert r.readiness_score <= weak.readiness_score


def test_incomplete_profile_is_insufficient_information(banks):
    for cfg in banks.values():
        r = run(PROFILES_BY_KEY["SYN-15"].applicant, cfg)
        assert r.classification == Classification.INSUFFICIENT


def test_hard_failure_not_overridden_by_high_score(banks):
    r = run(PROFILES_BY_KEY["SYN-16"].applicant, banks["bank_c"])
    assert r.readiness_score >= banks["bank_c"].scoring.likely_min
    assert any(c.status == CheckStatus.FAIL for c in r.checks)
    assert r.classification == Classification.UNLIKELY


def test_any_fail_always_means_unlikely(banks):
    for p in PROFILES:
        for cfg in banks.values():
            r = run(p.applicant, cfg)
            if any(c.status == CheckStatus.FAIL for c in r.checks):
                assert r.classification == Classification.UNLIKELY


def test_likely_requires_no_open_review(banks):
    for p in PROFILES:
        for cfg in banks.values():
            r = run(p.applicant, cfg)
            if r.classification == Classification.LIKELY:
                assert all(c.status in (CheckStatus.PASS, CheckStatus.NOT_APPLICABLE) for c in r.checks)


def test_missing_documents_not_treated_as_failure(banks):
    for cfg in banks.values():
        r = run(PROFILES_BY_KEY["SYN-10"].applicant, cfg)
        docs = {c.key: c for c in r.checks}["documents"]
        assert docs.status == CheckStatus.REVIEW
        assert r.classification == Classification.BORDERLINE
        text = " ".join(f.text for f in r.findings).lower()
        assert "fraud" not in text.replace("do not indicate fraud", "")


# ------------------------------------------------------------ monotonicity (section 8)
@pytest.mark.parametrize("bank", ["bank_a", "bank_b", "bank_c"])
def test_more_existing_emis_never_increase_capacity(banks, worked, bank):
    cfg = banks[bank]
    uppers = [capacity_upper(variant(worked, existing_monthly_emis=e), cfg).upper
              for e in np.linspace(0, 60_000, 25)]
    assert all(b <= a + 1e-6 for a, b in zip(uppers, uppers[1:]))


@pytest.mark.parametrize("bank", ["bank_a", "bank_b", "bank_c"])
def test_more_income_never_reduces_emi_capacity(banks, worked, bank):
    cfg = banks[bank]
    vals = [capacity_upper(variant(worked, net_monthly_income=i, verified_monthly_income=i), cfg).emi_supported_principal
            for i in np.linspace(10_000, 300_000, 30)]
    assert all(b >= a - 1e-6 for a, b in zip(vals, vals[1:]))


def test_larger_loan_never_reduces_emi():
    for rate in (0, 8, 12, 24):
        emis = [emi(p, rate, 48) for p in np.linspace(10_000, 5_000_000, 40)]
        assert all(b >= a for a, b in zip(emis, emis[1:]))


def test_higher_utilisation_never_raises_score(bank_b, worked):
    scores = [run(variant(worked, credit_card_balance=b), bank_b).readiness_score
              for b in np.linspace(0, 250_000, 26)]
    assert all(b <= a + 1e-9 for a, b in zip(scores, scores[1:]))


def test_higher_credit_score_never_lowers_score(bank_b, worked):
    scores = [run(variant(worked, credit_score=s), bank_b).readiness_score for s in range(300, 901, 25)]
    assert all(b >= a - 1e-9 for a, b in zip(scores, scores[1:]))


# ------------------------------------------------------------ configuration & determinism
def test_changing_bank_changes_assessment_when_policy_differs(banks):
    for key in ("SYN-02", "SYN-11", "SYN-17", "WORKED-EX"):
        results = {run(PROFILES_BY_KEY[key].applicant, cfg).classification for cfg in banks.values()}
        assert len(results) > 1, key


def test_custom_policy_change_changes_result(worked):
    raw = yaml.safe_load((CONFIG_DIR / "bank_b_moderate.yaml").read_text(encoding="utf-8"))
    raw["eligibility"]["foir_cap"]["salaried"] = 0.60
    custom = config_from_dict(raw)
    assert {c.key: c for c in run(worked, custom).checks}["foir"].status == CheckStatus.PASS


def test_same_inputs_same_outputs(banks):
    for p in PROFILES:
        for cfg in banks.values():
            assert run(p.applicant, cfg).model_dump() == run(p.applicant, cfg).model_dump()


# ------------------------------------------------------------ guardrails
def test_no_probability_without_a_validated_model_and_disclaimer_present(banks):
    r = run(PROFILES_BY_KEY["SYN-01"].applicant, banks["bank_b"])
    assert r.probability is None   # no model saved in the test model directory
    assert "readiness score is not a probability" in r.disclaimer
    assert "does not guarantee approval" in r.disclaimer


def test_capacity_is_a_range_with_buffer(bank_b, worked):
    c = capacity_upper(worked, bank_b)
    assert c.lower <= c.upper
    assert c.upper <= c.emi_supported_principal * (1 - bank_b.amount.safety_buffer_min) + 1e-6


def test_capacity_respects_age_tenure_cap(bank_a):
    a = PROFILES_BY_KEY["SYN-11"].applicant  # age 55, max age 58 -> 36 months
    assert capacity_upper(a, bank_a).tenure_used == 36


def test_existing_emis_over_capacity_flagged(banks):
    r = run(PROFILES_BY_KEY["SYN-13"].applicant, banks["bank_b"])
    assert r.capacity.existing_emis_exceed_capacity and r.capacity.max_new_emi == 0
    assert r.capacity.upper == 0


def test_completeness_separate_and_optional_missing_not_a_failure(bank_b, worked):
    a = variant(worked, active_loan_count=None, continuous_employment_12m=None)
    r = run(a, bank_b)
    assert r.completeness.required_score == 100
    assert r.completeness.weighted_score < 100
    base = run(worked, bank_b)
    assert r.readiness_score == pytest.approx(base.readiness_score)
    assert [c.status for c in r.checks] == [c.status for c in base.checks]
