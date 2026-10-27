"""Privacy and guardrails for the fictional demo and future pilot sandbox."""
import hashlib
import hmac

import pytest
from pydantic import SecretStr

from advisor import advise
from conftest import variant
from engine import assess
from kyc import pan_hash, pan_key
from policy_rules import FIELD_REGISTRY, FairnessError, FieldSpec, check_field_allowed
from reports import customer_letter, report_json, report_markdown


def test_pan_hash_is_hmac_and_pilot_requires_private_key(monkeypatch):
    pan = SecretStr("ABCDE1234F")
    monkeypatch.setenv("APEX_PAN_SALT", "a private test key")
    expected = hmac.new(b"a private test key", b"ABCDE1234F", hashlib.sha256).hexdigest()
    assert pan_hash(pan) == expected
    monkeypatch.delenv("APEX_PAN_SALT")
    monkeypatch.setenv("APEX_MODE", "pilot_sandbox")
    with pytest.raises(RuntimeError, match="APEX_PAN_SALT"):
        pan_key()
    with pytest.raises(RuntimeError, match="APEX_PAN_SALT"):
        pan_hash(pan)


def test_raw_pan_stays_out_of_all_exports(bank_b, worked):
    raw_pan = "ABCDE1234F"
    applicant = variant(worked, pan=raw_pan, pan_consent=True)
    assessment = assess(applicant, bank_b, timestamp="2026-10-27T00:00:00+00:00", use_model=False)
    advice = advise(assessment, bank_b)
    for exported in (report_json(assessment), report_markdown(assessment, advice),
                     customer_letter(assessment, advice)):
        assert raw_pan not in exported
    assert applicant.to_state()["pan"] == raw_pan  # Server-side session state only.


def test_blocklist_matches_complete_tokens_without_false_positives(monkeypatch):
    monkeypatch.setitem(FIELD_REGISTRY, "company", FieldSpec("Company", "bool", "input"))
    monkeypatch.setitem(FIELD_REGISTRY, "rename", FieldSpec("Rename", "bool", "input"))
    assert check_field_allowed("company").label == "Company"
    assert check_field_allowed("rename").label == "Rename"
    for field in ("pan", "company_name", "caste_category", "pin_code"):
        with pytest.raises(FairnessError):
            check_field_allowed(field)
