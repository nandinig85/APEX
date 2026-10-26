"""Tests for the v3 decision advisor: maximum amount, counter-offers, reasons, improvements, letter."""
import math
from dataclasses import replace

import numpy as np
import pytest

import calculations as calc
from advisor import (advise, build_ctx, limits_at, max_amount, max_existing_emis, required_income, rerun,
                     round_down, tenure_for)
from engine import assess
from models import CheckStatus, Classification
from profiles import PROFILES, PROFILES_BY_KEY
from reports import customer_letter, report_markdown

TS = "2026-10-26T00:00:00+00:00"


def run(key, cfg):
    return assess(PROFILES_BY_KEY[key].applicant, cfg, timestamp=TS, use_model=False)


@pytest.fixture(scope="module")
def all_advice(banks):
    out = []
    for p in PROFILES:
        for b, cfg in banks.items():
            r = assess(p.applicant, cfg, timestamp=TS, use_model=False)
            out.append((p.key, b, cfg, r, advise(r, cfg)))
    return out


# --------------------------------------------------------------------------
# New maths
# --------------------------------------------------------------------------
@pytest.mark.parametrize("p", [1e-12, 1e-6, 0.001, 0.02, 0.2, 0.5, 0.8, 0.975, 0.999, 1 - 1e-9])
def test_normal_ppf_inverts_cdf(p):
    assert calc.normal_cdf(calc.normal_ppf(p)) == pytest.approx(p, rel=1e-9, abs=1e-15)


def test_normal_ppf_known_values():
    assert calc.normal_ppf(0.5) == pytest.approx(0.0, abs=1e-12)
    assert calc.normal_ppf(0.975) == pytest.approx(1.959963984540054, abs=1e-10)
    assert calc.normal_ppf(0.2) == pytest.approx(-0.8416212335729143, abs=1e-10)
    for bad in (0, 1, -0.1, 1.5):
        with pytest.raises(ValueError):
            calc.normal_ppf(bad)


@pytest.mark.parametrize("cv, p", [(0.05, 0.2), (0.3, 0.2), (0.6, 0.05), (1.0, 0.5)])
def test_lognormal_quantile_factor_matches_shortfall_probability(cv, p):
    m = 50_000.0
    k = calc.lognormal_quantile_factor(cv, p)
    assert calc.lognormal_shortfall_probability(m, cv, k * m) == pytest.approx(p, abs=1e-10)
    assert calc.lognormal_quantile_factor(0.0, p) == 1.0


def test_lognormal_quantile_factor_monte_carlo():
    cv, p, m = 0.35, 0.2, 40_000.0
    s2 = math.log(1 + cv * cv)
    y = np.random.default_rng(3).lognormal(math.log(m) - s2 / 2, math.sqrt(s2), 400_000)
    assert np.quantile(y, p) == pytest.approx(calc.lognormal_quantile_factor(cv, p) * m, rel=5e-3)


@pytest.mark.parametrize("rate, n", [(0.0, 12), (12.0, 48), (18.5, 60), (9.0, 240)])
def test_annuity_factor_is_inverse_emi(rate, n):
    assert calc.emi(calc.annuity_factor(rate, n), rate, n) == pytest.approx(1.0, rel=1e-12)


def test_round_down():
    assert round_down(99_999) == 99_000
    assert round_down(635_252) == 635_000
    assert round_down(-5) == 0


# --------------------------------------------------------------------------
# Maximum amount
# --------------------------------------------------------------------------
def test_worked_example_bank_b(banks):
    cfg = banks["bank_b"]
    r = run("WORKED-EX", cfg)
    d = advise(r, cfg)
    lim = {x.key: x for x in d.limits}
    assert lim["foir"].max_amount == pytest.approx(649_355, abs=1)
    assert lim["stress"].max_amount == pytest.approx(672_139, abs=1)
    assert lim["residual"].max_amount == pytest.approx(1_443_010, abs=1)
    assert lim["volatility"].max_amount == pytest.approx(635_252, abs=1)
    assert lim["income_multiple"].max_amount == pytest.approx(1_440_000)
    assert lim["volatility"].binding and sum(x.binding for x in d.limits) == 1
    assert d.max_eligible_amount == 635_000 and d.recommended_amount == 635_000
    assert d.recommended_emi == pytest.approx(16_722, abs=1)
    assert d.required_income == pytest.approx(69_061, abs=1)
    assert d.max_existing_emis == pytest.approx(7_662, abs=1)
    rec = [o for o in d.options if o.recommended]
    assert len(rec) == 1 and rec[0].amount == 750_000 and rec[0].tenure_months == 60


def test_max_eligible_is_rounded_minimum_and_within_capacity(all_advice):
    for key, b, cfg, r, d in all_advice:
        if d.max_eligible_amount is None:
            continue
        assert d.max_eligible_amount == round_down(min(x.max_amount for x in d.limits)), (key, b)
        if r.capacity is not None and r.applicant.tenure_months == d.tenure_used:
            assert d.max_eligible_amount <= r.capacity.upper + 1e-6, (key, b)


def test_checks_pass_at_the_recommended_amount(all_advice):
    """By construction, FOIR and the stress test pass at the recommended amount."""
    for key, b, cfg, r, d in all_advice:
        if d.recommended_amount is None:
            continue
        x = rerun(r, cfg, requested_amount=d.recommended_amount, tenure_months=d.tenure_used)
        c = {ch.key: ch for ch in x.checks}
        assert c["foir"].status == CheckStatus.PASS, (key, b)
        assert c["stress_test"].status == CheckStatus.PASS, (key, b)
        assert not any(ch.status == CheckStatus.FAIL for ch in x.checks), (key, b)


def test_limits_monotone(banks):
    cfg = banks["bank_b"]
    ctx = build_ctx(run("WORKED-EX", cfg), cfg)
    base = max_amount(ctx, cfg, 48)[0]
    assert max_amount(replace(ctx, income=ctx.income + 5_000), cfg, 48)[0] >= base
    assert max_amount(replace(ctx, existing=ctx.existing + 2_000), cfg, 48)[0] <= base
    assert max_amount(replace(ctx, rate=ctx.rate + 2), cfg, 48)[0] <= base
    seq = [max_amount(ctx, cfg, n)[0] for n in range(12, 61, 6)]
    assert all(b >= a - 1e-6 for a, b in zip(seq, seq[1:]))
    ys = np.linspace(10_000, 200_000, 40)
    m = np.array([max_amount(replace(ctx, income=float(y)), cfg, 48)[0] for y in ys])
    # M = max(0, min of affine functions): concave wherever it is positive.
    pos = m > 0
    d2 = np.diff(m[pos], 2)
    assert pos.sum() > 10 and (d2 <= 1e-6).all()
    assert (np.diff(m) >= -1e-6).all()


# --------------------------------------------------------------------------
# Closed-form counter-offers are tight
# --------------------------------------------------------------------------
@pytest.mark.parametrize("key", ["WORKED-EX", "SYN-03", "SYN-07", "SYN-28", "SYN-18"])
def test_required_income_and_max_emis_are_tight(banks, key):
    cfg = banks["bank_b"]
    r = run(key, cfg)
    ctx = build_ctx(r, cfg)
    L = r.applicant.requested_amount
    n = min(max(r.applicant.tenure_months, ctx.n_min), ctx.n_max)
    y = required_income(ctx, cfg, L, n)
    assert y is not None
    assert max_amount(replace(ctx, income=y), cfg, n)[0] == pytest.approx(L, rel=1e-9) or \
        max_amount(replace(ctx, income=y), cfg, n)[0] >= L - 1e-6
    assert max_amount(replace(ctx, income=y * 0.999), cfg, n)[0] < L
    x = max_existing_emis(ctx, cfg, L, n)
    if x is not None:
        assert max_amount(replace(ctx, existing=x), cfg, n)[0] >= L - 1e-6
        assert max_amount(replace(ctx, existing=x + 50), cfg, n)[0] < L


def test_shortest_tenure_is_minimal(banks):
    cfg = banks["bank_b"]
    r = run("SYN-04", cfg)
    ctx = build_ctx(r, cfg)
    L = r.applicant.requested_amount
    n = tenure_for(ctx, cfg, L, r.applicant.tenure_months)
    assert n is not None and n > r.applicant.tenure_months
    assert max_amount(ctx, cfg, n)[0] >= L > max_amount(ctx, cfg, n - 1)[0]


# --------------------------------------------------------------------------
# Verdicts, blockers, options, improvements
# --------------------------------------------------------------------------
def test_blocker_not_cured_by_amount(banks):
    d = advise(run("SYN-16", banks["bank_b"]), banks["bank_b"])          # below minimum age
    assert d.tone == "red" and d.recommended_amount is None
    assert any("Minimum age" in b for b in d.blockers)
    assert any(i.key == "age" for i in d.improvements)


def test_foir_overload_is_not_a_blocker(banks):
    d = advise(run("SYN-13", banks["bank_b"]), banks["bank_b"])          # FOIR > 100 %
    assert d.blockers == [] and d.recommended_amount is None and d.tone == "red"
    assert "do not support" in d.verdict
    assert any(i.key == "existing_emis" for i in d.improvements)


def test_insufficient_information_is_grey(banks):
    r = run("SYN-15", banks["bank_b"])
    d = advise(r, banks["bank_b"])
    assert r.classification == Classification.INSUFFICIENT
    assert d.tone == "grey" and d.recommended_amount is None


def test_full_request_fits(banks):
    d = advise(run("SYN-01", banks["bank_b"]), banks["bank_b"])
    assert d.tone == "green" and d.recommended_amount == d.requested_amount
    assert any(x.title == "Request fits every affordability limit" for x in d.why)
    assert [o.key for o in d.options if o.recommended] == ["requested"]


def test_options_are_reassessed_and_recommendation_is_feasible(all_advice):
    for key, b, cfg, r, d in all_advice:
        rec = [o for o in d.options if o.recommended]
        assert len(rec) <= 1
        if rec:
            assert rec[0].fails == 0 and rec[0].classification != Classification.INSUFFICIENT
        req = d.options[0]
        assert req.key == "requested" and req.classification == r.classification
        for o in d.options:
            if o.key == "longer":
                assert o.emi < req.emi and o.tenure_months > req.tenure_months


def test_improvements_measured(banks):
    cfg = banks["bank_b"]
    r = run("WORKED-EX", cfg)
    d = advise(r, cfg)
    emis = next(i for i in d.improvements if i.key == "existing_emis")
    assert emis.classification_before == Classification.BORDERLINE
    assert emis.classification_after == Classification.LIKELY
    assert emis.amount_after >= r.applicant.requested_amount > emis.amount_before
    assert emis.score_after > emis.score_before
    assert d.improvements[0].key == "existing_emis"           # ranked by impact
    assert any(i.key == "combined" for i in d.improvements)


def test_improvements_never_hurt_the_score(all_advice):
    for key, b, cfg, r, d in all_advice:
        for i in d.improvements:
            if i.quantified and i.key not in ("fixed_rate",) and i.score_before is not None and i.score_after is not None:
                assert i.score_after >= i.score_before - 1e-9, (key, b, i.key)


def test_why_not_orders_blockers_first(all_advice):
    order = {"blocker": 0, "concern": 1, "missing": 2}
    for key, b, cfg, r, d in all_advice:
        kinds = [order[x.kind] for x in d.why_not]
        assert kinds == sorted(kinds), (key, b)
        if any(c.status == CheckStatus.FAIL for c in r.checks):
            assert d.why_not and d.why_not[0].kind == "blocker", (key, b)


def test_advice_is_deterministic(banks):
    cfg = banks["bank_a"]
    r = run("SYN-28", cfg)
    assert advise(r, cfg).model_dump() == advise(r, cfg).model_dump()


# --------------------------------------------------------------------------
# Customer letter and report
# --------------------------------------------------------------------------
def test_letter_never_leaks_pan_or_internal_scores(all_advice):
    for key, b, cfg, r, d in all_advice:
        letter = customer_letter(r, d)
        raw = r.applicant.pan.get_secret_value() if r.applicant.pan else None
        if raw:
            assert raw not in letter
            assert raw not in report_markdown(r, d)
        low = letter.lower()
        assert "readiness" not in low and "probability" not in low
        assert "not a loan sanction or a guarantee" in letter
        assert "assumed interest rate" in low


def test_report_includes_decision_section(banks):
    cfg = banks["bank_b"]
    r = run("WORKED-EX", cfg)
    md = report_markdown(r, advise(r, cfg))
    assert "Decision support: how much, why and why not" in md
    assert "Where to improve" in md and "Income-volatility limit" in md
    assert "Conditional affordability ceiling" in md


def test_limits_table_marks_exactly_one_binding(all_advice):
    for key, b, cfg, r, d in all_advice:
        if d.limits:
            assert sum(x.binding for x in d.limits) == 1
            ctx = build_ctx(r, cfg)
            assert [x.max_amount for x in limits_at(ctx, cfg, d.tenure_used)] == [x.max_amount for x in d.limits]
