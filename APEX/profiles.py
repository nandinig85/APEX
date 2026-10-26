"""Synthetic borrower profiles. Entirely fictional test data - no real persons."""
from __future__ import annotations

from dataclasses import dataclass

from models import ApplicantInput

A, NA, UNK, NAPP = "available", "not_available", "unknown", "not_applicable"
SALARIED_DOCS = {"identity": A, "salary_slips": A, "bank_statements": A, "income_proof": A}
SE_DOCS = {"identity": A, "bank_statements": A, "itr": A, "gst": A, "income_proof": A}


@dataclass(frozen=True)
class Profile:
    key: str
    title: str
    description: str
    applicant: ApplicantInput


# A clean, verified KYC + bureau + banking record. Profiles override what they test.
# PANs are synthetic, format-valid test values and do not refer to real people.
CLEAN = dict(pan_consent=True, pan_status="verified", max_dpd_12m=0, max_dpd_24m=0,
             written_off_or_settled_36m=False, enquiries_3m=0, enquiries_6m=1, credit_history_months=60,
             unsecured_loans_active=0, emi_bounces_6m=0, internal_negative_list_hit=False, existing_customer=False)
_counter = iter(range(1, 1000))


def _p(key, title, description, clean=True, **kw) -> Profile:
    data = {}
    if clean:
        data.update(CLEAN, pan=f"ZZZPX{next(_counter):04d}Z")
        if kw.get("no_credit_history"):
            for k in ("max_dpd_12m", "max_dpd_24m", "written_off_or_settled_36m", "credit_history_months"):
                data.pop(k)
    data.update(kw)
    return Profile(key, title, description, ApplicantInput(applicant_id=key, **data))


PROFILES: list[Profile] = [
    _p("SYN-01", "High-income salaried", "Senior salaried professional, strong credit, verified income.",
       age=35, employment_category="salaried", requested_amount=1_500_000, tenure_months=60, annual_interest_rate=12,
       net_monthly_income=250_000, verified_monthly_income=250_000, years_in_employment=8, existing_monthly_emis=20_000,
       active_loan_count=1, has_credit_card=True, credit_card_limit=500_000, credit_card_balance=50_000,
       credit_score=800, continuous_employment_12m=True, documents=SALARIED_DOCS),
    _p("SYN-02", "Low-income salaried", "Entry-level salaried employee below some minimum-income rules.",
       age=26, employment_category="salaried", requested_amount=150_000, tenure_months=36, annual_interest_rate=14,
       net_monthly_income=18_000, verified_monthly_income=18_000, years_in_employment=1.5, existing_monthly_emis=0,
       active_loan_count=0, has_credit_card=False, credit_score=710, continuous_employment_12m=True,
       documents=SALARIED_DOCS),
    _p("SYN-03", "Self-employed", "Established shop owner with 12 months of moderately variable income.",
       age=40, employment_category="self_employed", requested_amount=1_000_000, tenure_months=60,
       annual_interest_rate=15, net_monthly_income=90_000, years_in_employment=6, existing_monthly_emis=15_000,
       monthly_income_observations=[85_000, 92_000, 78_000, 95_000, 88_000, 101_000,
                                    83_000, 90_000, 97_000, 80_000, 94_000, 87_000],
       active_loan_count=1, has_credit_card=True, credit_card_limit=300_000, credit_card_balance=60_000,
       credit_score=760, continuous_employment_12m=True, documents=SE_DOCS),
    _p("SYN-04", "Informal income", "Cash-income worker, new to credit, highly variable income.",
       age=38, employment_category="informal", requested_amount=200_000, tenure_months=36, annual_interest_rate=18,
       net_monthly_income=30_000, years_in_employment=5, existing_monthly_emis=0, active_loan_count=0,
       monthly_income_observations=[22_000, 35_000, 28_000, 40_000, 18_000, 33_000],
       income_variability="high", has_credit_card=False, no_credit_history=True,
       documents={"identity": A, "bank_statements": A, "income_proof": NA, "upi_history": A}),
    _p("SYN-05", "First-time borrower", "Young salaried employee, no bureau history, no existing loans.",
       age=23, employment_category="salaried", requested_amount=200_000, tenure_months=24, annual_interest_rate=13,
       net_monthly_income=35_000, verified_monthly_income=35_000, years_in_employment=1.2, existing_monthly_emis=0,
       active_loan_count=0, has_credit_card=False, no_credit_history=True, continuous_employment_12m=True,
       documents=SALARIED_DOCS),
    _p("SYN-06", "No credit score available", "Salaried applicant whose score is simply unknown (not new to credit).",
       age=30, employment_category="salaried", requested_amount=400_000, tenure_months=48, annual_interest_rate=13,
       net_monthly_income=50_000, verified_monthly_income=50_000, years_in_employment=3, existing_monthly_emis=5_000,
       active_loan_count=1, has_credit_card=True, credit_card_limit=100_000, credit_card_balance=20_000,
       continuous_employment_12m=True, documents=SALARIED_DOCS),
    _p("SYN-07", "High existing EMIs", "Salaried applicant already servicing a home and car loan.",
       age=45, employment_category="salaried", requested_amount=500_000, tenure_months=48, annual_interest_rate=13,
       net_monthly_income=80_000, verified_monthly_income=80_000, years_in_employment=10, existing_monthly_emis=35_000,
       active_loan_count=2, has_credit_card=True, credit_card_limit=200_000, credit_card_balance=40_000,
       credit_score=770, continuous_employment_12m=True, documents=SALARIED_DOCS),
    _p("SYN-08", "High credit utilisation", "Salaried applicant with cards close to their limit.",
       age=33, employment_category="salaried", requested_amount=300_000, tenure_months=36, annual_interest_rate=13,
       net_monthly_income=70_000, verified_monthly_income=70_000, years_in_employment=5, existing_monthly_emis=8_000,
       active_loan_count=1, has_credit_card=True, credit_card_limit=300_000, credit_card_balance=260_000,
       credit_score=690, continuous_employment_12m=True, documents=SALARIED_DOCS),
    _p("SYN-09", "Unstable income", "Young self-employed consultant with large month-to-month swings.",
       age=29, employment_category="self_employed", requested_amount=500_000, tenure_months=48,
       annual_interest_rate=16, net_monthly_income=60_000, years_in_employment=1.5, existing_monthly_emis=5_000,
       monthly_income_observations=[20_000, 95_000, 40_000, 110_000, 15_000, 70_000,
                                    30_000, 85_000, 25_000, 60_000, 45_000, 100_000],
       active_loan_count=1, has_credit_card=True, credit_card_limit=150_000, credit_card_balance=30_000,
       credit_score=720, continuous_employment_12m=True, documents=SE_DOCS),
    _p("SYN-10", "Missing documents", "Otherwise sound salaried applicant who has not supplied documents yet.",
       age=31, employment_category="salaried", requested_amount=300_000, tenure_months=36, annual_interest_rate=13,
       net_monthly_income=55_000, years_in_employment=4, existing_monthly_emis=5_000, active_loan_count=1,
       has_credit_card=True, credit_card_limit=100_000, credit_card_balance=15_000, credit_score=760,
       continuous_employment_12m=True,
       documents={"identity": A, "salary_slips": NA, "bank_statements": UNK, "income_proof": NA}),
    _p("SYN-11", "Close to maximum age", "Salaried applicant aged 55 requesting a 5-year loan.",
       age=55, employment_category="salaried", requested_amount=600_000, tenure_months=60, annual_interest_rate=12,
       net_monthly_income=100_000, verified_monthly_income=100_000, years_in_employment=20, existing_monthly_emis=10_000,
       active_loan_count=1, has_credit_card=True, credit_card_limit=300_000, credit_card_balance=30_000,
       credit_score=790, continuous_employment_12m=True, documents=SALARIED_DOCS),
    _p("SYN-12", "Unusually large loan", "Salaried applicant requesting INR 50 lakh unsecured over 7 years.",
       age=34, employment_category="salaried", requested_amount=5_000_000, tenure_months=84, annual_interest_rate=13,
       net_monthly_income=120_000, verified_monthly_income=120_000, years_in_employment=7, existing_monthly_emis=0,
       active_loan_count=0, has_credit_card=True, credit_card_limit=400_000, credit_card_balance=40_000,
       credit_score=780, continuous_employment_12m=True, documents=SALARIED_DOCS),
    _p("SYN-13", "EMIs exceed FOIR capacity", "Self-employed applicant whose existing EMIs exceed the FOIR cap.",
       age=42, employment_category="self_employed", requested_amount=300_000, tenure_months=36,
       annual_interest_rate=15, net_monthly_income=50_000, verified_monthly_income=50_000, years_in_employment=9,
       existing_monthly_emis=30_000, active_loan_count=3, has_credit_card=True, credit_card_limit=100_000,
       credit_card_balance=45_000, credit_score=730, continuous_employment_12m=True, documents=SE_DOCS),
    _p("SYN-14", "No existing debt", "Salaried applicant with no loans and a lightly used card.",
       age=28, employment_category="salaried", requested_amount=300_000, tenure_months=36, annual_interest_rate=12,
       net_monthly_income=65_000, verified_monthly_income=65_000, years_in_employment=3, existing_monthly_emis=0,
       active_loan_count=0, has_credit_card=True, credit_card_limit=200_000, credit_card_balance=10_000,
       credit_score=780, continuous_employment_12m=True, documents=SALARIED_DOCS),
    _p("SYN-15", "Incomplete financial information", "Only the loan request, age and category are known.",
       clean=False, age=36, employment_category="salaried", requested_amount=400_000, tenure_months=48, annual_interest_rate=13),
    _p("SYN-16", "Below minimum age", "Applicant aged 20, below every fictional bank's minimum age.",
       age=20, employment_category="salaried", requested_amount=100_000, tenure_months=24, annual_interest_rate=14,
       net_monthly_income=30_000, verified_monthly_income=30_000, years_in_employment=1, existing_monthly_emis=0,
       active_loan_count=0, has_credit_card=False, no_credit_history=True, continuous_employment_12m=True,
       documents=SALARIED_DOCS),
    _p("SYN-17", "Credit score range only", "Applicant knows only that their score is 680-720.",
       age=37, employment_category="salaried", requested_amount=350_000, tenure_months=36, annual_interest_rate=13,
       net_monthly_income=60_000, verified_monthly_income=60_000, years_in_employment=6, existing_monthly_emis=6_000,
       active_loan_count=1, has_credit_card=True, credit_card_limit=150_000, credit_card_balance=30_000,
       credit_score_range_low=680, credit_score_range_high=720, continuous_employment_12m=True, documents=SALARIED_DOCS),
    _p("SYN-18", "Informal, declaration only", "Informal worker with declared income only; documents not yet recorded.",
       age=44, employment_category="informal", requested_amount=150_000, tenure_months=24, annual_interest_rate=18,
       net_monthly_income=28_000, years_in_employment=10, existing_monthly_emis=2_000, has_credit_card=False,
       credit_score=705),
    _p("SYN-19", "Young business, GST not applicable", "Self-employed for 1.5 years, below GST threshold.",
       age=27, employment_category="self_employed", requested_amount=250_000, tenure_months=36,
       annual_interest_rate=16, net_monthly_income=45_000, years_in_employment=1.5, existing_monthly_emis=0,
       income_variability="medium", active_loan_count=0, has_credit_card=True, credit_card_limit=50_000,
       credit_card_balance=10_000, credit_score=735, continuous_employment_12m=True,
       documents={"identity": A, "bank_statements": A, "itr": A, "gst": NAPP}),
    _p("SYN-20", "Zero-interest promotional scenario", "Edge case: 0% rate, so EMI = P / n.",
       age=30, employment_category="salaried", requested_amount=120_000, tenure_months=12, annual_interest_rate=0,
       net_monthly_income=45_000, verified_monthly_income=45_000, years_in_employment=2, existing_monthly_emis=3_000,
       active_loan_count=1, has_credit_card=False, credit_score=745, continuous_employment_12m=True,
       documents=SALARIED_DOCS),
    _p("SYN-21", "Heavy other obligations", "Affordable on FOIR, but high verified rent reduces residual income.",
       age=32, employment_category="salaried", requested_amount=400_000, tenure_months=48, annual_interest_rate=13,
       net_monthly_income=55_000, verified_monthly_income=55_000, years_in_employment=4, existing_monthly_emis=5_000,
       other_recurring_obligations=25_000, active_loan_count=1, has_credit_card=True, credit_card_limit=100_000,
       credit_card_balance=20_000, credit_score=750, continuous_employment_12m=True, documents=SALARIED_DOCS),
    _p("SYN-22", "Recent job change with gap", "Salaried, 7 months in current job after an employment gap.",
       age=29, employment_category="salaried", requested_amount=250_000, tenure_months=36, annual_interest_rate=13,
       net_monthly_income=48_000, verified_monthly_income=48_000, years_in_employment=0.6, existing_monthly_emis=4_000,
       active_loan_count=1, has_credit_card=True, credit_card_limit=80_000, credit_card_balance=20_000,
       credit_score=735, continuous_employment_12m=False, documents=SALARIED_DOCS),
    _p("SYN-23", "Recent 45-day delinquency", "Sound salaried applicant with a 45 DPD in the last 12 months.",
       age=34, employment_category="salaried", requested_amount=300_000, tenure_months=36, annual_interest_rate=13,
       net_monthly_income=70_000, verified_monthly_income=70_000, years_in_employment=6, existing_monthly_emis=8_000,
       active_loan_count=1, has_credit_card=True, credit_card_limit=200_000, credit_card_balance=30_000,
       credit_score=760, continuous_employment_12m=True, documents=SALARIED_DOCS, max_dpd_12m=45, max_dpd_24m=45),
    _p("SYN-24", "Settled account", "A loan was settled (not paid in full) 2 years ago.",
       age=39, employment_category="salaried", requested_amount=300_000, tenure_months=36, annual_interest_rate=14,
       net_monthly_income=75_000, verified_monthly_income=75_000, years_in_employment=7, existing_monthly_emis=5_000,
       active_loan_count=1, has_credit_card=False, credit_score=760, continuous_employment_12m=True,
       documents=SALARIED_DOCS, written_off_or_settled_36m=True),
    _p("SYN-25", "Credit-hungry applicant", "Six bureau enquiries in 3 months, nine in 6 months.",
       age=31, employment_category="salaried", requested_amount=300_000, tenure_months=36, annual_interest_rate=13,
       net_monthly_income=65_000, verified_monthly_income=65_000, years_in_employment=4, existing_monthly_emis=6_000,
       active_loan_count=2, unsecured_loans_active=2, has_credit_card=True, credit_card_limit=150_000,
       credit_card_balance=40_000, credit_score=745, continuous_employment_12m=True, documents=SALARIED_DOCS,
       enquiries_3m=6, enquiries_6m=9),
    _p("SYN-26", "Business PAN supplied", "A company PAN (holder type C) was recorded for a personal loan.",
       age=41, employment_category="self_employed", requested_amount=500_000, tenure_months=48,
       annual_interest_rate=15, net_monthly_income=120_000, verified_monthly_income=120_000, years_in_employment=8,
       existing_monthly_emis=10_000, has_credit_card=False, credit_score=770, continuous_employment_12m=True,
       documents=SE_DOCS, pan="ZZZCX9001Z"),
    _p("SYN-27", "Inoperative PAN", "PAN not linked to Aadhaar; otherwise strong profile.",
       age=36, employment_category="salaried", requested_amount=400_000, tenure_months=48, annual_interest_rate=12,
       net_monthly_income=90_000, verified_monthly_income=90_000, years_in_employment=9, existing_monthly_emis=10_000,
       has_credit_card=True, credit_card_limit=300_000, credit_card_balance=30_000, credit_score=790,
       continuous_employment_12m=True, documents=SALARIED_DOCS, pan_status="inoperative"),
    _p("SYN-28", "Floating rate, thin margin", "Floating-rate loan near the FOIR cap; fails the rate/income stress test.",
       age=33, employment_category="self_employed", requested_amount=700_000, tenure_months=60, annual_interest_rate=14,
       rate_type="floating", net_monthly_income=80_000, verified_monthly_income=80_000, years_in_employment=6,
       existing_monthly_emis=11_000, income_variability="high", has_credit_card=False, credit_score=760,
       continuous_employment_12m=True, documents=SE_DOCS),
    _p("SYN-29", "Internal negative-list hit", "Strong profile flagged on the bank's internal list.",
       age=44, employment_category="salaried", requested_amount=300_000, tenure_months=36, annual_interest_rate=12,
       net_monthly_income=95_000, verified_monthly_income=95_000, years_in_employment=12, existing_monthly_emis=0,
       has_credit_card=True, credit_card_limit=300_000, credit_card_balance=20_000, credit_score=800,
       continuous_employment_12m=True, documents=SALARIED_DOCS, internal_negative_list_hit=True),
    _p("SYN-30", "Relationship customer", "Existing customer with regular salary credits and healthy balances.",
       age=35, employment_category="salaried", requested_amount=500_000, tenure_months=48, annual_interest_rate=12,
       net_monthly_income=70_000, verified_monthly_income=70_000, years_in_employment=5, existing_monthly_emis=12_000,
       has_credit_card=True, credit_card_limit=200_000, credit_card_balance=50_000, credit_score=720,
       continuous_employment_12m=True, documents=SALARIED_DOCS, existing_customer=True, salary_credit_regular=True,
       avg_monthly_balance=60_000, employer_category="A"),
    _p("WORKED-EX", "Worked example (technical document)", "The applicant used in docs/MATHEMATICAL_MODEL.md.",
       age=32, employment_category="salaried", requested_amount=800_000, tenure_months=48, annual_interest_rate=12,
       net_monthly_income=60_000, verified_monthly_income=60_000, years_in_employment=4, existing_monthly_emis=12_000,
       active_loan_count=1, has_credit_card=True, credit_card_limit=200_000, credit_card_balance=50_000,
       credit_score=740, continuous_employment_12m=True, documents=SALARIED_DOCS),
]

PROFILES_BY_KEY = {p.key: p for p in PROFILES}
