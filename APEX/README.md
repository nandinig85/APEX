# APEX – Loan Eligibility & Decision Support (Prototype v3)

APEX helps a bank manager answer five questions about a personal-loan applicant, and explain the answers
to the customer:

1. **Does the applicant meet the bank's preliminary criteria?** APEX underwrites the application in the
   same stages a lender would: KYC (PAN), internal checks, bureau, eligibility, affordability and stress,
   banking behaviour, documents and deviations. It then gives an explainable readiness score and a
   classification: **Likely**, **Borderline**, **Unlikely** or **Insufficient information**.
2. **What amount is affordable under the assumptions?** APEX tests the loan against every affordability
   limit, takes the lowest, and names the limit that decides the conditional ceiling.
3. **Why, and why not?** It lists strengths, blockers, exceptions, concerns and missing information in
   plain language.
4. **What can the customer do?** It offers alternative loan structures and an improvement plan. Each
   option and step is re-assessed, so its effect is measured rather than assumed. It also drafts a
   **customer letter** the manager can review and share.
5. **What rate does the fictional policy suggest?** A transparent example starts from the selected bank's
   base rate, adjusts for exact bureau score and existing EMI burden, and stays within its configured range.

> ⚠️ **Not production software. Not a credit decision.** All three banks are **fictional**. Every
> threshold and weight is a placeholder. The new pricing bands are fictional examples. The readiness score is **not**
> a probability. APEX never guarantees approval, and final decisions remain with the lender. Use
> synthetic data only.

**Amount and rate shown:** the amount is a *conditional affordability ceiling* calculated from the
entered information and an **assumed** interest rate. APEX also suggests an illustrative, rule-based rate
with visible policy adjustments. Apply that rate to recalculate the amount, EMI and APR. Neither figure is
a bank offer or a personalised AI prediction.
An approval model stops showing probabilities when the bank policy version changes.

**Documentation**

* [`docs/APEX_HANDBOOK.md`](docs/APEX_HANDBOOK.md): how to use APEX, its specification, the maths, and
  decision justification (how much, why, why not, where to improve). The handbook is also available
  inside the app on the **Guide** page.
* [`docs/MATHEMATICAL_MODEL.md`](docs/MATHEMATICAL_MODEL.md): every formula with derivations, edge cases
  and worked examples. The decision advisor and fictional rate rule are in §16–17.

## What is new in v3

* **Renamed from NIKA to APEX.** The environment variables are now `APEX_PAN_SALT` and `APEX_MODEL_DIR`.
* **Decision advisor (`advisor.py`).**
  * **Conditional affordability ceiling:** the lowest of six limits: FOIR with a safety buffer, the stress test, a
    living-cost buffer, an income-volatility limit (lognormal quantile), the income multiple, and the
    product maximum.
  * **Closed-form counter-offers:** the income needed, the highest existing EMIs allowed, and the
    shortest tenure that fits.
  * **Sensitivities:** the effect of +INR 1,000 income, −INR 1,000 EMIs, −1 percentage point of rate and
    +12 months of tenure.
  * **Options:** each one is re-assessed by the engine, and one is marked recommended.
  * **Improvement plan:** each step is a counterfactual re-run with its measured effect on score, maximum
    amount and classification.
  * **Why / why not:** reasons in plain language.
* **Customer letter:** a plain-language letter with no PAN, internal score or model output.
* **Redesigned UI:**
  * a theme, a branded sidebar and page header strips;
  * a score gauge, KPI tiles, a limits chart and an affordability frontier;
  * option cards and improvement-step cards;
  * new **Decision & advice** and **Guide** pages, and a **Reports & customer letter** page.
* **Maths:** an inverse normal CDF (Acklam's approximation plus a Halley step), lognormal quantile
  factors and the annuity factor.

### Carried over from v2

* PAN KYC: consent, masking, hashing; PAN is never scored.
* Bureau and bank-statement filters.
* Bank rules and preferences, with a fairness guard, versioning and an audit log.
* The deviation matrix.
* APR by IRR, amortisation and the stress test.
* A calibrated logistic approval scorecard, with a validation gate and subgroup monitoring.

## Install

### One-click install (recommended)

1. Download `APEX_loan_decision_support.zip`. You can get it from the **⬇️ Download APEX (.zip)** button
   in the app's sidebar or on the **Guide** page, or build it yourself with `python bundle.py`.
2. Extract the zip and open the `APEX` folder.
3. Run the installer:
   * **Windows:** double-click `install_and_run.bat`.
   * **macOS / Linux:** run `bash install_and_run.sh`.

The installer creates `.venv`, installs the dependencies and starts APEX at http://localhost:8501. Run it
again whenever you want to start APEX.

### Manual install

Requires Python 3.10 or newer (developed on 3.14).

```bash
python -m venv .venv
```

Activate the environment: `.venv\Scripts\activate` on Windows, or `source .venv/bin/activate` on
macOS/Linux. Then install the dependencies:

```bash
pip install -r requirements.txt
```

## Run the app

```bash
streamlit run app.py
```

Open http://localhost:8501. To try it quickly, pick a **synthetic profile** in the sidebar (for example
`WORKED-EX`), click **Load profile**, and switch between **Bank A / B / C**.

| Page | What it shows |
|---|---|
| 📝 Customer information | A validated input form. Leave unknown fields empty: missing data gives REVIEW, never FAIL. |
| 📊 Assessment dashboard | The classification with a score gauge, a decision snapshot, the underwriting pipeline, the approval probability (only from a validated model), metric cards, the checks table, and a comparison across banks. |
| 🎯 Decision & advice | Conditional affordability ceiling, fictional rate suggestion with its policy factors, a button to reassess at that rate, limits, options, improvement plan and customer letter. |
| 🔍 Explanation & diagnosis | Classification reasons, findings, a "points lost" chart, income adjustment, the stress model, completeness, and next steps. |
| 🧮 Loan simulator | Hypothetical scenarios: EMI, FOIR and capacity against tenure; amortisation; rate sensitivity. |
| 📄 Reports & customer letter | The manager report (`.md`), data (`.json`, PAN masked) and the customer letter (`.md`). |
| ⚙️ Policy studio | Add, preview, disable or delete bank rules and preferences; view the deviation matrix and the audit log. |
| 🧪 Model lab | Train, validate and activate an approval scorecard on synthetic or uploaded history. |
| 📘 Guide | The handbook. |

## Run the tests

```bash
python -m pytest -q
```

The suite covers:

* the financial maths, checked against closed forms, finite differences and Monte Carlo;
* the rules, deviations, the fairness guard, scoring and classification;
* pinned outcomes for all synthetic profiles under all banks;
* the scorecard maths and its gate;
* the v3 advisor:
  * the inverse normal;
  * the lognormal quantile;
  * that the maximum amount equals the lowest limit;
  * that the FOIR and stress checks pass at the maximum;
  * that the closed-form income, EMI and tenure counter-offers are tight;
  * monotonicity in income, EMIs, tenure and rate;
  * blockers;
  * that options are re-assessed;
  * counterfactual improvements;
  * that the customer letter never contains the PAN or the score.
* fictional rate suggestions: visible factor arithmetic, bank bounds, missing-data blockers and stability
  when the manager changes the assumed scenario rate.

## Project layout

```
app.py                 Streamlit UI (no business logic)
models.py              Pydantic input / config / output models (incl. DecisionAdvice)
calculations.py        Pure financial maths (EMI, FOIR, APR/IRR, amortisation, normal CDF and inverse, lognormal)
eligibility.py         Config loading (defaults + bank deep-merge + overlays) and built-in checks
scoring.py             Sub-scores, weighted readiness, completeness, classification
engine.py              assess(): orchestrates one assessment
advisor.py             advise(): maximum amount, limits, counter-offers, why / why not, improvement plan
pricing.py             Transparent fictional rate policy and its explanation
reports.py             Manager report, JSON and customer letter
policy_rules.py        Bank/custom rules, fairness guard, deviation matrix, overlays and audit log
approval_model.py      Calibrated logistic scorecard: binning, IRLS, calibration, validation, prediction
predictors.py          Predictor interface
kyc.py                 PAN masking, hashing, holder type
synthetic_history.py   Synthetic historical decisions (demo training data only)
profiles.py            30 synthetic borrower profiles + the worked example
config/                _defaults.yaml and fictional bank_a / bank_b / bank_c configurations
config/overlays/       Custom rules saved from the Policy studio (+ audit_log.jsonl), created on first save
model_store/           Activated approval models (JSON), created on first activation
.streamlit/config.toml UI theme
docs/                  APEX_HANDBOOK.md, MATHEMATICAL_MODEL.md
tests/                 pytest suite
bundle.py              ZIP packaging from a reviewed file manifest
```

## Customising a bank

Copy one of `config/bank_*.yaml`, give it a new `bank_id`, `policy_version` and `last_verified`, and
override any key from `_defaults.yaml`. The handbook (§1.8 and §2.6) lists every key. The app picks up the
file on the next rerun, and invalid configs are rejected with a clear error. Only `fictional: true`
configurations are accepted; real lender criteria need verification first.

## Guardrails built in

* Missing information yields REVIEW or Insufficient information, never FAIL. Missing documents are
  remediable gaps, never fraud.
* A hard FAIL can never be hidden by a high score, a preference, a model probability or a recommended
  option. Blockers are shown before any amount.
* Every output carries an evidence label. Every limit, reason, option and improvement can be traced to a
  formula or a re-run of the engine.
* Protected characteristics and geographic proxies are never collected, and rules on them are refused.
  Loan purpose is recorded only and never scored.
* The customer letter contains no PAN, internal score or model output. It says that the assessment is
  preliminary and not a guarantee, and it points to the bank's grievance process.
