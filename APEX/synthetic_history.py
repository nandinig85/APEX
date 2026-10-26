"""Synthetic historical loan decisions for demonstrating the Model Lab.

THIS IS NOT REAL DATA. Applicants are drawn from simple distributions and the
"decision" is produced by a hidden, hand-written decision process (a latent
logit plus noise, with hard declines for write-offs and severe delinquency, and
a slight tightening of policy over time). A model trained on it can only learn
that invented process, so any probability it produces is for demonstration.

A bank would replace this with its own permissioned history: the same
ApplicantInput columns plus ``decision`` (1 approved / 0 declined) and
``decision_date``.
"""
from __future__ import annotations

import json
from datetime import date, timedelta

import numpy as np
import pandas as pd

import calculations as calc
from models import BankConfig, EmploymentCategory


def generate(cfg: BankConfig, n: int = 3000, seed: int = 7, months: int = 24,
             start: date = date(2024, 10, 1)) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    cats = rng.choice(["salaried", "self_employed", "informal"], size=n, p=[0.62, 0.28, 0.10])
    median_income = {"salaried": 55_000, "self_employed": 65_000, "informal": 28_000}
    rows = []
    for i in range(n):
        cat = cats[i]
        income = float(np.round(median_income[cat] * rng.lognormal(0, 0.45), -2))
        age = int(rng.integers(21, 59))
        years = float(np.round(min(age - 20, rng.gamma(2.0, 2.5)), 1))
        existing = 0.0 if rng.random() < 0.35 else float(np.round(income * rng.beta(2, 7) * 0.8, -2))
        tenure = int(rng.choice([12, 24, 36, 48, 60]))
        rate = float(np.round(rng.uniform(11, 18), 2))
        amount = float(np.round(income * rng.uniform(2, 24), -3))
        ntc = rng.random() < 0.08
        score = None if ntc or rng.random() < 0.04 else int(np.clip(rng.normal(735, 50), 300, 900))
        has_card = (not ntc) and rng.random() < 0.55
        limit = float(np.round(income * rng.uniform(1, 4), -3)) if has_card else None
        balance = float(np.round(limit * rng.beta(2, 4), -2)) if has_card else None
        dpd24 = 0 if ntc or rng.random() < 0.78 else int(rng.choice([15, 30, 45, 60, 90, 120]))
        dpd12 = dpd24 if rng.random() < 0.5 else (0 if dpd24 < 30 else int(rng.choice([0, 15, 30])))
        dpd12 = min(dpd12, dpd24)
        wo = (not ntc) and rng.random() < 0.03
        enq6 = int(rng.poisson(1.3))
        enq3 = int(rng.binomial(enq6, 0.5))
        hist = 0 if ntc else int(rng.gamma(3, 24))
        bounces = int(rng.poisson(0.35))
        verified = income if (cat == "salaried" and rng.random() < 0.85) or rng.random() < 0.3 else None
        obs = None
        if cat != "salaried" and rng.random() < 0.6:
            cv = {"self_employed": 0.25, "informal": 0.35}[cat] * rng.uniform(0.4, 1.6)
            obs = [float(max(0, np.round(income * rng.normal(1, cv), -2))) for _ in range(12)]
        docs_all = {d: "available" for d in cfg.eligibility.mandatory_documents.get(EmploymentCategory(cat), [])}
        if rng.random() < 0.12 and docs_all:
            docs_all[rng.choice(list(docs_all))] = "not_available"
        docs_all["income_proof"] = "available" if verified else "not_available"

        # ---- hidden decision process (invented) ----
        e = calc.emi(amount, rate, tenure)
        foir = (existing + e) / income
        cap = cfg.eligibility.foir_cap.get(EmploymentCategory(cat))
        util = balance / limit if has_card and limit else 0.3
        month = int(rng.integers(0, months))
        z = (1.4
             + (0.02 * (score - 720) if score is not None else (-0.4 if ntc else -1.0))
             - 9.0 * max(0.0, foir - cap) - 2.0 * foir
             - 0.03 * dpd24 - 0.35 * enq3 - 0.6 * bounces
             + 0.12 * min(years, 8) - 1.5 * max(0.0, util - 0.3)
             - 0.5 * (amount / (12 * income)) + (0.4 if verified else 0.0)
             - 0.02 * month                     # policy tightens over time
             + rng.normal(0, 0.8))
        approved = int(z > 0 and not wo and dpd12 < 60)
        rows.append(dict(
            applicant_id=f"HIST-{i:05d}", age=age, employment_category=cat, requested_amount=amount,
            tenure_months=tenure, annual_interest_rate=rate, net_monthly_income=income,
            years_in_employment=years, verified_monthly_income=verified,
            monthly_income_observations=json.dumps(obs) if obs else "",
            existing_monthly_emis=existing, has_credit_card=has_card, credit_card_limit=limit,
            credit_card_balance=balance, credit_score=score, no_credit_history=ntc,
            max_dpd_12m=None if ntc else dpd12, max_dpd_24m=None if ntc else dpd24,
            written_off_or_settled_36m=None if ntc else wo, enquiries_3m=enq3, enquiries_6m=enq6,
            credit_history_months=hist, emi_bounces_6m=bounces, documents=json.dumps(docs_all),
            decision=approved,
            decision_date=(start + timedelta(days=int(month * 30.4 + rng.integers(0, 30)))).isoformat(),
        ))
    return pd.DataFrame(rows)
