"""PAN handling for the KYC stage.

PAN is used only to identify the customer for KYC, de-duplication and (later)
a consented bureau pull. It is never a scoring input. Outside this module it
appears only masked (``ABXXX1234X`` -> ``XXXXX1234X``) or as a keyed hash.

Structure of a PAN (Income Tax Department):
  chars 1-3  alphabetic series
  char 4     holder type: P individual, C company, H HUF, F firm, A AOP,
             T trust, B body of individuals, L local authority,
             J artificial juridical person, G government
  char 5     first letter of surname (individuals) or of the entity's name
  chars 6-9  sequence number
  char 10    alphabetic check character

Only the format is validated here. Real verification (name/DOB match, Aadhaar
link status) happens in the bank's KYC system and is recorded by the manager
as ``pan_status``.
"""
from __future__ import annotations

import hashlib
import hmac
import os
from typing import Optional

from pydantic import SecretStr

HOLDER_TYPES = {
    "P": "Individual", "C": "Company", "H": "Hindu Undivided Family", "F": "Firm / LLP",
    "A": "Association of Persons", "T": "Trust", "B": "Body of Individuals", "L": "Local authority",
    "J": "Artificial juridical person", "G": "Government",
}

_DEMO_KEY = "apex-prototype-key-do-not-use-with-real-data"


def pan_key(mode: Optional[str] = None) -> bytes:
    """A private HMAC key is required outside the fictional demo mode."""
    selected_mode = mode or os.environ.get("APEX_MODE", "demo")
    if selected_mode not in {"demo", "pilot_sandbox"}:
        raise ValueError("APEX_MODE must be demo or pilot_sandbox")
    key = os.environ.get("APEX_PAN_SALT")
    if not key:
        if selected_mode != "demo":
            raise RuntimeError("APEX_PAN_SALT must be set before using pilot_sandbox mode")
        key = _DEMO_KEY
    return key.encode("utf-8")


def demo_key_in_use() -> bool:
    return os.environ.get("APEX_MODE", "demo") == "demo" and not os.environ.get("APEX_PAN_SALT")


def _raw(pan: Optional[SecretStr]) -> Optional[str]:
    return None if pan is None else pan.get_secret_value()


def mask_pan(pan: Optional[SecretStr]) -> str:
    raw = _raw(pan)
    return "Not provided" if raw is None else "XXXXX" + raw[5:9] + "X"


def pan_hash(pan: Optional[SecretStr]) -> Optional[str]:
    """HMAC-SHA-256, for de-duplication without storing the PAN."""
    raw = _raw(pan)
    return None if raw is None else hmac.new(pan_key(), raw.encode("utf-8"), hashlib.sha256).hexdigest()


def holder_type(pan: Optional[SecretStr]) -> Optional[str]:
    raw = _raw(pan)
    return None if raw is None else raw[3]


def kyc_summary(pan: Optional[SecretStr], consent: bool, status: str) -> dict:
    h = holder_type(pan)
    digest = pan_hash(pan)
    return {
        "pan_masked": mask_pan(pan),
        "pan_hash_prefix": digest[:12] if digest else None,
        "holder_type": h,
        "holder_type_label": HOLDER_TYPES.get(h, "Unknown") if h else None,
        "consent": consent,
        "status": status,
    }
