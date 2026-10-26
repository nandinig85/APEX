"""Integration tests over the synthetic borrower profiles."""
import pytest

from engine import assess
from models import CheckStatus, Classification as C, FactorStatus
from profiles import PROFILES, PROFILES_BY_KEY
from reports import report_json, report_markdown

U, B, L, I = C.UNLIKELY, C.BORDERLINE, C.LIKELY, C.INSUFFICIENT

# Expected classification under (Bank A, Bank B, Bank C). Pinning these makes
# any change to the rules or fictional configs a visible, reviewed diff.
EXPECTED = {
    "SYN-01": (L, L, L),
    "SYN-02": (U, U, L),   # income below A/B minimum
    "SYN-04": (U, B, L),   # informal excluded by A; FOIR deviation at B
    "SYN-05": (U, B, L),   # first-time borrower: tenure (A), NTC review (B), accepted (C)
    "SYN-06": (B, B, B),   # unknown credit score -> review
    "SYN-07": (U, U, B),   # high existing EMIs; within C's FOIR deviation band
    "SYN-10": (B, B, B),   # missing documents -> review, not failure
    "SYN-11": (U, L, L),   # age at maturity fails only at A
    "SYN-12": (U, U, U),   # above product maximum
    "SYN-13": (U, U, U),   # EMIs exceed FOIR capacity
    "SYN-14": (L, L, L),
    "SYN-15": (I, I, I),
    "SYN-16": (U, U, U),   # below minimum age
    "SYN-17": (U, B, L),   # score range straddles B's minimum
    "SYN-23": (U, B, L),   # 45 DPD: hard at A, deviation at B, no 30-DPD rule at C
    "SYN-24": (U, U, B),   # settlement: hard at A/B, deviation at C
    "SYN-25": (B, B, B),   # enquiries -> review
    "SYN-26": (U, U, U),   # company PAN on a personal loan -> KYC FAIL
    "SYN-27": (B, B, B),   # inoperative PAN -> KYC REVIEW
    "SYN-29": (U, U, U),   # internal negative list -> hard
    "SYN-30": (U, L, L),   # score 720 below A's floor; preferences at B/C
}


def test_at_least_twenty_synthetic_profiles():
    assert len([p for p in PROFILES if p.key.startswith("SYN-")]) >= 20


@pytest.mark.parametrize("key", sorted(EXPECTED))
def test_expected_classifications(banks, key):
    got = tuple(assess(PROFILES_BY_KEY[key].applicant, banks[b], timestamp="t").classification
                for b in ("bank_a", "bank_b", "bank_c"))
    assert got == EXPECTED[key]


@pytest.mark.parametrize("profile", PROFILES, ids=lambda p: p.key)
def test_every_profile_assesses_and_reports(banks, profile):
    for cfg in banks.values():
        r = assess(profile.applicant, cfg, timestamp="2026-10-24T00:00:00+00:00")
        md = report_markdown(r)
        for section in ("Applicant reference", "Selected bank configuration", "Input summary",
                        "Calculated financial metrics", "Hard eligibility checks", "Readiness score",
                        "Potential concerns", "Missing documentation", "Suggested manual review actions",
                        "Assumptions and limitations"):
            assert section in md
        assert cfg.meta.policy_version in md and "2026-10-24T00:00:00" in md
        assert report_json(r)


def test_first_time_borrower_has_no_artificial_utilisation(banks):
    r = assess(PROFILES_BY_KEY["SYN-05"].applicant, banks["bank_b"], timestamp="t")
    f = {x.factor: x for x in r.factors}
    assert f["credit_utilisation"].status == FactorStatus.NOT_APPLICABLE
    assert f["credit_score"].status == FactorStatus.NOT_APPLICABLE


def test_unstable_income_reduces_usable_income(banks):
    r = assess(PROFILES_BY_KEY["SYN-09"].applicant, banks["bank_b"], timestamp="t")
    assert r.income.coefficient_of_variation > 0.4
    assert r.income.variability_factor < 1
    assert r.metrics["income_stability"].value < 60


def test_heavy_obligations_lower_residual_income(banks):
    r = assess(PROFILES_BY_KEY["SYN-21"].applicant, banks["bank_a"], timestamp="t")
    assert r.metrics["residual_income"].value < banks["bank_a"].indicators.min_residual_income
    assert r.metrics["residual_income"].indicator.value == "amber"


def test_zero_interest_profile(banks):
    r = assess(PROFILES_BY_KEY["SYN-20"].applicant, banks["bank_b"], timestamp="t")
    assert r.emi.emi == 10_000 and r.emi.total_interest == 0
