"""Tests for v2 features: PAN/KYC, APR, amortisation, stress, policy rules,
deviations, overlays and the approval-probability scorecard."""
import json
import math
import shutil

import numpy as np
import pytest
from pydantic import ValidationError

import approval_model as am
import calculations as calc
from conftest import variant
from eligibility import CONFIG_DIR, available_banks
from engine import assess
from kyc import mask_pan, pan_hash
from models import CheckStatus, Classification, PolicyRule
from policy_rules import (FIELD_REGISTRY, FairnessError, RuleError, check_field_allowed, read_audit_log,
                          save_overlay, validate_rule)
from profiles import PROFILES, PROFILES_BY_KEY
from reports import report_json, report_markdown
from synthetic_history import generate


def run(a, cfg, **kw):
    return assess(a, cfg, timestamp="2026-10-24T00:00:00+00:00", **kw)


def checks(a, cfg):
    return {c.key: c for c in run(a, cfg).checks}


# ------------------------------------------------------------------ PAN / KYC
def test_pan_format_and_consent(worked):
    with pytest.raises(ValidationError):
        variant(worked, pan="ABCDE12345")
    with pytest.raises(ValidationError):
        variant(worked, pan="ABCPE1234F", pan_consent=False)
    a = variant(worked, pan=" abcpe1234f ")
    assert a.pan.get_secret_value() == "ABCPE1234F"


def test_pan_is_masked_everywhere(bank_b, worked):
    a = variant(worked, pan="ABCPE1234F")
    r = run(a, bank_b)
    assert mask_pan(a.pan) == "XXXXX1234X"
    md, js = report_markdown(r), report_json(r)
    assert "ABCPE1234F" not in md and "ABCPE1234F" not in js
    assert "XXXXX1234X" in md
    assert pan_hash(a.pan) != "ABCPE1234F" and len(pan_hash(a.pan)) == 64


@pytest.mark.parametrize("pan, status, expected", [
    ("ABCPE1234F", "verified", CheckStatus.PASS),
    ("ABCCE1234F", "verified", CheckStatus.FAIL),      # company PAN on a personal loan
    ("ABCPE1234F", "inoperative", CheckStatus.REVIEW),
    ("ABCPE1234F", "mismatch", CheckStatus.REVIEW),
    ("ABCPE1234F", "not_verified", CheckStatus.REVIEW),
])
def test_kyc_check(bank_b, worked, pan, status, expected):
    assert checks(variant(worked, pan=pan, pan_status=status), bank_b)["kyc_pan"].status == expected


def test_missing_pan_is_review_and_pan_never_scored(bank_b, worked):
    a = variant(worked, pan=None, pan_consent=False)
    assert checks(a, bank_b)["kyc_pan"].status == CheckStatus.REVIEW
    s1 = run(a, bank_b).base_readiness_score
    s2 = run(variant(worked, pan="ABCPE1234F"), bank_b).base_readiness_score
    assert s1 == pytest.approx(s2)
    with pytest.raises(FairnessError):
        check_field_allowed("pan")


# ------------------------------------------------------------------ APR, amortisation, sensitivity
def test_apr_without_charges_equals_contract_rate():
    i, nominal, effective = calc.apr(800_000, 12, 48, 0)
    assert nominal == pytest.approx(12, abs=1e-6)
    assert effective == pytest.approx(100 * (1.01 ** 12 - 1), abs=1e-6)


def test_apr_with_charges_reproduces_present_value():
    i, nominal, _ = calc.apr(800_000, 12, 48, 18_880)
    e = calc.emi(800_000, 12, 48)
    assert calc.annuity_pv(e, i, 48) == pytest.approx(800_000 - 18_880, rel=1e-9)
    assert nominal > 12


def test_apr_rejects_charges_above_principal():
    with pytest.raises(ValueError):
        calc.apr(100_000, 12, 12, 100_000)


@pytest.mark.parametrize("rate", [0, 9.5, 18])
def test_amortisation_schedule(rate):
    sched = calc.amortisation_schedule(500_000, rate, 36)
    assert sum(r["principal"] for r in sched) == pytest.approx(500_000, rel=1e-9)
    assert sched[-1]["balance"] == pytest.approx(0, abs=1e-6)
    tot = calc.emi_breakdown(500_000, rate, 36).total_interest
    assert sum(r["interest"] for r in sched) == pytest.approx(tot, abs=0.01)
    assert calc.outstanding_balance(500_000, rate, 36, 12) == pytest.approx(sched[11]["balance"], abs=1e-6)


@pytest.mark.parametrize("rate", [0, 6, 12, 24])
def test_emi_rate_sensitivity_matches_finite_difference(rate):
    h = 1e-4
    fd = (calc.emi(800_000, rate + h, 48) - calc.emi(800_000, max(0, rate - h), 48)) / (rate + h - max(0, rate - h))
    assert calc.emi_rate_sensitivity(800_000, rate, 48) == pytest.approx(fd, rel=1e-3)


# ------------------------------------------------------------------ stress model
def test_lognormal_shortfall_against_monte_carlo():
    rng = np.random.default_rng(0)
    mean, cv, thr = 60_000, 0.3, 50_000
    s2 = math.log(1 + cv * cv)
    sample = rng.lognormal(math.log(mean) - s2 / 2, math.sqrt(s2), 400_000)
    assert sample.mean() == pytest.approx(mean, rel=0.01)
    assert calc.lognormal_shortfall_probability(mean, cv, thr) == pytest.approx((sample < thr).mean(), abs=0.003)


def test_shortfall_probability_properties():
    p = [calc.lognormal_shortfall_probability(60_000, 0.25, t) for t in range(20_000, 120_000, 5_000)]
    assert all(b >= a for a, b in zip(p, p[1:]))
    assert calc.lognormal_shortfall_probability(60_000, 0, 59_999) == 0
    assert calc.lognormal_shortfall_probability(60_000, 0, 60_001) == 1


def test_floating_rate_gets_rate_shock(bank_b, worked):
    fixed = run(worked, bank_b).stress
    floating = run(variant(worked, rate_type="floating"), bank_b).stress
    assert fixed.rate_shock_applied_bps == 0 and floating.rate_shock_applied_bps == 200
    assert floating.stressed_emi > fixed.stressed_emi
    assert floating.stressed_foir > fixed.stressed_foir


# ------------------------------------------------------------------ policy rules & fairness
@pytest.mark.parametrize("field", ["religion", "caste_category", "gender", "pincode", "pin_code", "marital_status",
                                   "residence_district", "applicant_name"])
def test_protected_attributes_are_blocked(field):
    with pytest.raises(FairnessError):
        check_field_allowed(field)


def test_every_registered_field_is_allowed():
    for f in FIELD_REGISTRY:
        check_field_allowed(f)


def test_rule_validation_errors():
    with pytest.raises(RuleError):
        validate_rule(PolicyRule(id="X", label="bad", field="no_such_field", operator="<", value=1, action="hard"))
    with pytest.raises(RuleError):
        validate_rule(PolicyRule(id="X", label="bad op", field="existing_customer", operator="<", value=1,
                                 action="hard"))
    with pytest.raises(ValidationError):
        PolicyRule(id="X", label="no authority", field="foir", operator="<", value=0.5, action="deviation")
    warn = validate_rule(PolicyRule(id="X", label="relationship", field="existing_customer", operator="is_true",
                                    action="hard"))
    assert any("excludes whole groups" in w for w in warn)


def _with_rules(cfg, *rules):
    return cfg.model_copy(update={"custom_rules": list(rules)})


def test_custom_rule_changes_outcome_and_missing_is_review(bank_c, worked):
    rule = PolicyRule(id="C-ABB", label="Average balance at least 2 EMIs", stage="Banking behaviour",
                      field="abb_to_emi", operator=">=", value=2.0, action="hard")
    cfg = _with_rules(bank_c, rule)
    low = checks(variant(worked, avg_monthly_balance=10_000), cfg)["rule:C-ABB"]
    assert low.status == CheckStatus.FAIL and low.source == "custom_rule"
    missing = checks(worked, cfg)["rule:C-ABB"]
    assert missing.status == CheckStatus.REVIEW and missing.missing_info
    ignore = _with_rules(bank_c, rule.model_copy(update={"on_missing": "ignore"}))
    assert checks(worked, ignore)["rule:C-ABB"].status == CheckStatus.NOT_APPLICABLE


def test_score_preferences_are_capped(bank_c, worked):
    rules = [PolicyRule(id=f"P{i}", label=f"pref {i}", field="years_in_employment", operator=">=", value=1,
                        action="score", points=10) for i in range(5)]
    r = run(worked, _with_rules(bank_c, *rules))
    assert r.readiness_score - r.base_readiness_score <= bank_c.score_adjustment_cap + 1e-9


def test_preference_cannot_override_hard_fail(bank_c):
    a = PROFILES_BY_KEY["SYN-29"].applicant   # negative-list hit
    rule = PolicyRule(id="P-BIG", label="big preference", field="years_in_employment", operator=">=", value=0,
                      action="score", points=50)
    assert run(a, _with_rules(bank_c, rule)).classification == Classification.UNLIKELY


def test_new_to_credit_has_no_delinquency_record(bank_b):
    c = checks(PROFILES_BY_KEY["SYN-05"].applicant, bank_b)
    assert c["rule:BUR-DPD90"].status == CheckStatus.PASS
    assert c["rule:BUR-WO"].status == CheckStatus.PASS


def test_deviation_tiers_and_classification(banks):
    r = run(PROFILES_BY_KEY["SYN-23"].applicant, banks["bank_b"])   # 45 DPD, B allows up to 60
    dev = {c.key: c for c in r.checks}["rule:BUR-DPD30"]
    assert dev.status == CheckStatus.DEVIATION and dev.deviation_authority == "Regional Credit Head"
    assert r.classification == Classification.BORDERLINE
    worse = variant(PROFILES_BY_KEY["SYN-23"].applicant, max_dpd_12m=75, max_dpd_24m=75)
    assert {c.key: c for c in run(worse, banks["bank_b"]).checks}["rule:BUR-DPD30"].status == CheckStatus.FAIL


def test_deviation_never_classified_likely(banks):
    for p in PROFILES:
        for cfg in banks.values():
            r = run(p.applicant, cfg)
            if any(c.status == CheckStatus.DEVIATION for c in r.checks):
                assert r.classification != Classification.LIKELY


def test_pipeline_stage_status_is_worst_check(banks):
    r = run(PROFILES_BY_KEY["SYN-29"].applicant, banks["bank_b"])
    stages = {s.stage: s.status for s in r.pipeline}
    assert stages["Internal checks"] == CheckStatus.FAIL
    assert stages["KYC"] == CheckStatus.PASS


def test_overlay_roundtrip_and_audit(tmp_path):
    cfg_dir = tmp_path / "config"
    shutil.copytree(CONFIG_DIR, cfg_dir, ignore=shutil.ignore_patterns("overlays"))
    rule = PolicyRule(id="CUS-1", label="Max 2 unsecured loans", stage="Bureau", field="unsecured_loans_active",
                      operator="<=", value=2, action="review", author="tester")
    save_overlay(cfg_dir, "bank_b", [rule], "add", "CUS-1", "tester")
    cfg = available_banks(cfg_dir)["bank_b"]
    assert [r.id for r in cfg.custom_rules] == ["CUS-1"]
    assert "+custom." in cfg.meta.policy_version
    log = read_audit_log(cfg_dir, "bank_b")
    assert log[-1]["action"] == "add" and log[-1]["author"] == "tester"
    bad = PolicyRule(id="CUS-2", label="religion filter", field="religion", operator="==", value=1, action="hard")
    with pytest.raises(FairnessError):
        save_overlay(cfg_dir, "bank_b", [rule, bad], "add", "CUS-2", "tester")


# ------------------------------------------------------------------ scorecard maths
def test_auc_matches_pairwise_definition():
    rng = np.random.default_rng(1)
    y = rng.integers(0, 2, 200).astype(float)
    s = np.round(rng.normal(size=200) + y, 1)   # rounding creates ties
    pos, neg = s[y == 1], s[y == 0]
    brute = np.mean([(p > n) + 0.5 * (p == n) for p in pos for n in neg])
    assert am.auc(y, s) == pytest.approx(brute, abs=1e-12)


def test_ks_and_psi():
    y = np.array([0, 0, 1, 1.0])
    assert am.ks_stat(y, np.array([0.1, 0.2, 0.8, 0.9])) == pytest.approx(1.0)
    x = np.random.default_rng(2).normal(size=5000)
    assert am.psi(x, x) == pytest.approx(0, abs=1e-9)
    assert am.psi(x, x + 1) > 0.25


def test_pav_is_monotone_least_squares():
    y = np.array([1, 3, 2, 4, 3, 5.0])
    fit = am.pav(y)
    assert all(b >= a for a, b in zip(fit, fit[1:]))
    assert fit.tolist() == [1, 2.5, 2.5, 3.5, 3.5, 5]


def test_logistic_irls_reaches_stationary_point():
    rng = np.random.default_rng(3)
    X = rng.normal(size=(800, 3))
    y = (rng.random(800) < am.sigmoid(0.5 + X @ np.array([1.0, -2.0, 0.5]))).astype(float)
    beta, cov = am.fit_logistic(X, y, l2=1.0)
    X1 = np.column_stack([np.ones(800), X])
    grad = X1.T @ (y - am.sigmoid(X1 @ beta)) - np.r_[0, beta[1:]]
    assert np.max(np.abs(grad)) < 1e-6
    assert np.all(np.linalg.eigvalsh(cov) > 0)
    assert beta[2] < 0 < beta[1]


def test_woe_bins_are_monotone():
    rng = np.random.default_rng(4)
    x = rng.uniform(0, 1, 3000)
    y = (rng.random(3000) < 0.8 - 0.6 * x + 0.1 * np.sin(20 * x)).astype(float)
    b = am.fit_bins(x, y, n_bins=8, direction=-1)
    assert all(w2 <= w1 for w1, w2 in zip(b["woe"], b["woe"][1:]))
    assert b["iv"] > 0


@pytest.fixture(scope="module")
def trained(banks):
    cfg = banks["bank_b"]
    return cfg, am.train(generate(cfg, n=3000, seed=7), cfg, "synthetic", True)


def test_trained_model_passes_gate_and_is_calibrated(trained):
    cfg, m = trained
    assert m["gate"]["passed"], m["gate"]["reasons"]
    assert m["metrics"]["test_auc"] > 0.75
    assert m["metrics"]["test_ece"] < cfg.model_gate.max_test_ece
    assert m["period_train"][1] <= m["period_test"][0]   # out-of-time split


def test_probability_never_changes_classification(trained):
    cfg, m = trained
    am.save_model(m)
    try:
        for p in PROFILES:
            with_m, without = run(p.applicant, cfg), run(p.applicant, cfg, use_model=False)
            assert with_m.classification == without.classification
            if with_m.probability is not None:
                pr = with_m.probability
                assert 0 <= pr.lower_95 <= pr.probability <= pr.upper_95 <= 1
                assert pr.synthetic
            if with_m.classification == Classification.INSUFFICIENT:
                assert with_m.probability is None   # no number from mostly-missing inputs
        assert run(PROFILES_BY_KEY["WORKED-EX"].applicant, cfg).probability is not None
    finally:
        am.delete_model(cfg.meta.bank_id)


def test_policy_change_disables_saved_probability(trained):
    cfg, model = trained
    applicant = PROFILES_BY_KEY["WORKED-EX"].applicant
    am.save_model(model)
    try:
        assert run(applicant, cfg).probability is not None
        changed_meta = cfg.meta.model_copy(update={"policy_version": cfg.meta.policy_version + "+changed"})
        changed_cfg = cfg.model_copy(update={"meta": changed_meta})
        assert run(applicant, changed_cfg).probability is None
        assessment = run(applicant, changed_cfg, use_model=False)
        assert am.predict(model, assessment, changed_cfg) is None
    finally:
        am.delete_model(cfg.meta.bank_id)


def test_probability_monotone_in_foir(trained):
    cfg, m = trained
    am.save_model(m)
    try:
        base = PROFILES_BY_KEY["WORKED-EX"].applicant
        probs = [run(variant(base, requested_amount=amt), cfg).probability.probability
                 for amt in range(200_000, 1_200_001, 100_000)]
        assert all(b <= a + 1e-9 for a, b in zip(probs, probs[1:]))
    finally:
        am.delete_model(cfg.meta.bank_id)


def test_gate_blocks_small_samples(banks):
    cfg = banks["bank_b"]
    m = am.train(generate(cfg, n=400, seed=5), cfg, "synthetic", True)
    assert not m["gate"]["passed"]
    am.save_model(m)
    try:
        assert run(PROFILES_BY_KEY["SYN-01"].applicant, cfg).probability is None
    finally:
        am.delete_model(cfg.meta.bank_id)


def test_model_artifact_is_json_and_has_no_pan(trained):
    _, m = trained
    blob = json.dumps(m)
    assert "pan" not in json.loads(blob)["features"]
