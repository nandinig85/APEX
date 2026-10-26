# APEX Handbook – How to use, Specification, Mathematics and Decision justification

APEX is a loan **eligibility and decision-support** assistant for bank managers. It does six things:

1. **Assesses** a customer the way a lender underwrites a personal loan: KYC → internal checks → bureau →
   eligibility → affordability and stress → banking behaviour → documents → deviations.
2. **Works out a conditional affordability ceiling**, and shows which limit sets that amount.
3. **Explains why and why not** in plain language, separating hard blockers, exceptions, concerns and
   missing information.
4. **Proposes options and an improvement plan**: a smaller amount, a longer tenure, lower existing EMIs, or
   documented income. It shows what the customer can do and how much each step would help, measured by
   re-running the assessment.
5. **Produces a customer letter** the manager can review and share, so the customer leaves the meeting
   knowing where they stand and what to do next.
6. **Suggests an illustrative interest rate** from the fictional bank's policy, with every adjustment shown.
   The manager can apply it to recalculate the loan assessment.

> **What APEX is not.** It is not a credit decision, a sanction, or a guarantee of approval. The three banks
> are fictional, every threshold is a placeholder, and the bundled data is synthetic. The final decision
> always rests with the lender.

This handbook has four parts:

| Part | For | Contents |
|---|---|---|
| **Part 1 – How to use APEX** | Bank managers, trainers | Install, daily workflow, page-by-page guide, talking to customers, adding rules, training the model |
| **Part 2 – Specification** | Product owners, auditors, developers | Scope, functional requirements, inputs and outputs, pipeline, configuration, guardrails, limits |
| **Part 3 – The mathematics** | Credit-risk teams, model validators | Every formula, in summary, with references to `MATHEMATICAL_MODEL.md` for derivations |
| **Part 4 – Decision justification** | Everyone who explains a decision to a customer | How much can be granted, why, why not, options, where to improve, the customer letter, a worked example |

```
 Customer information ─► APEX engine ───────────────────────────────────────────► Assessment
                          │ KYC · internal · bureau · eligibility · affordability  │ classification, score, checks
                          │ stress · banking · documents · deviations · score      │
                          ▼                                                         ▼
                   Bank policy (YAML + Policy studio)                     Decision advisor
                   Approval model (Model lab, optional)                   how much · why · why not
                                                                          options · improvement plan
                                                                          customer letter
```

# Part 1 – How to use APEX

## 1.1 Install and start

You need Python 3.10 or newer (developed on 3.14).

```bash
python -m venv .venv
```

Activate the environment (`.venv\Scripts\activate` on Windows, `source .venv/bin/activate` on
macOS/Linux), then:

```bash
pip install -r requirements.txt
```

```bash
streamlit run app.py
```

Open http://localhost:8501. To check the installation, run `python -m pytest -q`; every test should pass.

## 1.2 The screen

* **Left sidebar**
  * **Bank configuration**: Bank A (conservative), B (moderate) or C (flexible). Each page recalculates
    immediately for the selected bank.
  * Policy version, last-verified date, the number of custom rules and the model status.
  * **Navigate**: the nine pages.
  * **Synthetic profile** and **Load profile**: 30 test customers plus the worked example, for training and
    demonstrations.
* **Main area**: each page opens with a header strip showing the applicant ID, the request and the
  current classification.

## 1.3 A typical customer meeting, step by step

1. **Select the bank configuration** in the sidebar.
2. **Customer information**: enter what the customer has shared. Leave anything unknown empty: APEX
   reports it as *missing*, never as a failure. Tick PAN consent before typing the PAN.
3. Click **Save and assess**. The **Assessment dashboard** opens.
4. Read the **classification banner** and the **decision snapshot** ("Up to INR 6,35,000 over 48 months
   can be considered", for example).
5. Open **Decision & advice** and go through the tabs:
   * **How much & why**: the limits and the one that binds.
   * **Why / why not**: strengths and concerns.
   * **Options**: alternative loan structures.
   * **Where to improve**: the customer's action plan.
   * **Customer letter**: the summary to share.
6. If you need to explain a single number, use **Explanation & diagnosis**. It shows the factor
   contributions, the income-adjustment steps and the stress model.
7. Use the **Loan simulator** for "what if we did 60 months at 11.5%?" questions. It never changes the
   saved record.
8. Go to **Reports & customer letter** and download:
   * the **customer letter**, to review and then share;
   * the **manager report**, for the file;
   * the **JSON**, for systems.
9. Proceed to the bank's full underwriting. APEX is a pre-screen.

## 1.4 Page-by-page guide

| Page | Use it to | Look at |
|---|---|---|
| 📝 **Customer information** | Enter or correct the application. Sections: A loan, B KYC (PAN), C income, D existing debt and bureau, E bank statement and relationship, F documents. | Red errors under the buttons. A PAN preview shows only the masked form and the holder type. |
| 📊 **Assessment dashboard** | Get the overall picture. | Classification and score gauge (bands for this bank), decision snapshot, underwriting pipeline (worst status per stage), approval probability panel (only with a validated model), 14 metric cards, checks table, and the same applicant under all banks. |
| 🎯 **Decision & advice** | Answer "how much, why, why not, what can I do?" | Verdict, KPIs (requested / conditional affordability ceiling / indicative amount / longest tenure / limiting factor), limits chart, affordability frontier, sensitivities, why/why not, option cards, improvement plan, letter. |
| 🔍 **Explanation & diagnosis** | Defend the classification. | Classification reasons, findings grouped by type, "points lost" chart, income-adjustment steps, stress model, completeness, next steps for the manager. |
| 🧮 **Loan simulator** | Explore scenarios. | Side-by-side table (recorded vs scenario), EMI / FOIR / capacity against tenure, amortisation charts, rate sensitivity. |
| 📄 **Reports & customer letter** | Produce documents. | Customer letter (shareable: no PAN, scores or model output) and the manager screening report with a decision-support section. |
| ⚙️ **Policy studio** | Add the bank's own filters and preferences. | Current thresholds and rules, add a rule, preview its effect on all profiles, deviation matrix, audit log. |
| 🧪 **Model lab** | Train or activate the approval-probability model. | Gate result, AUC / KS / Brier / calibration, ROC, reliability diagram, IV, scorecard, subgroup monitoring. |
| 📘 **Guide** | Read this handbook inside the app. | – |

## 1.5 Talking to the customer

APEX is designed so that a customer who is not approved for the full amount still leaves with a clear
picture. A suggested conversation, using the **Decision & advice** page:

1. **Start with what is possible.** "Based on what you have shared, we can consider up to INR X over N
   months; the EMI would be about INR E." (verdict and KPIs)
2. **Explain how the amount was set.** "We check several limits: your EMIs as a share of income, a
   cushion if income dips, money left for living costs, and a ceiling on unsecured loans. In your case
   the deciding limit is ___." (How much & why tab)
3. **Acknowledge the strengths.** For example: "Your repayment record is clean and your income is stable."
   (Why column)
4. **Be specific about what holds it back.** Name the blockers first, then the concerns. Avoid internal
   jargon; the letter already rephrases checks in plain words. (Why not column)
5. **Offer choices.** "Alternatively, the full amount fits over 60 months, or if you close the car loan
   it fits at 48 months." (Options tab: each card is re-assessed and the recommended one is marked)
6. **Give a plan.** "If you bring card usage under 30%, the amount we can consider rises to about INR Y."
   (Where to improve tab: each step shows its measured effect)
7. **Hand over the letter** after reviewing it, and remind the customer that this is preliminary and that
   the final decision follows verification.

**Do not** quote the readiness score or the model probability to the customer. They are internal
decision aids. The letter deliberately leaves them out.

## 1.6 Adding your own rules (Policy studio)

Managers can add bank-specific filters without code.

1. Go to **Policy studio**, open **Add a rule**, and enter:
   * an ID and a label;
   * your name (for the audit log);
   * the stage, for example Bureau.
2. Pick a **field**. The list is a whitelist of inputs and derived metrics, such as
   `enquiries_6m`, `foir`, `stressed_foir`, `abb_to_emi` or `age_at_maturity`. Fields that can act as a
   stand-in for protected groups (age, employer category, existing customer) show a fairness note.
3. Choose the **operator and value**, stated as the *requirement*. Example: `enquiries_6m <= 5`.
4. Choose the **action**:

   | Action | Effect when the requirement is not met |
   |---|---|
   | Hard filter | FAIL. The applicant becomes Unlikely. |
   | Deviation | DEVIATION. Needs approval by the named authority. |
   | Manual review | REVIEW. |
   | Preference | Adds or subtracts points *when the condition is true*. The total of all preferences is capped at ±15. |

5. Choose what happens when the data is missing: **review**, or **ignore**.
6. Click **Preview impact** to see which of the 31 profiles change classification, then click **Save
   rule**. The policy version gains a suffix, and the change is written to `config/overlays/` and
   `audit_log.jsonl`.

**Blocked by design:** rules on gender, religion, caste, marital status, pincode, address, name,
nationality, disability, language, PAN and similar attributes. RBI's Fair Practices Code forbids that kind
of discrimination, and APEX refuses such rules with an explanation.

## 1.7 Training the approval model (Model lab)

1. Choose the data: **Synthetic history (demo)**, or **Upload bank history (CSV)**. Download the template
   to see the required columns: the APEX inputs, `decision` (1 = approved, 0 = declined) and
   `decision_date`.
2. Set the bins, the L2 penalty, the calibration method, the PDO and the base score. The defaults are
   sensible.
3. Click **Train model**. APEX splits the history chronologically (60 / 20 / 20), fits the scorecard,
   calibrates it and validates it on the newest 20%.
4. If it passes the gate, click **Activate**. The gate requires:
   * at least 500 training cases and 50 per class;
   * test AUC of at least 0.65;
   * calibration error of at most 0.05.
5. The dashboard then shows the **approval probability** with a 95% interval and reason codes. It never
   overrides a rule outcome. A model trained on synthetic data is labelled **SYNTHETIC** everywhere.

## 1.8 Adding or changing a bank configuration

Copy `config/bank_b_moderate.yaml`, then change `bank_id`, `display_name`, `policy_version` and
`last_verified`, and override any key from `config/_defaults.yaml`:

* FOIR caps, minimum income and score, age, amount and tenure limits;
* income factors, buffers and the income multiple;
* scoring weights and curves;
* stress settings, KYC, charges and the model gate;
* the deviation matrix and policy rules.

APEX picks up the file on the next page refresh. Invalid files are rejected with a clear message, for
example when weights do not sum to 1 or curves do not increase.

## 1.9 Frequently asked questions

**The classification is Insufficient information. What do I do?** At least one critical input (age,
category, income, years in employment, existing EMIs) is missing, or too little of the score is backed by
data. The Decision page lists what is missing.

**Why is the maximum amount lower than "FOIR allows"?** Other limits can bind first: the stress test, the
living-cost buffer, income volatility, the income multiple or the product maximum. The limits chart shows
each one, with the binding limit in orange.

**The customer is salaried with a fixed salary. Why is there an income-volatility limit?** When no monthly
income history is entered, APEX assumes a small coefficient of variation for the category (0.05 for
salaried) and says so. Enter 6 or more months of income to measure it instead.

**Can I promise the customer the indicative amount or interest rate?** No. The affordability ceiling is
conditional on verified inputs and the rate entered for the scenario. APEX suggests only a fictional,
rule-based rate, which is not a lender quote or a personalised AI prediction.
The final amount and rate follow the bank's verification and pricing process.

**Where are the files?** Reports are downloaded from the Reports page. Custom rules are in
`config/overlays/`, and activated models are in `model_store/`.

# Part 2 – Specification

## 2.1 Purpose and scope

| Item | Specification |
|---|---|
| Purpose | Preliminary screening and decision support for unsecured personal loans to individuals in India |
| Users | Branch and credit managers (primary); credit policy owners (Policy studio); model validators (Model lab) |
| In scope | Data capture, KYC (PAN) checks, bureau and banking filters, eligibility, affordability, stress test, deviations, readiness score, classification, conditional loan amount, fictional policy rate suggestion, options, improvement plan, reports, customer letter, policy rules, approval-probability model |
| Out of scope | Live bureau or KYC integrations, document OCR, sanction and disbursal, real lender pricing decisions, collections |
| Status | Prototype. Fictional banks, placeholder parameters, synthetic data |

## 2.2 Functional requirements

| ID | Requirement |
|---|---|
| FR-01 | Capture applicant inputs with validation. Unknown values are allowed and treated as *missing*. |
| FR-02 | Record PAN only with consent. Validate the format (`AAAAA9999A`) and holder type, mask it everywhere, store only a salted hash, and never use it in scoring. |
| FR-03 | Calculate EMI, FOIR, maximum EMI, utilisation, LTI, residual income, income stability, data completeness, APR (including fees and GST), stressed FOIR, and the monthly probability that FOIR exceeds the cap. |
| FR-04 | Run built-in checks and bank rules per underwriting stage. Each returns PASS, FAIL, DEVIATION, REVIEW or N/A. Missing data gives REVIEW, never FAIL. |
| FR-05 | Apply the deviation matrix: a breach within a tier becomes DEVIATION with a named approving authority. |
| FR-06 | Compute an explainable readiness score (0–100) with bank preference points capped at ±15, and classify the applicant as Likely, Borderline, Unlikely or Insufficient information. A FAIL always gives Unlikely. |
| FR-07 | Compute the **conditional affordability ceiling** as the lowest of all affordability limits, an **indicative amount** after rule checks, and the **limiting factor**. The entered interest rate is an assumption, not a quote. |
| FR-08 | Generate **why** (strengths) and **why not** (blockers, exceptions, concerns, missing information) reasons in plain language. |
| FR-09 | Generate **options**: reduced amount, the full amount over a longer tenure, the maximum at the longest tenure, lower existing EMIs, and documented income. Each is re-assessed by the engine, and one is marked recommended. |
| FR-10 | Generate an **improvement plan** in which each step's effect on score, maximum amount and classification is measured by re-running the assessment, plus a combined effect. |
| FR-11 | Produce a manager report (Markdown), the assessment data (JSON) and a **customer letter** (Markdown, without PAN, score or model output). |
| FR-12 | Let managers add, preview, enable, disable and delete custom rules, with versioning, an audit log and a fairness guard. |
| FR-13 | Train, validate, gate and activate a bank-specific approval-probability model. Show the probability only if the model passes the gate and matches the current bank policy version; never let it override rules. |
| FR-14 | Suggest a fictional policy rate from the bank's base rate, exact bureau-score tier and existing EMI burden; show each adjustment and the bank range. Withhold a rate when essential inputs are missing or fixed hard criteria fail. |
| FR-14 | Compare the same applicant across all configured banks. |

## 2.3 Inputs (data dictionary)

| Group | Field | Type / range | Required for a full assessment |
|---|---|---|---|
| Loan | `applicant_id` | text, `[A-Za-z0-9_-]`, ≤ 40 | yes |
| | `requested_amount`, `tenure_months`, `annual_interest_rate` | > 0; 1–480; 0–60 % | yes |
| | `rate_type` | fixed / floating | default fixed |
| | `processing_fee_pct`, `other_upfront_charges` | 0–10 %; ≥ 0 | optional (bank default fee) |
| | `loan_purpose` | text | recorded only, never scored |
| Personal | `age` | 18–100 | yes |
| | `employment_category` | salaried / self_employed / informal | yes |
| KYC | `pan_consent`, `pan`, `pan_status` | bool; `AAAAA9999A`; verified / not_verified / inoperative / mismatch | PAN per bank KYC policy |
| Income | `net_monthly_income`, `years_in_employment` | ≥ 0; 0–60 | yes |
| | `verified_monthly_income` | ≥ 0 | optional (otherwise an unverified factor applies) |
| | `monthly_income_observations` | list of ≥ 0 values, at least 6 to be used | optional |
| | `income_variability`, `continuous_employment_12m`, `other_recurring_obligations` | enum; bool; ≥ 0 | optional |
| Debt and bureau | `existing_monthly_emis` | ≥ 0 | yes |
| | `credit_score` or `credit_score_range_low/high`, or `no_credit_history` | 300–900 | recommended |
| | `max_dpd_12m` ≤ `max_dpd_24m`, `written_off_or_settled_36m`, `enquiries_3m` ≤ `enquiries_6m`, `credit_history_months`, `active_loan_count`, `unsecured_loans_active` | integers, bool | optional |
| | `has_credit_card`, `credit_card_limit`, `credit_card_balance` | bool; ≥ 0 | optional |
| Banking | `emi_bounces_6m`, `avg_monthly_balance`, `salary_credit_regular` | int; ≥ 0; bool | optional |
| Relationship | `existing_customer`, `internal_negative_list_hit`, `employer_category` | bool; bool; enum | optional |
| Documents | `documents{type: available / not_available / not_applicable}` | per document type | mandatory set per category and bank |

**Never collected:** gender, religion, caste, marital status, address, pincode or any other protected
characteristic.

## 2.4 Outputs

| Output | Contents |
|---|---|
| `Assessment` | Metrics with traffic lights and evidence labels; checks with stage, source, observed and threshold values and authority; pipeline; factors; readiness and base score; preference adjustments; completeness; capacity range; APR; stress; KYC summary (masked); classification and reasons; findings; next steps; assumptions; optional probability |
| `DecisionAdvice` | Verdict and tone; requested, conditional affordability ceiling, indicative and longest-tenure amounts; limiting factor; limits table; blockers; why and why not; options; improvement plan; sensitivities; required income; maximum existing EMIs; affordability frontier |
| `RateRecommendation` | Example base rate, visible score and EMI-burden adjustments, bank range, illustrative suggested rate or reasons a rate was withheld |
| Documents | Manager report (`.md`), assessment data (`.json`, PAN masked), customer letter (`.md`) |

## 2.5 Underwriting pipeline

| Stage | Built-in checks | Typical bank rules (examples from the fictional banks) |
|---|---|---|
| KYC | PAN present, valid, individual holder, verified | – |
| Internal checks | – | Internal negative list |
| Bureau | Minimum credit score (exact, range, new-to-credit policy) | Write-off or settlement in 36 months; DPD 30/60/90; enquiries in 3 or 6 months; thin file |
| Eligibility | Minimum age; age at maturity; minimum income; accepted category; minimum tenure in job; amount and tenure within product limits | – |
| Affordability | FOIR ≤ cap; stress test (stressed FOIR and shortfall probability) | APR above the bank maximum |
| Banking behaviour | – | EMI bounces; average balance against EMI; preferences such as regular salary credits |
| Documentation | Mandatory documents (missing → REVIEW, never FAIL) | – |

The pipeline strip on the dashboard shows the worst status in each stage: FAIL, then DEVIATION, then
REVIEW, then PASS.

## 2.6 Configuration

Configuration is YAML in `config/`. `_defaults.yaml` holds the shared values, and each `bank_*.yaml`
overrides them by deep merge. Custom rules from the Policy studio are added from `config/overlays/`.

| Block | Main keys |
|---|---|
| `meta` | `bank_id`, `display_name`, `fictional: true`, `policy_version`, `last_verified`, `product` |
| `eligibility` | `min_age`, `max_age_at_maturity`, `min_net_monthly_income`, `accepted_employment_categories`, `min_employment_years`, `credit_score{min_score,new_to_credit}`, `loan_amount{min,max}`, `tenure_months{min,max}`, `foir_cap`, `mandatory_documents` |
| `income_adjustment` | `category_factor`, `unverified_factor`, `self_reported_variability_factor`, `cv_haircut`, `min_observation_months` |
| `amount` | `income_multiple`, `safety_buffer_min/max`, `interest_rate_low/base/high` |
| `pricing` | `credit_score_bands`, low/high existing-EMI share thresholds and their rate adjustments in percentage points |
| `indicators` | Traffic-light margins, `min_residual_income` (also the living-cost buffer in the decision advisor) |
| `scoring` | `weights` (sum to 1), `curves`, `missing_factor_score`, `min_score_coverage`, `likely_min`, `borderline_min`, `income_stability` |
| `kyc` / `charges` / `stress` / `model_gate` | PAN policy; fee and GST, maximum APR; shocks, CVs, maximum shortfall probability; model acceptance gate |
| `deviation_matrix` | Check (or `rule:ID`) → tiers `{within, authority}` |
| `policy_rules` | `{id, label, stage, field, operator, value, applies_to, action, points, authority, on_missing, rationale}` |
| `score_adjustment_cap` | Maximum absolute total of preference points |

## 2.7 Architecture

| Module | Responsibility |
|---|---|
| `app.py` | Streamlit UI: 9 pages, no business logic |
| `models.py` | Pydantic models for inputs, configuration and outputs (including `DecisionAdvice`) |
| `calculations.py` | Pure maths: EMI, inverse EMI, FOIR, APR (IRR), amortisation, normal CDF and inverse, lognormal quantiles, annuity factor |
| `eligibility.py` | Configuration loading and merging; built-in checks |
| `policy_rules.py` | Field whitelist, fairness guard, rule evaluation, deviation matrix, overlays and audit log |
| `scoring.py` | Sub-scores, readiness, completeness, classification |
| `engine.py` | `assess()`: orchestrates one assessment |
| `advisor.py` | `advise()`: maximum amount, limits, options, why and why not, improvement plan |
| `pricing.py` | `recommend_rate()`: fictional policy rate, visible factors and reasons to withhold a rate |
| `reports.py` | Manager report, JSON, customer letter |
| `approval_model.py`, `synthetic_history.py`, `predictors.py` | Scorecard training, validation and prediction; synthetic history; predictor interface |
| `kyc.py`, `profiles.py` | PAN masking and hashing; 31 synthetic profiles |

## 2.8 Non-functional requirements

* **Deterministic.** The same input, configuration and model always give the same result. Only the
  timestamp varies, and it can be fixed.
* **Explainable.** Every number carries an evidence label (input, calculation, rule-based estimate,
  policy check or statistical model). Every limit, reason and option can be traced to a formula or a
  check.
* **Fast.** An assessment takes milliseconds. Decision advice, with about 15 re-assessments, takes tens of
  milliseconds.
* **Private.** PAN is held as a secret, masked in every output and stored only as a salted SHA-256 hash.
  Set `APEX_PAN_SALT` in production. Protected characteristics are not collected.
* **Auditable.** Policy versions include a hash of the custom rules. Every rule change is appended to the
  audit log. Every model records its data source, split, metrics and gate result.

## 2.9 Guardrails

* Never guarantees approval. Every page, report and letter says that the decision rests with the lender.
* Missing information gives REVIEW or Insufficient information, never FAIL. Missing documents are
  treated as remediable, never as fraud.
* A FAIL cannot be offset by a score, a preference, a model probability or a recommended option.
  Blockers are listed before any amount.
* The probability is shown only from a gated, bank-specific model, never for Insufficient cases, and with
  a SYNTHETIC label when it was trained on synthetic data.
* The fairness guard blocks rules on protected characteristics and proxies. Subgroup adverse-impact
  monitoring runs on every model.
* The customer letter contains no PAN, internal score or model output.

## 2.10 Verification

`python -m pytest -q` runs the automated suite. It covers:

* the financial maths against closed forms, finite differences and Monte Carlo;
* rules, deviations, the fairness guard, scoring and classification;
* pinned outcomes for all synthetic profiles under all banks;
* the scorecard maths and gate;
* the decision advisor: limits, closed-form counter-offers, the inverse normal, counterfactual
  improvements, and that the letter never leaks the PAN.

## 2.11 What production use would need

* Real, verified lender policies in place of the fictional placeholders, with owner sign-off.
* Integrations with the credit bureau, PAN verification (NSDL/UTIITSL) and bank-statement analysis.
* The bank's own labelled decision history to train and validate the probability model, and independent
  model validation.
* Legal review of the customer-letter wording, the KYC and DPDP consent flows, and Key Fact Statement
  alignment.
* Authentication, role-based access, persistent storage and a secret `APEX_PAN_SALT`.

# Part 3 – The mathematics

Notation: `P` principal, `R` annual rate (%), `r = R/1200`, `n` tenure in months, `Y` adjusted (usable)
monthly income, `X` existing EMIs, `O` other monthly obligations, `c` the FOIR cap for the category.
`MATHEMATICAL_MODEL.md` gives the full derivations, edge cases and the worked example.

## 3.1 Loan arithmetic

| Quantity | Formula |
|---|---|
| EMI | `E = P r (1+r)^n / ((1+r)^n − 1)`, or `P/n` when `r = 0` |
| Annuity factor | `A(R,n) = (1 − (1+r)^−n) / r`, so `P = E · A` (inverse EMI) |
| Outstanding after k payments | `B_k = P(1+r)^k − E((1+r)^k − 1)/r` |
| EMI rate sensitivity | `∂E/∂R = P[g/(g−1) − r n (1+r)^(n−1)/(g−1)²]/1200`, with `g = (1+r)^n` |
| APR | Solve `P − charges = Σ_{t=1..n} E/(1+i)^t` for the monthly IRR `i` (Newton with bisection safeguard). Nominal APR = `12i`; effective APR = `(1+i)^12 − 1`. Charges = fee × (1 + GST) + other charges. |

## 3.2 Income and affordability

| Quantity | Formula |
|---|---|
| Usable income | `Y = base × category factor × verification factor × variability factor` |
| Variability factor | From the CV of ≥ 6 monthly observations: `1 − min(max_haircut, slope·max(0, CV − tolerance))`; otherwise from self-reported variability |
| FOIR | `(X + E) / Y` |
| Maximum new EMI | `max(0, cY − X)` |
| Loan-to-income | `P / 12Y` |
| Residual income | `Y − X − E (− O)` |
| Indicative capacity | `[min(A·maxEMI·(1−b_max), 12mY, L_max), min(A·maxEMI·(1−b_min), 12mY, L_max)]` |
| Stressed FOIR | `(X + E(R + s·1_floating)) / (Y(1 − δ))` |
| Shortfall probability | Income is lognormal with mean `m = Y/v` and coefficient of variation CV. `σ² = ln(1+CV²)`, `μ = ln m − σ²/2`, and `P(FOIR > c) = Φ((ln((X+E)/c) − μ)/σ)` |

## 3.3 Readiness score and classification

* Sub-scores come from piecewise-linear curves of `FOIR/c`, credit score, utilisation, `LTI/m`, stability,
  residual share and the documentation share.
* `Readiness = Σ w'_i s_i`. Weights are renormalised over applicable factors, and a missing factor scores
  25, a conservative placeholder.
* Coverage is `Σ` of the weights of factors backed by real data. Bank preference points are added,
  capped at ±15.
* Classification: any FAIL → Unlikely. Critical data missing or coverage < 0.75 → Insufficient.
  Score ≥ likely threshold with no REVIEW or DEVIATION → Likely. Score ≥ borderline threshold → Borderline.
  Otherwise Unlikely.

## 3.4 Deviations

For a check with threshold `θ` and observed value `x`, the breach is `x − θ` for "at most" limits and
`θ − x` for "at least" limits. The first tier with `breach ≤ within` converts FAIL into DEVIATION with that
tier's authority.

## 3.5 Approval-probability scorecard

1. **Binning.** Weight of evidence (WoE) binning with monotone merging:
   `WoE_b = ln(%good_b / %bad_b)`, and `IV = Σ(%good − %bad)·WoE`. Features with IV < 0.02 are dropped.
2. **Fit.** L2-penalised logistic regression fitted by IRLS, refitted with sign constraints.
3. **Points.** `Points = offset + factor·ln(odds)`, with `factor = PDO/ln 2` (20 points to double the odds,
   600 points at 50:1).
4. **Calibration.** Platt or isotonic (pool-adjacent-violators) on a chronological calibration slice.
5. **Validation.** On the newest 20%: AUC (Mann–Whitney), Gini, KS, Brier, log-loss, ECE and PSI.
6. **Gate.** n ≥ 500, ≥ 50 cases per class, AUC ≥ 0.65 and ECE ≤ 0.05.
7. **Interval.** A 95% interval by the delta method.

## 3.6 Decision advisor: maximum amount, counter-offers and sensitivities

**Every affordability limit is linear in Y and X.** For tenure `n`:

```
L_k(n) = A(R_k, n) · (1 − b_k) · max(0, a_k·Y − X − d_k)
```

| Limit k | a_k | d_k | Rate R_k | Buffer b_k | Meaning |
|---|---|---|---|---|---|
| FOIR | `c` | 0 | `R` | `b_min` | All EMIs within the cap, with a safety margin |
| Stress test | `(c + e)(1 − δ)` | 0 | `R + s` if floating, else `R` | 0 | Still affordable if income falls by δ and the rate rises by s |
| Living-cost buffer | 1 | `O + R_min` | `R` | 0 | At least `R_min` a month left after EMIs |
| Income volatility | `c·k/v` | 0 | `R` | 0 | FOIR within the cap in all but the weakest `p_max` share of months |

with the lognormal quantile factor `k = exp(σ z_p − σ²/2)`, `z_p = Φ⁻¹(p_max)` and `σ² = ln(1 + CV²)`.
The volatility limit follows from requiring `P(income < (X+E)/c) ≤ p_max`. Because the lognormal
p-quantile equals `k·m` with `m = Y/v`, that condition is `E ≤ c·k·Y/v − X`, which is again linear.

Two ceilings complete the set: the income multiple, `12·mult·Y`, and the product maximum, `L_max`.

* **Conditional affordability ceiling:** `M(n) = min_k L_k(n)`, rounded down to INR 1,000 below 1 lakh and to
  INR 5,000 above. The **limiting factor** is the arg-min.
* **Recommended amount:** `min(request, M(n))` at `n = clamp(requested tenure, product minimum,
  min(product maximum, age cap))`, provided it clears the product minimum and no blocker remains.

**Properties.** `M` is the positive part of a minimum of affine functions, so wherever `M > 0` it is concave and
piecewise linear in `Y`. It never decreases in `Y` or `n`, and never increases in `X` or `R`. At amount `M` the FOIR check and the stress
check both pass by construction.

**Closed-form counter-offers.** For a requested amount `L`:

```
Required usable income:   Y* = max( L / (12·mult),  max_k (L / (A_k(1−b_k)) + X + d_k) / a_k )
Required documented income:  V* = Y* / (category factor × variability factor)
Maximum existing EMIs:    X* = min_k ( a_k·Y − d_k − L / (A_k(1−b_k)) )    (valid if X* ≥ 0 and L ≤ ceilings)
Shortest tenure:          n* = min{ n ∈ [n_req, n_max] : M(n) ≥ L }
```

**Sensitivities.** When a linear limit binds, `∂M/∂Y = A_b(1−b_b)a_b` and `∂M/∂X = −A_b(1−b_b)`. When the
income multiple binds, `∂M/∂Y = 12·mult`. APEX reports finite differences for +INR 1,000 income,
−INR 1,000 EMIs, −1 percentage point of rate and +12 months of tenure.

**Inverse normal.** `Φ⁻¹` uses Acklam's rational approximation followed by one Halley step against `Φ`,
accurate to about 1e-15.

**Counterfactual improvement plan.** For each action `j`, change only the inputs `Δ_j` it affects, re-run
the full engine, and report the change in score, maximum amount and classification. The actions are ranked
by classification gain, then score gain, then amount gain, and a combined row applies every `Δ_j`. These
are effects under the bank's APEX rules, not predictions of what a bureau or lender will do.

## 3.7 Fictional interest-rate suggestion

This is a **policy example, not an AI prediction or a lender quote**. The selected bank supplies an
illustrative base rate and low/high bounds. The shared fictional pricing policy supplies two adjustments:
an exact bureau-score tier and existing EMIs as a share of adjusted monthly income.

```
s = existing monthly EMIs / adjusted monthly income
raw rate = bank base rate + score-tier adjustment + EMI-burden adjustment
suggested rate = min(bank high rate, max(bank low rate, raw rate))
```

The default score-tier adjustments are −1 percentage point for 800–900, −0.5 for 750–799, zero for
700–749, +1 for 650–699, and +2 for 300–649. Existing EMI burden of 15% or less subtracts 0.5 point;
35% or more adds 0.5 point; the middle band adds zero. Every factor, including a rate-range cap, is shown
on the Decision page and in the manager report. These numbers are invented examples, not fitted to outcomes.

No rate is suggested without an exact credit score, positive usable income, recorded existing EMIs and an
employment category. A fixed hard-policy failure or existing EMIs already at the bank's FOIR cap also
withholds the rate. A request that is too large or too long can still receive a *conditional* rate because
the manager can change that structure; applying the rate re-runs the loan assessment. The customer letter
continues to identify any interest rate used in an EMI calculation as an assumption.

# Part 4 – Decision justification: how much, why, why not, where to improve

The goal is a decision a customer can understand and act on: an honest answer, the reasons behind it, and
a concrete route to a better outcome.

## 4.1 How much can be granted

APEX tests the loan against every affordability limit (Part 3.6) and uses the lowest one.

| Limit | Plain-language explanation given to the customer |
|---|---|
| EMI-to-income (FOIR) | "All EMIs together may use at most c% of usable income, and a safety margin is kept." |
| Stress test | "EMIs must stay manageable if income falls by 10% (and, for floating rates, if the rate rises by 2 points)." |
| Living-cost buffer | "At least INR 10,000 a month should remain after all EMIs for living costs." |
| Income volatility | "Allowing for month-to-month variation, EMIs should stay within the limit in all but the weakest 20% of months." |
| Income multiple | "Unsecured loans are limited to m times annual usable income." |
| Product maximum | "This product lends at most L_max." |

The **Decision & advice** page shows:

* the requested amount;
* the **conditional affordability ceiling** at the requested tenure;
* the **indicative** amount after rule checks;
* the maximum at the longest allowed tenure;
* the **limiting factor**.

A bar chart compares every limit with the request. The **affordability frontier** shows how the maximum
grows with tenure.

## 4.2 Why: the strengths

Strengths come from checks that passed and from score factors with sub-scores of 75 or more. Examples:

* a comfortable EMI burden;
* a good credit score;
* low card usage;
* a loan size that suits income;
* stable income;
* healthy money left each month;
* documents in order;
* a clean repayment record;
* verified identity.

They are ordered by how much they contribute to the score. Strengths matter for customer satisfaction: a
customer who hears what they are doing well is more likely to engage with the improvement plan.

## 4.3 Why not: blockers, exceptions, concerns and missing information

| Type | Source | Meaning for the customer |
|---|---|---|
| **Blocker** | A FAIL check that a different amount or tenure would not fix, such as minimum age, KYC, a write-off or the negative list | Must be resolved before any offer |
| **Exception needed** | A DEVIATION | Outside standard policy but within a band that a named authority may approve |
| **Concern** | A REVIEW item, a factor sub-score of 50 or less, or a request above the affordability ceiling | Weakens the application; addressed by the options or the plan |
| **Missing** | REVIEW caused by missing data | Information or documents needed to complete the assessment |

Blockers are found by **re-assessing at the recommended amount**. A FOIR failure that disappears at a
lower amount is therefore not a blocker; it becomes an option instead.

## 4.4 Options

Each option is a complete loan structure, re-run through the engine (rules, deviations, stress, score):

* **As requested.**
* **Reduced amount:** the maximum eligible at the same tenure.
* **Full amount over a longer tenure:** the shortest tenure `n*` that fits. The EMI is lower and the total
  interest higher; both are shown.
* **Full amount within the tenure limit:** offered when the requested tenure breaks the age-at-maturity
  or product limit.
* **Maximum at the longest allowed tenure.**
* **Full amount after cutting existing EMIs** to `X*`.
* **Full amount with documented income** of `V*`, for example with an earning co-applicant if the bank's
  policy allows one.

APEX marks one option as **recommended**. It is the feasible option (no FAIL) with the best classification,
preferring structures the bank can offer today over ones that need the customer to change their finances,
then the request itself, then the larger amount and the lower total interest.

## 4.5 Where to improve

Each applicable step is tested by changing that one input and re-running the assessment:

| Area | Trigger | Customer action | Typical time frame |
|---|---|---|---|
| Existing EMIs | FOIR in the review range, or the request above the maximum | Prepay or close a loan to bring EMIs to `X*` | 1–3 months |
| Credit card usage | Utilisation above 30% | Pay balances down to 30% of the limit | 1–2 billing cycles |
| Credit score | Below the next curve point or the bank minimum | Pay on time, keep usage low, avoid new applications, correct report errors | 6–12 months |
| Income proof | Income unverified | Salary slips, ITR or bank statements | Immediate |
| Documents | Mandatory documents missing | Submit the listed documents | Immediate |
| Credit applications | 3 or more enquiries in 3 months | Pause new applications | 3–6 months |
| Late payments, bounces | DPD or bounces present | Clear overdues; keep a buffer; use auto-debit | 6–12 months |
| Settled or written-off account | Present in the last 36 months | Repay in full, get a no-dues certificate, update the bureau record | Up to 36 months |
| Time in job, age, minimum income | Below the bank's minimum | Re-apply when met, or consider another product | As stated |
| PAN | Unverified, inoperative or the wrong holder type | Complete verification or use a personal PAN | Immediate |
| Rate type | Floating, under stress | Ask for a fixed rate | At sanction |

Each step shows the measured change in score, maximum amount and classification. A **combined** row shows
the effect of completing every step. Steps that cannot be quantified without new data (pulling a bureau
report, providing a PAN, sharing statements) are listed with a note instead of a number.

## 4.6 The customer letter

The letter (Reports page or the Decision page's last tab) contains:

1. what can be considered (amount, tenure, EMI);
2. how the amount was worked out (each limit in plain words) and which limit decided it;
3. what works in the customer's favour;
4. what is holding the application back, and any information needed;
5. options to discuss;
6. how to strengthen the application, with the amount each step could unlock;
7. a **Please note** section: preliminary, not a sanction or a guarantee; the Key Fact Statement will
   show the exact APR; the customer may ask for reasons and use the bank's grievance process.

It never contains the PAN, the readiness score or the model probability. **The manager must review the
letter before sharing it.**

## 4.7 Worked example (profile WORKED-EX, Bank B)

**The customer:**

* salaried for 4 years, verified income INR 60,000 a month;
* existing EMIs INR 12,000;
* credit score 740, card usage 25%, clean bureau record, PAN verified, documents complete.

**The request:** INR 8,00,000 over 48 months at 12% (fixed).

* **EMI:** INR 21,067, so FOIR = (12,000 + 21,067) / 60,000 = **55.1%**, against Bank B's 50% cap.
  This is a DEVIATION (within 5 percentage points), so it needs the Regional Credit Head's approval.
* **Classification:** Borderline (readiness score 68).
* **Limits at 48 months** (`A(12%, 48) = 37.974`):

  | Limit | Calculation | Maximum |
  |---|---|---|
  | FOIR (5% buffer) | 37.974 × 0.95 × (0.50 × 60,000 − 12,000) | INR 6,49,355 |
  | Stress test | 37.974 × (0.55 × 0.90 × 60,000 − 12,000) | INR 6,72,139 |
  | Living-cost buffer | 37.974 × (60,000 − 12,000 − 10,000) | INR 14,43,010 |
  | **Income volatility** (CV 0.05 assumed, k = 0.9576) | 37.974 × (0.50 × 0.9576 × 60,000 − 12,000) | **INR 6,35,252** |
  | Income multiple | 2 × 12 × 60,000 | INR 14,40,000 |
  | Product maximum | – | INR 25,00,000 |

* **Verdict:** "Up to INR 6,35,000 over 48 months can be considered" (EMI INR 16,722). The limiting factor
  is income volatility.
* **Sensitivities:**
  * +INR 1,000 income → +INR 18,182;
  * −INR 1,000 EMIs → +INR 37,974;
  * −1 percentage point of rate → +INR 12,002;
  * +12 months → +INR 1,16,784.
* **Counter-offers:**
  * The full amount needs usable income of INR 69,061 (documented income INR 69,100), or existing EMIs of
    at most INR 7,661.
  * Options: INR 6,35,000 over 48 months; **INR 7,50,000 over 60 months (recommended, Likely)**; the full
    amount after cutting EMIs to INR 7,600; the full amount with documented income of INR 69,100.
* **Where to improve:**
  1. Cut EMIs by INR 4,400: score 68 → 82, maximum INR 6,35,000 → INR 8,00,000, Borderline → Likely.
  2. Credit score 740 → 750: a small effect.
* **Why:** a clean record, stable income, a good score, complete documents, verified identity, a loan size
  that suits income, low card usage, and healthy money left each month.
* **Why not:** the request is INR 1,65,000 above the maximum; FOIR 55.1% needs an exception; the stress
  test needs review; the EMI burden is high.

Under Bank A (conservative: 45% FOIR cap for salaried applicants) the maximum is INR 5,20,000. Under Bank C the full INR 8,00,000 fits and
the case is Likely. The same customer gets different, explainable answers from different policies.

## 4.8 Principles

* **Honest:** never promise approval. Blockers come before amounts.
* **Specific:** every reason names the number and the limit.
* **Measured:** every claimed benefit comes from re-running the assessment.
* **Actionable:** every concern has a matching option or improvement step where one exists.
* **Respectful:** plain language and no internal scores in customer documents. Missing information is
  described as something to provide, not as a fault.
