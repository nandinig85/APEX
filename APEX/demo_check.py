"""Run repeatable, synthetic-only acceptance checks for the APEX first draft.

Usage: python demo_check.py
No files, applicant records, or model artefacts are written.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from time import perf_counter

from data_lab import batch_compare, synthetic_sample_csv, validate_csv
from eligibility import available_banks
from engine import assess
from pricing import recommend_rate
from profiles import PROFILES
from reports import report_json

FIXED_TIMESTAMP = "2026-10-27T00:00:00+00:00"


def run_demo_checks() -> dict:
    started = perf_counter()
    banks = available_banks(with_overlay=False)
    classifications = Counter()
    priced = 0
    total = 0
    for cfg in banks.values():
        for profile in PROFILES:
            first = assess(profile.applicant, cfg, timestamp=FIXED_TIMESTAMP, use_model=False)
            second = assess(profile.applicant, cfg, timestamp=FIXED_TIMESTAMP, use_model=False)
            if first != second:
                raise AssertionError(f"Assessment changed for {cfg.meta.bank_id}/{profile.key}")
            price = recommend_rate(first, cfg)
            if price.status == "available":
                priced += 1
                assert price.range_low_pct <= price.recommended_rate_pct <= price.range_high_pct
                assert abs(price.base_rate_pct + sum(f.adjustment_pp for f in price.factors)
                           - price.recommended_rate_pct) < 0.011
            exported = report_json(first, price)
            if profile.applicant.pan and profile.applicant.pan.get_secret_value() in exported:
                raise AssertionError(f"Raw PAN appeared in report for {profile.key}")
            classifications[first.classification.value] += 1
            total += 1

    batch_cfg = banks["bank_b"]
    intake = validate_csv(synthetic_sample_csv(batch_cfg, 5000), cfg=batch_cfg)
    if intake.total_rows != 5000 or len(intake.valid) != 5000 or intake.errors:
        raise AssertionError("The 5,000-row synthetic intake did not validate")
    comparison = batch_compare(intake, batch_cfg)
    if len(comparison) != 5000:
        raise AssertionError("The 5,000-row comparison is incomplete")
    return {"checked_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "configurations": len(banks), "golden_profile_assessments": total,
            "illustrative_rates_available": priced, "classification_mix": dict(classifications),
            "batch_rows": len(comparison), "elapsed_seconds": round(perf_counter() - started, 2)}


if __name__ == "__main__":
    import json
    print(json.dumps(run_demo_checks(), indent=2))
