# APEX first-draft status

APEX is ready for a **synthetic-data presentation**. It is not ready for real
customer records or a live bank pilot. No HDFC policy, pricing, customer data,
or decision history has been imported or verified.

## Working in this draft

- Ten-page Streamlit demonstration: input, assessment, explanations, advice,
  simulator, reports, policy rules, model training, guide, and Data Lab.
- Three clearly fictional lender configurations and a transparent, fictional
  rate recommendation. The manager can apply that rate and recalculate EMI,
  affordability and APR.
- Optional birth date for exact age-at-maturity calculations. Entered completed
  age remains the fallback.
- Synthetic CSV and Excel templates, 200- or 5,000-row examples, strict row
  validation, unit conversion, selected product and policy checks, duplicate
  application checks, row error export, and comparison with recorded dummy
  decisions. Excel formulas are refused.
- PAN consent, masking and HMAC hashing. A private key is required in the
  `pilot_sandbox` run mode; local demo data can use the displayed demo key.
- Validated dummy rows can feed the optional approval-model lab. Its outputs
  describe historical dummy decisions and do not override policy checks.
- `python demo_check.py` checks 93 synthetic profile assessments, rate arithmetic,
  PAN exclusion from JSON reports, and a 5,000-row batch. The automated test
  suite runs with `python -m pytest -q` after installing developer requirements.

## Work before a real pilot

1. Obtain approved product rules and pricing policy from the bank, with a named
   owner, effective dates, source documents and a review/sign-off process.
   Replace fictional values only after independent validation.
2. Define a field mapping and data contract for bank exports. Add a bank-approved
   de-identification process, retention rules and access controls before handling
   any non-dummy records.
3. Add authenticated roles, maker/checker approval for policy changes and
   overrides, tamper-evident audit storage, and secure deployment.
4. Independently validate the decision rules and model on representative bank
   data, including calibration, drift, subgroup effects, failure cases and
   human review procedures. Historical decision agreement alone is insufficient.
5. Review customer-facing wording, APR/KFS calculations, KYC handling and any
   legal or regulatory statements with the bank's credit, legal and compliance
   teams before external use.

For the presentation, use only the supplied synthetic profiles or dummy files.
Do not place real applicant data, secrets, model artifacts or pilot exports in
the GitHub repository.
