# APEX – Mathematical Model (Version 3)

> **Status:** prototype. Every threshold, weight and curve in this document is a placeholder from
> `NIKA_Model_Parameters.docx`, the project brief, or an explicitly fictional pricing example. None is a verified lender policy or an empirically
> validated parameter. The three banks are **fictional**. The readiness score is **not** a probability
> of approval or rejection. Final credit decisions remain with the lender.

## Contents

1. Architecture
2. Notation and conventions
3. Inputs and parameters
4. Income adjustment
5. Financial metrics (formula, meaning, inputs, invalid/missing handling, example)
6. Indicative loan capacity
7. Layer A – hard eligibility rules
8. Layer B – readiness score
9. Data completeness
10. Classification
11. Traffic-light indicators
12. Configurable parameters (full list)
13. Complete worked example
14. Assumptions and limitations
15. Version 2 extensions (KYC, APR, stress, rules, deviations, scorecard)
16. Version 3: decision advisor (maximum amount, counter-offers, improvement plan)
17. Fictional rate suggestion
18. Path forward

---

## 1. Architecture

```
            ApplicantInput (models.py, validated by Pydantic)
                       │
   config/_defaults.yaml + config/bank_*.yaml ──► BankConfig (deep-merged, validated)
                       │
                       ▼
 engine.assess() ──► calculations.py   pure maths: EMI, FOIR, capacity, CV, income adjustment …
                 ──► eligibility.py    Layer A: PASS / FAIL / REVIEW / NOT APPLICABLE
                 ──► scoring.py        Layer B: sub-scores, weighted score, completeness, classification
                 ──► predictors.py     Part B hook – NullPredictor (always None in v1)
                       │
                       ▼
                 Assessment ──► reports.py (Markdown / JSON) ──► app.py (Streamlit UI)
```

* The **mathematical engine never reads files or the UI**: `calculations.py` functions are pure and
  raise `ValueError` on invalid input.
* **All lender policy lives in YAML.** A bank changes behaviour only through configuration; the core
  engine is unchanged. Each config carries `policy_version` and `last_verified`.
* `assess()` is **deterministic**: identical inputs and configuration give identical output (only the
  timestamp is external, and it can be passed in).

## 2. Notation and conventions

| Symbol | Meaning |
|---|---|
| `P` | loan principal (INR) |
| `R` | annual interest rate in percent |
| `r = R / 1200` | monthly rate as a fraction |
| `n` | tenure in months |
| `Y` | adjusted monthly income (Section 4) |
| `E₀` | existing monthly EMIs |
| `E` | proposed EMI |
| `c` | FOIR cap for the applicant's employment category |
| `m` | income-multiple ceiling (× annual adjusted income) |
| `b_min, b_max` | safety buffer range |

Ratios are stored as fractions and displayed as percentages. `None` always means **not provided**,
which is never the same as zero.

**Piecewise-linear scoring curve.** A curve is a list of breakpoints `(x₁,s₁) … (x_k,s_k)` with strictly
increasing `x`. For input `x`:

```
f(x) = s₁                                   if x ≤ x₁
f(x) = s_k                                  if x ≥ x_k
f(x) = s_j + (x − x_j)(s_{j+1} − s_j)/(x_{j+1} − x_j)   for x_j ≤ x ≤ x_{j+1}
```

Each curve is monotone in the direction that makes financial sense. This guarantees monotonicity
properties such as "higher utilisation never raises the score", and the tests check them.

## 3. Inputs and parameters

### 3.1 Applicant inputs

| Group | Field | Required? | Used in |
|---|---|---|---|
| A | Applicant ID (non-sensitive test ID) | yes | report only |
| A | Age (completed years) | required for completeness | age checks, tenure cap |
| A | Employment category: salaried / self-employed / informal | required | FOIR cap, income factor, documents, tenure rule |
| A | Requested amount `P`, tenure `n`, assumed rate `R` | **mandatory to run** | everything |
| A | Loan purpose | optional | **record only – never scored** |
| B | Net monthly income (declared) | required | base income |
| B | Years in current employment / business | required | tenure rule, stability |
| B | Self-reported income variability (low / medium / high / unknown) | optional | income factor, stability |
| B | Monthly income observations | optional | CV, base income |
| B | Verified / documented monthly income | optional | base income, verification factor |
| B | Continuous employment in last 12 months | optional | risk indicator only |
| B | Other verified recurring obligations | optional (Part B1) | adjusted residual income |
| C | Existing monthly EMIs `E₀` | required (0 if none) | FOIR, capacity, residual |
| C | Number of active loans | optional | report only |
| C | Has credit card, card limit, card balance | optional | utilisation |
| C | Credit score (exact), score range, or "no credit history" | optional | score rule, sub-score |
| D | Status of each document: available / not available / unknown / not applicable | required for mandatory docs | documents rule, sub-score, stability |

Document types: identity, income proof, salary slips, bank statements, ITR, GST, UPI history, other.
Required documents differ by employment category and are set per bank.

**Deliberately not collected:** name, PAN, Aadhaar, address, pincode, gender, religion, caste, marital
status, or any other protected characteristic or geographic proxy.

### 3.2 Input validation (hard errors, rejected before assessment)

`P > 0`; `0 < n ≤ 480` and an integer; `0 ≤ R ≤ 60`; `18 ≤ age ≤ 100`; all money values `≥ 0`;
credit score in `[300, 900]`; a range needs `low ≤ high`; a credit score and "no credit history" cannot
both be set; card values cannot be given when "no credit card" is selected; only known document types
are allowed.

## 4. Income adjustment

**Meaning.** The declared figure overstates repayment capacity when income is unverified, informal or
volatile. The doc parameter A3 says to count only a conservative share of it.

**Base income `B`:**

1. If verified income `V` is given: `B = V` (verified).
2. Otherwise, if declared income `D` is given: `B = D`. If at least `k_min` monthly observations exist
   and their mean `μ < D`, then `B = μ`. The evidence caps the declaration; it never raises it.
3. Otherwise, if at least `k_min` observations exist: `B = μ`.
4. Otherwise income is **missing**, `Y = None`, and every income-dependent check becomes REVIEW.

**Adjusted income:**

```
Y = B × f_cat × f_ver × f_var
```

| Factor | Definition | Default |
|---|---|---|
| `f_cat` | category factor (informal haircut) | salaried 1.0, self-employed 0.9, informal 0.75 |
| `f_ver` | 1.0 if verified, else `unverified_factor` | 0.9 |
| `f_var` | see below | — |

If the category is missing, the **lowest** category factor is used, which is conservative.

**Variability factor `f_var`:**

* With `≥ k_min` observations: `CV = s / μ`, where `s` is the sample standard deviation (ddof = 1).
  Then `f_var = 1 − min(h_max, β · max(0, CV − τ))`. Defaults: `τ = 0.10`, `β = 1.0`, `h_max = 0.40`,
  `k_min = 6`. Variability up to τ is free, each 0.1 of CV above it removes 10 % of usable income, and
  the total haircut is capped at 40 %.
* Without observations, for categories in `variability_applies_to` (default: self-employed, informal) or
  an unknown category: the self-reported factor is used (low 1.0, medium 0.9, high 0.75, unknown 0.85).
  Unknown is deliberately below 1.
* Otherwise (salaried, no observations): `f_var = 1`.

**Example:** informal, `D = 30,000`, unverified, variability "high":
`Y = 30,000 × 0.75 × 0.9 × 0.75 = 15,187.50`.

## 5. Financial metrics

### 5.1 EMI, total repayment, total interest (brief 4.1)

```
EMI = P · r · (1+r)^n / ((1+r)^n − 1)        r > 0
EMI = P / n                                    r = 0
Total repayment = EMI · n
Total interest  = EMI · n − P
```

* **Meaning:** the level monthly payment that amortises `P` over `n` months.
* **Inputs:** `P`, `R`, `n`.
* **Invalid input:** `P < 0`, `n ≤ 0`, a non-integer `n`, `R < 0`, NaN or ∞ all raise `ValueError`.
  The zero-rate case is handled separately to avoid 0/0.
* **Example:** `P = 8,00,000`, `R = 12` (`r = 0.01`), `n = 48`. Then `(1.01)^48 = 1.612226`, so
  `EMI = 8,000 × 1.612226 / 0.612226 = ₹21,067.07`, total repayment `₹10,11,219.28`, total interest
  `₹2,11,219.28`.
* **Code:** `calculations.emi`, `emi_breakdown`. **Tests:** known values, the zero-rate case, invalid
  inputs, and monotonicity in `P`.

### 5.2 FOIR (brief 4.2)

```
FOIR = (E₀ + EMI) / Y
```

* **Meaning:** the share of usable monthly income committed to loan repayments. The doc calls it the
  "single most important affordability measure".
* **Inputs:** `E₀`, EMI, `Y`.
* **Missing:** if `Y` or `E₀` is missing, FOIR is not computed and the rule returns REVIEW. If `Y = 0`,
  FOIR is undefined (`None`) and the rule returns REVIEW. Negative inputs raise an error.
* **Caps (configurable, per category):** the illustrative brief limits are 50 / 45 / 40 % (salaried /
  self-employed / informal). These are used by Bank B.
* **Example:** `(12,000 + 21,067.07) / 60,000 = 55.11 %`.

### 5.3 Maximum permissible new EMI (brief 4.3)

```
E_max = max(0, c·Y − E₀)
```

If `c·Y − E₀ ≤ 0`, the applicant's existing EMIs already use up the FOIR capacity. Capacity is then
floored at zero and the condition is flagged for review.
**Example (c = 50 %):** `0.5 × 60,000 − 12,000 = ₹18,000`.

### 5.4 Credit utilisation (brief 4.5)

```
U = revolving balance / revolving limit
```

* If the applicant has no credit card, or a zero limit, `U` is **not applicable**. No artificial value
  is assigned, the factor is excluded and its weight is redistributed.
* A card with an unknown limit or balance makes `U` **missing**.
* Values above 100 % (over-limit) are shown as they are.
* Indicator: amber above 30 %, red above 90 %. These are review flags, **not** rejection rules.
* **Example:** `50,000 / 2,00,000 = 25 %`.

### 5.5 Loan-to-income multiple (brief 4.6)

```
LTI = P / (12·Y)
```

LTI is compared with the bank's income-multiple ceiling `m`. If `Y = 0`, LTI is undefined and the
sub-score is 0. **Example:** `8,00,000 / 7,20,000 = 1.11×`.

### 5.6 Residual income (brief 4.7)

```
RI      = Y − E₀ − EMI
RI_adj  = Y − E₀ − EMI − O          (O = other verified recurring obligations, if provided)
```

`RI_adj` is used whenever `O` is provided. A positive `RI` alone is **not** treated as proof of
affordability: it is one factor among seven, plus an amber indicator below a configurable floor (for
example ₹10,000). **Example:** `60,000 − 12,000 − 21,067.07 = ₹26,932.93`.

### 5.7 Income stability (brief 4.8)

The score is a weighted combination of components, each scored 0–100:

| Component | Score | Weight |
|---|---|---|
| Tenure (years) | curve `[(0,0),(1,40),(2,65),(3,80),(5,100)]` | 0.50 |
| Variability | CV curve `[(0.10,100),(0.25,70),(0.50,30),(0.75,0)]`, or self-report low 90 / medium 60 / high 25 | 0.35 |
| Documented income history | 100 if verified income or income proof / salary slips / ITR available; 50 if only ≥ k_min observations; 0 if income documents recorded as not available | 0.15 |
| Employment category | **only if** a bank config sets `employment_category_score` (null by default, because no explicit policy supports it) | 0 |

Components that do not apply (for example variability for a salaried applicant with no observations)
are dropped and the remaining weights renormalised. Missing components use the conservative placeholder
(25). **Example:** 4 years gives `80 + (1/2)·20 = 90`. Variability is not applicable. Verified income
gives 100. The score is `(0.5·90 + 0.15·100)/0.65 = 92.31`.

### 5.8 Data completeness (brief 4.9)

Each item is `provided`, `partial`, `missing` or `not_applicable`. Required items have weight 2 and
optional items weight 1 (both configurable).

```
Completeness          = Σ w_i · f_i / Σ w_i       over applicable items, f_i ∈ [0,1]
Required completeness = mean f_i over required items
```

Required items: loan request, age, employment category, income, years in employment, existing EMIs,
and the status of mandatory documents (partial credit for the fraction recorded). Optional items:
credit score information, card details, income variability (not applicable for salaried applicants
without data), verified income, number of active loans, employment continuity, and other obligations.

Completeness is **shown separately from readiness**. A missing optional item lowers completeness only.
It never fails a check and never changes the readiness score; a test verifies this.

## 6. Indicative loan capacity (brief 4.4)

```
n'          = min(n, lender max tenure, age-based cap)
P_EMI       = E_max · ((1+r)^n' − 1) / (r (1+r)^n')         (= E_max · n' if r = 0)
P_income    = 12 · Y · m
P_product   = product maximum
Upper       = min(P_EMI·(1 − b_min), P_income, P_product)
Lower       = min(P_EMI·(1 − b_max), P_income, P_product)
```

* The safety buffer applies **only** to the EMI-supported amount, because that is the estimate. The
  income multiple and product maximum are already hard ceilings.
* The output is a **range**, as the doc requires, and the binding constraint is named.
* It is recomputed at the bank's low / base / high (stress) rates for sensitivity.
* It is labelled a *rule-based estimate, not a lender-approved limit*.
* Monotonicity: `∂Upper/∂E₀ ≤ 0` and `∂P_EMI/∂Y ≥ 0`. Both are tested over grids.

## 7. Layer A – hard eligibility rules

Every rule returns PASS / FAIL / REVIEW / NOT APPLICABLE. **Missing data is always REVIEW (flagged
`missing_info`), never FAIL.**

| # | Rule | PASS condition | Missing / special handling |
|---|---|---|---|
| 1 | Minimum age | `age ≥ min_age` | age missing → REVIEW |
| 2 | Max age at maturity | `age + n/12 ≤ max_age_at_maturity` | age missing → REVIEW; FAIL detail gives the longest permissible tenure |
| 3 | Minimum income | `B ≥ min_net_monthly_income` (base, pre-haircut) | income missing → REVIEW; notes when unverified |
| 4 | Accepted employment category | category ∈ accepted list | missing → REVIEW |
| 5 | Minimum employment tenure | `years ≥ min_years[category]` | years or category missing → REVIEW |
| 6 | Minimum credit score | exact score `≥ min` | range: PASS if `low ≥ min`, FAIL if `high < min`, else REVIEW. No credit history: policy `accept` → NOT APPLICABLE, `review` → REVIEW. Unknown → REVIEW |
| 7 | Loan amount | `min ≤ P ≤ max` | — |
| 8 | Tenure | `min ≤ n ≤ max` | — |
| 9 | Mandatory documents | every mandatory doc available | not available / unknown → **REVIEW** (a remediable gap, explicitly not fraud). "Not applicable" is accepted only for GST and "other" |
| 10 | FOIR ceiling | `FOIR ≤ c` | income, EMIs or category missing → REVIEW; zero income → REVIEW |

## 8. Layer B – readiness score

### 8.1 Sub-scores (0–100)

| Factor `i` | Input `x` | Curve (default) |
|---|---|---|
| Affordability | `FOIR / c` (relative to each bank's cap) | `(0.6,100) (0.8,85) (1.0,60) (1.1,20) (1.25,0)` |
| Credit score | exact score, or the **lower** bound of a range | `(600,0) (650,30) (700,60) (750,85) (800,100)` |
| Credit utilisation | `U` | `(0,100) (0.3,80) (0.5,50) (0.75,20) (1.0,0)` |
| Loan-to-income | `LTI / m` | `(0.5,100) (0.75,80) (1.0,55) (1.25,20) (1.5,0)` |
| Income stability | Section 5.7 | (already 0–100) |
| Residual income | `RI / Y` (share left over) | `(0,0) (0.2,40) (0.4,75) (0.6,100)` |
| Documentation | `100 × available mandatory / mandatory` ("unknown" counts as not available) | linear |

### 8.2 Aggregation

Let `A` be the set of applicable factors (status `scored` or `missing`). Not-applicable factors (no
card, new to credit, no mandatory documents) are removed:

```
w'_i = w_i / Σ_{j∈A} w_j                      (Σ w'_i = 1)
s_i  = sub-score                if scored
s_i  = s_missing  (= 25)        if missing      ← conservative placeholder, never favourable
Readiness = Σ_{i∈A} w'_i · s_i                 ∈ [0, 100]
Coverage  = Σ_{i scored} w'_i                  ∈ [0, 1]
Points lost_i = w'_i · (100 − s_i)             (drives the "greatest effect" explanation)
```

### 8.3 Illustrative weights and rationale (not empirically validated)

| Factor | Weight | Rationale |
|---|---|---|
| Affordability (FOIR) | 0.30 | The doc calls it the single most important affordability measure. |
| Credit score | 0.15 | A key lender screening criterion, but often self-reported or unavailable. |
| Income stability | 0.15 | The doc calls tenure the "main income-stability signal". It matters for repayment continuity. |
| Credit utilisation | 0.10 | A stress signal, but optional and only for card holders. |
| Loan-to-income | 0.10 | Flags requests that are large relative to income; partly overlaps with FOIR. |
| Residual income | 0.10 | Captures absolute headroom; overlaps with FOIR, so a smaller weight. |
| Documentation | 0.10 | Missing documents are a common, avoidable rejection cause. Kept low because they are remediable. |
| **Sum** | **1.00** | Enforced by config validation. |

## 9. Data completeness

See Section 5.8. Completeness tells the manager **how reliable** the estimate is. It is not a credit
criterion.

## 10. Classification

The rules are applied in this order. The first match wins.

1. **Any FAIL** in Layer A → **Unlikely to meet preliminary criteria**. A high score can never override
   a hard failure.
2. Any **critical input missing** (age, category, income, years in employment, existing EMIs) **or**
   `Coverage < min_score_coverage` (0.75) → **Insufficient information**.
3. `Readiness ≥ likely_min` **and** no REVIEW checks → **Likely to meet preliminary criteria**.
4. `Readiness ≥ borderline_min`, **or** a Likely-level score with open REVIEW items →
   **Borderline / Manual review recommended**.
5. Otherwise → **Unlikely to meet preliminary criteria**. This is labelled as score-based and as an
   indication for manual review, not a rejection.

Default bands: Likely ≥ 70, Borderline ≥ 50. Bank A uses 75 / 55 and Bank C uses 65 / 45. These are
prototype assumptions, not calibrated cut-offs.

## 11. Traffic-light indicators

| Metric | Green | Amber | Red | Grey |
|---|---|---|---|---|
| FOIR | `≤ c − 5pp` | `(c − 5pp, c]` | `> c` | not computable |
| Utilisation | `≤ 30 %` | `≤ 90 %` | `> 90 %` | no card / missing |
| LTI | `≤ 0.75m` | `≤ m` | `> m` | income missing |
| Residual income | `≥ floor` | `0 ≤ RI < floor` | `< 0` | missing |
| Income stability | `≥ 70` | `≥ 40` | `< 40` | missing |
| Indicative max | `P ≤ Lower` | `Lower < P ≤ Upper` | `P > Upper` | missing |
| Readiness | `≥ likely_min` | `≥ borderline_min` | below | n/a |
| Data completeness | required = 100 % | otherwise | — | — |

Proposed EMI and adjusted income are informational (blue), because they are not criteria. Every output
also carries an **evidence label**: *Input as entered*, *Deterministic calculation*, *Rule-based
estimate*, or *Policy check against the selected (fictional) configuration*. The *Statistical model
prediction* label is reserved for Part B and never emitted in v1.

## 12. Configurable parameters

Each bank file overrides `config/_defaults.yaml`. All of these keys are validated by `models.BankConfig`.

| Section | Key | Bank A (conservative) | Bank B (moderate) | Bank C (flexible) |
|---|---|---|---|---|
| meta | policy_version / last_verified | A-proto-0.1.0 / 2026-10-24 | B-proto-0.1.0 / 2026-10-24 | C-proto-0.1.0 / 2026-10-24 |
| eligibility | min_age / max_age_at_maturity | 23 / 58 | 21 / 60 | 21 / 65 |
| | min_net_monthly_income | 35,000 | 25,000 | 15,000 |
| | accepted categories | salaried, self-emp. | all | all |
| | min_employment_years (sal / SE / inf) | 2 / 3 / 3 | 1 / 2 / 2 | 0.5 / 1 / 1 |
| | credit score min / new-to-credit | 750 / review | 700 / review | 650 / accept |
| | loan amount min–max | 50k – 15 L | 50k – 25 L | 25k – 40 L |
| | tenure min–max (months) | 12 – 60 | 12 – 60 | 6 – 84 |
| | FOIR cap (sal / SE / inf) | 45 / 40 / 35 % | 50 / 45 / 40 % | 60 / 50 / 45 % |
| | mandatory docs (salaried) | ID, salary slips, bank stmts | same | ID, bank stmts |
| income_adjustment | category_factor (sal / SE / inf) | 1.0 / 0.85 / 0.70 | 1.0 / 0.9 / 0.75 | 1.0 / 0.9 / 0.80 |
| | unverified_factor | 0.85 | 0.90 | 0.90 |
| | cv_haircut τ / β / h_max, k_min | 0.10 / 1.0 / 0.40, 6 | same | same |
| amount | income_multiple | 1.5× | 2.0× | 2.5× |
| | safety buffer | 8–10 % | 5–10 % | 5–8 % |
| | rates low / base / high | 12 / 14 / 17 % | 11 / 13 / 16 % | 13 / 15 / 19 % |
| indicators | min_residual_income | 15,000 | 10,000 | 8,000 |
| scoring | likely_min / borderline_min | 75 / 55 | 70 / 50 | 65 / 45 |
| | weights, curves, missing score (25), min coverage (0.75) | defaults | defaults | defaults |

## 13. Complete worked example

**Applicant (profile `WORKED-EX`):** salaried, age 32, 4 years in the job. Net monthly income ₹60,000
(verified against salary slips). Existing EMIs ₹12,000. Requested loan ₹8,00,000 over 48 months at
12 % p.a. Credit score 740. Card limit ₹2,00,000 with balance ₹50,000 (25 % utilisation). Identity
documents, salary slips and bank statements are available.

### Step 1 – Adjusted income
Income is verified, so `B = 60,000`, `f_cat = 1.0` (salaried) and `f_ver = 1.0`. With no observations
and salaried status, `f_var = 1.0`. So **Y = ₹60,000** and annual adjusted income is ₹7,20,000.

### Step 2 – EMI
`r = 12/1200 = 0.01`, `(1.01)^48 = 1.612226`.
`EMI = 8,00,000 × 0.01 × 1.612226 / 0.612226 = ` **₹21,067.07**.
Total repayment is ₹10,11,219.28 and total interest is ₹2,11,219.28.

### Step 3 – Ratios (the same under every bank)
* FOIR = (12,000 + 21,067.07) / 60,000 = **55.11 %**
* LTI = 8,00,000 / 7,20,000 = **1.11×**
* Utilisation = 50,000 / 2,00,000 = **25 %** (green, ≤ 30 %)
* Residual income = 60,000 − 12,000 − 21,067.07 = **₹26,932.93** (44.9 % of Y)
* Income stability = **92.3** (Section 5.7)
* Data completeness = **95 %** (all required items provided; "other obligations" optional and missing)

### Step 4 – Capacity, eligibility and score by bank

| | Bank A (conservative) | Bank B (moderate) | Bank C (flexible) |
|---|---|---|---|
| FOIR cap `c` | 45 % | 50 % | 60 % |
| `E_max = cY − E₀` | 27,000 − 12,000 = 15,000 | 30,000 − 12,000 = 18,000 | 36,000 − 12,000 = 24,000 |
| `P_EMI` (12 %, 48 m; annuity factor 37.974) | 5,69,609 | 6,83,531 | 9,11,375 |
| Income ceiling `12Y·m` | 10,80,000 (1.5×) | 14,40,000 (2.0×) | 18,00,000 (2.5×) |
| Buffer | 8–10 % | 5–10 % | 5–8 % |
| **Indicative range** | **₹5,12,648 – ₹5,24,041** | **₹6,15,178 – ₹6,49,355** | **₹8,38,465 – ₹8,65,806** |
| Capacity at the bank's stress rate | 4,67,856 – 4,78,253 (17 %) | 5,71,625 – 6,03,381 (16 %) | 7,38,459 – 7,62,539 (19 %) |
| Credit score rule | 740 < 750 → **FAIL** | 740 ≥ 700 → PASS | 740 ≥ 650 → PASS |
| FOIR rule | 55.1 % > 45 % → **FAIL** | 55.1 % > 50 % → **FAIL** | 55.1 % ≤ 60 % → PASS (amber: within 5 pp) |
| Other 8 rules | PASS | PASS | PASS |

**Sub-scores.** The worked arithmetic is shown for Bank B; Banks A and C are in the table.

* Affordability: `x = 0.5511/0.50 = 1.1022`, which falls in the segment (1.1, 20)–(1.25, 0), so
  `20 − (0.0022/0.15)·20 =` **19.70**. (Bank A: x = 1.2247 → 3.37. Bank C: x = 0.9185 → 70.18.)
* Credit score: 740 lies between (700, 60) and (750, 85), so `60 + (40/50)·25 =` **80.00**.
* Utilisation: `100 − (0.25/0.30)·20 =` **83.33**.
* LTI: `x = 1.111/2.0 = 0.556`, so `100 − (0.056/0.25)·20 =` **95.56**. (Bank A: 80.74. Bank C: 100.)
* Income stability: **92.31**.
* Residual: `x = 0.4489`, so `75 + (0.0489/0.2)·25 =` **81.11**.
* Documentation: 3/3 → **100**. (Bank C: 2/2 → 100.)

| Factor | w | Bank A s | Bank B s | Bank C s | Bank B w·s |
|---|---|---|---|---|---|
| Affordability | 0.30 | 3.37 | 19.70 | 70.18 | 5.91 |
| Credit score | 0.15 | 80.00 | 80.00 | 80.00 | 12.00 |
| Utilisation | 0.10 | 83.33 | 83.33 | 83.33 | 8.33 |
| LTI | 0.10 | 80.74 | 95.56 | 100.00 | 9.56 |
| Income stability | 0.15 | 92.31 | 92.31 | 92.31 | 13.85 |
| Residual income | 0.10 | 81.11 | 81.11 | 81.11 | 8.11 |
| Documentation | 0.10 | 100 | 100 | 100 | 10.00 |
| **Readiness** | | **61.4** | **67.8** | **83.4** | **67.76** |

### Step 5 – Classification

| Bank | Layer A | Score vs bands | **Classification** |
|---|---|---|---|
| A | FOIR FAIL (10.1 pp over the cap, beyond A's 3 pp deviation band); credit score 740 is a DEVIATION (within 10 points, Credit Committee) | 61.4 (bands 75/55) | **Unlikely** – a hard failure remains |
| B | FOIR breach of 5.1 pp is within B's 10 pp tier → DEVIATION (Regional Credit Head); stress test REVIEW | 67.8 (bands 70/50) | **Borderline** – approvable only through a deviation |
| C | All PASS | 83.4 ≥ 65 | **Likely to meet preliminary criteria** |

(Before the deviation matrix was added in v2, Bank B returned Unlikely for this applicant.)

### Step 6 – Why the same applicant gets different results

* **Different FOIR caps.** 55.1 % fails at 45 % and 50 % but passes at 60 %. The affordability
  sub-score is measured *relative to each cap*, so it also moves: 3.4 → 19.7 → 70.2.
* **Different credit-score floors.** 740 fails only at Bank A's 750.
* **Different income multiples** move the LTI sub-score: 80.7 → 95.6 → 100.
* **Practical guidance the tool produces.** At Bank B, a request within ₹6.15–6.49 lakh, or the same
  ₹8 lakh with lower existing EMIs, would remove the FOIR failure. The simulator (page 4) shows this
  without changing the recorded profile. At Bank C, ₹8 lakh fits at 12 %, but not at the bank's 19 %
  stress rate (upper ₹7.63 lakh), so the manager should confirm the actual rate.

All the figures above are asserted in `tests/test_scoring_and_logic.py::test_worked_example_*`.

## 14. Assumptions and limitations

* All thresholds, weights, curves, buffers and bands are **placeholders**, to be calibrated against
  user interviews and pilot outcomes.
* All three banks are **fictional**. Real lender criteria require verification and a deliberate code
  change, because the config validator refuses `fictional: false`.
* Inputs are entered by the manager and are **unverified** unless verified income is supplied. Credit
  scores are self-reported unless a bureau report is on file.
* Age is in completed years, so the age-at-maturity result can be off by up to 11 months.
* FOIR uses EMIs only. Rent and other obligations enter residual income, not FOIR.
* The CV uses the sample standard deviation and requires at least 6 months of data.
* Loan purpose and protected characteristics are never used.
* The readiness score is **not** a probability. Bands are not calibrated. No machine learning is used
  in v1.

## 15. Version 2 extensions

Version 2 models the way a lender actually underwrites an unsecured personal loan. The stages are:

1. KYC
2. Internal checks (de-duplication and negative list)
3. Credit bureau
4. Eligibility
5. Affordability, including a stress test
6. Banking behaviour
7. Documentation
8. Deviations
9. Recommendation

Every check carries one of these stages, and the assessment shows the worst status in each stage as a
pipeline.

### 15.1 PAN and KYC

* **Collection.** PAN is collected only when a consent flag is set, as the DPDP Act requires.
* **Format check.** The format must match `^[A-Z]{5}[0-9]{4}[A-Z]$`. The 4th character gives the holder
  type: P individual, C company, H HUF, F firm, and so on.
* **KYC rule.**
  * PAN missing → REVIEW.
  * Holder type not allowed by the bank (default: P only) → FAIL.
  * Status inoperative, mismatch, or not verified (when the bank requires verification) → REVIEW.
  * Otherwise → PASS.
* **Display and storage.** PAN appears only masked (`XXXXX1234X`) or as a salted SHA-256 hash. It is
  never a scoring input, a model feature or a rule field (rules on `pan` are refused).

### 15.2 APR (Key Fact Statement basis)

Upfront charges are

```
C = P · fee% · (1 + GST%) + other charges
```

The monthly internal rate of return `i` solves

```
P − C = Σ_{t=1..n} EMI / (1+i)^t
```

The present value on the right falls strictly as `i` rises, so the root is unique. It is found with
Newton's method, safeguarded by bisection on [0, 1]. From `i`:

```
APR_nominal   = 12 · i
APR_effective = (1 + i)^12 − 1
```

With no charges, the nominal APR equals the contract rate (tested).

### 15.3 Amortisation and rate sensitivity

* Balance after `k` payments: `B_k = P(1+r)^k − EMI·((1+r)^k − 1)/r`.
* Each EMI splits into interest `B_{k−1}·r` and principal `EMI − B_{k−1}·r`.
* Rate sensitivity, with `g = (1+r)^n`:

  ```
  ∂EMI/∂R = (1/1200) · P [ g/(g−1) − r·n·(1+r)^(n−1)/(g−1)² ]
  ```

  At `r = 0` the limit is `P(n+1)/(2n)/1200`. The tests check this against finite differences.

### 15.4 Stress test and monthly probability that FOIR exceeds the cap

**Stressed FOIR.**

```
FOIR_s = (E₀ + EMI(R + Δ)) / (Y · (1 − s))
```

The defaults are `Δ = 200 bps` (floating-rate loans only) and `s = 10 %`. Monthly income is modelled as
lognormal with mean `μ_Y = Y / f_var` and coefficient of variation `CV`. The CV comes from the first
available of: monthly observations, then self-report, then a category default.

**Distribution parameters.**

```
σ² = ln(1 + CV²)
μ  = ln μ_Y − σ²/2
```

**Monthly probability that FOIR exceeds the cap.**

```
P(FOIR_month > c) = P(Y < (E₀ + EMI)/c) = Φ( (ln((E₀+EMI)/c) − μ) / σ )
```

**Expected months per year above the cap** is `12·p`. By linearity of expectation this needs no
independence assumption between months.

The stress check returns REVIEW when `FOIR_s > c + 5 pp` or `p > 20 %` (both configurable). The tests
compare the closed form with a 400,000-draw Monte Carlo simulation.

### 15.5 Bank policy rules, preferences and the fairness guard

**Rule format.** Banks (in YAML) and managers (in the Policy Studio) add rules of the form
`field  operator  value → action`. The rule text states the requirement:

* `hard` → FAIL when the requirement is not met;
* `deviation` → DEVIATION, approvable by the named authority;
* `review` → REVIEW;
* `score` → adds points when the condition is true (a bank preference).

**Safeguards.**

* Missing data gives REVIEW or is ignored, never FAIL.
* Preference points are capped in total (default ±15).
* A preference can never undo a FAIL.
* Fields must come from a whitelist of inputs and derived metrics.
* Names containing protected characteristics or known proxies (religion, caste, gender, marital status,
  pincode, district, name, PAN, and so on) are refused with a fairness error.
* Lawful but proxy-prone fields (age, employer category, existing customer) show a fairness note when a
  rule uses them.

**Versioning and audit.** Custom rules are saved to `config/overlays/<bank>.yaml`. The policy version
gains a content-hash suffix, and every change is appended to `audit_log.jsonl`.

**New-to-credit applicants.** Their bureau report has no delinquency record, so DPD and write-off rules
treat them as 0 / No.

### 15.6 Deviation matrix

For a numeric check with observed value `x` and threshold `θ`, the breach is

* `gap = x − θ` for a maximum limit, or
* `gap = θ − x` for a minimum limit.

The smallest tier whose `within ≥ gap` turns the FAIL into a DEVIATION and names the approver. With
tiers configured, a DEVIATION is never classified Likely. Without tiers, the check stays FAIL.

Bank B's FOIR tiers, for example:

* up to 5 pp → Branch Credit Manager;
* up to 10 pp → Regional Credit Head.

### 15.7 Approval-probability model (calibrated logistic scorecard)

1. **Features.** The engine derives 13 features for each historical case:
   * FOIR, credit score, utilisation, LTI, years in employment, income stability, residual share and
     the share of documents available;
   * max DPD (24 m), enquiries (6 m), credit history length and bounces (6 m);
   * monthly P(FOIR > cap).

   PAN, age, employment category and loan purpose are not features.
2. **Binning.** Quantile bins plus a separate missing bin. Adjacent bins are merged until each holds at
   least 5 % of cases and the Weight of Evidence is monotone in the expected direction.
   * Weight of Evidence, with `a = 0.5`:
     `WoE_b = ln[((g_b + a)/(G + a·m)) / ((b_b + a)/(B + a·m))]`.
   * Information Value: `IV = Σ (g_b/G − b_b/B)·WoE_b`.
   * Features with IV < 0.02 are dropped.
3. **Fit.** Logistic regression on the WoE values, maximising `ℓ(β) − (λ/2)‖β₁:‖²` by Newton–Raphson /
   IRLS. Each step solves `β ← β + (XᵀWX + Λ)⁻¹(Xᵀ(y − p) − Λβ)`. The covariance is
   `Σ = (XᵀWX + Λ)⁻¹`. A feature whose coefficient comes out negative (against its WoE direction, a sign
   of collinearity) is removed and the model refitted.
4. **Points scaling.**
   * `score = offset + factor·logit`
   * `factor = PDO/ln 2`
   * `offset = base − factor·ln(base_odds)`
   * Defaults: PDO 20, base score 600 at odds 50:1.
   * Points for each feature bin: `factor·β_j·WoE_jb`.
5. **Calibration and split.** Platt scaling `p = σ(a·z + b)`, or isotonic regression by
   pool-adjacent-violators. The data is split by decision date: oldest 60 % for training, next 20 % for
   calibration, newest 20 % for an out-of-time test.
6. **Evaluation (test period).**
   * AUC, computed as Mann–Whitney U with tied ranks averaged, and Gini = 2·AUC − 1.
   * KS = max |TPR − FPR|.
   * Brier score and log-loss.
   * Calibration error over 10 bins: `ECE = Σ_k (n_k/N)·|p̄_k − ȳ_k|`.
   * Population Stability Index between training and test scores: `PSI = Σ (a − e) ln(a/e)`.
   * Subgroup approval rates and the adverse-impact ratio (group rate ÷ best group rate; below 0.8 is
     flagged) by employment category and age band. These attributes are monitored, not used as inputs.
7. **Gate.** The model is used only if it has at least 500 training cases, at least 50 cases of each
   class, test AUC of at least 0.65 and ECE of at most 0.05 (all configurable per bank).
8. **Prediction.**
   * `z = xᵀβ` with a 95 % interval `z ± 1.96·√(xᵀΣx)` (delta method), mapped through the calibrator.
   * Reason codes: the features whose bin earns the most points below that feature's best bin.
   * No probability is produced for "Insufficient information" cases.
   * The probability never changes the classification, and the UI states that a hard FAIL stands.

**Interpretation.** The probability estimates how often *this bank* approved similar applications in its
own history. It inherits any bias in those decisions, which is why subgroup monitoring runs on every
training. The bundled `synthetic_history.py` produces invented decisions for demonstration only, and
every output from a model trained on it is labelled SYNTHETIC.

## 16. Version 3: decision advisor

`advisor.advise(assessment, config)` answers four questions for the manager and the customer: how much
can be offered, why, why not, and what would improve the outcome. It adds no new credit criteria. It
rearranges the existing limits so that each one yields an amount, then re-runs the engine on concrete
alternatives.

### 16.1 Affordability limits as linear functions

Let `Y` be adjusted monthly income, `X` existing EMIs, `O` other monthly obligations (0 if not given),
`c` the FOIR cap, `A(R, n) = (1 − (1 + R/1200)^−n) / (R/1200)` the annuity factor (`n` when `R = 0`).
Every affordability limit has the form

```
L_k(n) = A(R_k, n) · (1 − b_k) · max(0, a_k·Y − X − d_k)
```

| k | a_k | d_k | R_k | b_k | Source in v2 |
|---|---|---|---|---|---|
| FOIR | c | 0 | R | `safety_buffer_min` | §5.2, §6 |
| Stress | (c + e)(1 − δ) | 0 | R + s if floating, else R | 0 | §15.4: stressed FOIR ≤ c + e |
| Living-cost buffer | 1 | O + `min_residual_income` | R | 0 | §5.6 indicator threshold |
| Income volatility | c·k/v | 0 | R | 0 | §15.4: P(FOIR > c) ≤ p_max |

Here `e = max_stressed_foir_excess`, `δ = income_shock_pct`, `s = rate_shock_bps/100` and `v` is the
variability factor of §4.

**Volatility limit.** Monthly income `Ỹ` is lognormal with mean `m = Y/v` and coefficient of variation
CV, so `σ² = ln(1 + CV²)` and `μ = ln m − σ²/2`. Its p-quantile is

```
q_p = exp(μ + σ z_p) = m · k,   k = exp(σ z_p − σ²/2),   z_p = Φ⁻¹(p)
```

`P(Ỹ < (X + E)/c) ≤ p_max` holds exactly when `(X + E)/c ≤ q_{p_max}`, that is when
`E ≤ c·k·Y/v − X`. The quantile is linear in the mean, so the limit stays linear in `Y`.

Two ceilings complete the set: `12·mult·Y` (income multiple) and `L_max` (product maximum).

### 16.2 Maximum eligible and recommended amount

```
n_used = clamp(n_requested, n_min, n_max),   n_max = min(product max tenure, 12·(max_age_at_maturity − age))
M(n)   = min_k L_k(n)                                     (lower envelope; binding limit = arg min)
Maximum eligible = roundDown(M(n_used))                    (INR 1,000 steps below 1 lakh, INR 5,000 above)
Recommended      = min(request, Maximum eligible)          if ≥ product minimum and no blocker remains
```

**Properties.**

* `M = max(0, min_k(affine in Y and X))`, so wherever `M > 0` it is concave and piecewise linear in `Y`.
  It never decreases in `Y` and never increases in `X`.
* `A(R, n)` increases in `n` and decreases in `R`, so `M` never decreases with tenure and never increases
  with the rate.
* `M ≤` the upper end of the §6 capacity range, because §6 uses the FOIR limit and both ceilings.
* At amount `M`, the FOIR check (§7 #10) and the stress-test check (§15.4) both pass by construction.

**Blockers.** The engine is re-run at the recommended amount and `n_used`. Any FAIL that remains (other
than the amount and tenure product limits) is a blocker that no amount or tenure would fix, such as
minimum age, KYC, a write-off or the negative list. When no amount is feasible, the FOIR, amount, tenure
and age-at-maturity checks are not counted as blockers, because a different structure could cure them.

### 16.3 Closed-form counter-offers

Each limit is linear, so the conditions under which a requested amount `L` fits at tenure `n` invert
directly:

```
Usable income needed:        Y* = max( L/(12·mult),  max_k [ L/(A_k(1−b_k)) + X + d_k ] / a_k )
Documented income needed:    V* = Y* / (category factor × variability factor)        (verification factor = 1)
Existing EMIs allowed:       X* = min_k [ a_k·Y − d_k − L/(A_k(1−b_k)) ]             (only if X* ≥ 0, L ≤ 12·mult·Y, L ≤ L_max)
Shortest tenure that fits:   n* = min{ n ∈ [n_used, n_max] : M(n) ≥ L }               (M is monotone in n)
```

Each counter-offer is re-assessed by the full engine, including rules, deviations, stress and score:

* the reduced amount;
* the full amount over `n*`;
* the maximum at `n_max`;
* the full amount with EMIs cut to `X*`;
* the full amount with documented income `V*`.

The recommended option maximises, in lexicographic order:

1. the classification rank;
2. not needing the customer to change their finances;
3. being the request itself;
4. the amount;
5. lower total interest.

Only options with no FAIL are eligible.

### 16.4 Sensitivities

Where a linear limit `b` binds: `∂M/∂Y = A_b(1 − b_b)·a_b` and `∂M/∂X = −A_b(1 − b_b)`. Where the income
multiple binds: `∂M/∂Y = 12·mult` and `∂M/∂X = 0`. The advisor reports finite differences, which remain
correct when the binding limit changes:

* +INR 1,000 usable income;
* −INR 1,000 existing EMIs;
* −1 percentage point of rate;
* +12 months of tenure.

### 16.5 Inverse normal CDF

`Φ⁻¹(p)` uses Acklam's rational approximation (relative error < 1.2·10⁻⁹) in three regions
(`p < 0.02425`, central, upper tail), followed by one Halley step:

```
e = Φ(x) − p,   u = e·√(2π)·exp(x²/2),   x ← x − u / (1 + x·u/2)
```

This gives near machine precision. `Φ(x) = ½(1 + erf(x/√2))`.

### 16.6 Why / why not

* **Why (strengths).**
  * Factors with a sub-score of 75 or more, ordered by contribution `w'_i·s_i`.
  * A clean bureau record (DPD 0 and no write-off).
  * A verified PAN.
  * A request that fits every limit.
* **Why not.**
  * Blockers: FAIL checks.
  * Exceptions: DEVIATION checks, with the approving authority.
  * Concerns: REVIEW checks, factors with a sub-score of 50 or less ordered by points lost
    `w'_i(100 − s_i)`, and a request above the maximum eligible amount (naming the binding limit).
  * Missing: REVIEW caused by missing data.

  Duplicate titles are removed.

### 16.7 Counterfactual improvement plan

For each applicable action `j`, with its input change `Δ_j`:

* lower card balance to `utilisation_warn × limit`;
* raise the credit score to the next curve breakpoint or the bank minimum;
* verify income;
* provide the missing mandatory documents;
* lower EMIs to `X*` (or by half if `X*` does not exist);
* reduce enquiries in 3 months to 0;
* reduce bounces to 0;
* reduce DPD in 12 months to 0;
* clear a write-off;
* reach the minimum time in job, minimum age or minimum income;
* verify the PAN;
* switch to a fixed rate.

```
a_j = a ⊕ Δ_j,   r_j = assess(a_j, config)
report  Δscore = score(r_j) − score(r),   ΔM = M(r_j) − M(r),   class(r) → class(r_j)
rank by (class-rank gain, Δscore, ΔM) descending;   combined row: a ⊕ Δ_1 ⊕ … ⊕ Δ_J
```

These are effects **under the configured APEX rules**. They are not predictions of bureau behaviour or of
a lender's decision, and the UI and the letter say so. Actions that need new data (a bureau report, a PAN,
income history) are listed without numbers.

### 16.8 Worked example (profile WORKED-EX, Bank B, 48 months at 12%)

`A(12, 48) = 37.9740`. `Y = 60,000` (verified, salaried), `X = 12,000`, `c = 0.50`, `b_min = 0.05`,
`e = 0.05`, `δ = 0.10`, fixed rate. The CV is the salaried category default of 0.05 (an assumption), so
`σ = 0.04997`, `z_0.20 = −0.84162` and `k = exp(σz − σ²/2) = 0.95762`.

| Limit | Calculation | Maximum (INR) |
|---|---|---|
| FOIR | 37.974 × 0.95 × (30,000 − 12,000) | 649,355 |
| Stress | 37.974 × (0.55 × 0.9 × 60,000 − 12,000) | 672,139 |
| Living cost | 37.974 × (60,000 − 12,000 − 10,000) | 1,443,010 |
| **Volatility** | 37.974 × (0.5 × 0.95762 × 60,000 − 12,000) | **635,252** |
| Income multiple | 2 × 12 × 60,000 | 1,440,000 |
| Product maximum | – | 2,500,000 |

* **Maximum eligible:** INR 635,000, with EMI INR 16,722.
* **Usable income for the full INR 800,000:** `Y* = (800,000/37.974 + 12,000)/0.47881 = 69,061`.
* **Existing EMIs allowed:** `X* = 0.47881 × 60,000 − 800,000/37.974 = 7,662`.
* **Shortest tenure:** none up to 60 months fits INR 800,000. `M(60)` gives INR 750,000, which is
  re-assessed as Likely and recommended.
* **Sensitivities:** +INR 1,000 of income gives `0.47881 × 37.974 × 1,000 = +18,182`; −INR 1,000 of EMIs
  gives +37,974.
* **Improvement plan:** cutting EMIs to INR 7,600 raises the score from 67.8 to 82.4 and the maximum to
  INR 800,000, and moves the case from Borderline to Likely.

## 17. Fictional rate suggestion

Let `B` be the selected fictional bank's base annual rate, `C` the exact bureau score, `X` existing monthly
EMIs and `Y` adjusted monthly income. The fictional pricing rule is:

```
s = X / Y
R_raw = B + Δ_score(C) + Δ_burden(s)
R_demo = min(R_high, max(R_low, R_raw))
```

The adjustments are measured in **percentage points**. The score adjustment is −1 for `C ≥ 800`, −0.5
for `750 ≤ C < 800`, zero for `700 ≤ C < 750`, +1 for `650 ≤ C < 700`, and +2 below 650. The burden
adjustment is −0.5 when `s ≤ 0.15`, +0.5 when `s ≥ 0.35`, and zero otherwise. The bank's configured
low and high rates bound the result. These values are fictional; they are neither inferred from the
approval scorecard nor estimated from repayment outcomes.

The formula does not depend on the manager's assumed rate. No suggestion is made when the exact score,
positive usable income, existing EMIs or employment category is missing; when fixed hard criteria fail;
or when existing EMIs already meet the bank's FOIR cap. A rate is conditional on a loan structure that
passes the bank's checks. Applying it to a scenario triggers a fresh assessment of EMI, APR and capacity.

## 18. Path forward

* Replace the placeholder parameters with verified lender policy, and keep `last_verified` current.
* Feed bureau, KYC and bank-statement data from integrations instead of manual entry.
* Replace the fictional pricing tiers with a verified bank pricing policy and independently validate it.
* Train the approval scorecard on the bank's own labelled history, with independent validation, then
  monitor PSI and subgroup adverse impact.
* Have legal and compliance review the customer-letter wording, consent flows and KFS alignment.
