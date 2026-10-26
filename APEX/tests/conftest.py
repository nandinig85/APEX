import os
import tempfile

# Keep tests hermetic: models trained in the app and custom rules saved from the
# Policy Studio must not change test outcomes.
os.environ["APEX_MODEL_DIR"] = tempfile.mkdtemp(prefix="apex_models_")

import pytest  # noqa: E402

from eligibility import available_banks  # noqa: E402
from profiles import PROFILES_BY_KEY  # noqa: E402


@pytest.fixture(scope="session")
def banks():
    return available_banks(with_overlay=False)


@pytest.fixture(scope="session")
def bank_a(banks):
    return banks["bank_a"]


@pytest.fixture(scope="session")
def bank_b(banks):
    return banks["bank_b"]


@pytest.fixture(scope="session")
def bank_c(banks):
    return banks["bank_c"]


@pytest.fixture
def worked():
    return PROFILES_BY_KEY["WORKED-EX"].applicant.model_copy(deep=True)


def variant(applicant, **changes):
    """Copy of an applicant with fields changed, re-validated (PAN preserved)."""
    data = applicant.to_state()
    data.update(changes)
    return type(applicant).model_validate(data)
