"""Predictor interface for statistical outcome models.

``engine.assess`` asks a predictor for an approval-probability estimate after
the rules and readiness score are computed. The estimate is shown alongside
the rule outcome and never changes it.

* ``NullPredictor`` - no model (used while training, and in tests).
* ``approval_model.ActiveModelPredictor`` - the bank's saved scorecard, used
  only if it passed that bank's ``model_gate`` (sample size, AUC, calibration).

Every model output carries ``Evidence.MODEL`` so it is labelled separately
from rule-based results.
"""
from __future__ import annotations

from typing import Optional, Protocol

from models import Assessment, BankConfig, ProbabilityEstimate


class OutcomePredictor(Protocol):
    name: str

    def predict(self, assessment: Assessment, config: BankConfig) -> Optional[ProbabilityEstimate]: ...


class NullPredictor:
    name = "none"

    def predict(self, assessment: Assessment, config: BankConfig) -> None:
        return None
